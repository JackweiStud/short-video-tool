[**中文**](README_ZH.md) | **English**

# Short Video Tool

Default documentation is in English. For Chinese documentation, see [`README_ZH.md`](README_ZH.md).

`short-video-tool` turns a public video URL or a local video file into clipped videos, bilingual subtitles, integrated outputs, and optional LLM-generated summaries.

Core entry point: [`main.py`](main.py)

## What It Does

- Download videos from YouTube, TikTok, and X/Twitter, or process a local file directly
- Run ASR with `mlx-whisper` on Apple Silicon when available, otherwise fall back to `faster-whisper` or `whisper` CLI
- Chunk long videos for ASR with per-chunk cache reuse and resume support
- Detect scene changes and topic structure for clip selection
- Translate and generate bilingual subtitles
- Soft-embed or hard-burn subtitles into output clips
- Generate a per-video Markdown summary with:
  - one-sentence summary
  - core points
  - evidence points
  - actionable takeaways
  - caveats
  - X post copy

## Requirements

- macOS recommended
- `Python 3`
- `ffmpeg` and `ffprobe`
- A working virtual environment
- LLM API key for translation and summary features

Install system tools with Homebrew:

```bash
brew install python ffmpeg
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Quick verification:

```bash
python main.py --help
which ffmpeg
which whisper
```

## Common Commands

Full pipeline from a public URL:

```bash
./venv/bin/python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Full pipeline for a local file:

```bash
./venv/bin/python main.py --local-file "./my_video.mp4"
```

Full video, no clipping, hard-burn bilingual subtitles:

```bash
./venv/bin/python main.py --local-file "./my_video.mp4" --no-clip --burn-subtitles
```

Summary only, skip clipping / translation / integration:

```bash
./venv/bin/python main.py --summary-only --local-file "./my_video.mp4" --output "./result"
```

Full pipeline plus video summary:

```bash
./venv/bin/python main.py --summary --url "https://www.youtube.com/watch?v=VIDEO_ID" --output "./result"
```

Burn-only mode with existing subtitles:

```bash
./venv/bin/python main.py --burn-only --video "./video.mp4" --output "./Out-0403"
```

## Key CLI Options

### Input

- `--url <URL>`: remote video URL
- `--local-file <path>`: local video file

### Output

- `--output <dir>`: output root directory, default `output/`

### Summary

- `--summary`: run the full pipeline, then generate a video summary Markdown file
- `--summary-only`: run only `ASR + LLM summary`

Summary files are written to:

```text
<output>/summary/<video_stem>_video_summary.md
```

### Clipping

- `--no-clip`: treat the full video as one segment
- `--min-duration <sec>`: minimum clip duration
- `--max-duration <sec>`: maximum clip duration
- `--max-clips <n>`: maximum number of output clips
- `--clip-strategy opinion|topic|hybrid`: clip selection strategy

### Subtitles

- `--embed-subtitles`: soft subtitle track
- `--burn-subtitles`: hard-burn bilingual subtitles
- `--subtitle-status auto|en|zh|none`: subtitle handling mode

### ASR and Download

- `--language <code>`: ASR language, default `en`
- `--quality <value>`: download quality such as `best`, `1080p`, `720p`
- Chinese ASR can optionally use `initial_prompt` for punctuation/style guidance when `ASR_INITIAL_PROMPT_ENABLED=true`

### Burn-only

- `--burn-only`: skip ASR, analysis, translation, and clipping
- `--video <path>`: source video for burn-only mode
- `--en-subtitle <path>`: English subtitle file
- `--zh-subtitle <path>`: Chinese subtitle file

## ASR Notes

- Apple Silicon prefers `mlx-whisper`
- Long videos are chunked before ASR
- Each chunk has its own cache file
- Re-running the same video can reuse cached chunks
- Interrupted runs can resume without starting ASR from scratch
- `mlx-whisper` cache keys are isolated from `faster-whisper` and `whisper` CLI caches

## Environment Variables

Common variables:

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
- `WHISPER_CLI_PATH`
- `DOWNLOADS_DIR`
- `OUTPUT_DIR`
- `ANALYSIS_DIR`
- `CLIPS_DIR`
- `SUBTITLES_DIR`
- `LOG_LEVEL`

## Output Structure

Typical output layout:

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
├── clips_with_subtitles/   # optional
├── integration_metadata.json
└── summary.md
cache/
└── asr/
```

In summary Markdown, the final section is an `X Post` copy block that can be reused directly.

## Recommended Workflow

1. Run `python main.py --help`
2. Test a public URL first
3. Use `--local-file` for local media
4. Use `--summary-only` when you only need transcript-driven understanding
5. Use `--burn-only` when subtitles already exist

## Troubleshooting

`whisper` not found:

- ensure the virtual environment is activated
- install `openai-whisper` into the same environment

X/Twitter download fails:

- verify Chrome is logged in
- upgrade `yt-dlp`

```bash
./venv/bin/pip install --upgrade yt-dlp
```

Summary generation fails:

- verify `LLM_API_KEY` or provider-specific key is configured
- verify the selected `LLM_MODEL` is available

## Additional Files

- [README_ZH.md](README_ZH.md)
- [main.py](main.py)
- [skill/SKILL.md](skill/SKILL.md)
