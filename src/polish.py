"""Polish a raw transcript with Local (LM Studio), Claude, GPT, or Gemini.

Audio never leaves the machine. Only transcript text is sent to the chosen backend.
API keys are read from the macOS Keychain, never from disk or logs.
"""

from __future__ import annotations

import json
import re
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

DEFAULT_LEAD_PROMPT = """你是一個藏傳佛教的大勘布，學過和圓滿五部大論和大圓滿密法。請依原文順序，校正能從上下文確定的同音錯字、請一個字一個字的教對，不改句字的結構，不刪句字，但修掉不必要的字如“哈哈哈” ， 把錯別字改正，前後文要合理對應，都是用佛教用語，佛學術語、人名和標點。保留原句、數字、例子、否定詞和講者的口語。禁止摘要、改寫、刪句、補句或新增標題。遇到不確定的詞，保留原字並在旁加上〔待回聽〕。只輸出校對後的逐字稿。
詞彙參考：慈誠羅珠堪布、米拉日巴、龍樹菩薩、《親友書》、論典、五部大論、善根、根機、殊勝、出離心、菩提心、不墮兩邊、常見、斷見、順緣、違緣、因明、依止五火、阿賴耶識、苦行、精進、五欲、少欲知足。請依上下文判斷，尤其核對出離心與菩提心、順緣與違緣。謝謝。"""

SYSTEM_PROMPT = """请继续按上面的要求做逐字校对。补充说明：
1. 只改正能从上下文确定的同音错字、佛教术语、人名和标点，不要摘要、改写、删句、补句或新增标题。
2. 文中的〔待回聽〕或〔待回听〕必须原样保留，不得删除或改写。
3. 专有名词以词库为准，尤其核对出离心与菩提心、顺缘与违缘、论典、不堕两边、因明、依止五火。
4. 可以直接输出校对后的逐字稿；若使用 JSON，格式必须是 {"paragraphs": ["段落1", "段落2"]}，heading 必须是空字符串。
"""

SUMMARY_PROMPT = """请为下面已经润色的佛教开示文字写一段不超过200字的摘要。
只概括讲者实际说到的内容，不要添加新观点。只用简体中文。不要用 Markdown。直接输出摘要正文。"""


REVIEW_MARKERS = ("〔待回聽〕", "〔待回听〕")


def lead_prompt(cfg: Optional[dict] = None) -> str:
    cfg = cfg or load_config()
    user = cfg.get("polish_lead_prompt")
    if isinstance(user, str) and user.strip():
        return user.strip()
    return DEFAULT_LEAD_PROMPT


def polish_system_prompt(cfg: Optional[dict] = None) -> str:
    cfg = cfg or load_config()
    return lead_prompt(cfg) + "\n\n" + SYSTEM_PROMPT


def protect_review_markers(text: str) -> tuple[str, dict[str, str]]:
    held: dict[str, str] = {}
    out = text or ""
    for i, marker in enumerate(REVIEW_MARKERS):
        key = f"<<<ASRREV{i}>>>"
        if marker in out:
            out = out.replace(marker, key)
            held[key] = marker
    return out, held


def restore_review_markers(text: str, held: dict[str, str]) -> str:
    out = text or ""
    for key, marker in held.items():
        out = out.replace(key, marker)
    return out


def _content_chars(text: str) -> str:
    out: list[str] = []
    for ch in text or "":
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf" or "\u0f00" <= ch <= "\u0fff":
            out.append(ch)
    return "".join(out)


def _number_tokens(text: str) -> list[str]:
    return re.findall(r"[0-9]+|[零〇一二三四五六七八九十百千万亿兩两]{2,}", text or "")


def _negation_counts(text: str) -> dict[str, int]:
    keys = "不非没未無无莫勿否"
    return {ch: (text or "").count(ch) for ch in keys}


def _split_polish_chunks(text: str, target: int = 1200, max_len: int = 1500) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    pieces = re.split(r"(?<=[。！？；!?;\n])", text)
    parts: list[str] = []
    buf = ""
    for piece in pieces:
        if not piece:
            continue
        if buf and len(buf) + len(piece) > max_len:
            parts.append(buf.strip())
            buf = piece
        else:
            buf += piece
            if len(buf) >= target:
                parts.append(buf.strip())
                buf = ""
    if buf.strip():
        parts.append(buf.strip())
    return parts or [text]


