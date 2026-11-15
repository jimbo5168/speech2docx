#!/usr/bin/env python3
"""Score a model on the held-out lecture's aligned segments.
Usage: eval_segments.py MODEL_DIR TEST_JSONL OUT_PREFIX [--max-end SEC] [--system-prompt-file F]
Prints char accuracy (same normalisation as scripts/eval_accuracy.py) and key-term hits."""
import argparse, json, os, sys, time
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval_accuracy import score  # noqa
from mlx_audio.stt import load
from train_lora import resource_guard

TERMS = ["论典", "不堕两边", "顺缘", "违缘", "出离心", "菩提心", "因明", "依止五火", "五部大论", "殊胜", "善根"]
ap = argparse.ArgumentParser()
ap.add_argument("model"); ap.add_argument("test"); ap.add_argument("out")
ap.add_argument("--max-end", type=float, default=0); ap.add_argument("--system-prompt-file", default="")
ap.add_argument("--normalize-variants", action="store_true",
                help="Add variant-normalized metrics while retaining original accuracy/edits")
a = ap.parse_args()
rows = [json.loads(l) for l in open(a.test)]
rows = [r for r in rows if r.get("keep", True) and (not a.max_end or r["end"] <= a.max_end + 1)]
try:
    from opencc import OpenCC; cc = OpenCC("t2s").convert
except Exception:
    cc = lambda s: s
m = load(a.model)
kw = dict(language="Chinese", repetition_penalty=1.15, verbose=False)
if a.system_prompt_file:
    kw["system_prompt"] = open(a.system_prompt_file).read().strip()
t0 = time.time(); hyps = []
for r in rows:
    resource_guard()
    out = m.generate(r["audio"], max_tokens=int((r["end"] - r["start"]) * 10) + 40, **kw)
    hyps.append(cc((getattr(out, "text", "") or "").strip()))
ref = "\n".join(r["text"] for r in rows); hyp = "\n".join(hyps)
Path(a.out + ".hyp.txt").write_text(hyp); Path(a.out + ".ref.txt").write_text(ref)
s = score(ref, hyp)
terms = {t: f"{hyp.count(t)}/{ref.count(t)}" for t in TERMS if ref.count(t)}
res = {"model": a.model, "segments": len(rows), "audio_min": round(sum(r['end']-r['start'] for r in rows)/60, 1),
       "accuracy": round(s["accuracy"] * 100, 2), "edits": s["edits"], "ref_chars": s["ref_chars"],
       "terms(hyp/ref)": terms, "secs": round(time.time() - t0)}
if a.normalize_variants:
    variant = score(ref, hyp, normalize_variants=True)
    res["variant_accuracy"] = round(variant["accuracy"] * 100, 2)
    res["variant_edits"] = variant["edits"]
    res["variant_ref_chars"] = variant["ref_chars"]
Path(a.out + ".json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(json.dumps(res, ensure_ascii=False))
