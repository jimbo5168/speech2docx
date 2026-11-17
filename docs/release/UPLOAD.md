# Release upload file list

Run from the repository root after credentials are supplied.
CLI references: https://huggingface.co/docs/huggingface_hub/en/guides/cli and https://github.com/modelscope/modelscope_hub#ms-hub-upload

- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/chat_template.json → chat_template.json | 1161 bytes | SHA256 `75a8cfca24f00de72d796fbfed6858fc9614ef3dabd8696684cc3bc03a9c58ff`
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/config.json → config.json | 6244 bytes | SHA256 `ee9200cc6ca0d84c24860935b11f47de9c5114cdd2020c51ba0fe1dc97950002`
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/generation_config.json → generation_config.json | 142 bytes | SHA256 `1da527824d81e07118facff437e03f2e24a23311e3bdeb2368973fe77e5f275c`
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/merges.txt → merges.txt | 1671853 bytes | SHA256 `8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5`
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/model.safetensors → model.safetensors | 2463307529 bytes | SHA256 `0f7b16c8adde1b941624d97281afd08d2b7e10e38ecb9e01aabc878060a91085`
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/preprocessor_config.json → preprocessor_config.json | 330 bytes | SHA256 `45e120a4eda2c20c5d7f2ea9354e63536bf35e27aa573fb7cdf78017b378770d`
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/tokenizer_config.json → tokenizer_config.json | 12487 bytes | SHA256 `4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c`
- /Users/jimbo/ASR/models/qwen3-asr-1.7b-buddhist-8bit/vocab.json → vocab.json | 2776833 bytes | SHA256 `ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910`
- docs/release/model_manifest.json → model_manifest.json | 1276 bytes | SHA256 `d3c33db4e0bc1f45bfdd7fabd6b1d5432f4af74732868e29f853f6efee7ff6aa`
- /Users/jimbo/ASR/dist/release/runtime-macos-arm64-cpython312-r3.tar.gz → runtime-macos-arm64-cpython312-r3.tar.gz | 249544912 bytes | SHA256 `e8c8061bce91f9ba5429fe62abaa038e8968e9072e8262802c1aaa7c76a84bb8`
- docs/release/runtime_manifest.json → runtime_manifest.json | 444 bytes | SHA256 `1742ec754e63724326bd9f43764fa99df73daa6f16c12298b5c87d45c398636c`

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
hf upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/dist/release/runtime-macos-arm64-cpython312-r3.tar.gz runtime-macos-arm64-cpython312-r3.tar.gz
hf upload jimbo5168/weiwowensi-asr-r3-8bit docs/release/runtime_manifest.json runtime_manifest.json
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
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit /Users/jimbo/ASR/dist/release/runtime-macos-arm64-cpython312-r3.tar.gz runtime-macos-arm64-cpython312-r3.tar.gz --repo-type model
ms-hub upload jimbo5168/weiwowensi-asr-r3-8bit docs/release/runtime_manifest.json runtime_manifest.json --repo-type model
```