def _polish_is_safe(raw: str, polished: str) -> tuple[bool, str]:
    raw_c = _content_chars(raw)
    pol_c = _content_chars(polished)
    if not raw_c:
        return True, ""
    ratio = len(pol_c) / max(len(raw_c), 1)
    if ratio < 0.85 or ratio > 1.15:
        return False, f"长度变化 {ratio:.0%}（超过 15%）"
    missing_nums = [tok for tok in _number_tokens(raw) if tok not in (polished or "")]
    if missing_nums:
        return False, "数字被删：" + "、".join(missing_nums[:6])
    phrases = ("并非", "不是", "没有", "不必", "不能", "不会", "不要", "不需要", "不得")
    missing = [ph for ph in phrases if ph in raw and ph not in polished]
    if missing:
        return False, "否定词被删：" + "、".join(missing)
    if raw.count("非") > polished.count("非"):
        return False, "否定词减少：非"
    return True, ""



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
        "temperature": 0.0,
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
        "temperature": 0.0,
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
        "temperature": 0.0,
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
        "generationConfig": {"temperature": 0.0},
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

    system = polish_system_prompt(cfg)
    include_headings = bool(cfg.get("include_headings", False))
    chunks = _split_polish_chunks(text)
    paragraphs: list[str] = []
    headings: list[str] = []
    used = wanted
    fallback_notes: list[str] = []

    def _plain_paras(out: str) -> list[str]:
        parsed = _parse_json_obj(out)
        if parsed and (parsed.get("paragraphs") or parsed.get("heading") is not None):
            paras = [str(p).strip() for p in (parsed.get("paragraphs") or []) if str(p).strip()]
            heading = str(parsed.get("heading") or "").strip()
            return paras or _fallback_paragraphs(out), heading
        return _fallback_paragraphs(out), ""

    def _run(prompt: str, provider: str, mode: str) -> str:
        return _call_backend(provider, mode, prompt, system, cfg)

    try:
        for i, chunk in enumerate(chunks):
            prompt = _user_payload(chunk)
            out = _run(prompt, wanted, auth_mode)
            paras, heading = _plain_paras(out)
            polished = "\n".join(paras).strip() or (out or "").strip()
            ok, reason = _polish_is_safe(chunk, polished)
            if not ok:
                polished = chunk.rstrip() + "〔待回聽〕"
                paras = _fallback_paragraphs(polished)
                fallback_notes.append(f"第{i+1}段校对未通过（{reason}），已回退原文并标〔待回聽〕")
            paragraphs.extend(paras)
            if include_headings and heading:
                headings.append(heading)
        used = wanted
    except Exception as exc:
        if wanted != "local":
            try:
                paragraphs = []
                headings = []
                fallback_notes = []
                for i, chunk in enumerate(chunks):
                    prompt = _user_payload(chunk)
                    out = _run(prompt, "local", "login")
                    paras, heading = _plain_paras(out)
                    polished = "\n".join(paras).strip() or (out or "").strip()
                    ok, reason = _polish_is_safe(chunk, polished)
                    if not ok:
                        polished = chunk.rstrip() + "〔待回聽〕"
                        paras = _fallback_paragraphs(polished)
                        fallback_notes.append(f"第{i+1}段校对未通过（{reason}），已回退原文并标〔待回聽〕")
                    paragraphs.extend(paras)
                    if include_headings and heading:
                        headings.append(heading)
                used = "local"
                note = f"原选 {wanted} 不可用（{_safe_err(exc)}），已改用本地千问修饰。"
            except Exception as exc2:
                paragraphs = _fallback_paragraphs(text)
                used = "none"
                note = f"修饰失败（{_safe_err(exc)}；本地亦失败：{_safe_err(exc2)}），已输出未修饰初稿。"
        else:
            paragraphs = _fallback_paragraphs(text)
            used = "none"
            note = f"本地千问修饰失败（{_safe_err(exc)}），已输出未修饰初稿。"

    if not paragraphs:
        paragraphs = _fallback_paragraphs(text)
        if not note:
            note = "修饰结果为空，已回退到初稿。"
            used = "none"
    if fallback_notes:
        extra = "；".join(fallback_notes)
        note = (note + "；" + extra).strip("；") if note else extra
    return {
        "heading": headings[0] if include_headings and headings else "",
        "paragraphs": paragraphs,
        "provider_used": used,
        "note": note,
        "raw_used": used == "none",
        "lead_chars": len(lead_prompt(cfg)),
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


