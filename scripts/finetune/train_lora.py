#!/usr/bin/env python3
"""LoRA fine-tune of Qwen3-ASR-1.7B (MLX) on (segment wav, reference text) pairs.

The audio encoder stays frozen (features are cached once); LoRA adapters go on
the text decoder's attention and MLP projections.

Usage: train_lora.py --data data/train.jsonl --val data/val.jsonl --out runs/r1
"""
import argparse, json, math, os, random, time, re, subprocess, shutil
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
import numpy as np
from mlx.utils import tree_flatten, tree_map

BASE = "Qwen/Qwen3-ASR-1.7B"
TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


def snapshot(repo):
    root = Path.home() / ".cache/huggingface/hub" / ("models--" + repo.replace("/", "--")) / "snapshots"
    return str(sorted(root.iterdir())[-1])


class LoRALinear(nn.Module):
    def __init__(self, base, r=16, alpha=32, dropout=0.05):
        super().__init__()
        self.base = base
        out_d, in_d = base.weight.shape[0], base.weight.shape[1]
        if hasattr(base, "scales"):
            in_d = in_d * 32 // base.bits
        self.scale = alpha / r
        self.dropout = nn.Dropout(dropout)
        self.lora_a = mx.random.uniform(-1 / math.sqrt(in_d), 1 / math.sqrt(in_d), (in_d, r))
        self.lora_b = mx.zeros((r, out_d))

    def __call__(self, x):
        y = self.base(x)
        z = (self.dropout(x) @ self.lora_a.astype(x.dtype)) @ self.lora_b.astype(x.dtype)
        return y + self.scale * z


def apply_lora(inner, r, alpha, dropout):
    for layer in inner.model.layers:
        for parent in (layer.self_attn, layer.mlp):
            for name in TARGETS:
                if hasattr(parent, name):
                    setattr(parent, name, LoRALinear(getattr(parent, name), r, alpha, dropout))


def load_base(path=None):
    from mlx_audio.stt import load
    m = load(path or snapshot(BASE))
    return m, m._model


def prompt_ids(inner, n_audio, system=""):
    sys_c = f"{system}\n" if system else ""
    p = (f"<|im_start|>system\n{sys_c}<|im_end|>\n<|im_start|>user\n<|audio_start|>"
         f"{'<|audio_pad|>' * n_audio}<|audio_end|><|im_end|>\n<|im_start|>assistant\nlanguage Chinese<asr_text>")
    return inner._tokenizer.encode(p)


def audio_feats(inner, wav_path):
    from mlx_audio.stt.utils import load_audio
    feats, mask, n = inner._preprocess_audio(np.array(load_audio(str(wav_path))))
    f = inner.get_audio_features(feats, mask)
    return f.astype(mx.bfloat16), n


def build(inner, feat, text, system=""):
    n = feat.shape[0]
    p = prompt_ids(inner, n, system)
    tgt = inner._tokenizer.encode(text) + inner._tokenizer.encode("<|im_end|>")
    ids = mx.array(p + tgt)
    pad_id = inner.config.audio_token_id
    first = p.index(pad_id)
    return ids, first, n, len(p)


def loss_fn(inner, ids, feat, first, n, plen):
    emb = inner.model.embed_tokens(ids[None, :-1])
    emb = mx.concatenate([emb[:, :first], feat[None].astype(emb.dtype), emb[:, first + n:]], axis=1)
    h = inner.model(inputs_embeds=emb)
    logits = inner.model.embed_tokens.as_linear(h) if inner.lm_head is None else inner.lm_head(h)
    logits = logits[0, plen - 1:].astype(mx.float32)
    labels = ids[plen:]
    return nn.losses.cross_entropy(logits, labels, reduction="mean")


def resource_guard():
    """Pause under memory pressure; never change other loaded models."""
    while True:
        result = subprocess.check_output(["memory_pressure"], text=True)
        free = int(re.search(r"free percentage: (\d+)", result)[1])
        if free >= 15:
            break
        print(f"Memory free {free}%; pausing 60s", flush=True)
        time.sleep(60)
    if shutil.disk_usage(Path.cwd()).free < 50 * 1024**3:
        raise RuntimeError("Less than 50 GiB free disk")


