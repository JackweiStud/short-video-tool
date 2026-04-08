---
name: short-video-tool
description: Processes videos from YouTube/X/Twitter with ASR, translation, and bilingual subtitle burning. Use when the user mentions video processing, subtitle generation, video translation, or downloading from YouTube/X/Twitter.
---

# Short Video Tool

Automated video processing: download → ASR → translation → bilingual subtitles.

## Core Capabilities

- Multi-platform download (YouTube, X/Twitter, TikTok)
- High-performance ASR (mlx-whisper: 11.9x realtime on Apple Silicon)
- Concurrent translation (DeepSeek-V3, 6 workers)
- Bilingual subtitle burning (auto OCR detection, FFmpeg rendering)

## Quick Start

```bash
cd /Users/jackwl/Code/gitcode/short-video-tool

# Process YouTube video
./venv/bin/python main.py --url "https://youtube.com/watch?v=VIDEO_ID"

# Process X/Twitter video
./venv/bin/python main.py --url "https://x.com/i/status/STATUS_ID"

# Process local video with subtitle burning
./venv/bin/python main.py --local-file video.mp4 --burn-subtitles

# Full video (no clipping)
./venv/bin/python main.py --url "URL" --no-clip --burn-subtitles

# Burn-only mode (skip ASR, use existing subtitles)
./venv/bin/python main.py --burn-only --video video.mp4
```

## Key Parameters

**Video source** (required):
- `--url <URL>`: Video URL
- `--local-file <path>`: Local video file

**Common options**:
- `--burn-subtitles`: Burn bilingual subtitles
- `--no-clip`: Process full video (skip clipping)
- `--output <dir>`: Output directory (default: output/)
- `--subtitle-status <mode>`: auto/en/zh/none

**Burn-only mode**:
- `--burn-only`: Skip ASR, burn existing subtitles
- `--video <path>`: Video file
- `--en-subtitle <path>`: English subtitle (optional, auto-detected)
- `--zh-subtitle <path>`: Chinese subtitle (optional, auto-detected)

## Performance

- 53-min video: ASR 4m27s + translation 9m = <10m total
- 57-sec video: 97s total (ASR 9s + translation 9s + burning 19s)

## Output Structure

```
output/
├── clips_with_subtitles/  # Final videos with burned subtitles
├── subtitles/             # *_en.srt, *_zh.srt, *_zh_aligned.srt
├── original/              # Downloaded video
└── summary.md             # Processing report
```

## Environment Setup

Required environment variable:
```bash
export SILICONFLOW_API_KEY="your_api_key"
```

Optional:
```bash
export MLX_WHISPER_LOCAL_MODEL_DIR="~/models"
```

## Common Scenarios

**YouTube tutorial → bilingual**:
```bash
cd /Users/jackwl/Code/gitcode/short-video-tool
./venv/bin/python main.py --url "YOUTUBE_URL" --no-clip --burn-subtitles
```

**X/Twitter short video**:
```bash
cd /Users/jackwl/Code/gitcode/short-video-tool
./venv/bin/python main.py --url "X_URL" --burn-subtitles
```

**Batch local videos**:
```bash
cd /Users/jackwl/Code/gitcode/short-video-tool
for video in *.mp4; do
  ./venv/bin/python main.py --local-file "$video" --burn-subtitles
done
```

**Burn existing subtitles**:
```bash
cd /Users/jackwl/Code/gitcode/short-video-tool
./venv/bin/python main.py --burn-only --video video.mp4
```

## Troubleshooting

**SSL error (X/Twitter)**:
```bash
cd /Users/jackwl/Code/gitcode/short-video-tool
./venv/bin/pip install --upgrade yt-dlp
```

**GPU memory issue**: Edit `config.py`, set `whisper_model = "small"`

**Translation slow**: Already optimized with 6 concurrent workers

## Additional Resources

- Roadmap: `doc/roadmap.md`
- Full docs: `doc/README.md`
- Main script: `main.py`
