#!/usr/bin/env python3
"""Score a model on the held-out lecture's aligned segments.
Usage: eval_segments.py MODEL_DIR TEST_JSONL OUT_PREFIX [--max-end SEC] [--system-prompt-file F]
Prints char accuracy (same normalisation as scripts/eval_accuracy.py) and key-term hits."""
import argparse, json, math, os, sys, time
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
ap.add_argument("--repetition-penalty", type=float, default=1.15)
ap.add_argument("--token-budget-multiplier", type=int, default=1)
ap.add_argument("--qa-mode", choices=["omission", "repetition", "combined",
                                      "repetition_sensitive", "combined_sensitive"], default="")
ap.add_argument("--normalize-variants", action="store_true",
                help="Add variant-normalized metrics while retaining original accuracy/edits")
a = ap.parse_args()
rows = [json.loads(l) for l in open(a.test)]
rows = [r for r in rows if r.get("keep", True) and (not a.max_end or r["end"] <= a.max_end + 1)]
try:
    from opencc import OpenCC; cc = OpenCC("t2s").convert
except Exception:
    cc = lambda s: s
if not math.isfinite(a.repetition_penalty) or a.repetition_penalty <= 0 or a.token_budget_multiplier < 1:
    ap.error("repetition penalty must be positive and token budget multiplier must be >= 1")
m = load(a.model)
kw = dict(language="Chinese", repetition_penalty=a.repetition_penalty, verbose=False)
if a.system_prompt_file:
    kw["system_prompt"] = open(a.system_prompt_file).read().strip()
t0 = time.time()
hyps = []
token_usage = []
for r in rows:
    resource_guard()
    limit = (int((r["end"] - r["start"]) * 10) + 40) * a.token_budget_multiplier
    out = m.generate(r["audio"], max_tokens=limit, **kw)
    token_usage.append((getattr(out, "generation_tokens", 0), limit))
    hyps.append(cc((getattr(out, "text", "") or "").strip()))
events = []
if a.qa_mode:
    import tempfile
    import numpy as np
    from mlx_audio.stt.utils import load_audio
    from prep_segments import write_wav
    from decode_qa import run_qa

    with tempfile.TemporaryDirectory(prefix="qa-", dir=Path(a.out).parent) as temporary:
        piece = Path(temporary) / "piece.wav"

        def decode(row, start, end, kind):
            resource_guard()
            audio = np.array(load_audio(row["audio"]))
            write_wav(piece, audio[int(start * 16000):int(end * 16000)])
            result = m.generate(
                str(piece), language="Chinese", verbose=False,
                repetition_penalty=1.0 if kind == "short" else a.repetition_penalty,
                max_tokens=int((end - start) * 15) + 80,
            )
            return cc(result.text.strip())

        hyps, events = run_qa(rows, hyps, decode, a.qa_mode)
ref = "\n".join(r["text"] for r in rows)
hyp = "\n".join(hyps)
hyp_for_score = hyp.replace("〔待回听〕", "") if a.qa_mode else hyp
Path(a.out + ".hyp.txt").write_text(hyp); Path(a.out + ".ref.txt").write_text(ref)
s = score(ref, hyp_for_score)
terms = {t: f"{hyp.count(t)}/{ref.count(t)}" for t in TERMS if ref.count(t)}
res = {"model": a.model, "segments": len(rows), "audio_min": round(sum(r['end']-r['start'] for r in rows)/60, 1),
       "accuracy": round(s["accuracy"] * 100, 2), "edits": s["edits"], "ref_chars": s["ref_chars"],
       "terms(hyp/ref)": terms, "secs": round(time.time() - t0),
       "repetition_penalty": a.repetition_penalty,
       "token_budget_multiplier": a.token_budget_multiplier,
       "token_budget_hits": sum(n >= limit for n, limit in token_usage),
       "real_time_factor": (time.time() - t0) / max(1, sum(r["end"] - r["start"] for r in rows))}
if a.qa_mode:
    res["qa_mode"] = a.qa_mode
    res["qa_flags"] = len(events)
    res["qa_replacements"] = sum(event["chosen"].replace("〔待回听〕", "") != event["original"] for event in events)
    res["review_markers_per_hour"] = hyp.count("〔待回听〕") / max(1e-9, sum(row["end"] - row["start"] for row in rows) / 3600)
if a.normalize_variants:
    variant = score(ref, hyp_for_score, normalize_variants=True)
    res["variant_accuracy"] = round(variant["accuracy"] * 100, 2)
    res["variant_edits"] = variant["edits"]
    res["variant_ref_chars"] = variant["ref_chars"]
Path(a.out + ".json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(json.dumps(res, ensure_ascii=False))
