#!/usr/bin/env python3
"""
step7_full_video.py — 独立执行 Step 7: 全长双语字幕视频生成

跳过所有前置步骤，直接从已有素材生成带双语字幕的完整视频。

用法:
    venv/bin/python3 step7_full_video.py
    venv/bin/python3 step7_full_video.py --analysis analysis_results/analysis_result.json
    venv/bin/python3 step7_full_video.py --video downloads/xxx.mp4 --output output/xxx_bilingual.mp4
    venv/bin/python3 step7_full_video.py --subtitle-status none   # 烧录双语(默认)
    venv/bin/python3 step7_full_video.py --subtitle-status auto   # D+B 精准同步模式

默认值:
    --analysis  : analysis_results/analysis_result.json
    --output    : output/<video_basename>_full_bilingual.mp4
    --subtitle-status: none  (burn both EN+ZH)
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from config import get_config
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _PROJECT_ROOT / 'logs'
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(_LOG_DIR / 'step7_full_video.log'), encoding='utf-8'),
    ],
    force=True,
)


def main():
    parser = argparse.ArgumentParser(
        description='Step 7 standalone: Generate full-length bilingual video',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--analysis', type=str, default="analysis_results/analysis_result.json",
                        help='Path to analysis_result.json (default: analysis_results/analysis_result.json)')
    parser.add_argument('--video', type=str,
                        help='Override video path (default: read from analysis JSON)')
    parser.add_argument('--output', type=str,
                        help='Output file path (default: output/<basename>_full_bilingual.mp4)')
    parser.add_argument('--subtitle-status', type=str, choices=['auto', 'en', 'none'], default='auto',
                        help='Subtitle burn strategy: none=burn both EN+ZH, auto=D+B detection, en=ZH only (default: auto)')

    args = parser.parse_args()

    # --- Load Config ---
    config = get_config()

    # --- Resolve Paths ---
    # Project root is two levels up from scripts/step7_full_video.py
    project_root = Path(__file__).resolve().parent.parent
    logging.info(f"DEBUG: project_root = {project_root}")

    # analysis_path
    analysis_path = project_root / args.analysis
    if not analysis_path.exists():
        logging.error(f"Analysis file not found: {analysis_path}")
        sys.exit(1)

    # Load analysis result
    with open(analysis_path, 'r', encoding='utf-8') as f:
        analysis_result = json.load(f)

    # video_path (override from args or read from analysis_result)
    video_arg_path = Path(args.video) if args.video else None
    analysis_video_path = Path(analysis_result.get("video_path")) if analysis_result.get("video_path") else None

    logging.info(f"DEBUG: video_arg_path = {video_arg_path}")
    logging.info(f"DEBUG: analysis_video_path = {analysis_video_path}")

    if video_arg_path and not video_arg_path.is_absolute():
        video_path = project_root / video_arg_path
    elif video_arg_path: # Absolute path from argument
        video_path = video_arg_path
    elif analysis_video_path and not analysis_video_path.is_absolute():
        video_path = project_root / analysis_video_path
    elif analysis_video_path: # Absolute path from analysis
        video_path = analysis_video_path
    else:
        logging.error("No video path provided in arguments or analysis result.")
        sys.exit(1)
    logging.info(f"DEBUG: resolved video_path = {video_path}")
    if not video_path.exists():
        logging.error(f"Video file not found: {video_path}")
        sys.exit(1)

    # output_path
    output_basename = Path(analysis_result.get("video_path")).stem + "_full_bilingual.mp4"
    output_path = project_root / (args.output or (Path("output") / output_basename))
    output_path.parent.mkdir(parents=True, exist_ok=True) # Ensure output directory exists

    # Subtitle burn strategy
    subtitle_burn_strategy = args.subtitle_status

    logging.info("======================================================================")
    logging.info("Generating Full-Length Bilingual Subtitle Video (Auto Strategy)")
    logging.info("======================================================================")
    logging.info(f"Source: {video_path.name}")
    logging.info(f"Output: {output_path}")
    logging.info(f"Strategy: Auto-detect subtitle status and apply appropriate rendering")

    # Generate bilingual srt from analysis_result (always generate, might be needed for embedding)
    zh_srt_path, en_srt_path = generate_bilingual_srt_from_analysis(analysis_result, project_root / "subtitles")

    # Embed bilingual subtitles
    if subtitle_burn_strategy == 'none':
        # Default strategy: hard burn both Chinese (above English) and English (source)
        # Assumes English is already in the source video
        # Mask source English subtitles, then hard burn both
        # 1. Mask source video (if English subtitles detected)
        from subtitle_detect import detect_subtitle_status
        status, _ = detect_subtitle_status(str(video_path))
        if status == 'en':
            # Create a masked version of the video for burning our own subtitles
            masked_video_path = project_root / "output" / (video_path.stem + "_masked.mp4")
            # For simplicity, we just use the original video for now, assuming the overlay
            # logic handles positioning to avoid overlap.
            # A proper masking step would involve ffmpeg filters here.
            final_source_video = video_path
            logging.info(f"  Source English subtitles detected. Overlaying Chinese subtitles above them.")
            from embed_subtitles import _hard_burn_overlay_chinese
            if not _hard_burn_overlay_chinese(str(final_source_video), str(zh_srt_path), str(output_path)):
                logging.error(f"Failed to hard burn overlaid Chinese subtitle to {output_path}")
                sys.exit(1)
        else:
            # No source English subtitles detected, burn both Chinese and English
            from embed_subtitles import _hard_burn_both_subtitles
            if not _hard_burn_both_subtitles(str(video_path), str(zh_srt_path), str(en_srt_path), str(output_path)):
                logging.error(f"Failed to hard burn both Chinese and English subtitles to {output_path}")
                sys.exit(1)
    elif subtitle_burn_strategy == 'auto':
        from subtitle_detect import detect_subtitle_status
        status, confidence = detect_subtitle_status(str(video_path))
        logging.info(f"Auto-detected subtitle status: {status} (confidence: {confidence:.2f})")

        if status == 'en':
            # English subtitles detected, overlay Chinese only
            logging.info(f"  Source English subtitles detected. Overlaying Chinese subtitles above them.")
            from embed_subtitles import _hard_burn_overlay_chinese
            if not _hard_burn_overlay_chinese(str(video_path), str(zh_srt_path), str(output_path)):
                logging.error(f"Failed to hard burn overlaid Chinese subtitle to {output_path}")
                sys.exit(1)
        else:
            # No English subtitles detected, burn both Chinese and English
            logging.info(f"  No source English subtitles detected. Burning both Chinese and English.")
            from embed_subtitles import _hard_burn_both_subtitles
            if not _hard_burn_both_subtitles(str(video_path), str(zh_srt_path), str(en_srt_path), str(output_path)):
                logging.error(f"Failed to hard burn both Chinese and English subtitles to {output_path}")
                sys.exit(1)
    elif subtitle_burn_strategy == 'en':
        # Only overlay Chinese, assuming English is already present in source
        logging.info(f"  Forcing 'en' strategy: Overlaying Chinese subtitles, assuming source has English.")
        from embed_subtitles import _hard_burn_overlay_chinese
        if not _hard_burn_overlay_chinese(str(video_path), str(zh_srt_path), str(output_path)):
            logging.error(f"Failed to hard burn overlaid Chinese subtitle to {output_path}")
            sys.exit(1)
    
    else:
        logging.error(f"Unsupported subtitle burn strategy: {subtitle_burn_strategy}")
        sys.exit(1)


    logging.info("")
    logging.info("✅ Full video generated successfully!")
    logging.info("======================================================================")
    logging.info(f"Output: {output_path}")
    logging.info(f"Size: {output_path.stat().st_size / (1024*1024):.2f} MB")
