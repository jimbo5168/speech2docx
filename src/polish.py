"""Polish a raw transcript with Local (LM Studio), Claude, GPT, or Gemini.

Audio never leaves the machine. Only transcript text is sent to the chosen backend.
API keys are read from the macOS Keychain, never from disk or logs.
"""

from __future__ import annotations

import json
import time
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from config import CLOUD_PROVIDERS, KEYCHAIN_SERVICE, load_config
from glossary import as_prompt_block, as_translation_glossary
from paths import asr_home, work_dir

SYSTEM_PROMPT = """你是一位严谨的汉传与藏传佛教讲记文字编辑。请润色下面的语音识别初稿。

要求：
1. 修正同音或近音错字，尤其是佛教用语。若初稿写成「委员」，在讲修行障碍的语境里应改为「违缘」；其他类似错误一并按佛教惯用词改正。
2. 删除口语填充词（啊、呃、嗯、这个这个、那个那个等），但不要改变原意。
3. 补上正确的标点，并分成自然段落。
4. 忠实于原意：不要摘要代替正文，不要增补讲者没说的内容，不要改写教义。
5. 专有名词以词库为准。
6. 只用简体中文输出（藏文原文保持藏文，不要翻译）。
7. 严格按 JSON 输出，不要Markdown，不要额外说明。格式：
{"heading": "可选的本节小标题，若看不出主题则用空字符串", "paragraphs": ["段落1", "段落2"]}
"""

SUMMARY_PROMPT = """请为下面已经润色的佛教开示文字写一段不超过200字的摘要。
只概括讲者实际说到的内容，不要添加新观点。只用简体中文。不要用 Markdown。直接输出摘要正文。"""


def _keychain_get(provider: str) -> Optional[str]:
    service = KEYCHAIN_SERVICE.get(provider)
    if not service:
        return None
    try:
        proc = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                os.environ.get("USER", ""),
                "-s",
                service,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            key = (proc.stdout or "").strip()
            return key or None
    except Exception:
        return None
    return None


def keychain_set(provider: str, secret: str) -> None:
    service = KEYCHAIN_SERVICE[provider]
    account = os.environ.get("USER", "")
    subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
            secret,
            "-U",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )


def find_codex() -> Optional[Path]:
    found = shutil.which("codex")
    if found:
        return Path(found)
    for cand in (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
        Path.home() / ".codex/plugins/.plugin-appserver/codex-cli/bin/codex",
    ):
        if cand.exists():
            return cand
    return None


def find_claude() -> Optional[Path]:
    found = shutil.which("claude")
    return Path(found) if found else None


