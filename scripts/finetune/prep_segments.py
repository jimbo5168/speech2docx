#!/usr/bin/env python3
"""Cut a lecture into 8-28 s segments at pauses, run the base ASR on each,
align the ASR text to the human 听打稿, and write (audio, reference) pairs.

Usage: prep_segments.py NAME AUDIO REF_TXT OUTDIR
Writes OUTDIR/NAME/seg_XXXX.wav and OUTDIR/NAME.jsonl
"""
import difflib, json, os, re, subprocess, sys, time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
FFMPEG = "/opt/homebrew/bin/ffmpeg"
PUNCT = re.compile(r"[^\u3400-\u9fff0-9A-Za-z]")
BASE = "mlx-community/Qwen3-ASR-1.7B-8bit"


def snapshot(repo):
    root = Path.home() / ".cache/huggingface/hub" / ("models--" + repo.replace("/", "--")) / "snapshots"
    return str(sorted(root.iterdir())[-1])


def wav16k(src, dst):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)], check=True)


def silences(wav):
    p = subprocess.run([FFMPEG, "-i", str(wav), "-af", "silencedetect=noise=-32dB:d=0.35", "-f", "null", "-"],
                       capture_output=True, text=True)
    out, start = [], None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start + float(m.group(1))) / 2)
            start = None
    return out


def duration(wav):
    p = subprocess.run(["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(wav)], capture_output=True, text=True)
    return float(p.stdout.strip())


def plan(cuts, total, lo=8.0, hi=28.0):
    segs, s = [], 0.0
    cuts = [c for c in cuts if 0 < c < total]
    while s < total - 1.0:
        cand = [c for c in cuts if s + lo <= c <= s + hi]
        e = cand[-1] if cand else min(s + hi, total)
        if total - e < lo:
            e = total if total - s <= hi + 4 else e
        segs.append((s, e))
        s = e
    return segs


def write_wav(path, x):
    import wave, numpy as np
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm.tobytes())


def norm_with_map(text):
    chars, idx = [], []
    for i, ch in enumerate(text):
        if not PUNCT.match(ch):
            chars.append(ch); idx.append(i)
    return "".join(chars), idx


def main():
    name, audio, ref_path, outdir = sys.argv[1:5]
    outdir = Path(outdir); segdir = outdir / name; segdir.mkdir(parents=True, exist_ok=True)
    full = segdir / "full16k.wav"
    if not full.exists():
        wav16k(audio, full)
    total = duration(full)
    segs = plan(silences(full), total)
    print(f"{name}: {total/60:.1f} min -> {len(segs)} segments", flush=True)

    from mlx_audio.stt import load
    from mlx_audio.stt.utils import load_audio
    import numpy as np
    model = load(snapshot(BASE))
    wav = np.array(load_audio(str(full)))
    hyp_cache = segdir / "hyp.json"
    hyps = json.loads(hyp_cache.read_text()) if hyp_cache.exists() else {}
    t0 = time.time()
    for k, (s, e) in enumerate(segs):
        key = f"{k:04d}"
        seg_wav = segdir / f"seg_{key}.wav"
        if not seg_wav.exists():
            write_wav(seg_wav, wav[int(s * 16000):int(e * 16000)])
        if key not in hyps:
            r = model.generate(str(seg_wav), language="Chinese", repetition_penalty=1.15, verbose=False,
                               max_tokens=int((e - s) * 10) + 40)
            hyps[key] = (getattr(r, "text", "") or "").strip()
            if k % 20 == 0:
                hyp_cache.write_text(json.dumps(hyps, ensure_ascii=False))
                print(f"  asr {k+1}/{len(segs)}  {time.time()-t0:.0f}s", flush=True)
    hyp_cache.write_text(json.dumps(hyps, ensure_ascii=False))

    try:
        from opencc import OpenCC; cc = OpenCC("t2s")
    except Exception:
        cc = None
    ref_raw = Path(ref_path).read_text(encoding="utf-8")
    ref_raw = cc.convert(ref_raw) if cc else ref_raw
    ref_n, ref_idx = norm_with_map(ref_raw)
    hyp_parts = []
    for k in range(len(segs)):
        h = hyps[f"{k:04d}"]
        h = cc.convert(h) if cc else h
        hyp_parts.append(PUNCT.sub("", h))
    hyp_all = "".join(hyp_parts)
    # map each hyp char position -> ref char position
    sm = difflib.SequenceMatcher(None, hyp_all, ref_n, autojunk=False)
    hmap = [None] * (len(hyp_all) + 1)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for t in range(i1, i2):
            if tag == "equal":
                hmap[t] = j1 + (t - i1)
            else:
                hmap[t] = j1 + int((t - i1) * (j2 - j1) / max(1, i2 - i1))
    hmap[len(hyp_all)] = len(ref_n)
    rows, pos = [], 0
    for k, (s, e) in enumerate(segs):
        a, b = pos, pos + len(hyp_parts[k]); pos = b
        if b - a < 4:
            continue
        ra, rb = hmap[a], hmap[b]
        if rb is None or ra is None or rb <= ra:
            continue
        seg_ref_n = ref_n[ra:rb]
        r_lo = ref_idx[ra]; r_hi = ref_idx[rb - 1] + 1
        while r_hi < len(ref_raw) and PUNCT.match(ref_raw[r_hi]) and ref_raw[r_hi] not in "\n":
            r_hi += 1
        text = ref_raw[r_lo:r_hi].replace("\n", "").strip()
        sim = difflib.SequenceMatcher(None, hyp_parts[k], seg_ref_n, autojunk=False).ratio()
        dur = e - s
        cps = len(seg_ref_n) / dur
        ok = sim >= 0.6 and 1.5 <= cps <= 9.0
        rows.append({"id": f"{name}_{k:04d}", "audio": str(segdir / f"seg_{k:04d}.wav"), "start": round(s, 2),
                     "end": round(e, 2), "text": text, "hyp": hyps[f"{k:04d}"], "sim": round(sim, 3), "keep": ok})
    with open(outdir / f"{name}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    kept = [r for r in rows if r["keep"]]
    print(f"{name}: {len(kept)}/{len(rows)} kept, {sum(r['end']-r['start'] for r in kept)/3600:.2f} h", flush=True)


if __name__ == "__main__":
    main()
