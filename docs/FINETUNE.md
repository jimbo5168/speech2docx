# 佛学微调版中文识别模型（Qwen3-ASR LoRA）

为我闻思 默认用「佛学微调版」做中文识别：在 Qwen3-ASR-1.7B 上用堪布讲课录音 + 人工听打稿做 LoRA 微调，
合并后量化成 8-bit，放在 `~/ASR/models/qwen3-asr-1.7b-buddhist-8bit/`。没有这个目录时自动退回原版
`mlx-community/Qwen3-ASR-1.7B-8bit`；设置里也可以手动切回原版（`config.json` 的 `zh_asr_model`: `buddhist` / `base`）。

训练数据和模型权重是个人资料，不放进 GitHub。脚本在 `scripts/finetune/`：

1. `prep_segments.py NAME AUDIO REF.txt data` — 按停顿切 8–28 秒小段，用原版模型识别，再和听打稿对齐，得到（音频，正确文字）对。
2. `train_lora.py --data data/train.jsonl --val data/val.jsonl --out runs/r1 --epochs 3` — 冻结音频编码器，LoRA（r=16, α=32）加在文本解码器的 q/k/v/o/gate/up/down 上，纯 MLX，Apple Silicon 上运行。
3. `fuse_export.py runs/r1/adapters_ep3.safetensors OUT_DIR` — 合并 LoRA 并导出 8-bit 模型。
4. `eval_segments.py MODEL data/TEST.jsonl eval/NAME` — 在没参与训练的整讲上计算逐字一致率（与 `scripts/eval_accuracy.py` 同一套去标点规则）和术语命中。

第一次结果（2026-11-14，训练 3 讲约 3.4 小时，测试整讲「佛教徒的生活模式」68 分钟，未参与训练）：

| 模型 | 逐字一致率 |
|---|---|
| 原版 Qwen3-ASR-1.7B-8bit | 76.41% |
| 佛学微调版（第 3 轮） | 87.33% |

训练约 16 分钟（Mac mini，峰值显存约 16.5 GB，LM Studio 千问同时保持加载）。

| 模型 | 逐字一致率 |
|---|---|
| Round 2 epoch 1 | 82.45% |
| Round 2 epoch 2 | 83.39% |
| Round 2 epoch 3 | 83.18% |
