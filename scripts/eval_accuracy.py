#!/usr/bin/env python3
"""Character accuracy (1 - CER) ignoring punctuation and whitespace.

This follows the scoring used for 为我闻思 vs a human 听打稿:
skip punctuation, whitespace, and obvious cover/summary/heading lines;
keep spoken wording, numbers, and markers such as 〔待回聽〕.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SKIP_LINE_RE = re.compile(
    r"^(摘要|听打稿|聽打稿|来源：|來源：|校对说明|校對說明|附录[:：]|附錄[:：]"
    r"|识别：|識別：|修饰：|修飾：|说明：|說明：|转写时间|轉寫時間)"
)
PUNCT_RE = re.compile(
    r"[\s"
    r"\u3000-\u303f\uff00-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65"
    r"\u2000-\u206f\u2e00-\u2e7f"
    r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"
    r"，。！？、；：「」『』（）【】《》—…·]+"
)


def extract_text(path: Path) -> str:
    path = path.expanduser()
    suffix = path.suffix.lower()
    if suffix in {".doc", ".docx", ".rtf", ".html", ".htm"}:
        proc = subprocess.run(
            ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or f"textutil failed on {path}")
        return proc.stdout
    return path.read_text(encoding="utf-8")


def body_text(text: str) -> str:
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if SKIP_LINE_RE.match(line):
            continue
        if line.startswith("以下为识别模型的原文"):
            break
        lines.append(line)
    return "\n".join(lines)


def normalize(text: str) -> str:
    return PUNCT_RE.sub("", body_text(text))


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins, delete, sub = prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def score(reference: str, hypothesis: str) -> dict:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    dist = levenshtein(ref, hyp)
    n = len(ref)
    cer = dist / n if n else (0.0 if not hyp else 1.0)
    return {
        "ref_chars": n,
        "hyp_chars": len(hyp),
        "edits": dist,
        "cer": cer,
        "accuracy": 1.0 - cer,
        "ref_norm": ref,
        "hyp_norm": hyp,
    }


def prefix_by_length(reference: str, hyp_chars: int) -> str:
    """Take the start of the (already normalized) reference, length ≈ hypothesis."""
    ref = normalize(reference)
    if hyp_chars <= 0:
        return ""
    return ref[:hyp_chars]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐字一致率（忽略标点和空白）")
    parser.add_argument("--ref", required=True, help="人工听打稿（.doc/.docx/.txt）")
    parser.add_argument("--hyp", action="append", required=True, help="识别或校对稿，可多次：标签=路径")
    parser.add_argument("--prefix-from", default="", help="按这份稿的正文字数截取参考稿开头（用于只评前几分钟）")
    args = parser.parse_args(argv)

    ref_text = extract_text(Path(args.ref))
    if args.prefix_from:
        guide = extract_text(Path(args.prefix_from.split("=", 1)[-1]))
        n = len(normalize(guide))
        # Use a slightly longer window so deletions are still charged.
        ref_text = body_text(ref_text)
        ref_norm_full = normalize(ref_text)
        cut = min(len(ref_norm_full), max(n, int(n * 1.02)))
        # Reconstruct a raw prefix by walking the un-normalized body.
        kept, count = [], 0
        for ch in ref_text:
            if PUNCT_RE.match(ch):
                kept.append(ch)
                continue
            if count >= cut:
                break
            kept.append(ch)
            count += 1
        ref_text = "".join(kept)

    print(f"参考：{args.ref}")
    print(f"参考正文字数（去标点空白）：{len(normalize(ref_text))}")
    for item in args.hyp:
        if "=" in item:
            label, path = item.split("=", 1)
        else:
            label, path = Path(item).name, item
        hyp_text = extract_text(Path(path))
        result = score(ref_text, hyp_text)
        print(
            f"{label}: 一致率 {result['accuracy']*100:.2f}%  "
            f"（编辑 {result['edits']} / 参考 {result['ref_chars']}，稿件 {result['hyp_chars']}）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
