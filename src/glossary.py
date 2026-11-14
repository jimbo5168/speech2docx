"""Load and format the editable Buddhist glossary."""

from __future__ import annotations

from pathlib import Path

from paths import asr_home, glossary_path

# Packaged default copied by install.sh if the user file is missing.
PACKAGED_GLOSSARY = Path(__file__).resolve().parent.parent / "佛教词库.txt"


def ensure_glossary() -> Path:
    dest = glossary_path()
    if dest.exists():
        return dest
    src = PACKAGED_GLOSSARY
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        dest.write_text("# 佛教词库（一行一词，# 开头为注释）\n违缘\n出离心\n菩提心\n", encoding="utf-8")
    return dest


def load_terms(max_terms: int | None = None) -> list[str]:
    path = ensure_glossary()
    terms: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Allow "wrong -> correct" lines; keep the correct form
        if "->" in line:
            line = line.split("->", 1)[1].strip()
        if not line or line in seen:
            continue
        seen.add(line)
        terms.append(line)
        if max_terms and len(terms) >= max_terms:
            break
    return terms


def as_prompt_block(max_chars: int = 4000) -> str:
    terms = load_terms()
    if not terms:
        return ""
    text = "、".join(terms)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("、", 1)[0]
    return text


def as_hotwords(max_terms: int = 250) -> list[str]:
    return load_terms(max_terms=max_terms)
