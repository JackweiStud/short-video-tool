#!/usr/bin/env python3
"""
Generate full-length bilingual subtitle video with D+B Precision Sync.
This script uses the results from a previous main.py run to create
a complete version of the video with burned bilingual subtitles.

Usage:
    python generate_full_video.py
"""

import logging
import os
import sys
import json
import subprocess
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

def generate_full_video_subtitles(asr_result, config):
    """Generate Chinese SRT for full video using the already extracted ASR."""
    from translator import Translator
    
    zh_srt_path = os.path.join(config.subtitles_dir, "full_video_zh.srt")
    en_srt_path = os.path.join(config.subtitles_dir, "full_video_en.srt")
    
    translator = Translator(config=config)
    
    # 1. Generate English SRT from ASR (Sentence-level)
    def ms_to_srt_ts(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    en_lines = []
    for i, seg in enumerate(asr_result):
        en_lines.append(f"{i+1}")
        en_lines.append(f"{ms_to_srt_ts(seg['start'])} --> {ms_to_srt_ts(seg['end'])}")
        en_lines.append(seg['text'])
        en_lines.append("")
    
    with open(en_srt_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(en_lines))
    logging.info(f"✅ Generated English SRT: {en_srt_path}")

    # 2. Translate to Chinese
    texts = [seg['text'] for seg in asr_result]
    logging.info(f"Translating {len(texts)} segments to Chinese...")
    zh_texts = translator._batch_translate(texts, target_lang='zh')
    
    zh_lines = []
    for i, seg in enumerate(asr_result):
        zh_lines.append(f"{i+1}")
        zh_lines.append(f"{ms_to_srt_ts(seg['start'])} --> {ms_to_srt_ts(seg['end'])}")
        zh_lines.append(zh_texts[i] if i < len(zh_texts) else "...")
        zh_lines.append("")
        
    with open(zh_srt_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(zh_lines))
    
    logging.info(f"✅ Generated Chinese SRT: {zh_srt_path}")
    return en_srt_path, zh_srt_path

def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description="Generate full-length bilingual subtitle video")
    parser.add_argument(
        '--subtitle-status',
        choices=["auto", "en", "zh", "none"],
        default="auto",
        help='Force subtitle strategy (default: auto detection)'
    )
    return parser


def main():
    from config import get_config
    from embed_subtitles import _hard_burn_bilingual_auto

    parser = build_parser()
    args = parser.parse_args()
    
    config = get_config()
    analysis_file = os.path.join(config.analysis_dir, "analysis_result.json")
    
    if not os.path.exists(analysis_file):
        logging.error(f"Analysis file not found: {analysis_file}. Please run main.py first.")
        return 1
    
    with open(analysis_file, 'r', encoding='utf-8') as f:
        analysis_data = json.load(f)
    
    video_path = analysis_data.get('video_path')
    asr_result = analysis_data.get('asr_result', [])
    
    if not video_path or not os.path.exists(video_path):
        logging.error(f"Source video not found: {video_path}")
        return 1
    
    if not asr_result:
        logging.error("No ASR result found in analysis data")
        return 1
        
    # Generate subtitles
    en_srt, zh_srt = generate_full_video_subtitles(asr_result, config)
    
    # Render Output
    video_basename = os.path.splitext(os.path.basename(video_path))[0]
    output_path = f"output/{video_basename}_full_bilingual.mp4"
    
    logging.info("\n" + "="*70)
    logging.info("Generating FULL VIDEO with D+B Precision Subtitles")
    logging.info("="*70)
    logging.info(f"Source  : {os.path.basename(video_path)}")
    logging.info(f"Output  : {output_path}")
    logging.info(f"ASR Size: {len(asr_result)} segments")
    
    # USE THE CORE ENGINE'S D+B LOGIC
    success = _hard_burn_bilingual_auto(
        video_path, en_srt, zh_srt, output_path,
        subtitle_status=args.subtitle_status,
        asr_segments=asr_result
    )
    
    if success:
        file_size = os.path.getsize(output_path) / (1024 * 1024)
        logging.info("\n" + "="*70)
        logging.info(f"✅ Full video generated successfully!")
        logging.info(f"Output: {output_path}")
        logging.info(f"Size  : {file_size:.2f} MB")
        return 0
    else:
        logging.error("\n❌ Failed to generate full video")
        return 1

if __name__ == "__main__":
    sys.exit(main())
