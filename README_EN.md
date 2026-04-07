[**中文**](README.md) | **English**

# Cross-Platform Short Video Intelligent Clipping & Translation Tool

One command to turn any public or local video into clipped, translated, subtitle-burned content.

The main entry point is `main.py`: just provide a video URL or local file, and it automatically handles downloading, analysis, clipping, translation, subtitle generation, and output integration. Perfect for anyone on macOS who wants to quickly turn long videos into short-form content.

## What It Does

- Download videos from YouTube, TikTok, X/Twitter, or process local video files directly
- Automatic speech-to-text, scene detection, and **intelligent semantic segmentation (LLM)**
- Generate bilingual subtitles with soft-embed or hard-burn support
- **Smart hard subtitle handling**: Automatically detects existing hard-burned subtitles in the video, masks them, and overlays new bilingual subtitles to prevent overlap
- Clean output directory structure with summary reports and subtitle assets
- Full pipeline via `main.py`, or run individual steps and quick burn mode

## Getting Started on macOS

If this is your first time using the project on Mac, complete these three steps:

1. Install `Python` and `ffmpeg`
2. Create an isolated virtual environment (`venv`)
3. Install dependencies from `requirements.txt`

If you need to download X/Twitter videos that require login, you must first log in to the corresponding account in Chrome on your machine, as the project uses `yt-dlp` to read Chrome cookies by default.

## Prerequisites

### System Dependencies

Install Homebrew first, then run:

```bash
brew install python ffmpeg
```

### Python Dependencies

In the project root directory:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Post-Install Verification

```bash
python main.py --help
which ffmpeg
which whisper
```

If `which whisper` produces no output, verify that `openai-whisper` is installed in your current virtual environment.

## Key Features

### One-Command Pipeline

`main.py` is the recommended entry point. It works in the following order:

1. Download the video, or read a local file
2. Analyze audio, speech, and visual transitions
3. Select the best clips for publishing
4. Translate and generate subtitles
5. Integrate the output directory
6. Optionally soft-embed or hard-burn subtitles into the video

### ASR (Speech-to-Text)

- Prefers `faster-whisper`
- Falls back to `openai-whisper` CLI if unavailable
- Long videos are automatically chunked with caching and timeout control
- Subtitle sync combines word-level timestamps with visual boundary detection to minimize hard subtitle drift

### Downloading

- Uses `yt-dlp` by default
- Automatically attempts to read login cookies from local Chrome
- YouTube also tries a more stable `player_client`

### Subtitles

- `--embed-subtitles`: Soft-embed subtitle tracks (toggleable in player)
- `--burn-subtitles`: Hard-burn bilingual subtitles onto the video
- `--subtitle-status`: Control subtitle detection and burn strategy

### Full-Length Video

To generate a complete bilingual long video, use `generate_full_video.py` after running the main pipeline.

## Common Commands

### 1. Download and Process a Public Video

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 2. Full Pipeline for Local Video (Localization / Bilingual Burn)

```bash
python main.py --local-file "./my_video.mp4" --burn-subtitles --no-clip
```

> This command keeps the full video length without clipping, and automatically runs ASR → Translation → Bilingual subtitle hard-burn. If the video already has hard-burned English subtitles at the bottom, the tool will automatically detect, mask, and overlay new bilingual subtitles.

### 3. Specify Output Directory

```bash
python main.py --url "https://example.com/video" --output "./result"
```

### 4. Hard-Burn Bilingual Subtitles

```bash
python main.py --url "https://example.com/video" --burn-subtitles
```

### 5. Soft-Embed Subtitles Only

```bash
python main.py --url "https://example.com/video" --embed-subtitles
```

### 6. No Clipping, Process Full Video

```bash
python main.py --url "https://example.com/video" --no-clip --embed-subtitles
```

### 7. Quick Burn Mode

If you already have a video and subtitle files and just want to burn them:

```bash
python main.py --burn-only --video "./video.mp4" --output "./Out-0403"
```

If subtitle files are in the same directory as the video, the script will auto-discover:

- `<video_name>_en.srt`
- `<video_name>_zh.srt`
- `<video_name>.en.srt`
- `<video_name>.zh.srt`

## Parameters