def find_gemini() -> Optional[Path]:
    for name in ("gemini", "antigravity"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def cli_login_status() -> dict[str, dict[str, Any]]:
    """Report whether official CLIs are installed and logged in. Never returns secrets."""
    out: dict[str, dict[str, Any]] = {}

    claude = find_claude()
    out["claude"] = {"installed": bool(claude), "path": str(claude) if claude else None, "logged_in": False}
    if claude:
        for args in ([str(claude), "auth", "status"], [str(claude), "status"]):
            try:
                p = subprocess.run(args, capture_output=True, text=True, timeout=20)
                text = (p.stdout or "") + (p.stderr or "")
                if p.returncode == 0 and "not logged" not in text.lower():
                    out["claude"]["logged_in"] = True
                    break
            except Exception:
                continue

    codex = find_codex()
    out["gpt"] = {"installed": bool(codex), "path": str(codex) if codex else None, "logged_in": False}
    if codex:
        try:
            p = subprocess.run([str(codex), "login", "status"], capture_output=True, text=True, timeout=20)
            text = (p.stdout or "") + (p.stderr or "")
            if p.returncode == 0 and "logged in" in text.lower():
                out["gpt"]["logged_in"] = True
        except Exception:
            pass

    gemini = find_gemini()
    out["gemini"] = {"installed": bool(gemini), "path": str(gemini) if gemini else None, "logged_in": False}
    if gemini:
        for args in ([str(gemini), "auth", "status"], [str(gemini), "whoami"]):
            try:
                p = subprocess.run(args, capture_output=True, text=True, timeout=20)
                text = (p.stdout or "") + (p.stderr or "")
                if p.returncode == 0 and "not logged" not in text.lower() and "log in" not in text.lower():
                    out["gemini"]["logged_in"] = True
                    break
            except Exception:
                continue
    return out


def login_command(provider: str) -> str:
    if provider == "claude":
        if find_claude():
            return "claude auth login"
        return "brew install --cask claude-code && claude auth login"
    if provider == "gpt":
        codex = find_codex()
        if codex:
            return f'"{codex}" login'
        return "codex login"
    if provider == "gemini":
        if find_gemini():
            return "gemini"
        return "brew install --cask antigravity-cli && gemini"
    return ""


def _glossary_section() -> str:
    block = as_prompt_block()
    if not block:
        return ""
    return "佛教词库（出现近音错字时优先用这些词）：\n" + block + "\n\n"


def _user_payload(raw_text: str) -> str:
    return _glossary_section() + "初稿如下：\n" + raw_text.strip()


def _repair_json_newlines(s: str) -> str:
    """Escape raw newlines/tabs inside JSON strings, a common LLM slip."""
    out: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                out.append(ch)
                esc = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                continue
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _parse_json_obj(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for cand in candidates:
        for payload in (cand, _repair_json_newlines(cand)):
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            if isinstance(obj, dict) and any(k in obj for k in ("paragraphs", "pairs", "heading", "title_zh", "heading_zh")):
                return obj
    return None



def _assistant_text(result: dict) -> str:
    """LM Studio / Qwen may put the answer in reasoning_content when thinking is on."""
    msg = (result.get("choices") or [{}])[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if content:
        return content
    for key in ("reasoning_content", "reasoning", "thinking"):
        alt = msg.get(key)
        if isinstance(alt, str) and alt.strip():
            return alt.strip()
    return ""


def _http_json(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def polish_local(prompt: str, system: str, cfg: dict) -> str:
    url = cfg.get("lms_url", "http://127.0.0.1:1234/v1").rstrip("/") + "/chat/completions"
    model = cfg.get("models", {}).get("local", "qwen/qwen3.6-35b-a3b")
    # Try to start the server if needed (no-op if already running)
    lms = shutil.which("lms") or str(Path.home() / ".lmstudio/bin/lms")
    if Path(lms).exists():
        try:
            subprocess.run([lms, "server", "start"], capture_output=True, timeout=20)
        except Exception:
            pass
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system + "\n/no_think"},
            {"role": "user", "content": "/no_think\n" + prompt},
        ],
    }
    # Common LMS / Qwen knobs to keep thinking off.
    # On this Qwen 3.6 + LM Studio build, only reasoning_effort=none actually
    # disables thinking; enable_thinking / /no_think alone still fill reasoning_content.
    for extra in (
        {"chat_template_kwargs": {"enable_thinking": False}},
        {"enable_thinking": False},
        {"reasoning_effort": "none"},
    ):
        body.update(extra)
    body["max_tokens"] = 8192
    headers = {"Content-Type": "application/json"}
    last_err = None
    for attempt in range(2):
        try:
            result = _http_json(url, body, headers, timeout=900)
            text = _assistant_text(result)
            if not text:
                raise RuntimeError("模型返回了空内容")
            return text
        except Exception as exc:
            last_err = exc
            if attempt == 0:
                time.sleep(2)
                continue
            raise
    raise last_err  # pragma: no cover


def polish_claude_api(prompt: str, system: str, cfg: dict, key: str) -> str:
    model = cfg.get("models", {}).get("claude", "claude-sonnet-5-5")
    payload = {
        "model": model,
        "max_tokens": 8192,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    result = _http_json("https://api.anthropic.com/v1/messages", payload, headers)
    parts = result.get("content") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def polish_openai_api(prompt: str, system: str, cfg: dict, key: str) -> str:
    model = cfg.get("models", {}).get("gpt", "gpt-6.1-terra")
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    result = _http_json("https://api.openai.com/v1/chat/completions", payload, headers)
    return result["choices"][0]["message"]["content"]


def polish_gemini_api(prompt: str, system: str, cfg: dict, key: str) -> str:
    model = cfg.get("models", {}).get("gemini", "gemini-3.8-flash")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
    }
    headers = {"Content-Type": "application/json"}
    result = _http_json(url, payload, headers)
    cands = result.get("candidates") or []
    parts = (((cands[0] if cands else {}).get("content") or {}).get("parts") or [])
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def _run_cli(args: list[str], stdin: Optional[str] = None, timeout: int = 300) -> str:
    proc = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err[:500] or f"cli exit {proc.returncode}")
    return (proc.stdout or "").strip()


def polish_claude_login(prompt: str, system: str) -> str:
    claude = find_claude()
    if not claude:
        raise RuntimeError("未安装 Claude Code CLI")
    return _run_cli(
        [str(claude), "-p", system + "\n\n" + prompt, "--output-format", "text"],
        timeout=300,
    )


def polish_gpt_login(prompt: str, system: str, cfg: dict) -> str:
    codex = find_codex()
    if not codex:
        raise RuntimeError("未找到 Codex CLI")
    out = work_dir() / "codex-last.txt"
    model = cfg.get("models", {}).get("gpt")
    args = [
        str(codex),
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "-s",
        "read-only",
        "-o",
        str(out),
        system + "\n\n" + prompt,
    ]
    if model:
        args[2:2] = ["-m", model]
    _run_cli(args, timeout=300)
    if out.exists():
        return out.read_text(encoding="utf-8").strip()
    raise RuntimeError("Codex 没有写出结果")


