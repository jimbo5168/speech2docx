"""User settings stored in $ASR_HOME/config.json. Never store API keys here."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from paths import config_path

DEFAULTS: dict[str, Any] = {
    "configured": False,
    "polish_provider": "local",
    "auth_mode": {
        "claude": "login",
        "gpt": "login",
        "gemini": "login",
    },
    "models": {
        "local": "qwen/qwen3.6-35b-a3b",
        "claude": "claude-sonnet-5-5",
        "gpt": "gpt-6.1-terra",
        "gemini": "gemini-3.8-flash",
    },
    "lms_url": "http://127.0.0.1:1234/v1",
    "chunk_seconds": 480,
    "overlap_seconds": 4,
    "include_raw_appendix": False,
    "include_summary": False,
    "include_headings": False,
    "polish_lead_prompt": "",
    "template_path": "",
    "english_output": "bilingual",
    "zh_asr_model": "buddhist",
    "zh_variant_normalization": True,
    "zh_asr_context_enabled": False,
    "zh_asr_repetition_penalty": 1.0,
    "out_dir": "",
}

PROVIDERS = ("local", "claude", "gpt", "gemini")
CLOUD_PROVIDERS = ("claude", "gpt", "gemini")
PROVIDER_LABELS = {
    "local": "本地（LM Studio 千问）",
    "claude": "Claude",
    "gpt": "GPT",
    "gemini": "Gemini",
}
KEYCHAIN_SERVICE = {
    "claude": "speech2docx.polish.claude",
    "gpt": "speech2docx.polish.gpt",
    "gemini": "speech2docx.polish.gemini",
}


def load_config() -> dict[str, Any]:
    path = config_path()
    data = deepcopy(DEFAULTS)
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                data.update({k: v for k, v in user.items() if k != "models" and k != "auth_mode"})
                if isinstance(user.get("models"), dict):
                    data["models"].update(user["models"])
                if isinstance(user.get("auth_mode"), dict):
                    data["auth_mode"].update(user["auth_mode"])
        except Exception:
            pass
    return data


def save_config(data: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never persist secrets if a caller accidentally includes them
    clean = {k: v for k, v in data.items() if k not in {"api_key", "api_keys", "token"}}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
