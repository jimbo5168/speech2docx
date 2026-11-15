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
| Round 3 epoch 1 | 88.02% |
| Round 3 epoch 2 | 87.92% |

Round 4 复现（2026-11-15，同一 167 段、11,957 字，未加提示）：

| 模型 | 原规则 | 简繁及叫作/叫做归一 |
|---|---|---|
| base | 76.41% | 76.41% |
| r1 | 87.33% | 87.37% |
| r3 epoch 1 | 88.02% | 88.03% |

`eval_accuracy.py --normalize-variants` 在原分数之后额外显示归一分数；
`eval_segments.py --normalize-variants` 额外保存 `variant_accuracy`、`variant_edits` 和
`variant_ref_chars`，原有字段继续采用原规则。应用继续做简繁转换，并将语境明确的
「叫作」统一为「叫做」；`zh_variant_normalization: false` 可关闭后者（无需更改模型）。
不全局合并作/做、坐/座，不替换不多两边、物欲等真实术语错误。

两讲听打稿回归样本：语加持的修法 23 段约 9.0 分钟，原/归一均 89.95%；
四谛下集 24 段约 10.3 分钟，原 91.45%、归一 91.58%。仅输出改成「叫做」时，
四谛样本按原规则为 91.32%，因为参考也使用「叫作」；归一后的提升是拼写口径，非声学提升。

Round 4 提示/分段逐项对照（同一原评分规则）：

| 解码方式 | held-out | 语加持样本 | 四谛下样本 |
|---|---|---|---|
| 无提示（采用） | 88.02% | 89.95% | 91.45% |
| 原应用长词表及负例提示 | 87.21% | 88.92% | 89.49% |
| 扩展短词表 | 88.17% | 89.52% | 90.34% |
| 相邻两段合并 | 86.92% | — | — |

原应用的长提示在三项测试均退步，因此默认 `zh_asr_context_enabled: false`。
如需恢复原提示，可设为 `true`，词表仍由应用现有的可编辑「佛教词库.txt」补充。
扩展短词表虽改善 held-out，但损害两讲回归，未采用；分段未更改，r3 权重未更改。
这些分数比较使用相同的历史短片段，不冒充应用 480 秒长块的整讲端到端得分。

### Round 5 decode measurements (2026-11-15)

The original 169-cut / 167-passing evaluation, Chinese language, and old scoring rule reproduce 88.02% at repetition penalty 1.15. Penalties 1.0 / 1.05 / 1.1 score 92.01% / 91.85% / 90.55%. Doubling the original per-segment token budget leaves 88.02% unchanged with zero budget hits. At 1.0 the two fixed regression samples score 95.54% / 93.86% (previously 89.95% / 91.45%).

Reference-free speech-density/end-probe omission QA, short-window re-decoding, and two repeated-phrase thresholds were measured separately. At penalty 1.15 omission QA scores 88.22%. At 1.0 omission/combined QA scores 92.00%, repetition QA 92.01%, and the more sensitive repetition/combined variants 91.97% / 91.96%. The app uses the cheaper plain 1.0 setting for Chinese. `zh_asr_repetition_penalty` can override this default without rewriting existing user settings. Other languages retain 1.15. Chinese raw-chunk caches use a new parameter/model-revision-specific namespace. The existing long-run repeat guard remains active.

`eval_segments.py --repetition-penalty 1.0 --token-budget-multiplier 1` reproduces the accepted decoding parameters. Its defaults preserve the previous 1.15 scoring recipe, and its output includes token-budget hits and real-time factor. Term counts use the original un-normalized transcript strings, as in earlier rounds. Character deletion/insertion counts use the same full Levenshtein path (equal, substitution, deletion, insertion tie order).

The experimental QA remains available through `eval_segments.py --qa-mode omission|repetition|combined|repetition_sensitive|combined_sensitive`. It strips `〔待回听〕` only from the scored hypothesis and reports markers per audio hour. The selected app default uses plain decoding.

Two additional 1.0 QA variants use the actual lower-level `stream_generate` token log-probabilities. A mean selected-token log-probability improvement of at least 0.03 nats is required to retain a proposed replacement. Both combined and sensitive-combined confidence variants reproduce the plain 1.0 scores on all three lectures, with zero replacements. Their additional decoding cost supports the plain setting. Reproduce with `--qa-mode combined_sensitive --qa-confidence-margin 0.03 --repetition-penalty 1.0`. Whole-input confidence decoding preserves all samples of the source segment.
