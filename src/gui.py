#!/usr/bin/env python3
"""Desktop window for 为我闻思. Click the Dock icon to open it."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config import (
    CLOUD_PROVIDERS,
    PROVIDER_LABELS,
    load_config,
    save_config,
)
from glossary import ensure_glossary
from media import MEDIA_EXTS
from paths import asr_home, default_out_dir
from polish import DEFAULT_LEAD_PROMPT, cli_login_status, keychain_set, login_command
from settings import first_run_if_needed
from subtitles import is_media_url
from transcribe import _transcribe_arg

IPC_HOST = "127.0.0.1"
IPC_PORT = 17834
APP_NAME = "为我闻思"


def _adopt_app_identity() -> None:
    """Make the menu bar and process name 为我闻思, even if Cocoa started as Python."""
    try:
        from Foundation import NSBundle, NSProcessInfo

        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_NAME
            info["CFBundleDisplayName"] = APP_NAME
        NSProcessInfo.processInfo().setProcessName_(APP_NAME)
    except Exception:
        pass


def _running_inside_app() -> bool:
    parts = " ".join([sys.executable or "", *sys.argv])
    return "为我闻思.app" in parts or "语音转文字.app" in parts


def _apply_app_icon() -> None:
    try:
        from AppKit import NSApplication, NSImage

        candidates = [
            Path(sys.executable).resolve().parents[1] / "Resources" / "AppIcon.icns",
            Path("/Applications/为我闻思.app/Contents/Resources/AppIcon.icns"),
            Path.home() / "Applications/为我闻思.app/Contents/Resources/AppIcon.icns",
        ]
        for path in candidates:
            if path.is_file():
                img = NSImage.alloc().initWithContentsOfFile_(str(path))
                if img:
                    NSApplication.sharedApplication().setApplicationIconImage_(img)
                    return
    except Exception:
        pass


LANG_LABELS = {
    "zh": "中文",
    "en": "英文→中文",
    "bo": "藏文",
    "auto": "自动识别",
}

STAGE_STATUS = {
    "download": "转录中",
    "extract": "转录中",
    "transcribe": "转录中",
    "proofread": "校对中",
    "polish": "修饰中",
    "write": "修饰中",
    "done": "完成",
}


def ui_dir() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here / "ui", here.parent / "ui", asr_home() / "ui"):
        if (cand / "index.html").is_file():
            return cand
    return asr_home() / "ui"


def display_path(path: Path) -> str:
    path = path.expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    home = Path.home()
    try:
        rel = path.relative_to(home)
        return str(Path("~") / rel)
    except ValueError:
        return str(path)


def list_media(folder: Path) -> list[Path]:
    found: list[Path] = []
    try:
        for item in folder.rglob("*"):
            if any(part.startswith(".") for part in item.parts):
                continue
            if item.is_file() and item.suffix.lower() in MEDIA_EXTS:
                found.append(item)
    except OSError:
        return []
    return sorted(found)


def _try_send_ipc(payload: dict) -> bool:
    try:
        sock = socket.create_connection((IPC_HOST, IPC_PORT), timeout=0.6)
    except OSError:
        return False
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        path = payload.get("_path", "/enqueue")
        req = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {IPC_HOST}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8") + body
        sock.sendall(req)
        sock.recv(256)
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _ping() -> bool:
    try:
        sock = socket.create_connection((IPC_HOST, IPC_PORT), timeout=0.4)
        sock.sendall(b"GET /ping HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        data = sock.recv(128)
        sock.close()
        return b"pong" in data or b"200" in data
    except OSError:
        return False


class AppState:
    def __init__(self) -> None:
        first_run_if_needed()
        self.lock = threading.RLock()
        self.window = None
        self.jobs: list[dict[str, Any]] = []
        self.busy = False
        self.lang = "zh"
        cfg = load_config()
        out = str(cfg.get("out_dir") or "").strip()
        self.out_dir = Path(out).expanduser() if out else default_out_dir()

    def snapshot(self) -> dict[str, Any]:
        cfg = load_config()
        status = cli_login_status()
        with self.lock:
            jobs = list(self.jobs)
            out_dir = self.out_dir
            lang = self.lang
        template = str(cfg.get("template_path") or "").strip()
        return {
            "jobs": jobs,
            "busy": any(j.get("status") not in {"完成", "失败", "等待"} for j in jobs) or self.busy,
            "lang": lang,
            "out_dir": str(out_dir),
            "out_dir_display": display_path(out_dir),
            "settings": {
                "polish_provider": cfg.get("polish_provider", "local"),
                "auth_mode": cfg.get("auth_mode") or {},
                "include_raw_appendix": bool(cfg.get("include_raw_appendix", True)),
                "include_summary": bool(cfg.get("include_summary", False)),
                "include_headings": bool(cfg.get("include_headings", False)),
                "polish_lead_prompt": (cfg.get("polish_lead_prompt") or "").strip() or DEFAULT_LEAD_PROMPT,
                "polish_lead_prompt_default": DEFAULT_LEAD_PROMPT,
                "english_output": cfg.get("english_output") or "bilingual",
                "zh_asr_model": cfg.get("zh_asr_model") or "buddhist",
                "template_path": template,
                "template_display": Path(template).name if template else "默认版式",
                "login_status": {
                    name: {
                        "installed": bool(info.get("installed")),
                        "logged_in": bool(info.get("logged_in")),
                    }
                    for name, info in status.items()
                },
            },
            "labels": PROVIDER_LABELS,
        }

    def add_jobs(self, items: list[dict[str, str]]) -> list[str]:
        ids: list[str] = []
        with self.lock:
            existing = {(j.get("kind"), j.get("source")) for j in self.jobs if j.get("status") in {"等待", "转录中", "校对中", "修饰中"}}
            for item in items:
                key = (item["kind"], item["source"])
                if key in existing:
                    continue
                job = {
                    "id": uuid.uuid4().hex[:10],
                    "kind": item["kind"],
                    "source": item["source"],
                    "name": item["name"],
                    "status": "等待",
                    "percent": 0,
                    "stage": "waiting",
                    "started_at": None,
                    "finished_at": None,
                    "message": "等待开始",
                    "error": "",
                    "docx": "",
                }
                self.jobs.insert(0, job)
                existing.add(key)
                ids.append(job["id"])
        if ids:
            self.ensure_worker()
        return ids

    def find(self, job_id: str) -> Optional[dict[str, Any]]:
        with self.lock:
            for job in self.jobs:
                if job["id"] == job_id:
                    return job
        return None

    def update(self, job_id: str, **fields: Any) -> None:
        with self.lock:
            for job in self.jobs:
                if job["id"] == job_id:
                    job.update(fields)
                    return

    def ensure_worker(self) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
        threading.Thread(target=self._run_queue, name="speech2docx-queue", daemon=True).start()

    def _run_queue(self) -> None:
        try:
            while True:
                with self.lock:
                    pending = [j for j in self.jobs if j["status"] == "等待"]
                    job = pending[-1] if pending else None  # FIFO despite insert(0)
                    if pending:
                        # jobs inserted at front, so oldest is at the end
                        job = pending[-1]
                if not job:
                    return
                self._run_one(job)
        finally:
            with self.lock:
                self.busy = False
            if any(j.get("status") == "等待" for j in self.jobs):
                self.ensure_worker()

    def _run_one(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        self.update(job_id, status="转录中", stage="transcribe", started_at=time.time(), percent=1, message="准备中…", error="")

        def on_progress(stage: str, percent=None, message: str = "") -> None:
            status = STAGE_STATUS.get(stage, "转录中")
            if stage == "done":
                status = "修饰中" if not job.get("docx") else "完成"
            fields: dict[str, Any] = {
                "status": status if status != "完成" else "修饰中",
                "stage": stage,
                "message": message or "",
            }
            if percent is not None:
                fields["percent"] = int(percent)
            self.update(job_id, **fields)

        try:
            result = _transcribe_arg(
                job["source"],
                lang=self.lang,
                out_dir=self.out_dir,
                polish=None,
                do_notify=True,
                open_docx=False,
                on_progress=on_progress,
            )
            docx = str(result.get("docx") or "")
            self.update(
                job_id,
                status="完成",
                stage="done",
                finished_at=time.time(),
                percent=100,
                message="已保存 Word 文档",
                docx=docx,
                error="",
            )
        except Exception as exc:
            raw = str(exc) or exc.__class__.__name__
            # Keep the last meaningful ERROR line so the queue stays readable.
            lines = [ln.strip() for ln in raw.replace("下载失败：", "").splitlines() if ln.strip()]
            useful = [ln for ln in lines if ln.startswith("ERROR:") or "失败" in ln]
            short = (useful[-1] if useful else lines[-1] if lines else raw)
            if short.startswith("ERROR:"):
                short = short[6:].strip()
            if len(short) > 180:
                short = short[:177] + "…"
            self.update(
                job_id,
                status="失败",
                finished_at=time.time(),
                percent=0,
                message="",
                error=short,
            )
            from transcribe import log

            log(f"GUI ERROR {job.get('name')}: {exc}\n{traceback.format_exc()}")


STATE = AppState()


class JsApi:
    def get_state(self) -> dict[str, Any]:
        return STATE.snapshot()

    def set_lang(self, lang: str) -> dict[str, Any]:
        if lang in LANG_LABELS:
            STATE.lang = lang
        return STATE.snapshot()

    def pick_files(self) -> dict[str, Any]:
        import webview

        paths = STATE.window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=(
                "音视频 (*.mp3;*.mp4;*.m4a;*.wav;*.mov;*.mkv;*.webm;*.aiff;*.aac;*.flac;*.m4v)",
                "所有文件 (*.*)",
            ),
        )
        return self.enqueue_files(list(paths or []))

    def pick_folder(self) -> dict[str, Any]:
        import webview

        paths = STATE.window.create_file_dialog(webview.FileDialog.FOLDER)
        if not paths:
            return STATE.snapshot()
        folder = Path(paths[0])
        media = list_media(folder)
        if not media:
            snap = STATE.snapshot()
            snap["notice"] = f"这个文件夹里没有可识别的音频或视频：{display_path(folder)}"
            return snap
        items = [{"kind": "file", "source": str(p), "name": p.name} for p in media]
        STATE.add_jobs(items)
        return STATE.snapshot()

    def pick_out_dir(self) -> dict[str, Any]:
        import webview

        paths = STATE.window.create_file_dialog(webview.FileDialog.FOLDER)
        if paths:
            STATE.out_dir = Path(paths[0])
            cfg = load_config()
            cfg["out_dir"] = str(STATE.out_dir)
            save_config(cfg)
        return STATE.snapshot()

    def enqueue_files(self, paths: list[str]) -> dict[str, Any]:
        items = []
        skipped = 0
        for raw in paths or []:
            path = Path(raw).expanduser()
            if path.is_dir():
                for media in list_media(path):
                    items.append({"kind": "file", "source": str(media), "name": media.name})
            elif path.is_file() and path.suffix.lower() in MEDIA_EXTS:
                items.append({"kind": "file", "source": str(path), "name": path.name})
            else:
                skipped += 1
        STATE.add_jobs(items)
        snap = STATE.snapshot()
        if skipped and not items:
            snap["notice"] = "这些文件不是支持的音频或视频格式。"
        return snap

    def enqueue_url(self, url: str) -> dict[str, Any]:
        url = (url or "").strip()
        snap_base = STATE.snapshot()
        if not url:
            snap_base["notice"] = "请先贴上 YouTube 链接。"
            return snap_base
        if not is_media_url(url):
            snap_base["notice"] = "这不像是可用的视频链接，请检查后再试。"
            return snap_base
        parsed = urlparse(url)
        name = "YouTube 视频"
        if "v=" in parsed.query:
            for part in parsed.query.split("&"):
                if part.startswith("v=") and len(part) > 2:
                    name = f"YouTube（{part[2:][:12]}）"
                    break
        elif parsed.path.strip("/"):
            name = f"YouTube（{parsed.path.strip('/').split('/')[-1][:16]}）"
        STATE.add_jobs([{"kind": "url", "source": url, "name": name}])
        return STATE.snapshot()

    def open_docx(self, job_id: str) -> bool:
        job = STATE.find(job_id)
        if not job or not job.get("docx"):
            return False
        import subprocess

        subprocess.run(["/usr/bin/open", job["docx"]], check=False)
        return True

    def reveal_docx(self, job_id: str) -> bool:
        job = STATE.find(job_id)
        if not job or not job.get("docx"):
            return False
        import subprocess

        subprocess.run(["/usr/bin/open", "-R", job["docx"]], check=False)
        return True

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = load_config()
        provider = str(payload.get("polish_provider") or cfg.get("polish_provider") or "local")
        if provider in {"local", "claude", "gpt", "gemini"}:
            cfg["polish_provider"] = provider
        if "include_raw_appendix" in payload:
            cfg["include_raw_appendix"] = bool(payload.get("include_raw_appendix"))
        if "include_summary" in payload:
            cfg["include_summary"] = bool(payload.get("include_summary"))
        if "include_headings" in payload:
            cfg["include_headings"] = bool(payload.get("include_headings"))
        if "polish_lead_prompt" in payload:
            lead = str(payload.get("polish_lead_prompt") or "")
            cfg["polish_lead_prompt"] = lead if lead.strip() else DEFAULT_LEAD_PROMPT
        if payload.get("zh_asr_model") in {"buddhist", "base"}:
            cfg["zh_asr_model"] = payload["zh_asr_model"]
        if payload.get("english_output") in {"bilingual", "zh"}:
            cfg["english_output"] = payload["english_output"]
        if provider in CLOUD_PROVIDERS and payload.get("auth_mode") in {"login", "api_key"}:
            cfg.setdefault("auth_mode", {})[provider] = payload["auth_mode"]
        cfg["configured"] = True
        save_config(cfg)
        return STATE.snapshot()

    def save_api_key(self, provider: str, key: str) -> dict[str, Any]:
        provider = (provider or "").lower()
        key = (key or "").strip()
        snap = STATE.snapshot()
        if provider not in CLOUD_PROVIDERS:
            snap["notice"] = "请先选择 Claude、GPT 或 Gemini。"
            return snap
        if not key:
            snap["notice"] = "请输入 API 金钥。"
            return snap
        keychain_set(provider, key)
        cfg = load_config()
        cfg.setdefault("auth_mode", {})[provider] = "api_key"
        cfg["configured"] = True
        save_config(cfg)
        snap = STATE.snapshot()
        snap["notice"] = "金钥已保存到钥匙圈。"
        return snap

    def start_login(self, provider: str) -> dict[str, Any]:
        provider = (provider or "").lower()
        snap = STATE.snapshot()
        if provider not in CLOUD_PROVIDERS:
            snap["notice"] = "请先选择 Claude、GPT 或 Gemini。"
            return snap
        cmd = login_command(provider)
        if not cmd:
            snap["notice"] = "还没安装这一家的登录程序。"
            return snap
        cfg = load_config()
        cfg.setdefault("auth_mode", {})[provider] = "login"
        save_config(cfg)
        import subprocess

        script = f'''
tell application "Terminal"
  activate
  do script {json.dumps(cmd)}
end tell
'''
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False)
        snap = STATE.snapshot()
        snap["notice"] = "已打开登录窗口，请在终端里完成登录。"
        return snap

    def open_glossary(self) -> bool:
        path = ensure_glossary()
        import subprocess

        subprocess.run(["/usr/bin/open", "-a", "TextEdit", str(path)], check=False)
        return True

    def pick_template(self) -> dict[str, Any]:
        import webview

        paths = STATE.window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("Word 文档 (*.docx)",),
        )
        if paths:
            cfg = load_config()
            cfg["template_path"] = str(Path(paths[0]))
            save_config(cfg)
        return STATE.snapshot()

    def reset_template(self) -> dict[str, Any]:
        cfg = load_config()
        cfg["template_path"] = ""
        save_config(cfg)
        snap = STATE.snapshot()
        snap["notice"] = "已恢复为内置默认版式。"
        return snap

    def open_help(self) -> bool:
        webbrowser.open("https://github.com/jimbo5168/speech2docx")
        return True

    def clear_finished(self) -> dict[str, Any]:
        with STATE.lock:
            STATE.jobs = [j for j in STATE.jobs if j.get("status") not in {"完成", "失败"}]
        return STATE.snapshot()


def _hide_extra_dock_icon() -> None:
    """If we are not running from the official .app, hide a second Python Dock icon."""
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        if _running_inside_app():
            return
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass


def _focus_window() -> None:
    try:
        if STATE.window:
            STATE.window.show()
            STATE.window.restore()
        from AppKit import NSApplication, NSApp

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if NSApp:
            NSApp.activateIgnoringOtherApps_(True)
    except Exception:
        pass


def _start_ipc() -> ThreadingHTTPServer:
    state = STATE

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/ping"):
                self._send(200, {"ok": True, "pong": True})
            elif self.path.startswith("/state") or self.path.startswith("/queue"):
                self._send(200, state.snapshot())
            else:
                self._send(404, {"ok": False})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                data = {}
            if self.path.startswith("/focus"):
                threading.Thread(target=_focus_window, daemon=True).start()
                self._send(200, {"ok": True})
                return
            if self.path.startswith("/lang"):
                lang = str(data.get("lang") or "").strip()
                if lang in LANG_LABELS:
                    state.lang = lang
                self._send(200, {"ok": True, "lang": state.lang})
                return
            if self.path.startswith("/enqueue"):
                items = []
                for path in data.get("files") or []:
                    p = Path(path)
                    if p.is_dir():
                        for media in list_media(p):
                            items.append({"kind": "file", "source": str(media), "name": media.name})
                    else:
                        items.append({"kind": "file", "source": str(p), "name": p.name})
                for url in data.get("urls") or []:
                    items.append({"kind": "url", "source": url, "name": "YouTube 视频"})
                # also accept argv-style list
                for path in data.get("paths") or []:
                    items.append({"kind": "file", "source": str(path), "name": Path(path).name})
                ids = state.add_jobs(items)
                threading.Thread(target=_focus_window, daemon=True).start()
                self._send(200, {"ok": True, "ids": ids})
                return
            self._send(404, {"ok": False})

    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = True

        def server_bind(self):
            # Avoid socket.getfqdn() -> gethostbyaddr hang on macOS mDNS.
            TCPServer.server_bind(self)
            self.server_name = IPC_HOST
            self.server_port = self.socket.getsockname()[1]

    server = LocalServer((IPC_HOST, IPC_PORT), Handler)
    threading.Thread(target=server.serve_forever, name="speech2docx-ipc", daemon=True).start()
    return server


def _bind_drop_target(window) -> None:
    try:
        zone = window.dom.get_element("#drop-zone") or window.dom.body
        if not zone:
            return

        def handle_drop(event: dict) -> None:
            files = (event.get("dataTransfer") or {}).get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
            if paths:
                STATE.add_jobs(
                    [{"kind": "file", "source": p, "name": Path(p).name} for p in paths]
                )

        zone.events.drop += handle_drop
    except Exception:
        pass


def _collect_launch_items(argv: list[str]) -> dict[str, list[str]]:
    files: list[str] = []
    urls: list[str] = []
    for raw in argv:
        if raw.startswith("-"):
            continue
        if is_media_url(raw):
            urls.append(raw)
        else:
            files.append(raw)
    return {"files": files, "urls": urls, "paths": files, "_path": "/enqueue"}


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    first_run_if_needed()
    payload = _collect_launch_items(argv)
    if _ping():
        if payload["files"] or payload["urls"]:
            _try_send_ipc(payload)
        _try_send_ipc({"_path": "/focus"})
        return 0

    _start_ipc()
    print('IPC listening on', IPC_PORT, flush=True)
    if payload["files"] or payload["urls"]:
        items = []
        for path in payload["files"]:
            p = Path(path)
            if p.is_dir():
                items.extend({"kind": "file", "source": str(m), "name": m.name} for m in list_media(p))
            else:
                items.append({"kind": "file", "source": str(p), "name": p.name})
        for url in payload["urls"]:
            items.append({"kind": "url", "source": url, "name": "YouTube 视频"})
        STATE.add_jobs(items)

    _adopt_app_identity()
    import webview

    api = JsApi()
    index = ui_dir() / "index.html"
    if not index.is_file():
        raise SystemExit(f"找不到界面文件：{index}")
    window = webview.create_window(
        APP_NAME,
        url=index.as_uri(),
        js_api=api,
        width=880,
        height=780,
        min_size=(740, 620),
        text_select=True,
    )
    STATE.window = window
    window.events.loaded += lambda: _bind_drop_target(window)
    def on_shown():
        _adopt_app_identity()
        _apply_app_icon()
        _focus_window()
        _hide_extra_dock_icon()

    window.events.shown += on_shown
    print('starting webview', flush=True)
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
