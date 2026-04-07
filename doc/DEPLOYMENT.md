# 部署与环境准备

这份文档聚焦“把项目放到一台新机器或新目录后，怎样稳定跑起来”。
如果你是第一次接触这个仓库，先看根目录的 `README.md`；如果你已经知道项目价值，只想快速完成安装、配置和验证，可以直接按本文执行。

## 1. 当前项目的运行方式

- 核心入口：`python main.py`
- 推荐系统：macOS
- 推荐 Python：3.10+
- 必需系统工具：`ffmpeg`、`ffprobe`
- X/Twitter 下载额外依赖：本机 Chrome 登录态
- ASR 运行方式：
  - 优先 `faster-whisper`
  - 不可用时回退到 `openai-whisper` 提供的 `whisper` CLI

## 2. macOS 快速部署

### 2.1 安装系统依赖

```bash
brew install python ffmpeg
```

安装完成后先确认：

```bash
python3 --version
ffmpeg -version
ffprobe -version
```

### 2.2 复制项目到目标目录

```bash
cd /Users/jackwl/Code/gitcode
# 然后把项目目录放到这里，或在这里 git clone
```

建议只拷贝源码，不要同时搬运旧目录里的多个虚拟环境。
最稳妥的做法是在新目录里重新创建一个 `venv/`。

### 2.3 创建虚拟环境并安装依赖

在项目根目录执行：

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2.4 安装后的最小检查

```bash
python main.py --help
which ffmpeg
which whisper
```

说明：

- `which whisper` 有输出：说明 `openai-whisper` 的 CLI 已可用
- 没有输出也不一定立刻失败：如果 `faster-whisper` 正常可用，主流程仍可先跑
- 但为了保留 ASR fallback，建议仍然让 `whisper` CLI 可用

## 3. 推荐的 `.env` 配置

程序会自动读取项目根目录的 `.env` 文件，不需要手动 `source`。

最常用的一组配置如下：

```bash
# 下载
YTDLP_COOKIES_BROWSER=chrome
YTDLP_YOUTUBE_PLAYER_CLIENT=tv

# ASR
WHISPER_MODEL=medium
WHISPER_WORD_TIMESTAMPS=true
ASR_LANGUAGE=en
# 如果 whisper 不在 PATH 里，再手动指定
# WHISPER_CLI_PATH=/absolute/path/to/whisper

# faster-whisper 本地模型目录（可选）
# FASTER_WHISPER_LOCAL_MODEL_DIR=~/models

# 翻译
LLM_PROVIDER=siliconflow
LLM_MODEL=deepseek-ai/DeepSeek-V3
LLM_API_KEY=your-key
OPENAI_API_KEY=your-openai-key
TRANSLATION_BACKEND=auto

# 输出目录（可选）
DOWNLOADS_DIR=downloads
OUTPUT_DIR=output
ANALYSIS_DIR=analysis_results
CLIPS_DIR=clips
SUBTITLES_DIR=subtitles
```

## 4. 新目录下的验证顺序

建议按这个顺序验：

### 4.1 CLI 是否正常

```bash
python main.py --help
```

### 4.2 关键工具是否就绪

```bash
which ffmpeg
which whisper
python -c "import yt_dlp, librosa, scenedetect, faster_whisper, whisper; print('deps ok')"
```

### 4.3 跑一组轻量回归测试

```bash
python -m pytest -q tests/test_config_integration.py tests/test_downloader.py tests/test_main_lock.py
```

### 4.4 用一个公开视频做最小链路验证

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 4.5 如果只想检查本地素材链路

```bash
python main.py --local-file "./my_video.mp4"
```

## 5. X/Twitter 下载说明

项目自己不处理账号登录，也不会弹登录框。
当前做法是：

1. 你先在本机 Chrome 登录 X/Twitter
2. `downloader.py` 通过 `yt-dlp` 读取 Chrome cookies
3. 下载器再用这些 cookies 去抓取视频

对应配置已经从硬编码挪到了 `config.py`：

- `YTDLP_COOKIES_BROWSER`：默认 `chrome`
- `YTDLP_YOUTUBE_PLAYER_CLIENT`：默认 `tv`

如果 X 视频下载失败，先检查：

- Chrome 是否已登录目标账号
- 视频是否需要更高权限或地区访问
- `yt-dlp` 是否需要升级

## 6. 常用运行方式

### 6.1 一键处理公开视频

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 6.2 处理本地视频

```bash
python main.py --local-file "./my_video.mp4"
```

### 6.3 硬烧双语字幕

```bash
python main.py --url "https://example.com/video" --burn-subtitles
```

### 6.4 软嵌字幕轨道

```bash
python main.py --url "https://example.com/video" --embed-subtitles
```

### 6.5 不切片，直接整段输出

```bash
python main.py --url "https://example.com/video" --no-clip --embed-subtitles
```

### 6.6 只做烧录

```bash
python main.py --burn-only --video "./video.mp4" --output "./Out-0403"
```

## 7. 输出目录

默认输出结构如下：

```text
downloads/
output/
├── original/
├── clips/
├── subtitles/
├── analysis/
├── clips_with_subtitles/
├── integration_metadata.json
└── summary.md
cache/
└── asr/
```

如果你传了 `--output ./result`，这些内容会落到 `./result/` 下。

## 8. 常见问题

### 8.1 `whisper` 找不到

原因：

- `openai-whisper` 没装到当前虚拟环境
- `whisper` 不在 PATH

解决：

```bash
source venv/bin/activate
pip install -r requirements.txt
which whisper
```

如果仍找不到，可以通过 `.env` 设置：

```bash
WHISPER_CLI_PATH=/absolute/path/to/whisper
```

### 8.2 `ffmpeg` 找不到

```bash
brew install ffmpeg
```

### 8.3 X/Twitter 视频下载失败

优先排查：

- Chrome 是否已登录
- 该视频在浏览器里是否能正常播放
- 当前网络、地区和 cookies 是否有效

### 8.4 搬到新目录后环境失效

这是虚拟环境最常见的问题之一。
不要依赖旧目录里的解释器路径，直接在新目录重新执行：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 9. Linux 补充说明

项目也可以在 Linux 上运行，但当前仓库里有一部分 OCR / 字幕检测能力仍偏向 macOS 环境。
如果你是首次使用者，建议优先在 macOS 上完成首轮验证，再考虑迁移到 Linux。
