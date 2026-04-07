#!/usr/bin/env python3
"""
Test D+B subtitle sync on GTC clip_1.

Steps:
  1. ASR with Whisper medium + word_timestamps (via Homebrew Python)
  2. SubtitleSync.get_aligned_segments (D first, then B)
  3. Translate aligned EN segments → ZH
  4. Overlay ZH on clip → output_db_sync_clip1.mp4
  5. Extract proof frame for visual inspection
"""
import glob
import json
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CWD = os.path.dirname(os.path.abspath(__file__))
os.chdir(CWD)

# Prefer the current interpreter, but allow override for isolated Whisper envs.
WHISPER_PYTHON = os.getenv("WHISPER_PYTHON") or sys.executable

# ── Find input files ──────────────────────────────────────────────────────
video_files = glob.glob("clips/*GTC*_clip_2.mp4")
if not video_files:
    print("ERROR: GTC clip_2 not found in clips/"); sys.exit(1)

VIDEO  = video_files[0]
OUTPUT = "debug_frames/output_db_sync_clip2.mp4"
PROOF  = "debug_frames/db_sync_proof.jpg"
ASR_CACHE = "/tmp/db_sync_asr_clip2.json"

print(f"\n{'='*60}")
print("D+B Subtitle Sync Test")
print(f"{'='*60}")
print(f"Input : {os.path.basename(VIDEO)}")
print(f"Output: {OUTPUT}")

# ── Step 1: ASR with Whisper medium + word_timestamps ────────────────────
if os.path.exists(ASR_CACHE):
    print(f"\n✓ Loading cached ASR result from {ASR_CACHE}...")
    with open(ASR_CACHE, 'r', encoding='utf-8') as f:
        asr_segments = json.load(f)
    print(f"  {len(asr_segments)} segments loaded")
else:
    print("\n▶ Step 1: Extracting audio...")
    AUDIO = "/tmp/db_sync_audio.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", VIDEO,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", AUDIO
    ], capture_output=True, timeout=60)
    print(f"  Audio: {AUDIO}")

    print("▶ Step 1: Running Whisper medium (word_timestamps=True)...")
    whisper_script = f"""
import whisper, json, sys
model = whisper.load_model("medium")
print("Model loaded, transcribing...", file=sys.stderr)
result = model.transcribe("{AUDIO}", language="en", word_timestamps=True)
segs = []
for seg in result["segments"]:
    entry = {{"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}}
    if "words" in seg:
        entry["words"] = [{{"word": w["word"], "start": w["start"], "end": w["end"]}} for w in seg["words"]]
    segs.append(entry)
with open("{ASR_CACHE}", "w") as f:
    json.dump(segs, f, ensure_ascii=False, indent=2)
print(f"ASR done: {{len(segs)}} segments", file=sys.stderr)
"""
    res = subprocess.run([WHISPER_PYTHON, "-c", whisper_script],
                         timeout=300, cwd=CWD)

    if res.returncode != 0 or not os.path.exists(ASR_CACHE):
        print("ERROR: Whisper ASR failed"); sys.exit(1)

    with open(ASR_CACHE, 'r', encoding='utf-8') as f:
        asr_segments = json.load(f)
    print(f"  ✅ ASR complete: {len(asr_segments)} segments")

# Print sample
print("\n  Sample ASR segments with word timestamps:")
for seg in asr_segments[:3]:
    print(f"  [{seg['start']:.2f}s-{seg['end']:.2f}s] {seg['text'][:70]}")
    if 'words' in seg:
        sample_words = seg['words'][:5]
        print(f"    Words: {[(w['word'], round(w['start'],2)) for w in sample_words]}")

# ── Step 2: D+B Subtitle Sync ─────────────────────────────────────────────
print("\n▶ Step 2: Running D+B Subtitle Sync (frame-diff @ 5fps)...")
from subtitle_sync import SubtitleSync

