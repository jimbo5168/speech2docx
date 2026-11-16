"""Fill the bundled Word template (fonts, page size, header, footer).

The layout comes from a cleaned template that keeps typography and 版面 only.
Lecture transcript text is never stored in the template. Users may point
Settings at their own .docx; the bundled default lives in templates/default.docx.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from config import load_config
from paths import asr_home

TZ = ZoneInfo("America/Los_Angeles")

TITLE_FONT = "华文行楷"
SPEAKER_FONT = "华文楷体"
BODY_FONT = "宋体"
HEADING_FONT = "黑体"
TIBETAN_FONT = "Kailasa"

STYLE_NORMAL = "Normal"
STYLE_PLAIN = "纯文本"
STYLE_H2 = "Heading 2"

_SPEAKER_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z·．.]{2,24}(?:堪布|仁波切|法王|上师|喇嘛|活佛|尊者|法师|老师))"
)


def bundled_template() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        asr_home() / "templates" / "default.docx",
        here.parent / "templates" / "default.docx",
        here / "templates" / "default.docx",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def resolve_template(cfg: Optional[dict] = None) -> Optional[Path]:
    cfg = cfg if cfg is not None else load_config()
    custom = str(cfg.get("template_path") or "").strip()
    if custom:
        path = Path(custom).expanduser()
        if path.is_file():
            return path
    bundled = bundled_template()
    return bundled if bundled.is_file() else None


def display_title(stem: str) -> str:
    text = (stem or "").strip()
    text = re.sub(r"^ASR测试[-_–—\s]*", "", text)
    text = re.sub(r"[-_]\d{8}[-_]\d{6}$", "", text)
    return text.strip() or (stem or "讲课记录")


def infer_speaker(title: str, sections: list[dict], source_name: str = "") -> str:
    parts: list[str] = [title or "", source_name or ""]
    hay = "\n".join(parts)
    match = _SPEAKER_RE.search(hay)
    if not match:
        return ""
    name = match.group(1).strip("，。；、 \t")
    if "传讲" in name:
        return name
    return name + "　传讲"


def _set_run_font(run, font: str, size_pt: float, *, bold: bool = False, color=None, italic: bool = False) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    run.font.size = Pt(size_pt)
    run.font.name = font
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), font)
    rFonts.set(qn("w:hAnsi"), font)
    rFonts.set(qn("w:eastAsia"), font)
    rFonts.set(qn("w:cs"), font)


def _spacing(paragraph, *, line: int | None = None, line_rule: str = "exact", before: int = 0, after: int = 0) -> None:
    from docx.oxml.ns import qn
    from lxml import etree

    pPr = paragraph._p.get_or_add_pPr()
    el = pPr.find(qn("w:spacing"))
    if el is None:
        el = etree.SubElement(pPr, qn("w:spacing"))
    el.set(qn("w:before"), str(before))
    el.set(qn("w:after"), str(after))
    if line is not None:
        el.set(qn("w:line"), str(line))
        el.set(qn("w:lineRule"), line_rule)


def _align(paragraph, value: str) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    mapping = {
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
        "left": WD_ALIGN_PARAGRAPH.LEFT,
    }
    paragraph.alignment = mapping[value]


def _clear_body(doc) -> None:
    body = doc.element.body
    for child in list(body):
        if child.tag.endswith("sectPr"):
            continue
        body.remove(child)


def _drop_numbering(paragraph) -> None:
    from docx.oxml.ns import qn

    pPr = paragraph._p.get_or_add_pPr()
    for el in list(pPr):
        if el.tag == qn("w:numPr"):
            pPr.remove(el)


def _add_styled_paragraph(doc, style: str):
    try:
        return doc.add_paragraph(style=style)
    except KeyError:
        return doc.add_paragraph()


def _blank(doc, style: str = STYLE_NORMAL, line: int = 292) -> None:
    p = _add_styled_paragraph(doc, style)
    _spacing(p, line=line, after=0)
    return p


def _title_lines(title: str) -> list[str]:
    title = (title or "").strip()
    if not title:
        return ["讲课记录"]
    if len(title) <= 10:
        return [title]
    for sep in ("——", "—", "–", "：", ":", "｜", " | "):
        i = title.find(sep)
        if 2 <= i <= 12:
            left, right = title[:i].strip(), title[i + len(sep):].strip(" —–-")
            if left and right:
                return [left, right]
    return [title]


def _add_title_block(
    doc,
    title: str,
    speaker: str,
    source_name: str,
    duration_label: str,
    polish_label: str,
    when: str,
    asr_label: str = "",
) -> None:
    from docx.shared import RGBColor

    _blank(doc, STYLE_PLAIN, line=342)
    _blank(doc, STYLE_PLAIN, line=334)
    for line in _title_lines(title):
        p = _add_styled_paragraph(doc, STYLE_PLAIN)
        _align(p, "center")
        _spacing(p, line=710, after=0)
        run = p.add_run(line)
        _set_run_font(run, TITLE_FONT, 26, bold=True)

    if speaker:
        p = _add_styled_paragraph(doc, STYLE_PLAIN)
        _align(p, "center")
        _spacing(p, line=360, after=0)
        _set_run_font(p.add_run(speaker), SPEAKER_FONT, 12)

    p = _add_styled_paragraph(doc, STYLE_PLAIN)
    _align(p, "center")
    _spacing(p, line=360, after=0)
    _set_run_font(p.add_run("听打稿"), SPEAKER_FONT, 12)

    asr_bit = f"    识别：{asr_label}" if asr_label else ""
    meta = f"来源：{source_name}"
    p = _add_styled_paragraph(doc, STYLE_PLAIN)
    _align(p, "center")
    _spacing(p, line=280, before=80, after=80)
    run = p.add_run(meta)
    _set_run_font(run, SPEAKER_FONT, 9, italic=True, color=RGBColor(0x55, 0x55, 0x55))
    _blank(doc, STYLE_NORMAL, line=292)


def _add_heading(doc, text: str) -> None:
    p = _add_styled_paragraph(doc, STYLE_H2)
    _drop_numbering(p)
    _spacing(p, line=415, line_rule="auto", before=260, after=200)
    if p.runs:
        p.runs[0].text = text
        run = p.runs[0]
    else:
        run = p.add_run(text)
    _set_run_font(run, HEADING_FONT, 16, bold=True)


def _highlight(run) -> None:
    try:
        from docx.enum.text import WD_COLOR_INDEX

        run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    except Exception:
        run.font.highlight_color = 7



def highlight_spans(text: str, phrases: list[str]) -> list[tuple[str, bool]]:
    """Split text into (fragment, highlighted) runs."""
    if not text or not phrases:
        return [(text, False)]
    marks = [False] * len(text)
    for ph in sorted({p for p in phrases if p}, key=len, reverse=True):
        start = 0
        while True:
            i = text.find(ph, start)
            if i < 0:
                break
            for k in range(i, i + len(ph)):
                marks[k] = True
            start = i + max(len(ph), 1)
    runs: list[tuple[str, bool]] = []
    buf: list[str] = []
    flag = marks[0]
    for ch, hl in zip(text, marks):
        if hl != flag and buf:
            runs.append(("".join(buf), flag))
            buf = [ch]
            flag = hl
        else:
            buf.append(ch)
    if buf:
        runs.append(("".join(buf), flag))
    return runs


def add_body_paragraph(
    doc,
    text: str,
    lang: str,
    highlight_phrases: list[str] | None = None,
    *,
    size: float = 10.5,
    indent: bool = True,
    font: str | None = None,
):
    from docx.shared import Cm, RGBColor

    p = _add_styled_paragraph(doc, STYLE_NORMAL)
    _align(p, "both")
    _spacing(p, line=292, after=80)
    if indent:
        p.paragraph_format.first_line_indent = Cm(0.74)
    body_font = font or (TIBETAN_FONT if lang == "bo" else BODY_FONT)
    for frag, hl in highlight_spans(str(text).strip(), highlight_phrases or []):
        run = p.add_run(frag)
        _set_run_font(run, body_font, size)
        if hl:
            _highlight(run)
    return p



def add_bilingual_table(doc, pairs: list[dict]) -> None:
    from docx.oxml.ns import qn
    from lxml import etree
    from docx.shared import Emu

    if not pairs:
        return
    _add_heading(doc, "正文（中英对照）")
    table = doc.add_table(rows=1, cols=2)
    try:
        table.style = "Table Grid"
    except Exception:
        pass
    # Content width of the A5 template is about 3.94 inches.
    widths = (Emu(1800000), Emu(1800000))
    for cell, label, font in (
        (table.rows[0].cells[0], "English", "Times New Roman"),
        (table.rows[0].cells[1], "中文", HEADING_FONT),
    ):
        cell.text = ""
        para = cell.paragraphs[0]
        _align(para, "center")
        _set_run_font(para.add_run(label), font, 10.5, bold=True)
    for pair in pairs:
        row = table.add_row()
        for cell, text, font in (
            (row.cells[0], str(pair.get("en") or "").strip(), "Times New Roman"),
            (row.cells[1], str(pair.get("zh") or "").strip(), BODY_FONT),
        ):
            cell.text = ""
            para = cell.paragraphs[0]
            _align(para, "both")
            _spacing(para, line=260, after=40)
            if text:
                _set_run_font(para.add_run(text), font, 10)
    tbl = table._tbl
    tblPr = tbl.find(qn("w:tblPr"))
    if tblPr is None:
        tblPr = etree.SubElement(tbl, qn("w:tblPr"))
    grid = tbl.find(qn("w:tblGrid"))
    if grid is not None:
        for el, w in zip(list(grid), widths):
            el.set(qn("w:w"), str(int(w.twips if hasattr(w, "twips") else w // 635)))
    for row in table.rows:
        for cell, w in zip(row.cells, widths):
            cell.width = w


def write_from_template(
    dest: Path,
    *,
    title: str,
    source_name: str,
    sections: list[dict],
    summary: str,
    note: str,
    lang: str,
    duration_label: str,
    polish_label: str,
    asr_label: str = "",
    raw_appendix: str | list[str] | None = None,
    proofread_note: str = "",
    highlight_phrases: list[str] | None = None,
    speaker: str = "",
    template_path: Optional[Path] = None,
    title_zh: str = "",
    bilingual_pairs: list[dict] | None = None,
) -> Path:
    from docx import Document
    from docx.shared import RGBColor

    path = template_path or resolve_template()
    if path is None or not Path(path).is_file():
        raise FileNotFoundError("找不到 Word 模板")

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = Document(str(path))
    _clear_body(doc)

    when = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    shown = display_title(title)
    who = speaker or infer_speaker(shown, sections, source_name)
    _add_title_block(doc, shown, who, source_name, duration_label, polish_label, when, asr_label=asr_label)
    has_cjk = any("一" <= ch <= "鿿" for ch in (title_zh or ""))
    if title_zh and has_cjk and title_zh not in shown and title_zh not in (title or ""):
        p = _add_styled_paragraph(doc, STYLE_PLAIN)
        _align(p, "center")
        _spacing(p, line=360, after=80)
        _set_run_font(p.add_run(title_zh), TITLE_FONT, 16, bold=True)

    if note:
        p = _add_styled_paragraph(doc, STYLE_NORMAL)
        run = p.add_run("说明：" + note)
        _set_run_font(run, SPEAKER_FONT, 10, color=RGBColor(0x88, 0x44, 0x00))
        _spacing(p, line=280, after=80)

    if proofread_note:
        _add_heading(doc, "校对说明")
        for line in str(proofread_note).splitlines():
            if line.strip():
                add_body_paragraph(doc, line.strip(), "zh", size=10.5, indent=False)

    if summary:
        _add_heading(doc, "摘要")
        add_body_paragraph(doc, summary, lang, indent=True)

    wrote_body = False
    if bilingual_pairs and any(str(p.get("zh") or "").strip() for p in bilingual_pairs):
        add_bilingual_table(doc, bilingual_pairs)
        wrote_body = True
    else:
        for sec in sections:
            heading = (sec.get("heading") or "").strip()
            if heading:
                _add_heading(doc, heading)
            for para in sec.get("paragraphs") or []:
                if not str(para).strip():
                    continue
                add_body_paragraph(doc, str(para), lang, highlight_phrases)
                wrote_body = True

    if not wrote_body:
        add_body_paragraph(doc, "（未识别到语音内容）", "zh", indent=False)

    appendix_parts: list[str] = []
    if isinstance(raw_appendix, list):
        appendix_parts = [str(x).strip() for x in raw_appendix if str(x).strip()]
    elif isinstance(raw_appendix, str) and raw_appendix.strip():
        appendix_parts = [p.strip() for p in raw_appendix.split("\n") if p.strip()] or [raw_appendix.strip()]
    if appendix_parts:
        _add_heading(doc, "附录：原始转录")
        hint = _add_styled_paragraph(doc, STYLE_NORMAL)
        run = hint.add_run("以下为识别模型的原文，未经修饰，便于对照准确度。")
        _set_run_font(run, SPEAKER_FONT, 9, italic=True, color=RGBColor(0x55, 0x55, 0x55))
        _spacing(hint, line=260, after=80)
        for part in appendix_parts:
            add_body_paragraph(doc, part, lang, size=10.5, indent=True)

    doc.save(str(dest))
    return dest
