# LinkedIn draft (unpublished, review before posting)

Fine-tuning a local Chinese speech-to-text model for Tibetan Buddhist education (non-profit project)

This is a non-profit project for Tibetan Buddhist education. Long recorded Chinese lectures need accurate, word-for-word transcripts so students can study them. Off-the-shelf speech recognition kept getting Buddhist terms wrong, so I built a free, fully local tool on my Mac mini (Apple Silicon, 64 GB) that turns lecture audio or video into a formatted Word transcript. No audio ever leaves the machine.

What I did
• Base model: Qwen3-ASR-1.7B, run with MLX in 8-bit on Apple Silicon
• Fine-tuned it on-device with LoRA on 65 lectures (about 103 hours of audio)
• Tuned decoding (repetition penalty) and added a conservative re-decode step that recovers dropped words
• Benchmarked other Chinese open-source speech models and a 4-bit build on the same lectures
• Always scored on a 68-minute lecture that was never used in training, to check generalization
• Added a fast path: when a video already has text subtitles, the tool exports them in under a second instead of re-transcribing

Results (held-out lecture, strict word accuracy vs. human transcript)
1. Base Qwen3-ASR-1.7B: 76.41%
2. Fine-tune round 1 (3 lectures, 3.4 h): 87.33%  (+10.9 points)
3. Fine-tune round 2 (more, different data): 83.39%  (rejected)
4. Fine-tune round 3 (65 lectures, 103 h): 88.02%  (+11.6 points)
5. Round 3 + decoding fix: 92.01%
6. Round 3 + decoding fix + word-recovery step (current): 92.05%  (+15.6 points over base)

Verbatim-adjusted score of the current model (sentence-final particles not counted as errors)
• Held-out lecture: 97.56%
• Average across four lectures: 97.44%

Alternatives tested and not adopted (four-lecture adjusted score)
• FireRedASR2-LLM: 89.07%
• 4-bit build of my model: 94.31%
• Step-Audio 2 mini: stopped early at about 61% (heavy insertions)
• FireRed2-AED and MiMo-V2.5: about 76% and 77.90% strict on the held-out lecture

What I learned
• Adding about 30 times more similar data only gained 0.7 points; the biggest jump after fine-tuning came from fixing decoding, not from a bigger model.
• Bigger is not better: several larger models scored below my 1.7B fine-tune on this domain.
• A held-out test set and a clearly defined scoring rule mattered more than any single model choice.

The app is free for personal use and the full numbered evaluation record is open source:
https://github.com/jimbo5168/speech2docx

#SpeechRecognition #ASR #MachineLearning #LoRA #AppleSilicon #MLX #NonProfit #Buddhism
