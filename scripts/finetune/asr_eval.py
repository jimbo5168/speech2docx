#!/usr/bin/env python3
"""Transcribe one file with a given Qwen3-ASR model dir (base or fine-tuned).
Usage: asr_eval.py MODEL_DIR AUDIO OUT_TXT [--system-prompt-file F]"""
import argparse, os, time
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from mlx_audio.stt import load
ap = argparse.ArgumentParser()
ap.add_argument("model"); ap.add_argument("audio"); ap.add_argument("out")
ap.add_argument("--system-prompt-file", default="")
a = ap.parse_args()
m = load(a.model)
kw = dict(language="Chinese", repetition_penalty=1.15, verbose=False)
if a.system_prompt_file:
    kw["system_prompt"] = open(a.system_prompt_file).read().strip()
t = time.time()
r = m.generate(a.audio, **kw)
text = (getattr(r, "text", "") or "").strip()
try:
    from opencc import OpenCC; text = OpenCC("t2s").convert(text)
except Exception:
    pass
open(a.out, "w").write(text + "\n")
print(f"{a.model}: {len(text)} chars in {time.time()-t:.0f}s")