def polish_gemini_login(prompt: str, system: str) -> str:
    gemini = find_gemini()
    if not gemini:
        raise RuntimeError("未安装 Gemini CLI")
    return _run_cli([str(gemini), "-p", system + "\n\n" + prompt], timeout=300)


def _call_backend(provider: str, auth_mode: str, prompt: str, system: str, cfg: dict) -> str:
    if provider == "local":
        return polish_local(prompt, system, cfg)
    if provider == "claude":
        if auth_mode == "login":
            return polish_claude_login(prompt, system)
        key = _keychain_get("claude")
        if not key:
            raise RuntimeError("缺少 Claude API 金钥")
        return polish_claude_api(prompt, system, cfg, key)
    if provider == "gpt":
        if auth_mode == "login":
            return polish_gpt_login(prompt, system, cfg)
        key = _keychain_get("gpt")
        if not key:
            raise RuntimeError("缺少 GPT API 金钥")
        return polish_openai_api(prompt, system, cfg, key)
    if provider == "gemini":
        if auth_mode == "login":
            return polish_gemini_login(prompt, system)
        key = _keychain_get("gemini")
        if not key:
            raise RuntimeError("缺少 Gemini API 金钥")
        return polish_gemini_api(prompt, system, cfg, key)
    raise RuntimeError(f"未知的修饰后端：{provider}")


def polish_text(raw_text: str, provider: Optional[str] = None) -> dict:
    """Return {paragraphs, heading, provider_used, note, raw_used}."""
    cfg = load_config()
    wanted = (provider or cfg.get("polish_provider") or "local").lower()
    if wanted not in {"local", "claude", "gpt", "gemini", "none"}:
        wanted = "local"
    auth_mode = (cfg.get("auth_mode") or {}).get(wanted, "login")
    note = ""
    used = wanted
    text = raw_text.strip()
    if not text or wanted == "none":
        return {
            "heading": "",
            "paragraphs": [text] if text else [],
            "provider_used": "none",
            "note": "未修饰" if wanted == "none" else "正文为空",
            "raw_used": True,
        }

    prompt = _user_payload(text)
    try:
        out = _call_backend(wanted, auth_mode, prompt, SYSTEM_PROMPT, cfg)
        parsed = _parse_json_obj(out)
        if not parsed:
            # model may have returned plain text
            paras = [p.strip() for p in out.split("\n") if p.strip()]
            parsed = {"heading": "", "paragraphs": paras or [out.strip()]}
        used = wanted
    except Exception as exc:
        if wanted != "local":
            try:
                out = _call_backend("local", "login", prompt, SYSTEM_PROMPT, cfg)
                parsed = _parse_json_obj(out) or {
                    "heading": "",
                    "paragraphs": [p.strip() for p in out.split("\n") if p.strip()],
                }
                used = "local"
                note = f"原选 {wanted} 不可用（{_safe_err(exc)}），已改用本地千问修饰。"
            except Exception as exc2:
                parsed = {"heading": "", "paragraphs": _fallback_paragraphs(text)}
                used = "none"
                note = f"修饰失败（{_safe_err(exc)}；本地亦失败：{_safe_err(exc2)}），已输出未修饰初稿。"
        else:
            parsed = {"heading": "", "paragraphs": _fallback_paragraphs(text)}
            used = "none"
            note = f"本地千问修饰失败（{_safe_err(exc)}），已输出未修饰初稿。"

    paragraphs = [str(p).strip() for p in (parsed.get("paragraphs") or []) if str(p).strip()]
    if not paragraphs:
        paragraphs = _fallback_paragraphs(text)
        if not note:
            note = "修饰结果为空，已回退到初稿。"
            used = "none"
    return {
        "heading": str(parsed.get("heading") or "").strip(),
        "paragraphs": paragraphs,
        "provider_used": used,
        "note": note,
        "raw_used": used == "none",
    }


def make_summary(paragraphs: list[str], provider_used: str) -> str:
    text = "\n".join(paragraphs).strip()
    if not text or provider_used == "none":
        return ""
    cfg = load_config()
    auth_mode = (cfg.get("auth_mode") or {}).get(provider_used, "login")
    try:
        out = _call_backend(provider_used, auth_mode, text[:12000], SUMMARY_PROMPT, cfg)
        return (out or "").strip().replace("```", "")
    except Exception:
        if provider_used != "local":
            try:
                out = _call_backend("local", "login", text[:12000], SUMMARY_PROMPT, cfg)
                return (out or "").strip().replace("```", "")
            except Exception:
                return ""
        return ""


