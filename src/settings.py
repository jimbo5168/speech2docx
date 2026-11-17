"""First-run chooser and Settings menu (AppleScript dialogs)."""

from __future__ import annotations

import subprocess
from typing import Optional

from config import PROVIDER_LABELS, PROVIDERS, load_config, save_config
from glossary import ensure_glossary
from polish import local_qwen_available, cli_login_status, find_claude, find_codex, find_gemini, keychain_set, login_command


def _osascript(script: str, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or "cancelled")
    return (proc.stdout or "").strip()


def choose_provider(prompt: str = "请选择用来修饰文字稿的模型：") -> Optional[str]:
    labels = [PROVIDER_LABELS[p] for p in PROVIDERS if p != "local" or local_qwen_available(load_config())]
    listed = ", ".join(f'"{x}"' for x in labels)
    script = f'''
set opts to {{{listed}}}
set picked to choose from list opts with prompt "{prompt}" default items {{"{PROVIDER_LABELS['none']}"}} OK button name "确定" cancel button name "取消"
if picked is false then return ""
return item 1 of picked
'''
    try:
        raw = _osascript(script)
    except Exception:
        return None
    if not raw:
        return None
    for key, label in PROVIDER_LABELS.items():
        if raw == label:
            return key
    return None


def _choose_auth_mode(provider: str) -> Optional[str]:
    script = f'''
set opts to {{"登录（用已有订阅）", "API 金钥"}}
set picked to choose from list opts with prompt "「{PROVIDER_LABELS[provider]}」要用哪一种方式？" default items {{"登录（用已有订阅）"}} OK button name "确定" cancel button name "取消"
if picked is false then return ""
return item 1 of picked
'''
    try:
        raw = _osascript(script)
    except Exception:
        return None
    if "金钥" in raw:
        return "api_key"
    if raw:
        return "login"
    return None


def _prompt_api_key(provider: str) -> Optional[str]:
    label = PROVIDER_LABELS[provider]
    script = f'''
try
  set r to display dialog "请输入 {label} 的 API 金钥。金钥只保存在「钥匙圈」，不会写入文件或日志。" default answer "" with hidden answer buttons {{"取消", "保存"}} default button "保存" with title "为我闻思"
  if button returned of r is "保存" then return text returned of r
  return ""
on error
  return ""
end try
'''
    try:
        raw = _osascript(script)
    except Exception:
        return None
    return raw or None


def _open_login_terminal(provider: str) -> None:
    cmd = login_command(provider).replace('"', '\\"')
    script = f'''
tell application "Terminal"
  activate
  do script "{cmd}"
end tell
'''
    try:
        _osascript(script)
    except Exception:
        pass


def _offer_login_if_needed(provider: str) -> None:
    status = cli_login_status().get(provider) or {}
    if status.get("logged_in"):
        _osascript(f'''
display dialog "{PROVIDER_LABELS[provider]} 已经登录，可以直接用来修饰文字。" buttons {{"好"}} default button "好" with title "为我闻思"
''')
        return
    msg = f"{PROVIDER_LABELS[provider]} 的命令行工具尚未登录"
    if not status.get("installed"):
        msg = f"还没有安装 {PROVIDER_LABELS[provider]} 的命令行工具。可以在打开的终端里安装并登录。"
    script = f'''
set r to display dialog "{msg}要现在打开终端，由你自己登录吗？" buttons {{"以后再说", "打开终端"}} default button "打开终端" with title "为我闻思"
return button returned of r
'''
    try:
        if "打开" in _osascript(script):
            _open_login_terminal(provider)
    except Exception:
        pass


def first_run_if_needed() -> None:
    """Ensure a default local setup exists. No blocking dialog; settings live in the app window."""
    cfg = load_config()
    if cfg.get("configured"):
        return
    cfg["polish_provider"] = cfg.get("polish_provider") or "none"
    cfg["configured"] = True
    save_config(cfg)