| Parameter | Description | Default |
|---|---|---|
| `--url` | Video URL | Required (one of) |
| `--local-file` | Local video file path | Required (one of) |
| `--output` | Output root directory | `output/` |
| `--quality` | Download quality | `best` |
| `--language` | ASR recognition language | `en` |
| `--min-duration` | Minimum clip duration (seconds) | `60` |
| `--max-duration` | Maximum clip duration (seconds) | `180` |
| `--max-clips` | Maximum number of clips | `6` |
| `--clip-strategy` | Clipping strategy | `opinion` |
| `--subtitle-status` | Subtitle strategy | `auto` |
| `--burn-subtitles` | Hard-burn bilingual subtitles | Off |
| `--embed-subtitles` | Soft-embed subtitle tracks | Off |
| `--burn-only` | Burn subtitles only | Off |

## Environment Variables

You generally don't need to set all of these — the defaults work out of the box.

### Download

- `YTDLP_COOKIES_BROWSER`: Browser for yt-dlp cookie reading, default `chrome`
- `YTDLP_YOUTUBE_PLAYER_CLIENT`: YouTube `player_client`, default `tv`

### ASR

- `WHISPER_MODEL`: Whisper model size, default `medium`
- `WHISPER_WORD_TIMESTAMPS`: Enable word-level timestamps, default `true`
- `WHISPER_CLI_PATH`: Manual path if `whisper` is not in PATH
- `ASR_LANGUAGE`: Default recognition language, default `en`
- `FASTER_WHISPER_LOCAL_MODEL_DIR`: Local faster-whisper model directory

### Translation

- `LLM_PROVIDER`: `siliconflow` / `openai` / `anthropic`
- `LLM_MODEL`: Default `deepseek-ai/DeepSeek-V3`
- `LLM_API_KEY`: SiliconFlow or other LLM API key
- `OPENAI_API_KEY`: OpenAI translation/correction key
- `TRANSLATION_BACKEND`: `auto` / `openai` / `googletrans`

### Other

- `DOWNLOADS_DIR`
- `OUTPUT_DIR`
- `ANALYSIS_DIR`
- `CLIPS_DIR`
- `SUBTITLES_DIR`
- `LOG_LEVEL`

## Output Structure

By default, the project generates these directories and files:

```text
downloads/
output/
├── original/
├── clips/
├── subtitles/
├── analysis/
├── clips_with_subtitles/   # Optional, only generated when subtitle embedding is enabled
├── integration_metadata.json
└── summary.md
cache/
└── asr/
```

If you use `--output ./result`, all subdirectories will be placed under `./result/`.

## Dependencies

### Python Packages

`requirements.txt` covers all main pipeline dependencies, including:

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

### System Tools

- `ffmpeg`
- `ffprobe`
- Chrome browser

## Recommended Workflow

If this is your first run, follow this order:

1. Run `python main.py --help`
2. Test with a public video using `--url`
3. If you have a local file, use `--local-file`
4. To verify the subtitle pipeline only, use `--burn-only`
5. If X/Twitter download fails, check that Chrome is logged in

## FAQ

### 1. `whisper` not found

Verify that `openai-whisper` is installed in the current virtual environment and that `whisper` is in your PATH.

### 2. `ffmpeg` not found

Run:

```bash
brew install ffmpeg
```

### 3. X/Twitter download fails

Log in to the account in Chrome first, then retry. The default config reads cookies from Chrome.

### 4. First run is slow

This is normal. ASR models, translation models, and dependencies are downloaded and cached on first use.

## Individual Scripts

For finer control, you can run these scripts directly:

```bash
python downloader.py
python analyzer.py
python clipper.py
python translator.py
python integrator.py
python embed_subtitles.py
python generate_full_video.py
```

## Directory Overview

```text
main.py               # One-command pipeline entry point
downloader.py         # Video downloading
analyzer.py           # ASR, audio analysis, scene analysis
clipper.py            # Intelligent clipping
translator.py         # Translation and subtitle text generation
embed_subtitles.py    # Soft-embed / hard-burn subtitles
subtitle_detect.py    # Subtitle / hard subtitle detection
subtitle_sync.py      # Subtitle timeline sync
config.py             # Global configuration
requirements.txt      # Python dependencies
scripts/              # Verification, debugging, helper scripts
tests/                # Tests
```

## License

MIT License