syncer = SubtitleSync(sample_fps=5.0, diff_threshold=0.015)
en_aligned = syncer.get_aligned_segments(VIDEO, asr_segments)

print(f"\n  ✅ Aligned: {len(en_aligned)} segments")
avg_dur = sum(s['end']-s['start'] for s in en_aligned) / max(len(en_aligned), 1)
print(f"  Avg segment duration: {avg_dur:.1f}s")
print("\n  Sample aligned segments (EN):")
for seg in en_aligned[:8]:
    print(f"    [{seg['start']:.2f}s-{seg['end']:.2f}s] {seg['text'][:65]}")

# ── Step 3: Translate aligned EN → ZH ────────────────────────────────────
print("\n▶ Step 3: Translating to Chinese...")
from translator import Translator

translator = Translator()
en_texts  = [s['text'] for s in en_aligned]
zh_texts  = translator._batch_translate(en_texts, target_lang='zh')

zh_entries = []
for seg, zh_text in zip(en_aligned, zh_texts):
    zh_entries.append({'start': seg['start'], 'end': seg['end'], 'text': zh_text})

print(f"\n  ✅ Translation complete: {len(zh_entries)} segments")
print("  Sample bilingual pairs:")
for en_seg, zh_seg in zip(en_aligned[:5], zh_entries[:5]):
    print(f"    EN [{en_seg['start']:.1f}s]: {en_seg['text'][:55]}")
    print(f"    ZH [{zh_seg['start']:.1f}s]: {zh_seg['text'][:55]}")
    print()

# ── Step 4: Render output video ───────────────────────────────────────────
print("▶ Step 4: Rendering output video (preserving source EN subtitle)...")

from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
from embed_subtitles import _make_subtitle_overlay_chinese, _find_cjk_font
from subtitle_detect import detect_subtitle_boundary

os.makedirs("debug_frames", exist_ok=True)

font_path = _find_cjk_font()
boundary  = detect_subtitle_boundary(VIDEO)
if boundary is None:
    boundary = 0.85
print(f"  Subtitle boundary: {boundary:.3f}")

video = VideoFileClip(VIDEO)
w, h  = video.size
clips = []

for entry in zh_entries:
    if not entry['text'].strip():
        continue
    frame = _make_subtitle_overlay_chinese(w, h, entry['text'], font_path, boundary)
    c = (ImageClip(frame, transparent=True)
         .with_start(entry['start'])
         .with_duration(entry['end'] - entry['start']))
    clips.append(c)

final = CompositeVideoClip([video] + clips)
final.write_videofile(OUTPUT, codec="libx264", audio_codec="aac",
                      preset="medium", logger=None, threads=4)
video.close()
final.close()

if not os.path.exists(OUTPUT) or os.path.getsize(OUTPUT) == 0:
    print("ERROR: Output video not created"); sys.exit(1)

size_mb = os.path.getsize(OUTPUT) / 1024 / 1024
print(f"\n  ✅ Output: {OUTPUT} ({size_mb:.1f} MB)")

# ── Step 5: Extract proof frames ─────────────────────────────────────────
print("\n▶ Step 5: Extracting proof frames...")
for t, name in [(2, "proof_2s"), (5, "proof_5s"), (10, "proof_10s")]:
    path = f"debug_frames/db_sync_{name}.jpg"
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", OUTPUT,
                    "-vframes", "1", path],
                   capture_output=True, timeout=10)
    if os.path.exists(path):
        print(f"  ✅ {path}")

print(f"\n{'='*60}")
print(f"D+B Sync Summary")
print(f"{'='*60}")
print(f"  ASR segments     : {len(asr_segments)}")
print(f"  Aligned segments : {len(en_aligned)} (avg {avg_dur:.1f}s each)")
print(f"  ZH segments      : {len(zh_entries)}")
print(f"  Output           : {OUTPUT}")
print("\n✅ Test complete!")
