# 语音转文字 / Speech2Docx

本地语音转文字工具（macOS，Apple Silicon）。把 MP3 / M4A / WAV 等音频拖到应用图标或桌面文件夹，即会在「下载」文件夹生成排好版的 Word 文档（`.docx`）。

音频始终只在本机识别。可选的文字修饰可以使用本地大模型（LM Studio），或你已登录的 Claude / ChatGPT / Gemini 订阅，也可以改用 API 金钥。

---

Local speech-to-text for macOS on Apple Silicon. Drop an MP3, M4A, WAV, or a video (MP4, MOV, MKV, WEBM, …) onto the app icon or a Desktop folder and a formatted Word document appears in Downloads. Video files are converted by extracting the soundtrack first. If subtitles are embedded, sit in a sidecar file, or come from a YouTube URL, they are aligned with the ASR text for proofreading; unresolved disagreements are highlighted in yellow.

Audio is transcribed only on the machine. The first-run chooser preselects Local (LM Studio Qwen) so you can check accuracy before switching to a cloud model. By default the Word file also ends with an appendix, 附录：原始转录, containing the unpolished ASR text (turn this off in Settings). Optional polishing can use Local, or a signed-in Claude / ChatGPT / Gemini subscription, or an API key.

## 功能 Features

- **中文识别**：千问 Qwen3-ASR 1.7B（MLX，适合 Apple Silicon）
- **藏文识别**：`tenzin/whisper-small-tibetan-ct2`（faster-whisper）
- **长录音**：自动按约 8 分钟分片，可转 2 小时以上，支持中断后续传
- **视频输入**：可把 MP4 / MOV / MKV / WEBM 等视频拖到应用、桌面文件夹或命令行，会先抽出音轨再转写
- **字幕校对**：若视频有内嵌字幕、同名的 SRT/VTT/ASS，或命令行给出 YouTube 网址，会把字幕与识别结果对照；能确定的按字幕和佛教词库修正，无法确定的在 Word 里黄标，并写「校对说明」。YouTube 官方字幕优先，自动字幕会标明可信度较低
- **佛教词库**：可编辑的专有名词表，同时用于识别提示和文字修饰
- **自动修饰**：修正同音错字、去掉「啊 / 呃 / 嗯」、分段和标题；可加一段标明的「摘要」
- **只输出 DOCX**：默认在文档末尾附「附录：原始转录」（未修饰原文，便于对照准确度）；可在设定中关闭。识别初稿同时保存在本机日志目录
- **三种用法**：Dock 应用（拖文件）、桌面文件夹、命令行 `transcribe`
- **默认本机**：第一次使用预选「本地（LM Studio 千问）」，识别和修饰都在本机完成，方便先测准确度

## 环境要求 Requirements

- macOS，Apple Silicon
- Python 3.10+（建议 3.12）和 [Homebrew](https://brew.sh) 的 `ffmpeg`
- 本地修饰需要 [LM Studio](https://lmstudio.ai)，并载入 `qwen/qwen3.6-35b-a3b`（思考 / thinking 关闭），API 端口 `127.0.0.1:1234`
- 可选：Claude Code CLI、ChatGPT 自带的 Codex CLI、Gemini / Antigravity CLI

## 安装 Install

```bash
git clone <this-repo> Speech2Docx
cd Speech2Docx
./install.sh
```

安装脚本会：

1. 在 `~/ASR` 创建虚拟环境并安装依赖
2. 下载两个识别模型（已缓存则跳过）
3. 生成「语音转文字.app」，放到 `/Applications`（若无权限则放到 `~/Applications`），并加到 Dock
4. 安装登录时自动启动的文件夹监视（launchd）
5. 把 `transcribe` 链到 `~/.local/bin`

## 用法 Usage

### 应用 App

把一个或多个音频或视频文件拖到 Dock 或「应用程序」里的「语音转文字」图标上。开始和结束会有系统通知，完成后自动打开 Word 文档。

第一次使用会询问用哪一种模型修饰文字，并预选「本地（LM Studio 千问）」，建议先用本机测试准确度。以后只点击图标、不拖文件，即可打开设定：改模型、登录或填写金钥、编辑佛教词库、开关「附录：原始转录」。

### 桌面文件夹 Drop folders

`~/Desktop/语音转文字/`

- `中文`：用千问 ASR
- `藏文`：用藏文 Whisper
- 原文件会移到 `完成`
- Word 文档出现在 `~/Downloads`

### 命令行 CLI

```bash
transcribe lecture.mp3
transcribe lecture.mp4
transcribe https://www.youtube.com/watch?v=VIDEO_ID
transcribe lecture.mp3 --lang zh
transcribe lecture.mp3 --lang bo
transcribe lecture.mp3 --polish local
transcribe lecture.mp3 --polish none
transcribe --settings
```

## 修饰模型 Polishing models

| 选项 Option | 说明 |
| --- | --- |
| 本地 Local | LM Studio 中的千问，完全离线 |
| Claude | Claude Code 登录，或 Anthropic API 金钥 |
| GPT | Codex CLI（ChatGPT 登录），或 OpenAI API 金钥 |
| Gemini | Gemini / Antigravity CLI（Google 登录），或 Gemini API 金钥 |

金钥保存在 macOS 钥匙圈，不会写入设定文件或日志。云端不可用时会退回本地千问，并在文档里注明。默认模型编号写在 `~/ASR/config.json` 的 `models` 字段，可自行修改。

音频不会上传。只有转好的文字在你选择云端模型时才会发送。

## 佛教词库 Glossary

默认词库在仓库的 `佛教词库.txt`，安装后复制到 `~/ASR/佛教词库.txt`。一行一词，`#` 开头为注释。也可写成 `错词 -> 正词`。在设定里选择「编辑佛教词库」会用「文本编辑」打开该文件。

## 目录结构 Layout

```
~/ASR/                 运行数据（不进仓库）
  .venv/               虚拟环境
  config.json          用户设定（不含金钥）
  佛教词库.txt
  logs/                日志与识别初稿
  work/                分片缓存，可续传
  transcribe           命令行入口

Speech2Docx/           本仓库
  src/                 源代码
  scripts/             打包应用、图标
  launchd/             监视器模板
  install.sh
```

源代码中不硬编码某台电脑的用户路径，一律使用 `$HOME` / `$ASR_HOME`。

## 许可 License

MIT。见 [LICENSE](LICENSE)。

识别模型本身另有各自的许可证，请在使用前查看 Hugging Face 上的模型页：

- [mlx-community/Qwen3-ASR-1.7B-8bit](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit)
- [tenzin/whisper-small-tibetan-ct2](https://huggingface.co/tenzin/whisper-small-tibetan-ct2)
