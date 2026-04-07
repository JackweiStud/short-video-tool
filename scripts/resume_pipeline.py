#!/usr/bin/env python3
"""
resume_pipeline.py — 从已有素材继续执行 Pipeline (跳过下载和 ASR)

用法:
    venv/bin/python3 resume_pipeline.py \
        --analysis "output/analysis/<name>_analysis.json" \
        --video    "downloads/<name>.mp4" \
        [--burn-subtitles] [--subtitle-status none|auto|en]

该脚本直接从 Step 3 (裁剪) 开始，依次执行：
  Step 3: Clipper   — 自动裁剪出精彩片段
  Step 4: Translator — 翻译并生成双语字幕
  Step 5: Integrator — 整合输出目录
  Step 6: embed_subtitles — 烧录双语字幕到每个切片
  Step 7: generate_full_video — 生成全长双语视频
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _PROJECT_ROOT / 'logs'
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(_LOG_DIR / 'resume_pipeline.log'), encoding='utf-8'),
    ],
    force=True,
)


def main():
    parser = argparse.ArgumentParser(
        description='Resume pipeline from existing analysis JSON + video file'
    )
    parser.add_argument('--analysis', required=True,
                        help='Path to existing *_analysis.json from output/analysis/')
    parser.add_argument('--video', required=True,
                        help='Path to downloaded video .mp4')
    parser.add_argument('--output', default='output',
                        help='Output directory (default: output/)')
    parser.add_argument('--min-duration', type=int, default=15)
    parser.add_argument('--max-duration', type=int, default=60)
    parser.add_argument('--burn-subtitles', action='store_true', default=True,
                        help='Hard-burn bilingual subtitles (default: on)')
    parser.add_argument('--subtitle-status', choices=['auto', 'en', 'none'],
                        default='none',
                        help='Subtitle strategy (default: none = burn both EN+ZH)')
    args = parser.parse_args()

    # ── Validate inputs ──────────────────────────────────────────────────
    if not os.path.exists(args.analysis):
        logging.error(f"Analysis file not found: {args.analysis}")
        return 1
    if not os.path.exists(args.video):
        logging.error(f"Video file not found: {args.video}")
        return 1

    logging.info("=" * 70)
    logging.info("Resume Pipeline — Starting from Step 3 (Clip)")
    logging.info("=" * 70)
    logging.info(f"Analysis : {args.analysis}")
    logging.info(f"Video    : {args.video}")
    logging.info(f"Output   : {args.output}")

    start_time = datetime.now()

    # ── Load analysis JSON ───────────────────────────────────────────────
    with open(args.analysis, 'r', encoding='utf-8') as f:
        analysis_result = json.load(f)

    # Patch video_path to point to the actual file (may differ from stored path)
    analysis_result['video_path'] = args.video

    # ── Save to analysis_results/analysis_result.json (where tools expect it) ──
    from config import get_config
    config = get_config()
    os.makedirs(config.analysis_dir, exist_ok=True)
    canonical_analysis_path = os.path.join(config.analysis_dir, "analysis_result.json")
    with open(canonical_analysis_path, 'w', encoding='utf-8') as f:
        json.dump(analysis_result, f, ensure_ascii=False, indent=2)
    logging.info(f"✅ Analysis synced → {canonical_analysis_path}")

    # ── Step 3: Clip ─────────────────────────────────────────────────────
    logging.info("\n" + "=" * 70)
    logging.info("Step 3/5: Clipping video...")
    logging.info("=" * 70)

    from clipper import Clipper
    clipper = Clipper(
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        config=config
    )
    clip_result = clipper.clip_video(
        video_path=args.video,
        analysis_result=analysis_result,
        output_dir=config.clips_dir
    )
    if not clip_result or not clip_result.get('clips'):
        logging.error("Clipping failed or no clips generated")
        return 1

    clips_metadata_path = os.path.join(config.clips_dir, "clips_metadata.json")
    logging.info(f"✅ {len(clip_result['clips'])} clips created → {clips_metadata_path}")

    # ── Step 4: Translate ────────────────────────────────────────────────
    logging.info("\n" + "=" * 70)
    logging.info("Step 4/5: Translating & generating subtitles...")
    logging.info("=" * 70)

    from translator import Translator
    translator = Translator(config=config)
    translation_result = translator.translate_clips(
        clips_metadata_path=clips_metadata_path,
        output_dir=config.subtitles_dir
    )
    if not translation_result:
        logging.error("Translation failed")
        return 1

    translations_metadata_path = os.path.join(config.subtitles_dir, "translations_metadata.json")
    logging.info(f"✅ {len(translation_result['clips'])} clips translated → {translations_metadata_path}")

    # ── Step 5: Integrate ────────────────────────────────────────────────
    logging.info("\n" + "=" * 70)
    logging.info("Step 5/5: Integrating outputs...")
    logging.info("=" * 70)

    from integrator import Integrator
    integrator = Integrator(output_dir=args.output, config=config)
    integration_result = integrator.integrate(
        video_path=args.video,
        analysis_result_path=canonical_analysis_path,
        clips_metadata_path=clips_metadata_path,
        translations_metadata_path=translations_metadata_path
    )
    if not integration_result:
        logging.error("Integration failed")
        return 1

    logging.info(f"✅ Integration complete → {args.output}/")

    # ── Step 6: Embed bilingual subtitles into clips ──────────────────────
    logging.info("\n" + "=" * 70)
    logging.info("Step 6: Burning bilingual subtitles into clips...")
    logging.info("=" * 70)

    from embed_subtitles import embed_subtitles_batch
    embed_result = embed_subtitles_batch(
        clips_dir=os.path.join(args.output, "clips"),
        subtitles_dir=os.path.join(args.output, "subtitles"),
        output_dir=os.path.join(args.output, "clips_with_subtitles"),
        burn=args.burn_subtitles,
        subtitle_status=args.subtitle_status,
        asr_segments=analysis_result.get('asr_result'),
        clips_data=translation_result.get('clips', [])
    )
    if embed_result:
        logging.info(
            f"✅ Subtitles burned: {embed_result['successful']}/{embed_result['total_processed']} clips"
        )
    else:
        logging.warning("⚠️  Subtitle embed failed (non-fatal, clips still available)")

    # ── Step 7: Generate full-length bilingual video ──────────────────────
    logging.info("\n" + "=" * 70)
    logging.info("Step 7: Generating full-length bilingual video...")
    logging.info("=" * 70)

    from generate_full_video import generate_full_video_subtitles
    from embed_subtitles import _hard_burn_bilingual_auto

    asr_result = analysis_result.get('asr_result', [])
    if asr_result:
        en_srt, zh_srt = generate_full_video_subtitles(asr_result, config)
        video_basename = os.path.splitext(os.path.basename(args.video))[0]
        full_output_path = os.path.join(args.output, f"{video_basename}_full_bilingual.mp4")

        success = _hard_burn_bilingual_auto(
            args.video, en_srt, zh_srt, full_output_path,
            subtitle_status=args.subtitle_status,
            asr_segments=asr_result
        )
        if success:
            size_mb = os.path.getsize(full_output_path) / (1024 * 1024)
            logging.info(f"✅ Full bilingual video: {full_output_path} ({size_mb:.1f} MB)")
        else:
            logging.warning("⚠️  Full video generation failed (non-fatal)")
    else:
        logging.warning("⚠️  No ASR result in analysis — skipping full video generation")

    # ── Summary ──────────────────────────────────────────────────────────
    total_sec = (datetime.now() - start_time).total_seconds()
    logging.info("\n" + "=" * 70)
    logging.info("🎉 Resume Pipeline Complete!")
    logging.info("=" * 70)
    logging.info(f"Total time : {total_sec:.0f}s")
    logging.info(f"Clips      : {args.output}/clips_with_subtitles/")
    logging.info(f"Full video : {args.output}/<title>_full_bilingual.mp4")
    logging.info(f"Log        : resume_pipeline.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
