**中文** | [**English**](README.md)

# Short Video Tool

`short-video-tool` 可以把公开视频 URL 或本地视频文件处理成可剪辑片段、双语字幕、整合产物，以及可选的 LLM 视频总结。

核心入口：[`main.py`](main.py)

## 功能概览

- 下载 YouTube、TikTok、X/Twitter 视频，或直接处理本地文件
- 在 Apple Silicon 上优先使用 `mlx-whisper` 做 ASR，其余环境回退到 `faster-whisper`
- 对长视频按分片执行 ASR，并支持分片缓存复用与中断续跑
- 做场景变化检测和主题结构分析，辅助切片
- 翻译并生成双语字幕
- 支持字幕软嵌入和硬烧录
- 为每个视频生成 Markdown 总结，包含：
  - 一句话概括
  - 核心内容
  - 关键依据
  - 可执行建议
  - 适用边界
  - X Post 文案

## 环境要求

- 建议在 macOS 上运行
- `Python 3`
- `ffmpeg` 与 `ffprobe`
- 独立虚拟环境
- 用于翻译和总结的 LLM API Key

系统依赖安装：

```bash
brew install python ffmpeg
```

Python 环境准备：

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

安装完成后建议先检查：

```bash
python main.py --help
which ffmpeg
```

## 常用命令

处理公开视频完整流程：

```bash
./venv/bin/python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

处理本地视频完整流程：

```bash
./venv/bin/python main.py --local-file "./my_video.mp4"
```

整段视频不切片并硬烧双语字幕：

```bash
./venv/bin/python main.py --local-file "./my_video.mp4" --no-clip --burn-subtitles
```

只生成视频总结，跳过切片、翻译和整合：

```bash
./venv/bin/python main.py --summary-only --local-file "./my_video.mp4" --output "./result"
```

完整流程结束后额外生成视频总结：

```bash
./venv/bin/python main.py --summary --url "https://www.youtube.com/watch?v=VIDEO_ID" --output "./result"
```

仅烧录已有字幕：

```bash
./venv/bin/python main.py --burn-only --video "./video.mp4" --output "./Out-0403"
```

## 关键参数

### 输入

- `--url <URL>`：远程视频链接
- `--local-file <path>`：本地视频文件

### 输出

- `--output <dir>`：输出根目录，默认 `output/`

### 总结

- `--summary`：完整流程结束后额外生成 Markdown 视频总结
- `--summary-only`：只执行 `ASR + LLM 总结`

总结文件输出到：

```text
<output>/summary/<video_stem>_video_summary.md
```

### 切片

- `--no-clip`：把整段视频当成一个片段处理
- `--min-duration <sec>`：最短切片时长
- `--max-duration <sec>`：最长切片时长
- `--max-clips <n>`：最大输出片段数
- `--clip-strategy opinion|topic|hybrid`：切片策略

### 字幕

- `--embed-subtitles`：软嵌字幕轨道
- `--burn-subtitles`：硬烧双语字幕
- `--subtitle-status auto|en|zh|none`：字幕处理模式

### ASR 与下载

- `--language <code>`：ASR 语言，默认 `en`
- `--quality <value>`：下载画质，例如 `best`、`1080p`、`720p`
- 当 `ASR_INITIAL_PROMPT_ENABLED=true` 时，中文 ASR 会注入 `initial_prompt` 以辅助标点和风格

### Burn-only

- `--burn-only`：跳过 ASR、分析、翻译和切片
- `--video <path>`：burn-only 模式下的视频路径
- `--en-subtitle <path>`：英文字幕文件
- `--zh-subtitle <path>`：中文字幕文件

## ASR 说明

- Apple Silicon 优先使用 `mlx-whisper`
- 长视频会先分片再做 ASR
- 每个分片单独缓存
- 相同视频重复运行时可复用缓存分片
- 中途中断后可从未完成分片继续
- `mlx-whisper` 的缓存键与 `faster-whisper`、`whisper` CLI 相互隔离

## 环境变量

常用变量：

- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_API_KEY`
- `OPENAI_API_KEY`
- `TRANSLATION_BACKEND`
- `WHISPER_MODEL`
- `WHISPER_WORD_TIMESTAMPS`
- `ASR_INITIAL_PROMPT_ENABLED`
- `ASR_INITIAL_PROMPT_TEXT`
- `ASR_LANGUAGE`
- `FASTER_WHISPER_LOCAL_MODEL_DIR`
- `MLX_WHISPER_LOCAL_MODEL_DIR`
- `DOWNLOADS_DIR`
- `OUTPUT_DIR`
- `ANALYSIS_DIR`
- `CLIPS_DIR`
- `SUBTITLES_DIR`
- `LOG_LEVEL`

## 输出结构

典型输出目录：

```text
downloads/
output/
├── analysis/
│   ├── analysis_result.json
│   └── extracted_audio.wav
├── clips/
├── original/
├── subtitles/
├── summary/
│   └── <video_stem>_video_summary.md
├── clips_with_subtitles/   # 可选
├── integration_metadata.json
└── summary.md
cache/
└── asr/
```

视频总结 Markdown 的最后一节会额外输出一段可直接复用的 `X Post` 文案。

## 推荐使用顺序

1. 先运行 `python main.py --help`
2. 先用一个公开视频测试主流程
3. 本地文件使用 `--local-file`
4. 只想快速了解视频内容时用 `--summary-only`
5. 已有字幕时用 `--burn-only`

## 常见问题

X/Twitter 下载失败：

- 确认本机 Chrome 已登录
- 升级 `yt-dlp`

```bash
./venv/bin/pip install --upgrade yt-dlp
```

视频总结生成失败：

- 确认已配置 `LLM_API_KEY` 或对应服务商密钥
- 确认当前 `LLM_MODEL` 可用

## 相关文件

- [README.md](README.md)
- [main.py](main.py)
- [skill/SKILL.md](skill/SKILL.md)
