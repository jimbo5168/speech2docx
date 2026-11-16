# 为我闻思（Wei Wo Wen Si） / speech2docx

本地语音转文字工具（macOS，Apple Silicon）。把 MP3 / M4A / WAV 等音频拖到应用图标或桌面文件夹，即会在「下载」文件夹生成排好版的 Word 文档（`.docx`）。

音频始终只在本机识别。可选的文字修饰可以使用本地大模型（LM Studio），或你已登录的 Claude / ChatGPT / Gemini 订阅，也可以改用 API 金钥。

---

**Wei Wo Wen Si** (为我闻思; traditional 為我聞思) is published in this repository as **speech2docx**.

Local speech-to-text for macOS on Apple Silicon. Drop an MP3, M4A, WAV, or a video (MP4, MOV, MKV, WEBM, …) onto the app icon or a Desktop folder and a formatted Word document appears in Downloads. Video files are converted by extracting the soundtrack first. If subtitles are embedded, sit in a sidecar file, or come from a YouTube URL, they are aligned with the ASR text for proofreading; unresolved disagreements are highlighted in yellow.

Audio is transcribed only on the machine. The first-run chooser preselects Local (LM Studio Qwen) so you can check accuracy before switching to a cloud model. By default the Word file also ends with an appendix, 附录：原始转录, containing the unpolished ASR text (turn this off in Settings). English audio is transcribed, optionally proofread against English subtitles, then translated into Simplified Chinese with the Buddhist glossary (bodhicitta → 菩提心). The Word file is a two-column English/Chinese layout by default. Documents use a bundled typography template (A5, header banner, footer page numbers, 华文行楷 titles and 宋体 body). Choose your own template in Settings if you like. Optional polishing can use Local, or a signed-in Claude / ChatGPT / Gemini subscription, or an API key.

## 功能 Features

- **原字幕直出**：优先采用同名 SRT/VTT/ASS/SSA，其次提取内嵌文字字幕，直接生成同名 SRT 和当前模板的 Word，注明「本稿直接采用原字幕（信任原字幕），未经转写与校对」。跳过语音识别和润色。无可用文字字幕时沿用语音识别，不处理图像字幕或烧录字幕。

- **中文识别**：千问 Qwen3-ASR 1.7B（MLX，适合 Apple Silicon）
- **藏文识别**：`tenzin/whisper-small-tibetan-ct2`（faster-whisper）
- **长录音**：自动按约 8 分钟分片，可转 2 小时以上，支持中断后续传
- **视频输入**：可把 MP4 / MOV / MKV / WEBM 等视频拖到应用、桌面文件夹或命令行，会先抽出音轨再转写
- **字幕校对**：若视频有内嵌字幕、同名的 SRT/VTT/ASS，或命令行给出 YouTube 网址，会把字幕与识别结果对照；能确定的按字幕和佛教词库修正，无法确定的在 Word 里黄标，并写「校对说明」。YouTube 官方字幕优先，自动字幕会标明可信度较低
- **佛教词库**：可编辑的专有名词表，同时用于识别提示和文字修饰
- **自动修饰**：修正同音错字、去掉「啊 / 呃 / 嗯」、分段和标题；可加一段标明的「摘要」
- **只输出 DOCX**：默认在文档末尾附「附录：原始转录」（未修饰原文，便于对照准确度）；可在设定中关闭。识别初稿同时保存在本机日志目录
- **英文转中文**：英文录音先转成英文原文，再译成简体中文，佛教词库同样生效（例如 bodhicitta → 菩提心）。若有英文字幕会先校对英文再翻译。默认做成中英对照两栏，也可在设定里改成只出中文。桌面文件夹可把英文放进「英文」
- **固定版式**：输出套用内置 Word 模板（约 A5、页眉图案、页脚页码）。标题用华文行楷，讲者与「听打稿」用华文楷体，正文宋体，小节标题黑体。页眉左侧的册名已去掉。可在设定中改用自己的模板，或恢复默认版式
- **三种用法**：Dock 应用（拖文件）、桌面文件夹、命令行 `transcribe`
- **默认本机**：第一次使用预选「本地（LM Studio 千问）」，识别和修饰都在本机完成，方便先测准确度

