"""Load and format the editable Buddhist glossary."""

from __future__ import annotations

from pathlib import Path

from paths import asr_home, glossary_path

DEFAULT_ENGLISH_SECTION = '# --- English equivalents（英文转中文）---\nbodhicitta = 菩提心\nbodhisattva = 菩萨\nBuddha = 佛陀\nDharma = 佛法\nSangha = 僧众\nsamsara = 轮回\nnirvana = 涅槃\nkarma = 业\nemptiness = 空性\nguru yoga = 上师瑜伽\nguru = 上师\nlama = 喇嘛\nrinpoche = 仁波切\nkhenpo = 堪布\nngondro = 前行\npreliminary practices = 前行\nshamatha = 止\nvipashyana = 观\ntathagata = 如来\nsentient beings = 众生\nrefuge = 皈依\ndedication = 回向\nVajrayana = 密乘\nMahayana = 大乘\nmandala = 坛城\nmantra = 咒\nsadhana = 仪轨\nempowerment = 灌顶\nyidam = 本尊\ndakini = 空行母\nDharmakaya = 法身\nSambhogakaya = 报身\nNirmanakaya = 化身\nFour Noble Truths = 四谛\nNoble Eightfold Path = 八正道\nimpermanence = 无常\nrenunciation = 出离心\ndependent origination = 缘起\nprajna = 般若\nparamita = 波罗蜜\nabhidharma = 阿毗达磨\nsutra = 经\ntantra = 续部\nVinaya = 律\nbhumi = 地\nsamadhi = 三摩地\nmindfulness = 正念\ncompassion = 慈悲\nwisdom = 智慧\nliberation = 解脱\nenlightenment = 证悟\nawakening = 觉醒\nbardo = 中阴\nstupa = 佛塔\ndharma nature = 法性\ntwo truths = 二谛\nmiddle way = 中观\n'

# Packaged default copied by install.sh if the user file is missing.
def packaged_glossary() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here / "佛教词库.txt", here.parent / "佛教词库.txt"):
        if cand.is_file():
            return cand
    return here.parent / "佛教词库.txt"


PACKAGED_GLOSSARY = packaged_glossary()



def ensure_glossary() -> Path:
    dest = glossary_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        src = PACKAGED_GLOSSARY
        if src.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            dest.write_text("# 佛教词库（一行一词，# 开头为注释）\n违缘\n出离心\n菩提心\n", encoding="utf-8")
    _merge_english_aliases(dest)
    return dest


def _merge_english_aliases(dest: Path) -> None:
    """If the packaged word list gained English aliases, append any missing ones."""
    src = PACKAGED_GLOSSARY
    if not src.exists() or not dest.exists():
        return
    user = dest.read_text(encoding="utf-8")
    if "bodhicitta = " in user.lower() or "bodhicitta=" in user.lower().replace(" ", ""):
        return
    section = ""
    marker = "# --- English equivalents"
    pkg = src.read_text(encoding="utf-8") if src.exists() else ""
    if marker in pkg:
        section = pkg[pkg.index(marker):].rstrip() + "\n"
    if "bodhicitta" not in section.lower():
        section = DEFAULT_ENGLISH_SECTION
    if "bodhicitta" not in section.lower():
        return
    dest.write_text(user.rstrip() + "\n\n" + section, encoding="utf-8")


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "->" in line:
        line = line.split("->", 1)[1].strip()
        if not line:
            return None
        return ("", line)
    if "=" in line:
        left, right = line.split("=", 1)
        return (left.strip(), right.strip())
    if " / " in line:
        left, right = line.split(" / ", 1)
        # Chinese / English or English / Chinese
        return (left.strip(), right.strip())
    return ("", line)


def load_pairs() -> list[tuple[str, str]]:
    path = ensure_glossary()
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_line(raw)
        if not parsed:
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        pairs.append(parsed)
    return pairs


def load_terms(max_terms: int | None = None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for left, right in load_pairs():
        for item in (right, left):
            if not item or item in seen:
                continue
            seen.add(item)
            terms.append(item)
            if max_terms and len(terms) >= max_terms:
                return terms
    return terms


def as_prompt_block(max_chars: int = 4000) -> str:
    terms = load_terms()
    if not terms:
        return ""
    text = "、".join(terms)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("、", 1)[0]
    return text


def as_translation_glossary(max_chars: int = 3000) -> str:
    lines: list[str] = []
    for left, right in load_pairs():
        if left and right:
            # Prefer English = 中文
            if any("a" <= ch.lower() <= "z" for ch in left) and any("\u4e00" <= ch <= "\u9fff" for ch in right):
                lines.append(f"{left} = {right}")
            elif any("a" <= ch.lower() <= "z" for ch in right) and any("\u4e00" <= ch <= "\u9fff" for ch in left):
                lines.append(f"{right} = {left}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return text


def as_hotwords(max_terms: int = 250) -> list[str]:
    return load_terms(max_terms=max_terms)
