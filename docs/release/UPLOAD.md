# Release upload file list

Run from the repository root after credentials are supplied.
CLI references: https://huggingface.co/docs/huggingface_hub/en/guides/cli and https://github.com/modelscope/modelscope_hub#ms-hub-upload

- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/chat_template.json → chat_template.json
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/config.json → config.json
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/generation_config.json → generation_config.json
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/merges.txt → merges.txt
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/model.safetensors → model.safetensors
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/preprocessor_config.json → preprocessor_config.json
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/tokenizer_config.json → tokenizer_config.json
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/vocab.json → vocab.json
- docs/release/model_manifest.json → model_manifest.json

## Hugging Face

```sh
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/chat_template.json chat_template.json
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/config.json config.json
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/generation_config.json generation_config.json
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/merges.txt merges.txt
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/model.safetensors model.safetensors
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/preprocessor_config.json preprocessor_config.json
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/tokenizer_config.json tokenizer_config.json
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/vocab.json vocab.json
hf upload jimbo5168/weiwowensi-asr-r3-8bit docs/release/model_manifest.json model_manifest.json
```

## ModelScope

```sh
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/chat_template.json chat_template.json --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/config.json config.json --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/generation_config.json generation_config.json --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/merges.txt merges.txt --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/model.safetensors model.safetensors --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/preprocessor_config.json preprocessor_config.json --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/tokenizer_config.json tokenizer_config.json --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/vocab.json vocab.json --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit docs/release/model_manifest.json model_manifest.json --repo-type model
```
