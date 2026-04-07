**中文** | [**English**](README_EN.md)

# 跨平台短视频智能剪辑与翻译工具

一条命令，把公开视频或本地视频变成可剪辑、可翻译、可烧录字幕的成片。
这个项目的核心入口是 `main.py`：你只需要提供一个视频 URL 或本地文件，它就会自动完成下载、分析、切片、翻译、字幕生成和结果整合，适合想在 macOS 上快速把长视频处理成短视频素材的人。

## 它能帮你做什么

- 下载 YouTube、TikTok、X/Twitter 视频，或者直接处理本地视频文件
- 自动做语音转文字、场景检测、**智能语义分段（LLM）**
- 生成双语字幕，支持软嵌入和硬烧录
- **智能硬字幕处理**：自动检测视频原有的硬字幕，支持自动遮盖（Masking）并重新叠加双语字幕，防止重叠
- 输出清晰的目录结构、摘要报告和字幕素材
- 支持 `main.py` 一键跑完整流程，也支持分步执行和快速烧录

## 面向 macOS 首次使用者

如果你是第一次在 Mac 上使用这个项目，建议先完成下面三件事：

1. 安装 `Python` 和 `ffmpeg`
2. 建一个独立虚拟环境 `venv`
3. 安装 `requirements.txt` 里的依赖

如果你要下载 X/Twitter 上需要登录才能看的视频，还需要先在本机 Chrome 里登录对应账号，因为项目默认会让 `yt-dlp` 读取 Chrome 的 cookie。

## 运行前准备

### 系统依赖

建议先安装 Homebrew，然后执行：

```bash
brew install python ffmpeg
```

### Python 依赖

在项目根目录执行：

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 安装完成后先做一个检查

```bash
python main.py --help
which ffmpeg
which whisper
```

如果 `which whisper` 没有输出，先确认 `openai-whisper` 已安装到当前虚拟环境里。

## 主要功能

### 一键流水线

`main.py` 是推荐入口。它会按下面的顺序工作：

1. 下载视频，或直接读取本地文件
2. 分析音频、语音和画面变化
3. 挑出适合发布的片段
4. 翻译并生成字幕
5. 整合输出目录
6. 可选地把字幕软嵌入或硬烧进视频

### ASR

- 优先使用 `faster-whisper`
- 如果 `faster-whisper` 不可用，会回退到 `openai-whisper` CLI
- 长视频会自动分段处理，并带缓存和超时控制
- 字幕同步会结合词级时间戳和视觉边界检测，尽量减少硬字幕偏移

### 下载

- 默认使用 `yt-dlp`
- 默认会尝试从本机 Chrome 读取登录态 cookies
- YouTube 还会尝试使用更稳的 `player_client`

### 字幕

- `--embed-subtitles`：软嵌字幕轨道
- `--burn-subtitles`：硬烧双语字幕到画面
- `--subtitle-status`：控制字幕检测和烧录策略

### 全长视频

如果你想生成一条完整的双语长视频，可以在跑完主流程后使用 `generate_full_video.py`。

## 最常用命令

### 1. 下载并处理公开视频

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 2. 本地视频全流程处理（汉化 / 双语烧录）

```bash
python main.py --local-file "./my_video.mp4" --burn-subtitles --no-clip
```

> 该命令会保持视频完整长度不切片，自动完成 ASR → 翻译 → 双语字幕硬烧录。如果视频底部已有英文硬字幕，工具会自动检测并遮盖后重新叠加双语字幕。

### 3. 指定输出目录

```bash
python main.py --url "https://example.com/video" --output "./result"
```

### 4. 硬烧双语字幕

```bash
python main.py --url "https://example.com/video" --burn-subtitles
```

### 5. 只做字幕嵌入

```bash
python main.py --url "https://example.com/video" --embed-subtitles
```

### 6. 不切片，整段处理

```bash
python main.py --url "https://example.com/video" --no-clip --embed-subtitles
```

### 7. 快速烧录模式

如果你已经有视频和字幕文件，只想直接烧录：

```bash
python main.py --burn-only --video "./video.mp4" --output "./Out-0403"
```

如果字幕文件和视频同目录，脚本会自动找：

- `<视频名>_en.srt`
- `<视频名>_zh.srt`
- `<视频名>.en.srt`
- `<视频名>.zh.srt`

