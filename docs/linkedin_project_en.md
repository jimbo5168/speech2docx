# LinkedIn draft — local Chinese ASR fine-tune (unpublished)

Status: draft only. Do not post until you review and publish it yourself.

---

I spent a few days on my Mac mini (Apple Silicon, 64 GB) building and measuring a local speech-to-text model for long Chinese lectures with specialized Buddhist vocabulary.

The starting point was Qwen3-ASR-1.7B (MLX 8-bit). On a held-out lecture that was never used in training (佛教徒的生活模式, 68 minutes), strict word-level accuracy against human transcripts was 76.41%.

I then fine-tuned it with LoRA, entirely on-device:

- Round 1: 3 lectures, ~3.4 hours of audio → 87.33% strict, same held-out set.
- Round 3 (the installed weights, “r3”): 65 lectures, ~103 hours → 88.02% strict. Adding ~30× more similar data after round 1 only gained 0.7 points, so more of the same data was not the answer.
- A data-expansion round in between actually fell to 83.39% and was discarded.
- The next real gain came from decoding, not from a new model: lowering the repetition penalty stopped the model from dropping words. Held-out strict accuracy rose to 92.01%, then 92.05% after a conservative re-decode that only fills likely omissions.

We also rescore on a verbatim-adjusted basis: keep what the speaker actually said, and do not treat omitted sentence-final particles (or meaningless 的/了) as errors. On that scale, the current default — r3 + the new decoding rules — is 97.56% on the held-out lecture and 97.44% across four lectures. That 97% figure is not the same yardstick as the original 76.41%, and I do not report it as a like-for-like jump.

Other Chinese-team models were tested on the same lectures and not installed: FireRed2-AED ~76%, MiMo-V2.5 77.90%, FireRedASR2-LLM 89.07% (four-lecture adjusted), and Step-Audio 2 mini, which was stopped early at ~61% on the first seven segments because of heavy insertions. A 4-bit build of r3 scored 94.31% adjusted (3.11 points worse) and was rejected.

The app stays local. The current model SHA is 0f7b16c8adde1b941624d97281afd08d2b7e10e38ecb9e01aabc878060a91085. A full numbered record, scoring rules, and sources are in the open repo: https://github.com/jimbo5168/speech2docx/blob/main/docs/ASR评测记录.md