class DiskFeatures:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def __setitem__(self, key, value):
        mx.save(str(self.root / (key + ".npy")), value)

    def __getitem__(self, key):
        return mx.load(str(self.root / (key + ".npy")))

    def __len__(self):
        return len(list(self.root.glob("*.npy")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--val", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=3); ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16); ap.add_argument("--alpha", type=float, default=32)
    ap.add_argument("--accum", type=int, default=4); ap.add_argument("--sys-prob", type=float, default=0.3)
    ap.add_argument("--system-prompt-file", default="")
    ap.add_argument("--feature-cache-dir", default="", help="Keep audio features on disk to bound memory")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    random.seed(0); mx.random.seed(0)
    system_prompt = Path(a.system_prompt_file).read_text().strip() if a.system_prompt_file else ""

    model, inner = load_base()
    inner.freeze()
    rows = [json.loads(l) for l in open(a.data)]
    vrows = [json.loads(l) for l in open(a.val)]
    t0 = time.time()
    cache = DiskFeatures(a.feature_cache_dir) if a.feature_cache_dir else {}
    for r in rows + vrows:
        resource_guard()
        f, n = audio_feats(inner, r["audio"]); mx.eval(f); cache[r["id"]] = f
    print(f"cached {len(cache)} audio features in {time.time()-t0:.0f}s", flush=True)

    apply_lora(inner, a.rank, a.alpha, 0.05)
    inner.freeze()
    inner.unfreeze(keys=["lora_a", "lora_b"], strict=False)
    ntrain = sum(v.size for _, v in tree_flatten(inner.trainable_parameters()))
    print(f"trainable params: {ntrain/1e6:.2f}M", flush=True)

    steps_total = a.epochs * len(rows) // a.accum
    sched = optim.join_schedules([optim.linear_schedule(1e-6, a.lr, 30),
                                  optim.cosine_decay(a.lr, max(1, steps_total - 30), a.lr * 0.1)], [30])
    opt = optim.AdamW(learning_rate=sched, weight_decay=0.01)
    vg = nn.value_and_grad(inner, lambda ids, feat, first, n, plen: loss_fn(inner, ids, feat, first, n, plen))

    def val_loss():
        inner.eval(); tot = 0.0
        for r in vrows:
            ids, first, n, plen = build(inner, cache[r["id"]], r["text"])
            l = loss_fn(inner, ids, cache[r["id"]], first, n, plen); mx.eval(l); tot += l.item()
        inner.train(); return tot / max(1, len(vrows))

    best = val_loss(); print(f"epoch 0 val_loss {best:.4f}", flush=True)
    log = open(out / "train_log.jsonl", "a")
    step, acc_g, acc_n, run_l = 0, None, 0, []
    inner.train()
    for ep in range(1, a.epochs + 1):
        random.shuffle(rows)
        for r in rows:
            resource_guard()
            sp = system_prompt if (system_prompt and random.random() < a.sys_prob) else ""
            ids, first, n, plen = build(inner, cache[r["id"]], r["text"], sp)
            l, g = vg(ids, cache[r["id"]], first, n, plen)
            acc_g = g if acc_g is None else tree_map(lambda x, y: x + y, acc_g, g)
            acc_n += 1; mx.eval(acc_g, l); run_l.append(l.item())
            if acc_n == a.accum:
                opt.update(inner, tree_map(lambda x: x / a.accum, acc_g)); mx.eval(inner.parameters(), opt.state)
                acc_g, acc_n = None, 0; step += 1
                if step % 10 == 0:
                    m = sum(run_l) / len(run_l); run_l = []
                    mem = mx.get_peak_memory() / 1e9
                    print(f"ep {ep} step {step}/{steps_total} loss {m:.4f} lr {float(opt.learning_rate):.2e} "
                          f"peak_mem {mem:.1f}GB {time.time()-t0:.0f}s", flush=True)
                    log.write(json.dumps({"ep": ep, "step": step, "loss": m}) + "\n"); log.flush()
        vl = val_loss(); print(f"epoch {ep} val_loss {vl:.4f}", flush=True)
        ad = dict(tree_flatten(inner.trainable_parameters()))
        mx.save_safetensors(str(out / f"adapters_ep{ep}.safetensors"), ad)
        if vl < best:
            best = vl; mx.save_safetensors(str(out / "adapters.safetensors"), ad)
            print(f"  saved best (val {vl:.4f})", flush=True)
    json.dump({"base": BASE, "rank": a.rank, "alpha": a.alpha, "targets": TARGETS, "best_val": best},
              open(out / "adapter_config.json", "w"))
    print("TRAIN_DONE", flush=True)


if __name__ == "__main__":
    main()