def _safe_err(exc: BaseException) -> str:
    msg = str(exc)
    # Strip query strings that might contain API keys
    if "key=" in msg:
        msg = msg.split("key=")[0] + "key=***"
    for token in ("Bearer ", "x-api-key", "sk-", "AIza"):
        if token.lower() in msg.lower():
            return exc.__class__.__name__
    return msg[:200]


def _fallback_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in text.replace("\r", "\n").split("\n") if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


TRANSLATE_PROMPT = """你是一位严谨的汉传与藏传佛教讲记译者。请把下面的英文讲课记录译成规范简体中文。
规则：
1. 忠实原文，不增删教义，不添解释。
2. 专有名词必须按词库翻译，例如 bodhicitta 译为菩提心，ngondro 或 preliminary practices 译为前行。
3. 只输出 JSON，不要 Markdown，不要思考过程。格式：
{"title_zh":"中文标题，若无明显标题可留空","heading_zh":"本节小标题，可留空","pairs":[{"en":"英文原段","zh":"对应中文"}]}
4. pairs 按自然段划分，en 保持原意不断句错乱，zh 为对应译文。
"""


def translate_english(
    paragraphs: list[str],
    provider: Optional[str] = None,
    title: str = "",
) -> dict:
    """Translate English paragraphs into Simplified Chinese.

    Returns {pairs:[{en,zh}], title_zh, heading_zh, provider_used, note}.
    """
    cfg = load_config()
    wanted = (provider or cfg.get("polish_provider") or "local").lower()
    if wanted not in {"local", "claude", "gpt", "gemini"}:
        wanted = "local"
    auth_mode = (cfg.get("auth_mode") or {}).get(wanted, "login")
    paras = [str(p).strip() for p in paragraphs if str(p).strip()]
    if not paras:
        return {"pairs": [], "title_zh": "", "heading_zh": "", "provider_used": "none", "note": "没有可翻译的英文"}

    glossary = as_translation_glossary()
    payload = ""
    if glossary:
        payload += "佛教英译词库：\n" + glossary + "\n\n"
    if title:
        payload += "英文标题：" + title + "\n\n"
    payload += "英文正文：\n" + "\n\n".join(paras)

    def _parse(out: str) -> dict:
        parsed = _parse_json_obj(out) or {}
        pairs = []
        raw_pairs = parsed.get("pairs") or parsed.get("paragraphs") or []
        if isinstance(raw_pairs, list):
            for item in raw_pairs:
                if isinstance(item, dict):
                    en = str(item.get("en") or item.get("english") or item.get("src") or "").strip()
                    zh = str(item.get("zh") or item.get("chinese") or item.get("text") or "").strip()
                    if en or zh:
                        pairs.append({"en": en, "zh": zh})
                elif isinstance(item, str) and item.strip():
                    pairs.append({"en": "", "zh": item.strip()})
        if not pairs:
            # plain text fallback: treat whole output as Chinese, keep English source
            zh_text = (out or "").strip().replace("```", "")
            if zh_text and not zh_text.lstrip().startswith("{"):
                for src, zh in zip(paras, [zh_text] + [""] * (len(paras) - 1)):
                    pairs.append({"en": src, "zh": zh})
        return {
            "title_zh": str(parsed.get("title_zh") or "").strip(),
            "heading_zh": str(parsed.get("heading_zh") or parsed.get("heading") or "").strip(),
            "pairs": pairs,
        }

    note = ""
    used = wanted
    try:
        out = _call_backend(wanted, auth_mode, payload, TRANSLATE_PROMPT, cfg)
        result = _parse(out)
        if not result["pairs"] or not any(p.get("zh") for p in result["pairs"]):
            raise RuntimeError("翻译结果为空")
        used = wanted
    except Exception as exc:
        if wanted != "local":
            try:
                out = _call_backend("local", "login", payload, TRANSLATE_PROMPT, cfg)
                result = _parse(out)
                if not result["pairs"] or not any(p.get("zh") for p in result["pairs"]):
                    raise RuntimeError("翻译结果为空")
                used = "local"
                note = f"原选 {wanted} 不可用（{_safe_err(exc)}），已改用本地千问翻译。"
            except Exception as exc2:
                result = {"title_zh": "", "heading_zh": "", "pairs": [{"en": p, "zh": ""} for p in paras]}
                used = "none"
                note = f"翻译失败（{_safe_err(exc)}；本地亦失败：{_safe_err(exc2)}），已只保留英文原文。"
        else:
            result = {"title_zh": "", "heading_zh": "", "pairs": [{"en": p, "zh": ""} for p in paras]}
            used = "none"
            note = f"本地千问翻译失败（{_safe_err(exc)}），已只保留英文原文。"
    result["provider_used"] = used
    result["note"] = note
    return result