## 常用参数

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--url` | 视频链接 | 必填其一 |
| `--local-file` | 本地视频文件 | 必填其一 |
| `--output` | 输出根目录 | `output/` |
| `--quality` | 下载画质 | `best` |
| `--language` | ASR 识别语言 | `en` |
| `--min-duration` | 最短切片时长 | `60` |
| `--max-duration` | 最长切片时长 | `180` |
| `--max-clips` | 最大切片数量 | `6` |
| `--clip-strategy` | 切片策略 | `opinion` |
| `--subtitle-status` | 字幕策略 | `auto` |
| `--burn-subtitles` | 硬烧双语字幕 | 关闭 |
| `--embed-subtitles` | 软嵌字幕轨道 | 关闭 |
| `--burn-only` | 仅烧录字幕 | 关闭 |

## 环境变量

你一般不需要全部设置，默认值已经能跑。

### 下载相关

- `YTDLP_COOKIES_BROWSER`：yt-dlp 从哪个浏览器读取 cookies，默认 `chrome`
- `YTDLP_YOUTUBE_PLAYER_CLIENT`：YouTube 的 `player_client`，默认 `tv`

### ASR 相关

- `WHISPER_MODEL`：Whisper 模型，默认 `medium`
- `WHISPER_WORD_TIMESTAMPS`：是否开启词级时间戳，默认 `true`
- `WHISPER_CLI_PATH`：如果 `whisper` 不在 PATH 里，可手动指定
- `ASR_LANGUAGE`：默认识别语言，默认 `en`
- `FASTER_WHISPER_LOCAL_MODEL_DIR`：本地 faster-whisper 模型目录

### 翻译相关

- `LLM_PROVIDER`：`siliconflow` / `openai` / `anthropic`
- `LLM_MODEL`：默认 `deepseek-ai/DeepSeek-V3`
- `LLM_API_KEY`：SiliconFlow 等 LLM 密钥
- `OPENAI_API_KEY`：OpenAI 翻译或纠错密钥
- `TRANSLATION_BACKEND`：`auto` / `openai` / `googletrans`

### 其他常用项

- `DOWNLOADS_DIR`
- `OUTPUT_DIR`
- `ANALYSIS_DIR`
- `CLIPS_DIR`
- `SUBTITLES_DIR`
- `LOG_LEVEL`

## 输出结构

默认情况下，项目会生成这些目录和文件：

```text
downloads/
output/
├── original/
├── clips/
├── subtitles/
├── analysis/
├── clips_with_subtitles/   # 可选，只有开启字幕嵌入才会生成
├── integration_metadata.json
└── summary.md
cache/
└── asr/
```

如果你使用 `--output ./result`，那这些子目录都会跟着切到 `./result/` 下。

## 依赖说明

### Python 包

`requirements.txt` 已经覆盖项目主流程依赖，包括：

- `yt-dlp`
- `faster-whisper`
- `openai-whisper`
- `librosa`
- `scenedetect`
- `numpy`
- `scipy`
- `deep-translator`
- `openai`
- `requests`
- `moviepy`
- `imageio-ffmpeg`
- `Pillow`
- `pyobjc-framework-Vision`

### 系统工具

- `ffmpeg`
- `ffprobe`
- Chrome 浏览器

## 推荐工作流

如果你是第一次跑，建议按这个顺序：

1. 先跑 `python main.py --help`
2. 再用一个公开视频测试 `--url`
3. 如果你已经有本地文件，用 `--local-file`
4. 如果你只是想验证字幕链路，用 `--burn-only`
5. 如果 X/Twitter 下载失败，先检查 Chrome 是否已登录

## 常见问题

### 1. `whisper` 找不到

先确认当前虚拟环境里安装了 `openai-whisper`，并且 `whisper` 在 PATH 中。

### 2. `ffmpeg` 找不到

执行：

```bash
brew install ffmpeg
```

### 3. X/Twitter 下载失败

先在 Chrome 里登录账号，再重试。默认配置会从 Chrome 读取 cookies。

### 4. 第一次运行很慢

这是正常的。ASR 模型、翻译模型和依赖第一次会下载缓存。

## 分步脚本

如果你需要更细的控制，也可以直接跑这些脚本：

```bash
python downloader.py
python analyzer.py
python clipper.py
python translator.py
python integrator.py
python embed_subtitles.py
python generate_full_video.py
```

## 目录说明

```text
main.py               # 一键流水线入口
downloader.py         # 视频下载
analyzer.py           # ASR、音频分析、场景分析
clipper.py            # 智能剪辑
translator.py         # 翻译与字幕文本生成
embed_subtitles.py    # 软嵌/硬烧字幕
subtitle_detect.py    # 字幕/硬字幕检测
subtitle_sync.py      # 字幕时间轴同步
config.py             # 全局配置
requirements.txt      # Python 依赖
scripts/              # 验证、调试、辅助脚本
tests/                # 测试
```

## 许可证

MIT License
