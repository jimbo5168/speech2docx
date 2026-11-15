#!/usr/bin/env python3
"""Merge LoRA adapters into Qwen3-ASR-1.7B and export an 8-bit MLX model
that mlx_audio.stt.load() (and 为我闻思) can load like the stock one.

Usage: fuse_export.py ADAPTERS.safetensors OUT_DIR [--bits 8]
"""
import argparse, json, shutil, sys
from pathlib import Path
import mlx.core as mx
import mlx.nn as nn
from mlx.utils import tree_flatten

sys.path.insert(0, str(Path(__file__).parent))
from train_lora import LoRALinear, apply_lora, load_base, snapshot  # noqa

ap = argparse.ArgumentParser()
ap.add_argument("adapters"); ap.add_argument("out"); ap.add_argument("--bits", type=int, default=8)
a = ap.parse_args()
cfg = json.load(open(Path(a.adapters).parent / "adapter_config.json"))
model, inner = load_base()
apply_lora(inner, cfg["rank"], cfg["alpha"], 0.0)
inner.load_weights(list(mx.load(a.adapters).items()), strict=False)
for layer in inner.model.layers:
    for parent in (layer.self_attn, layer.mlp):
        for name, mod in list(parent.children().items()):
            if isinstance(mod, LoRALinear):
                base = mod.base
                delta = (mod.scale * (mod.lora_a.astype(mx.float32) @ mod.lora_b.astype(mx.float32))).T
                base.weight = (base.weight.astype(mx.float32) + delta).astype(base.weight.dtype)
                setattr(parent, name, base)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
qcfg = None
if a.bits:
    nn.quantize(inner, group_size=64, bits=a.bits,
                class_predicate=lambda p, m: hasattr(m, "to_quantized") and not p.startswith("audio_tower"))
    qcfg = {"group_size": 64, "bits": a.bits, "mode": "affine"}
mx.save_safetensors(str(out / "model.safetensors"), dict(tree_flatten(inner.parameters())))
src = Path(snapshot("mlx-community/Qwen3-ASR-1.7B-8bit" if a.bits == 8 else "Qwen/Qwen3-ASR-1.7B"))
for f in src.iterdir():
    if f.suffix in (".json", ".txt") and not f.name.startswith("model.safetensors"):
        shutil.copy(f, out / f.name)
c = json.load(open(out / "config.json"))
if qcfg:
    c["quantization"] = qcfg; c["quantization_config"] = qcfg
c["finetune"] = {"adapters": str(a.adapters), **cfg}
json.dump(c, open(out / "config.json", "w"), ensure_ascii=False, indent=2)
print("exported", out)
