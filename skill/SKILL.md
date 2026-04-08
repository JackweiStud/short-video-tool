---
name: short-video-tool
description: Processes videos from YouTube/X/Twitter with ASR, translation, bilingual subtitle burning, and video summaries. Use when the user mentions video processing, subtitle generation, video translation, downloading from YouTube/X/Twitter, or summary-only video understanding.
---

# Short Video Tool

Automated video processing: download → ASR → translation → bilingual subtitles.


## What The Tool Supports

- Download from YouTube, TikTok, and X/Twitter
- Run ASR with `mlx-whisper`, `faster-whisper`, or `whisper` CLI
- Reuse chunk-level ASR cache for long videos
- Generate clips, subtitles, and integrated outputs
- Hard-burn or soft-embed bilingual subtitles
- Generate video summaries with `--summary` or `--summary-only`

## When Using This Skill

1. Confirm whether the user wants:
   - full pipeline
   - summary only
   - burn-only
2. Prefer the existing CLI in `main.py` over custom scripts
3. Keep output paths explicit when the user cares about generated files
4. Mention summary output under `output/summary/` when discussing `--summary` or `--summary-only`


## Quick Start

```bash
cd /Users/jackwl/Code/gitcode/short-video-tool

# Process YouTube video
./venv/bin/python main.py --url "https://youtube.com/watch?v=VIDEO_ID"

# Process X/Twitter video
./venv/bin/python main.py --url "https://x.com/i/status/STATUS_ID"

# Process local video with subtitle burning
./venv/bin/python main.py --local-file video.mp4 --burn-subtitles --summary

# Full video use url(no clipping)
./venv/bin/python main.py --url "URL" --no-clip --burn-subtitles --summary

# Full video use file(no clipping)
./venv/bin/python main.py --local-file video.mp4 --no-clip --burn-subtitles --summary

# Burn-only mode (skip ASR, use existing subtitles)
./venv/bin/python main.py --burn-only --video video.mp4

# summary only
./venv/bin/python main.py --summary-only --local-file video.mp4
./venv/bin/python main.py --summary-only --url "https://x.com/i/status/STATUS_ID"

# summary after full pipeline
./venv/bin/python main.py --url "https://x.com/i/status/STATUS_ID" --summary

```

## Key Parameters

**Video source** (required, choose one):
- `--url <URL>`: Video URL (YouTube / TikTok / X/Twitter)
- `--local-file <path>`: Local video file path (skips download)

**Output control**:
- `--output <dir>`: Output directory (default: `output/`)

**Clipping control**:
- `--no-clip`: Skip clipping, treat the whole video as a single segment
- `--min-duration <sec>`: Minimum clip duration in seconds (default: 15)
- `--max-duration <sec>`: Maximum clip duration in seconds (default: 60)
- `--max-clips <n>`: Maximum number of clips to extract (default: 5)
- `--clip-strategy <strategy>`: Clip selection strategy (default: `opinion`)
  - `opinion` — Opinion-driven, extracts segments with independent viewpoints
  - `topic` — Topic/chapter-driven, splits by subject structure
  - `hybrid` — Hybrid mode combining opinion and topic

**Subtitle options**:
- `--embed-subtitles`: Soft-embed subtitle track (toggle in player)
- `--burn-subtitles`: Hard-burn bilingual subtitles (EN top, ZH bottom)
- `--subtitle-status <mode>`: Subtitle strategy (default: `auto`)
  - `auto` — Auto-detect source language
  - `en` — Source is English, overlay Chinese translation only
  - `zh` — Source is Chinese, keep original subtitles
  - `none` — Burn bilingual subtitles (same as burn mode)

**ASR & video quality**:
- `--language <code>`: ASR speech recognition language (default: `en`)
- `--quality <res>`: Download resolution, e.g. `1080p` / `720p` / `best`

**Burn-only mode** (skip ASR/analysis/translation, directly burn subtitles):
- `--burn-only`: Enable quick burn mode
- `--video <path>`: Video file path (required in burn-only mode)
- `--en-subtitle <path>`: English subtitle file (optional, auto-detects `<name>_en.srt`)
- `--zh-subtitle <path>`: Chinese subtitle file (optional, auto-detects `<name>_zh.srt`)

**Summary**
- `--summary`: generate summary after the full pipeline
- `--summary-only`: run only `ASR + LLM summary`

## Performance

- 53-min video: ASR 4m27s + translation 9m = <10m total
- 57-sec video: 97s total (ASR 9s + translation 9s + burning 19s)

## Output Structure

```
output/
├── analysis/
├── clips/
├── original/
├── subtitles/
├── summary/
│   └── <video_stem>_video_summary.md
├── clips_with_subtitles/   # optional
├── integration_metadata.json
└── summary.md
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