def show_current() -> str:
    cfg = load_config()
    p = cfg.get("polish_provider", "none")
    mode = (cfg.get("auth_mode") or {}).get(p, "")
    models = cfg.get("models") or {}
    st = cli_login_status()
    lines = [
        f"当前修饰模型：{PROVIDER_LABELS.get(p, p)}",
        f"认证方式：{'登录' if mode == 'login' else 'API 金钥' if p != 'local' else '本地'}",
        f"模型编号（可在设定文件里改）：{models.get(p, '')}",
        f"Word 模板：{cfg.get('template_path') or '默认版式'}",
    ]
    for name, info in st.items():
        inst = "已安装" if info.get("installed") else "未安装"
        logn = "已登录" if info.get("logged_in") else "未登录"
        lines.append(f"{PROVIDER_LABELS[name]} CLI：{inst}，{logn}")
    return "\n".join(lines)


def open_glossary() -> None:
    path = ensure_glossary()
    subprocess.run(["/usr/bin/open", "-a", "TextEdit", str(path)], check=False)



def _choose_template() -> None:
    script = """
set theFile to choose file with prompt "选择以后输出要用的 Word 模板（.docx）" of type {"org.openxmlformats.wordprocessingml.document"}
POSIX path of theFile
"""
    try:
        raw = _osascript(script)
    except Exception:
        return
    path = (raw or "").strip()
    if not path:
        return
    from pathlib import Path
    chosen = Path(path).expanduser()
    if not chosen.is_file():
        _osascript('display dialog "找不到所选文件。" buttons {"好"} default button "好" with title "为我闻思"')
        return
    cfg = load_config()
    cfg["template_path"] = str(chosen)
    save_config(cfg)
    _osascript(f'display dialog "已改用模板：{chosen.name}" buttons {{"好"}} default button "好" with title "为我闻思"')


def _reset_template() -> None:
    cfg = load_config()
    cfg["template_path"] = ""
    save_config(cfg)
    _osascript('display dialog "已恢复为内置默认版式。" buttons {"好"} default button "好" with title "为我闻思"')



def run_settings_menu() -> None:
    first_run_if_needed()
    while True:
        cfg = load_config()
        script = f'''
set opts to {{"查看当前设定", "选择修饰模型", "设置 API 金钥", "登录云端帐号", "编辑佛教词库", "选择 Word 模板", "恢复默认模板", "完成"}}
set picked to choose from list opts with prompt "为我闻思 · 设定" OK button name "确定" cancel button name "完成"
if picked is false then return "完成"
return item 1 of picked
'''
        try:
            choice = _osascript(script)
        except Exception:
            break
        if not choice or choice == "完成":
            break
        if choice.startswith("查看"):
            msg = show_current().replace('"', "“")
            try:
                _osascript(f'display dialog "{msg}" buttons {{"好"}} default button "好" with title "当前设定"')
            except Exception:
                pass
        elif "选择修饰" in choice:
            picked = choose_provider()
            if picked:
                cfg = load_config()
                cfg["polish_provider"] = picked
                cfg["local_polish_opt_in"] = picked == "local"
                cfg["configured"] = True
                if picked in {"claude", "gpt", "gemini"}:
                    mode = _choose_auth_mode(picked)
                    if mode:
                        cfg.setdefault("auth_mode", {})[picked] = mode
                        if mode == "api_key":
                            key = _prompt_api_key(picked)
                            if key:
                                keychain_set(picked, key)
                        else:
                            _offer_login_if_needed(picked)
                save_config(cfg)
        elif "金钥" in choice:
            picked = choose_provider("为哪一家保存 API 金钥？")
            if picked and picked != "local":
                key = _prompt_api_key(picked)
                if key:
                    keychain_set(picked, key)
                    cfg = load_config()
                    cfg.setdefault("auth_mode", {})[picked] = "api_key"
                    save_config(cfg)
        elif "登录" in choice:
            picked = choose_provider("要登录哪一家？")
            if picked and picked != "local":
                cfg = load_config()
                cfg.setdefault("auth_mode", {})[picked] = "login"
                save_config(cfg)
                _offer_login_if_needed(picked)
        elif "恢复默认模板" in choice:
            _reset_template()
        elif "Word 模板" in choice or "模板" in choice:
            _choose_template()
        elif "词库" in choice:
            open_glossary()