## 环境要求 Requirements

- macOS，Apple Silicon
- Python 3.10+（建议 3.12）和 [Homebrew](https://brew.sh) 的 `ffmpeg`
- 本地修饰需要 [LM Studio](https://lmstudio.ai)，并载入 `qwen/qwen3.6-35b-a3b`（思考 / thinking 关闭），API 端口 `127.0.0.1:1234`
- 可选：Claude Code CLI、ChatGPT 自带的 Codex CLI、Gemini / Antigravity CLI

## 安装 Install

```bash
git clone <this-repo> speech2docx
cd speech2docx
./install.sh
```

安装脚本会：

1. 在 `~/ASR` 创建虚拟环境并安装依赖
2. 下载两个识别模型（已缓存则跳过）
3. 生成「为我闻思.app」，放到 `/Applications`（若无权限则放到 `~/Applications`），并加到 Dock
4. 安装登录时自动启动的文件夹监视（launchd）
5. 把 `transcribe` 链到 `~/.local/bin`

## 用法 Usage

点 Dock 或「应用程序」里的「为我闻思」，就会打开应用窗口。第一次使用默认已选「本地千问」，可在窗口的「设定」页随时更改。

把音频或视频拖进来，或选择档案、文件夹，或贴上 YouTube 链接，转好的 Word 会放在输出位置（默认为「下载」文件夹）。

Click the Dock or Applications icon to open the window. You can drop files onto the window, choose files or a folder, or paste a YouTube link. Finished Word documents go to the output folder (Downloads by default). Dragging files onto the Dock icon still works and will queue them in the same window.

### 使用方法 How to use

1. 打开「为我闻思」
2. 选择语言：中文、英文→中文，或藏文
3. 用下面任一方式加入内容：
   - 把档案拖到窗口里，或点「选择档案…」
   - 点「选择文件夹…」，处理文件夹里的音视频
   - 贴上 YouTube 链接，再点「转换」（有官方字幕时会用来校对）
4. 需要的话点「更改…」另选输出位置
5. 队列里会显示「等待 / 转录中 / 校对中 / 修饰中 / 完成 / 失败」。完成后可「打开文档」或「在 Finder 中显示」
6. 「设定」页可改修饰模型、附录、是否生成摘要或小节标题（默认关闭）、校对提示词、佛教词库和 Word 模板。校对提示词会放在修饰模型提示的最前面。

![主画面](docs/gui-main.png)

![转换中](docs/gui-processing.png)

![转换完成](docs/gui-done.png)

![设定](docs/gui-settings.png)

### 应用图标 App icon

也可以把档案直接拖到 Dock 上的「为我闻思」图标，文件会加入同一窗口的队列。

### 桌面文件夹 Drop folders

`~/Desktop/语音转文字/`（文件夹名保持不变，与应用名不同）

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

speech2docx/           本仓库
  src/                 源代码
  scripts/             打包应用、图标
  launchd/             监视器模板
  templates/default.docx  版式模板（无讲课正文）
  ui/                    应用窗口界面
  docs/                  使用截图
  install.sh
```

源代码中不硬编码某台电脑的用户路径，一律使用 `$HOME` / `$ASR_HOME`。

## 许可 License

MIT。见 [LICENSE](LICENSE)。

识别模型本身另有各自的许可证，请在使用前查看 Hugging Face 上的模型页：

- [mlx-community/Qwen3-ASR-1.7B-8bit](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit)
- [tenzin/whisper-small-tibetan-ct2](https://huggingface.co/tenzin/whisper-small-tibetan-ct2)
