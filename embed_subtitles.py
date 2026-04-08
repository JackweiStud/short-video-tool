#!/usr/bin/env python3
"""
Embed Subtitles into Video Clips

Supports two modes:
  - Soft embed (default): mux SRT as a subtitle track (fast, no re-encoding)
  - Hard burn (--burn):   burn bilingual subtitles directly into the video frames

Usage:
    python embed_subtitles.py
    python embed_subtitles.py --burn
    python embed_subtitles.py --input output/clips --output output/clips_with_subtitles --burn
"""

import argparse
import copy
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
import json

from typing import Any, Dict, Optional
import shutil

from config import Config, get_config # Import Config and get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _build_burn_decision(
    requested_status: str,
    effective_status: str,
    confidence: Optional[float],
    ocr_lang: Optional[str],
    detected_boundary: Optional[float],
    chosen_burn_mode: str,
    used_zh_aligned: bool,
    subtitle_alignment_source: Optional[str] = None,
    burn_renderer: Optional[str] = None,
    auto_final_action: Optional[str] = None,
    hard_subtitle_mask: Optional[Dict[str, Any]] = None,
    subtitle_burn_policy: Optional[Dict[str, str]] = None,
    subtitle_burn_policy_summary: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "requested_subtitle_status": requested_status,
        "effective_subtitle_status": effective_status,
        "detected_status": effective_status if requested_status == "auto" else None,
        "confidence": confidence,
        "ocr_lang": ocr_lang,
        "detected_boundary": detected_boundary,
        "chosen_burn_mode": chosen_burn_mode,
        "used_zh_aligned": used_zh_aligned,
        "subtitle_alignment_source": subtitle_alignment_source,
        "burn_renderer": burn_renderer,
        "auto_final_action": auto_final_action,
        "hard_subtitle_mask": hard_subtitle_mask,
        "subtitle_burn_policy": subtitle_burn_policy,
        "subtitle_burn_policy_summary": subtitle_burn_policy_summary,
    }


def _build_visual_synced_subtitle_track(
    video_path: str,
    asr_segments: list,
    target_lang: str,
    output_srt_path: str,
) -> Optional[str]:
    """
    Build a subtitle track whose timestamps follow the original hard subtitle
    visual rhythm instead of the default ASR split rhythm.

    Returns the generated SRT path on success, otherwise None.
    """
    if not asr_segments:
        return None


def _estimate_hard_subtitle_mask(
    *,
    en_srt_path: str,
    zh_srt_path: str,
    video_width: int,
    video_height: int,
    is_vertical_video: bool,
    subtitle_boundary: float,
    config: Config,
) -> Dict[str, int]:
    """
    Estimate the bottom subtitle block to hide with a solid rectangle.

    `subtitle_boundary` is the detected upper boundary of the original hard subtitle
    area, normalized from the top.

    Width/height are estimated from the generated dual subtitle tracks so the
    mask only covers the actual subtitle card instead of the full bottom band.
    """
    from PIL import Image, ImageDraw, ImageFont

    base_h = 1920 if is_vertical_video else 1080
    padding_cfg = (
        config.subtitle_hard_mask_top_padding_vertical
        if is_vertical_video
        else config.subtitle_hard_mask_top_padding_horizontal
    )
    padding_px = max(8, round(padding_cfg * (video_height / base_h)))

    layout = _compute_subtitle_layout(video_height, is_vertical_video, config)
    en_fontsize = layout["en_fontsize"]
    zh_fontsize = layout["zh_fontsize"]
    inter_gap = layout["inter_gap"]

    canvas = Image.new("RGB", (video_width, video_height), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def _load_font(font_name: str, size: int, prefer_cjk: bool = False):
        candidates = []
        if prefer_cjk:
            cjk_font = _find_cjk_font(config)
            if cjk_font:
                candidates.append(cjk_font)
        if font_name:
            candidates.append(font_name)
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    en_font = _load_font(config.font_name_en, en_fontsize)
    zh_font = _load_font(config.font_name_zh, zh_fontsize, prefer_cjk=True)

    candidate_widths = []
    candidate_heights = []
    max_width_limit = int(video_width * 0.85)

    en_entries = _parse_srt(en_srt_path) if os.path.exists(en_srt_path) else []
    zh_entries = _parse_srt(zh_srt_path) if os.path.exists(zh_srt_path) else []
    max_len = max(len(en_entries), len(zh_entries), 1)

    for idx in range(max_len):
        en_text = en_entries[idx]["text"].strip() if idx < len(en_entries) else ""
        zh_text = zh_entries[idx]["text"].strip() if idx < len(zh_entries) else ""

        en_block_width = 0
        en_block_height = 0
        if en_text:
            wrapped_en = _wrap_text_by_pixel(en_text, en_font, draw, max_width_limit)
            en_lines = wrapped_en.split("\n")
            en_line_heights = []
            for line in en_lines:
                bbox = draw.textbbox((0, 0), line, font=en_font)
                en_block_width = max(en_block_width, bbox[2] - bbox[0])
                en_line_heights.append(bbox[3] - bbox[1])
            en_spacing = int(en_fontsize * 0.2)
            en_block_height = sum(en_line_heights) + en_spacing * max(len(en_lines) - 1, 0)

        zh_block_width = 0
        zh_block_height = 0
        if zh_text:
            wrapped_zh = _wrap_text_by_pixel(zh_text, zh_font, draw, max_width_limit)
            zh_lines = wrapped_zh.split("\n")
            zh_line_heights = []
            for line in zh_lines:
                bbox = draw.textbbox((0, 0), line, font=zh_font)
                zh_block_width = max(zh_block_width, bbox[2] - bbox[0])
                zh_line_heights.append(bbox[3] - bbox[1])
            zh_spacing = int(zh_fontsize * 0.2)
            zh_block_height = sum(zh_line_heights) + zh_spacing * max(len(zh_lines) - 1, 0)

        combined_width = max(en_block_width, zh_block_width)
        combined_height = en_block_height + zh_block_height + (
            inter_gap if en_block_height and zh_block_height else 0
        )
        if combined_width > 0:
            candidate_widths.append(combined_width)
        if combined_height > 0:
            candidate_heights.append(combined_height)

    def _pick_percentile(values, q: float, fallback: int) -> int:
        if not values:
            return fallback
        ordered = sorted(values)
        idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
        return ordered[idx]

    horizontal_padding = max(16, round(video_width * (0.04 if is_vertical_video else 0.03)))
    vertical_padding = max(10, round(video_height * (0.014 if is_vertical_video else 0.012)))
    content_width = _pick_percentile(candidate_widths, 0.68, int(video_width * 0.46))
    mask_width = min(
        int(video_width * (0.86 if is_vertical_video else 0.78)),
        max(
            int(video_width * (0.40 if is_vertical_video else 0.30)),
            content_width + horizontal_padding * 2,
        ),
    )
    x = max(0, (video_width - mask_width) // 2)

    content_height = _pick_percentile(
        candidate_heights,
        0.68,
        round(en_fontsize * 1.35 + zh_fontsize * 1.35 + inter_gap),
    )
    if content_height <= 0:
        content_height = round(video_height * 0.14)
    mask_height = min(
        round(video_height * (0.16 if is_vertical_video else 0.14)),
        max(round(video_height * (0.08 if is_vertical_video else 0.07)), content_height + vertical_padding * 2),
    )

    shortest_side = max(1, min(mask_width, mask_height))
    radius_scale = 0.42 if is_vertical_video else 0.38
    radius_px = max(14, round(shortest_side * radius_scale))
    feather_px = max(2, round(radius_px * 0.18))

    y = max(0, int(subtitle_boundary * video_height) - padding_px)
    y = min(y, video_height - mask_height - max(4, round(video_height * 0.006)))

    return {
        "x": x,
        "y": y,
        "w": mask_width,
        "h": mask_height,
        "radius_px": radius_px,
        "feather_px": feather_px,
        "padding_px": padding_px,
    }


def _resolve_hard_burn_mode(subtitle_status: str, config: Config) -> str:
    status = str(subtitle_status).strip().lower()
    if status == "en":
        return str(config.subtitle_hard_burn_mode_en).strip().lower()
    if status == "zh":
        return str(config.subtitle_hard_burn_mode_zh).strip().lower()
    if status == "bilingual":
        return str(config.subtitle_hard_burn_mode_bilingual).strip().lower()
    return "replace" if status in {"en", "bilingual"} else "skip"


def _build_hard_subtitle_policy(config: Config) -> Dict[str, str]:
    return {
        "en": str(config.subtitle_hard_burn_mode_en).strip().lower(),
        "zh": str(config.subtitle_hard_burn_mode_zh).strip().lower(),
        "bilingual": str(config.subtitle_hard_burn_mode_bilingual).strip().lower(),
    }


def _format_hard_subtitle_policy(policy: Dict[str, str]) -> str:
    return (
        f"EN={policy.get('en', 'replace')} / "
        f"ZH={policy.get('zh', 'skip')} / "
        f"BILINGUAL={policy.get('bilingual', 'replace')}"
    )


def _compute_mask_text_positions(
    *,
    mask_x: int,
    mask_y: int,
    mask_w: int,
    mask_h: int,
    video_h: int,
    en_fontsize: int,
    zh_fontsize: int,
    inter_gap: int,
) -> Dict[str, int]:
    """
    Compute absolute subtitle anchor positions inside the hard-subtitle mask card.

    The returned y coordinates are ASS \\an2 bottom-center anchors, which is also
    close enough for the moviepy fallback text layout.
    """
    center_x = mask_x + mask_w // 2
    inner_pad_y = max(8, round(min(mask_h * 0.12, video_h * 0.012)))
    zh_bottom = mask_y + mask_h - inner_pad_y
    en_bottom = zh_bottom - zh_fontsize - inter_gap

    min_en_bottom = mask_y + inner_pad_y + en_fontsize
    if en_bottom < min_en_bottom:
        en_bottom = min_en_bottom

    return {
        "center_x": center_x,
        "en_bottom": en_bottom,
        "zh_bottom": zh_bottom,
        "inner_pad_y": inner_pad_y,
    }

    try:
        from subtitle_sync import SubtitleSync
        from translator import Translator

        logging.info(
            "[Hard-sub main path] Building visual-synced %s subtitle track via SubtitleSync...",
            target_lang,
        )
        syncer = SubtitleSync(sample_fps=5.0)
        aligned_segments = syncer.get_aligned_segments(video_path, asr_segments)
        if not aligned_segments:
            logging.warning("[Hard-sub main path] SubtitleSync returned no aligned segments.")
            return None

        translator = Translator(config=get_config())
        translated_segments = translator._translate_segments(aligned_segments, target_lang=target_lang)
        translator._generate_srt(translated_segments, output_srt_path)
        logging.info(
            "[Hard-sub main path] Generated visual-synced %s subtitle track: %s (%d segments)",
            target_lang,
            os.path.basename(output_srt_path),
            len(translated_segments),
        )
        return output_srt_path
    except Exception as e:
        logging.warning(
            "[Hard-sub main path] Failed to build visual-synced %s subtitle track: %s",
            target_lang,
            e,
        )
        return None

def _get_video_dimensions(video_path: str) -> Optional[Dict]:
    """
    使用 ffprobe 获取视频的宽度和高度。
    """
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height',
            '-of', 'json',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if 'streams' in data and len(data['streams']) > 0:
            stream = data['streams'][0]
            return {'width': stream['width'], 'height': stream['height']}
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        logging.warning(f"无法获取视频尺寸 {video_path}: {e}")
    return None

# ──────────────────────────────────────────────
# SRT Parsing Helpers
# ──────────────────────────────────────────────

def _parse_srt(srt_path: str) -> list:
    """
    Parse an SRT file into a list of subtitle entries.

    Returns:
        list of dict: [{"start": float, "end": float, "text": str}, ...]
    """
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    blocks = content.strip().split('\n\n')
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        ts_match = re.match(
            r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})',
            lines[1]
        )
        if not ts_match:
            continue
        g = ts_match.groups()
        start_s = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
        end_s = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
        text = ' '.join(lines[2:]).strip()
        entries.append({"start": start_s, "end": end_s, "text": text})
    return entries


def _escape_drawtext(text: str) -> str:
    """Escape text for FFmpeg drawtext filter."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "'\\''")
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    return text


def _parse_ffmpeg_color_to_rgba(color: str) -> tuple[int, int, int, int]:
    """Parse a small subset of ffmpeg color strings into RGBA."""
    text = str(color or "").strip().lower()
    alpha = 255

    if "@" in text:
        base, alpha_text = text.split("@", 1)
        text = base.strip()
        try:
            alpha_value = float(alpha_text.strip())
            alpha = int(round(alpha_value * 255)) if alpha_value <= 1 else int(round(alpha_value))
        except Exception:
            alpha = 255

    named_colors = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "red": (255, 0, 0),
        "green": (0, 255, 0),
        "blue": (0, 0, 255),
        "yellow": (255, 255, 0),
    }
    if text in named_colors:
        r, g, b = named_colors[text]
    elif text.startswith("#") and len(text) in {4, 7}:
        hex_text = text[1:]
        if len(hex_text) == 3:
            r, g, b = (int(ch * 2, 16) for ch in hex_text)
        else:
            r = int(hex_text[0:2], 16)
            g = int(hex_text[2:4], 16)
            b = int(hex_text[4:6], 16)
    elif text.startswith("0x") and len(text) == 8:
        hex_text = text[2:]
        r = int(hex_text[0:2], 16)
        g = int(hex_text[2:4], 16)
        b = int(hex_text[4:6], 16)
    else:
        r, g, b = (0, 0, 0)

    return r, g, b, max(0, min(255, alpha))


def _build_hard_subtitle_mask_overlay_image(
    video_width: int,
    video_height: int,
    hard_subtitle_mask: Dict[str, Any],
    color: str,
) -> "Image.Image":
    """Build a rounded, softly feathered overlay image for hard-subtitle masking."""
    from PIL import Image, ImageDraw, ImageFilter

    mask_x = int(hard_subtitle_mask["x"])
    mask_y = int(hard_subtitle_mask["y"])
    mask_w = int(hard_subtitle_mask["w"])
    mask_h = int(hard_subtitle_mask["h"])
    radius_px = int(hard_subtitle_mask.get("radius_px", 0))
    feather_px = int(hard_subtitle_mask.get("feather_px", 0))

    radius_px = max(4, min(radius_px, max(4, min(mask_w, mask_h) // 2 - 1)))
    feather_px = max(0, feather_px)

    overlay = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    alpha_mask = Image.new("L", (mask_w, mask_h), 0)
    draw = ImageDraw.Draw(alpha_mask)
    draw.rounded_rectangle(
        (0, 0, mask_w - 1, mask_h - 1),
        radius=radius_px,
        fill=255,
    )
    if feather_px > 0:
        alpha_mask = alpha_mask.filter(ImageFilter.GaussianBlur(radius=feather_px))

    fill = Image.new("RGBA", (mask_w, mask_h), _parse_ffmpeg_color_to_rgba(color))
    overlay.paste(fill, (mask_x, mask_y), alpha_mask)
    return overlay


# ──────────────────────────────────────────────
# Strategy 1: Soft Embed (subtitle track)
# ──────────────────────────────────────────────

def _try_soft_embed(video_path: str, subtitle_path: str, output_path: str) -> bool:
    """Mux SRT as a subtitle track into the MP4 container."""
    try:
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-i", subtitle_path,
            "-c:v", "copy",
            "-c:a", "copy",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=chi",
            "-y",
            output_path
        ]
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=120
        )
        if result.returncode != 0:
            logging.debug(f"Soft embed failed: {result.stderr[-200:]}")
            return False
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False
        logging.info("  (soft subtitle track embedded)")
        return True
    except Exception as e:
        logging.debug(f"Soft embed exception: {e}")
        return False


# ──────────────────────────────────────────────
# Strategy 2: Hard Burn – bilingual (moviepy + Pillow)
# Works without libfreetype/libass in ffmpeg
# ──────────────────────────────────────────────

def _find_cjk_font(config: Config):
    """Find a CJK-capable font on macOS for Chinese rendering."""
    # Map known libass/CoreText family names to their actual file paths.
    # Family names (non-path strings) are NOT resolvable via os.path.exists;
    # we must map them explicitly to avoid silently falling through to PingFang.
    _family_to_path = {
        "Heiti SC": "/System/Library/Fonts/STHeiti Medium.ttc",
        "Heiti TC": "/System/Library/Fonts/STHeiti Medium.ttc",
        "STHeiti": "/System/Library/Fonts/STHeiti Medium.ttc",
        "PingFang SC": "/System/Library/Fonts/PingFang.ttc",
        "PingFang TC": "/System/Library/Fonts/PingFang.ttc",
        "PingFang HK": "/System/Library/Fonts/PingFang.ttc",
    }
    configured = config.font_name_zh
    # If configured value is a family name, resolve to path first
    if configured and not os.path.sep in configured:
        resolved = _family_to_path.get(configured)
        if resolved and os.path.exists(resolved):
            return resolved
    # If it looks like a path, check directly
    elif configured and os.path.exists(configured):
        return configured
    # Fallback candidates (paths only)
    fallback_paths = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for f in fallback_paths:
        if os.path.exists(f):
            return f
    return None


def _wrap_text_by_pixel(text: str, font, draw, max_width: int) -> str:
    """Wrap text so that it doesn't exceed max_width when rendered."""
    if not text:
        return ""
    
    # Try to determine if it's mostly ascii (space-separated) or CJK
    is_ascii = sum(1 for c in text[:20] if ord(c) < 128) > len(text[:20]) * 0.8
    words = text.split(' ') if is_ascii else list(text)
    sep = ' ' if is_ascii else ''
    
    lines = []
    curr_line = ""
    for w in words:
        test_line = curr_line + sep + w if curr_line else w
        bbox = draw.textbbox((0, 0), test_line, font=font)
        pw = bbox[2] - bbox[0]
        if pw <= max_width:
            curr_line = test_line
        else:
            if curr_line:
                lines.append(curr_line)
                curr_line = w
            else:
                lines.append(w)
                curr_line = ""
    if curr_line:
        lines.append(curr_line)
    return "\n".join(lines)


def _compute_subtitle_layout(
    video_height: int,
    is_vertical: bool,
    config: Config,
    hard_subtitle_lang: Optional[str] = None,
    subtitle_boundary: float = 0.80,
) -> dict:
    """
    Single source of truth for subtitle font sizes and margins.

    All values are in video pixel coordinates (same space as PlayResY = video_height).
    Config stores baseline values calibrated at:
      - vertical:   1920px height
      - horizontal: 1080px height

    Returns dict with keys:
      en_fontsize, zh_fontsize,
      margin_v_zh,   # distance from bottom edge to ZH subtitle bottom
      margin_v_en,   # distance from bottom edge to EN subtitle bottom (always > ZH)
      inter_gap      # pixel gap between EN bottom and ZH top (informational)

    Scenarios handled:
      A) Bilingual, no source hard sub  → EN above ZH, both near bottom
      B) Source has EN hard sub         → ZH only, pushed above existing EN hard sub
      C) Source has ZH hard sub         → EN only, pushed above existing ZH hard sub
    """
    if is_vertical:
        base_h = 1920
        cfg_en_fs  = config.font_size_en_vertical
        cfg_zh_fs  = config.font_size_zh_vertical
        cfg_zh_mv  = config.margin_v_zh_vertical
        cfg_en_mv  = config.margin_v_en_vertical   # only used in has_en_hard_sub path
    else:
        base_h = 1080
        cfg_en_fs  = config.font_size_en_horizontal
        cfg_zh_fs  = config.font_size_zh_horizontal
        cfg_zh_mv  = config.margin_v_zh_horizontal
        cfg_en_mv  = config.margin_v_en_horizontal

    scale = video_height / base_h

    en_fontsize = max(1, round(cfg_en_fs * scale))
    zh_fontsize = max(1, round(cfg_zh_fs * scale))

    if hard_subtitle_lang in {"en", "zh"}:
        # A single soft subtitle track is pushed above the existing hard subtitle.
        # margin_v_zh is in ASS MarginV space (distance from bottom).
        # Hard subtitle top ≈ subtitle_boundary * video_height from top
        #   → from bottom = (1 - subtitle_boundary) * video_height
        # Keep a small safety gap above the detected hard subtitle, but do not
        # overcompensate. The previous fixed 75px baseline offset pushed the
        # overlay too far upward on 1080p material.
        gap_px = max(3, round(zh_fontsize * 0.08))
        ass_render_offset = max(10, round(zh_fontsize * 0.30))
        margin_v_zh = int((1.0 - subtitle_boundary) * video_height) + ass_render_offset + gap_px
        margin_v_en = margin_v_zh
        inter_gap   = gap_px
    else:
        # Bilingual: ZH sits at bottom, EN floats above ZH.
        # margin_v_en = margin_v_zh + zh_fontsize (one line) + inter_gap
        # This guarantees EN bottom is always above ZH top.
        inter_gap   = max(4, round(8 * scale))
        margin_v_zh = max(1, round(cfg_zh_mv * scale))
        margin_v_en = margin_v_zh + zh_fontsize + inter_gap

    return {
        "en_fontsize": en_fontsize,
        "zh_fontsize": zh_fontsize,
        "margin_v_zh": margin_v_zh,
        "margin_v_en": margin_v_en,
        "inter_gap":   inter_gap,
    }


def _make_subtitle_frame(
    width,
    height,
    en_text,
    zh_text,
    font_path,
    config: Config,
    is_vertical_video: bool,
    hard_subtitle_mask: Optional[Dict[str, Any]] = None,
):
    """Render bilingual subtitle text onto a transparent RGBA image."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    layout = _compute_subtitle_layout(height, is_vertical_video, config)
    en_fontsize     = layout["en_fontsize"]
    zh_fontsize     = layout["zh_fontsize"]
    margin_bottom_zh = layout["margin_v_zh"]
    margin_bottom_en = layout["margin_v_en"]
    inter_gap = layout["inter_gap"]

    try:
        en_font = ImageFont.truetype(config.font_name_en, en_fontsize) if config.font_name_en else ImageFont.load_default()
        zh_font = ImageFont.truetype(config.font_name_zh, zh_fontsize) if config.font_name_zh else ImageFont.load_default()
    except Exception:
        en_font = ImageFont.load_default()
        zh_font = ImageFont.load_default()

    mask_positions = None
    if hard_subtitle_mask:
        mask_positions = _compute_mask_text_positions(
            mask_x=int(hard_subtitle_mask["x"]),
            mask_y=int(hard_subtitle_mask["y"]),
            mask_w=int(hard_subtitle_mask["w"]),
            mask_h=int(hard_subtitle_mask["h"]),
            video_h=height,
            en_fontsize=en_fontsize,
            zh_fontsize=zh_fontsize,
            inter_gap=inter_gap,
        )

    # Draw Chinese line (bottom, yellow)
    if zh_text:
        wrapped_zh = _wrap_text_by_pixel(zh_text, zh_font, draw, int(width * 0.85))
        zh_lines = wrapped_zh.split('\n')
        zh_line_heights = [draw.textbbox((0, 0), line, font=zh_font)[3] - draw.textbbox((0, 0), line, font=zh_font)[1] for line in zh_lines]
        zh_line_spacing = int(zh_fontsize * 0.2)
        total_zh_height = sum(zh_line_heights) + zh_line_spacing * (len(zh_lines) - 1)

        if mask_positions:
            y = mask_positions["zh_bottom"] - total_zh_height
        else:
            y = height - margin_bottom_zh - total_zh_height
        zh_top = y
        
        for i, line in enumerate(zh_lines):
            bbox = draw.textbbox((0, 0), line, font=zh_font)
            tw = bbox[2] - bbox[0]
            x = (width - tw) // 2
            # Black outline
            for dx in [-2, -1, 0, 1, 2]:
                for dy in [-2, -1, 0, 1, 2]:
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, font=zh_font, fill=(0, 0, 0, 200))
            draw.text((x, y), line, font=zh_font, fill=(255, 255, 0, 255))
            y += zh_line_heights[i] + zh_line_spacing
    else:
        zh_top = height - margin_bottom_zh

    # Draw English line (above Chinese, white)
    if en_text:
        wrapped_en = _wrap_text_by_pixel(en_text, en_font, draw, int(width * 0.85))
        en_lines = wrapped_en.split('\n')
        en_line_heights = [draw.textbbox((0, 0), line, font=en_font)[3] - draw.textbbox((0, 0), line, font=en_font)[1] for line in en_lines]
        en_line_spacing = int(en_fontsize * 0.2)
        total_en_height = sum(en_line_heights) + en_line_spacing * (len(en_lines) - 1)

        if mask_positions:
            y = mask_positions["en_bottom"] - total_en_height
        else:
            y = zh_top - total_en_height - margin_bottom_en # Use en margin for gap
        for i, line in enumerate(en_lines):
            bbox = draw.textbbox((0, 0), line, font=en_font)
            tw = bbox[2] - bbox[0]
            x = (width - tw) // 2
            for dx in [-2, -1, 0, 1, 2]:
                for dy in [-2, -1, 0, 1, 2]:
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, font=en_font, fill=(0, 0, 0, 200))
            draw.text((x, y), line, font=en_font, fill=(255, 255, 255, 255))
            y += en_line_heights[i] + en_line_spacing

    import numpy as np
    return np.array(img)


def _hard_burn_bilingual_auto(video_path: str, en_srt_path: str, zh_srt_path: str,
                               output_path: str, subtitle_status: str = "auto",
                               asr_segments: list = None,
                               zh_aligned_srt_path: str = None,
                               decision_meta: Optional[Dict[str, Any]] = None) -> bool:
    """
    Hard-burn bilingual subtitles with automatic strategy selection.
    
    Strategies:
    - "none": Source has no subtitle → render dual (EN + ZH)
    - "en" / "zh" / "bilingual": Source has hard subtitles → mask original subtitle band, then render unified dual subtitles
    - "auto": Auto-detect and decide
    
    Args:
        video_path: Source video path
        en_srt_path: English subtitle path
        zh_srt_path: Chinese subtitle path
        output_path: Output video path
        subtitle_status: "none" | "en" | "zh" | "auto"
    
    Returns:
        True if successful
    """
    config = get_config()
    auto_en_confidence_threshold = config.subtitle_auto_en_confidence_threshold
    auto_hard_ocr_sample_count = config.subtitle_auto_hard_ocr_sample_count
    hard_boundary_fallback = config.subtitle_hard_boundary_fallback
    requested_subtitle_status = subtitle_status
    # Auto-detect if needed
    ocr_lang = None
    detected_boundary = None  # dynamically detected subtitle_top_ratio from OCR
    confidence = None
    if subtitle_status == "auto":
        from subtitle_detect import detect_subtitle_status
        result = detect_subtitle_status(video_path, sample_count=auto_hard_ocr_sample_count)
        # detect_subtitle_status returns (status, confidence, ocr_lang, subtitle_top_ratio)
        if len(result) == 4:
            status, confidence, ocr_lang, detected_boundary = result
        elif len(result) == 3:
            status, confidence, ocr_lang = result
        else:
            status, confidence = result
        logging.info(f"Auto-detected subtitle status: {status} (confidence: {confidence:.2f}), ocr_lang={ocr_lang}, boundary={detected_boundary}")
        subtitle_status = status.value if hasattr(status, "value") else str(status)
        if subtitle_status == "en" and confidence < auto_en_confidence_threshold:
            logging.warning(
                "Auto-detected EN hard subtitle confidence %.2f < %.2f, using safe dual-subtitle mode instead.",
                confidence,
                auto_en_confidence_threshold,
            )
            subtitle_status = "none"
            ocr_lang = None
            detected_boundary = None
    elif subtitle_status == "en":
        ocr_lang = "en"  # Explicitly told source has EN subtitle
    elif subtitle_status == "zh":
        ocr_lang = "zh"
    elif hasattr(subtitle_status, "value"):
        subtitle_status = subtitle_status.value

    used_zh_aligned = False
    hard_sub_mask_meta: Optional[Dict[str, Any]] = None
    hard_sub_policy = _build_hard_subtitle_policy(config)
    hard_sub_policy_summary = _format_hard_subtitle_policy(hard_sub_policy)
    hard_burn_mode = _resolve_hard_burn_mode(subtitle_status, config)
    needs_hard_sub_mask = hard_burn_mode == "replace" and subtitle_status in {"en", "zh", "bilingual"}
    if needs_hard_sub_mask:
        video_dimensions = _get_video_dimensions(video_path)
        is_vertical_video = False
        video_height = 1080
        if video_dimensions:
            video_height = video_dimensions["height"]
            is_vertical_video = video_dimensions["height"] > video_dimensions["width"]
        boundary = detected_boundary if detected_boundary is not None else hard_boundary_fallback
        mask_region = _estimate_hard_subtitle_mask(
            en_srt_path=en_srt_path,
            zh_srt_path=zh_srt_path,
            video_width=video_dimensions["width"] if video_dimensions else 1920,
            video_height=video_height,
            is_vertical_video=is_vertical_video,
            subtitle_boundary=boundary,
            config=config,
        )
        hard_sub_mask_meta = {
            "enabled": True,
            "boundary": boundary,
            "x": mask_region["x"],
            "y": mask_region["y"],
            "w": mask_region["w"],
            "h": mask_region["h"],
            "padding_px": mask_region["padding_px"],
            "color": config.subtitle_hard_mask_color,
        }

    # Route to appropriate strategy
    if subtitle_status == "none":
        if decision_meta is not None:
            decision_meta.update(_build_burn_decision(
                requested_status=requested_subtitle_status,
                effective_status="none",
                confidence=confidence,
                ocr_lang=ocr_lang,
                detected_boundary=detected_boundary,
                chosen_burn_mode="dual_bottom_ffmpeg_or_moviepy",
                used_zh_aligned=used_zh_aligned,
                subtitle_alignment_source=None,
                burn_renderer="ffmpeg",
                auto_final_action="burn_dual_subtitles",
                hard_subtitle_mask=None,
                subtitle_burn_policy=hard_sub_policy,
                subtitle_burn_policy_summary=hard_sub_policy_summary,
            ))
        # No source subtitle → render dual (EN + ZH) at bottom
        if not _hard_burn_bilingual_ffmpeg(video_path, en_srt_path, zh_srt_path, output_path, ocr_lang=None):
            return _hard_burn_bilingual(video_path, en_srt_path, zh_srt_path, output_path)
        return True
    elif hard_burn_mode == "skip":
        logging.info(
            "Source has %s hard subtitle(s): skipping burn and keeping source video as-is.",
            subtitle_status.upper(),
        )
        if decision_meta is not None:
            decision_meta.update(_build_burn_decision(
                requested_status=requested_subtitle_status,
                effective_status=subtitle_status,
                confidence=confidence,
                ocr_lang=ocr_lang,
                detected_boundary=detected_boundary,
                chosen_burn_mode=f"skip_burn_keep_existing_{subtitle_status}_hard_subtitles",
                used_zh_aligned=False,
                subtitle_alignment_source=None,
                burn_renderer="copy",
                auto_final_action=f"keep_existing_{subtitle_status}_hard_subtitles_without_burn",
                hard_subtitle_mask=None,
                subtitle_burn_policy=hard_sub_policy,
                subtitle_burn_policy_summary=hard_sub_policy_summary,
            ))
        shutil.copy2(video_path, output_path)
        return True
    elif hard_burn_mode == "replace" and subtitle_status in {"en", "zh", "bilingual"}:
        boundary = detected_boundary if detected_boundary is not None else hard_boundary_fallback
        logging.info(
            "Source has %s hard subtitle(s): masking original subtitle band and burning unified dual subtitles (boundary=%.4f, fallback=%s, mode=%s).",
            subtitle_status.upper(),
            boundary,
            detected_boundary is None,
            hard_burn_mode,
        )
        if decision_meta is not None:
            decision_meta.update(_build_burn_decision(
                requested_status=requested_subtitle_status,
                effective_status=subtitle_status,
                confidence=confidence,
                ocr_lang=ocr_lang,
                detected_boundary=boundary,
                chosen_burn_mode="mask_existing_hard_subtitles_and_burn_dual",
                used_zh_aligned=False,
                subtitle_alignment_source="shared_dual_timeline",
                burn_renderer="ffmpeg",
                auto_final_action="mask_existing_hard_subtitles_and_burn_dual_subtitles",
                hard_subtitle_mask=hard_sub_mask_meta,
                subtitle_burn_policy=hard_sub_policy,
                subtitle_burn_policy_summary=hard_sub_policy_summary,
            ))
        if not _hard_burn_bilingual_ffmpeg(
            video_path,
            en_srt_path,
            zh_srt_path,
            output_path,
            ocr_lang=None,
            subtitle_boundary=boundary,
            hard_subtitle_mask=hard_sub_mask_meta,
        ):
            logging.warning(
                "FFmpeg fast path failed for hard-sub replacement mode, falling back to moviepy dual overlay with mask."
            )
            if decision_meta is not None:
                decision_meta.update({
                    "burn_renderer": "moviepy_overlay",
                    "chosen_burn_mode": "mask_existing_hard_subtitles_and_burn_dual_moviepy_fallback",
                    "auto_final_action": "mask_existing_hard_subtitles_and_burn_dual_subtitles",
                    "subtitle_burn_policy": hard_sub_policy,
                    "subtitle_burn_policy_summary": hard_sub_policy_summary,
                })
            return _hard_burn_bilingual(
                video_path,
                en_srt_path,
                zh_srt_path,
                output_path,
                hard_subtitle_mask=hard_sub_mask_meta,
            )
        return True
    else:
        # Fallback to dual (EN+ZH both at bottom)
        logging.warning(f"Unknown subtitle status: {subtitle_status}, falling back to dual")
        if decision_meta is not None:
            decision_meta.update(_build_burn_decision(
                requested_status=requested_subtitle_status,
                effective_status=str(subtitle_status),
                confidence=confidence,
                ocr_lang=ocr_lang,
                detected_boundary=detected_boundary,
                chosen_burn_mode="dual_bottom_unknown_status_fallback",
                used_zh_aligned=used_zh_aligned,
                subtitle_alignment_source=None,
                burn_renderer="ffmpeg",
                auto_final_action="burn_dual_subtitles",
                hard_subtitle_mask=None,
                subtitle_burn_policy=hard_sub_policy,
                subtitle_burn_policy_summary=hard_sub_policy_summary,
            ))
        if not _hard_burn_bilingual_ffmpeg(video_path, en_srt_path, zh_srt_path, output_path, ocr_lang=ocr_lang):
            return _hard_burn_bilingual(video_path, en_srt_path, zh_srt_path, output_path)
        return True


def _hard_burn_overlay_chinese_en_hard_sub_fallback(
    video_path: str,
    zh_srt_path: str,
    output_path: str,
    asr_segments: list = None,
) -> bool:
    """
    EN-hard-subtitle fallback renderer using moviepy overlay.

    This is not the main path. It is only intended as an advanced fallback when:
      1. the source video already has EN hard subtitles,
      2. FFmpeg fast-burn failed,
      3. and ASR segments are available.

    Optional visual-sync fallback:
      D: Try to extract soft subtitle track → translate each segment → overlay
      B: Frame-diff detection + ASR word timestamps → aligned segments → translate → overlay

    If visual sync cannot run or produces no aligned segments, fall back to the pre-generated
    zh subtitle track and still render via moviepy.
    """
    try:
        from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
        import numpy as np
    except ImportError:
        logging.error("moviepy is required for hard burn. Install: pip install moviepy")
        return False

    config = get_config()
    font_path = _find_cjk_font(config)
    if not font_path:
        logging.warning("No CJK font found, Chinese text may not render correctly")

    # ── Optional visual-sync fallback: get more precise aligned Chinese segments ──
    zh_entries = []

    if asr_segments is not None:
        try:
            from subtitle_sync import SubtitleSync
            from translator import Translator

            logging.info("[EN-hard-sub fallback] Running optional visual sync (SubtitleSync D+B)...")
            syncer = SubtitleSync(sample_fps=5.0)
            en_aligned = syncer.get_aligned_segments(video_path, asr_segments)

            if en_aligned:
                logging.info(
                    "[EN-hard-sub fallback] Visual sync produced %d aligned EN segments; translating to ZH...",
                    len(en_aligned),
                )
                translator = Translator(config=config) # Pass config
                en_texts = [s['text'] for s in en_aligned]
                zh_texts = translator._batch_translate(en_texts, target_lang='zh')

                for seg, zh_text in zip(en_aligned, zh_texts):
                    zh_entries.append({
                        'start': seg['start'],
                        'end':   seg['end'],
                        'text':  zh_text,
                    })
                logging.info(
                    "[EN-hard-sub fallback] Visual-sync translation complete: %d ZH segments",
                    len(zh_entries),
                )
        except Exception as e:
            logging.warning(
                "[EN-hard-sub fallback] Visual sync failed: %s; falling back to pre-generated zh subtitle",
                e,
            )

    # ── Fallback: use pre-generated zh_srt_path ──
    if not zh_entries:
        zh_entries = _parse_srt(zh_srt_path) if os.path.exists(zh_srt_path) else []
        if not zh_entries:
            logging.error("No Chinese subtitle entries found")
            return False
        logging.info(
            "[EN-hard-sub fallback] Using pre-generated zh subtitle track: %d segments",
            len(zh_entries),
        )

    # Detect subtitle boundary (use safe default; boundary detection not yet implemented)
    # Patched to remove non-existent detect_subtitle_boundary (TypeError)
    subtitle_boundary = 0.85

    try:
        video = VideoFileClip(video_path)
        w, h = video.size

        subtitle_clips = []
        
        video_dimensions = _get_video_dimensions(video_path)
        is_vertical_video = False
        if video_dimensions:
            is_vertical_video = video_dimensions['height'] > video_dimensions['width']

        for entry in zh_entries:
            start_s = entry["start"]
            end_s   = entry["end"]
            zh_text = entry["text"].strip()
            if not zh_text:
                continue
            
            frame = _make_subtitle_overlay_chinese(w, h, zh_text, font_path, subtitle_boundary, config, is_vertical_video)
            clip = (ImageClip(frame, transparent=True)
                    .with_start(start_s)
                    .with_duration(end_s - start_s))
            subtitle_clips.append(clip)

        final = CompositeVideoClip([video] + subtitle_clips)
        final.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            logger=None,
            threads=4
        )
        video.close()
        final.close()

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False

        logging.info(
            "  (EN-hard-sub fallback moviepy overlay at %.3f, %d segments)",
            subtitle_boundary,
            len(zh_entries),
        )
        return True

    except Exception as e:
        logging.error(f"Overlay Chinese subtitle failed: {e}")
        return False


def _hard_burn_overlay_english(
    video_path: str,
    en_srt_path: str,
    output_path: str,
    subtitle_boundary: float,
) -> bool:
    """Overlay English subtitle above an existing hard-burned ZH subtitle."""
    try:
        from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
    except ImportError:
        logging.error("moviepy is required for hard burn. Install: pip install moviepy")
        return False

    config = get_config()

    en_entries = _parse_srt(en_srt_path) if os.path.exists(en_srt_path) else []
    if not en_entries:
        logging.error("No English subtitle entries found")
        return False

    try:
        video = VideoFileClip(video_path)
        w, h = video.size

        subtitle_clips = []
        video_dimensions = _get_video_dimensions(video_path)
        is_vertical_video = False
        if video_dimensions:
            is_vertical_video = video_dimensions['height'] > video_dimensions['width']

        for entry in en_entries:
            en_text = entry["text"].strip()
            if not en_text:
                continue

            frame = _make_subtitle_overlay_english(
                w,
                h,
                en_text,
                subtitle_boundary=subtitle_boundary,
                config=config,
                is_vertical_video=is_vertical_video,
            )
            clip = (
                ImageClip(frame, transparent=True)
                .with_start(entry["start"])
                .with_duration(entry["end"] - entry["start"])
            )
            subtitle_clips.append(clip)

        final = CompositeVideoClip([video] + subtitle_clips)
        final.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            logger=None,
            threads=4,
        )

        video.close()
        final.close()

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False

        logging.info(
            "  (English subtitle overlaid at %.3f, %d segments)",
            subtitle_boundary,
            len(en_entries),
        )
        return True
    except Exception as e:
        logging.error(f"Overlay English subtitle failed: {e}")
        return False


def _make_subtitle_overlay_chinese(width: int, height: int, zh_text: str, 
                                   font_path: str = None, subtitle_boundary: float = 0.85,
                                   config: Config = None, is_vertical_video: bool = False):
    """
    Render Chinese subtitle overlay frame (above English subtitle).
    
    Args:
        width: Frame width
        height: Frame height
        zh_text: Chinese text
        font_path: Font file path
        subtitle_boundary: Detected English subtitle upper boundary (0.0-1.0 from top)
        config: Config object
        is_vertical_video: True if the video is vertical
    
    Position: subtitle_boundary - offset (offset = 1.3 * subtitle_height)
    """
    from PIL import Image, ImageDraw, ImageFont
    
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    if is_vertical_video:
        _base_h = 1920
        _scale = height / _base_h
        zh_font_size = max(1, round(config.font_size_zh_vertical * _scale))
        margin_v_zh = max(1, round(config.margin_v_zh_vertical * _scale))
    else:
        _base_h = 1080
        _scale = height / _base_h
        zh_font_size = max(1, round(config.font_size_zh_horizontal * _scale))
        margin_v_zh = max(1, round(config.margin_v_zh_horizontal * _scale))
    
    try:
        zh_font = ImageFont.truetype(config.font_name_zh, zh_font_size) if config.font_name_zh else ImageFont.load_default()
    except:
        zh_font = ImageFont.load_default()
    
    # Calculate text height (actual pixels, not estimated from font size)
    wrapped_zh = _wrap_text_by_pixel(zh_text, zh_font, draw, int(width * 0.85))
    zh_lines = wrapped_zh.split('\n')
    zh_line_heights = [draw.textbbox((0, 0), line, font=zh_font)[3] - draw.textbbox((0, 0), line, font=zh_font)[1] for line in zh_lines]
    zh_line_spacing = int(zh_font_size * 0.2)
    total_zh_height = sum(zh_line_heights) + zh_line_spacing * (len(zh_lines) - 1)

    # Position chinese subtitle ABOVE the english subtitle boundary.
    # margin_top = top-left y of the chinese text
    # We want: margin_top + total_zh_height + gap <= subtitle_boundary * height
    # => margin_top = subtitle_boundary * height - total_zh_height - gap
    GAP_PX = margin_v_zh # Use margin_v_zh as gap between EN and ZH
    margin_top = int(height * subtitle_boundary) - total_zh_height - GAP_PX

    # Safety clamp: never draw above 50% of the frame
    margin_top = max(int(height * 0.5), margin_top)

    y = margin_top
    for i, line in enumerate(zh_lines):
        bbox = draw.textbbox((0, 0), line, font=zh_font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        
        # Black outline
        for dx in [-2, -1, 0, 1, 2]:
            for dy in [-2, -1, 0, 1, 2]:
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), line, font=zh_font, fill=(0, 0, 0, 200))
        
        # Yellow text
        draw.text((x, y), line, font=zh_font, fill=(255, 255, 0, 255))
        y += zh_line_heights[i] + zh_line_spacing
    
    import numpy as np
    return np.array(img)


def _make_subtitle_overlay_english(
    width: int,
    height: int,
    en_text: str,
    subtitle_boundary: float = 0.85,
    config: Config = None,
    is_vertical_video: bool = False,
):
    """Render English subtitle overlay frame above an existing hard-burned ZH subtitle."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if config is None:
        config = get_config()

    if is_vertical_video:
        _scale = min(width, height) / 1080
        en_font_size = max(1, round(config.font_size_en_vertical * _scale))
        margin_v_en = max(1, round(config.margin_v_en_vertical * _scale))
    else:
        _scale = min(width, height) / 1080
        en_font_size = max(1, round(config.font_size_en_horizontal * _scale))
        margin_v_en = max(1, round(config.margin_v_en_horizontal * _scale))

    try:
        en_font = ImageFont.truetype(config.font_name_en, en_font_size) if config.font_name_en else ImageFont.load_default()
    except Exception:
        en_font = ImageFont.load_default()

    wrapped_en = _wrap_text_by_pixel(en_text, en_font, draw, int(width * 0.85))
    en_lines = wrapped_en.split('\n')
    en_line_heights = [draw.textbbox((0, 0), line, font=en_font)[3] - draw.textbbox((0, 0), line, font=en_font)[1] for line in en_lines]
    en_line_spacing = int(en_font_size * 0.2)
    total_en_height = sum(en_line_heights) + en_line_spacing * (len(en_lines) - 1)

    gap_px = margin_v_en
    margin_top = int(height * subtitle_boundary) - total_en_height - gap_px
    margin_top = max(20, margin_top)

    y = margin_top
    for i, line in enumerate(en_lines):
        bbox = draw.textbbox((0, 0), line, font=en_font)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        for dx in [-2, -1, 0, 1, 2]:
            for dy in [-2, -1, 0, 1, 2]:
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), line, font=en_font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=en_font, fill=(255, 255, 255, 255))
        y += en_line_heights[i] + en_line_spacing

    import numpy as np
    return np.array(img)


def _hard_burn_bilingual_ffmpeg(video_path: str, en_srt_path: str, zh_srt_path: str,
                                output_path: str,
                                ocr_lang: Optional[str] = None,
                                subtitle_boundary: float = 0.80,
                                hard_subtitle_mask: Optional[Dict[str, Any]] = None) -> bool:
    """
    Fast path: burn dual bilingual subtitles using FFmpeg subtitles filter.
    Uses two chained subtitles filters — EN (white, upper position) and ZH (yellow, lower).
    ~50-100x faster than moviepy for long videos.

    Args:
        ocr_lang: Language detected by OCR in the source video ('en', 'zh', 'bilingual', or None).
        hard_subtitle_mask: Optional solid bottom-band mask to hide existing hard subtitles
                            before burning unified EN+ZH dual subtitles.
    """
    config = get_config()
    video_dimensions = _get_video_dimensions(video_path)
    is_vertical_video = False
    if video_dimensions:
        is_vertical_video = video_dimensions['height'] > video_dimensions['width']
        logging.info(f"Video {video_path} dimensions: {video_dimensions['width']}x{video_dimensions['height']}, vertical: {is_vertical_video}")

    # Determine if source video already contains hard-burned subtitles at bottom.
    has_en_hard_subtitle = (ocr_lang == 'en')
    has_zh_hard_subtitle = (ocr_lang == 'zh')
    if has_en_hard_subtitle:
        logging.info(f"Source video has EN hard subtitles (ocr_lang={ocr_lang}): "
                     f"skipping EN soft subtitle, moving ZH to top.")
    elif has_zh_hard_subtitle:
        logging.info(f"Source video has ZH hard subtitles (ocr_lang={ocr_lang}): "
                     f"skipping ZH soft subtitle, moving EN to top.")

    video_height = video_dimensions['height'] if video_dimensions else 1080
    layout = _compute_subtitle_layout(
        video_height, is_vertical_video, config,
        hard_subtitle_lang=ocr_lang if ocr_lang in {"en", "zh"} else None,
        subtitle_boundary=subtitle_boundary,
    )
    en_fontsize = layout["en_fontsize"]
    zh_fontsize = layout["zh_fontsize"]
    margin_v_zh = layout["margin_v_zh"]
    margin_v_en = layout["margin_v_en"]

    # Escape colons in paths for FFmpeg filter syntax
    def esc(p):
        return p.replace('\\', '/').replace(':', '\\:')

    cjk_font = _find_cjk_font(config) or ""
    cjk_font_esc = esc(cjk_font) if cjk_font else ""

    # EN: white, bottom-center, MarginV (above ZH)
    en_style = (
        f"FontName={config.font_name_en},"
        f"Fontsize={en_fontsize},"
        f"PrimaryColour=&H00FFFFFF&,"
        f"OutlineColour=&H00000000&,"
        f"Outline=0.8,"
        f"Alignment=2,"        # bottom-center
        f"MarginV={margin_v_en}"
    )
    # ZH: yellow, bottom-center always; when EN hard subtitle detected, MarginV pushes it above EN hard sub
    zh_alignment = 2  # bottom-center (MarginV controls vertical position)
    zh_style = (
        f"FontName={config.font_name_zh if cjk_font_esc else config.font_name_en}," # Use configured ZH font
        f"Fontsize={zh_fontsize},"
        f"PrimaryColour=&H0000FFFF&,"
        f"OutlineColour=&H00000000&,"
        f"Outline=1,"
        f"Alignment={zh_alignment},"
        f"MarginV={margin_v_zh}"
    )

    def _srt_to_ass(srt_path: str, style_line: str, play_res_x: int, play_res_y: int, pos_override: tuple = None) -> str:
        """Convert SRT to ASS with explicit style, write to temp file, return path."""
        import tempfile, re
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
        
        def srt_time_to_ass(t: str) -> str:
            # 00:00:01,000 -> 0:00:01.00
            t = t.replace(',', '.')
            parts = t.split(':')
            h, m, s = parts[0], parts[1], parts[2]
            s_parts = s.split('.')
            sec = s_parts[0]
            cs = s_parts[1][:2] if len(s_parts) > 1 else '00'
            return f"{int(h)}:{m}:{sec}.{cs}"
        
        blocks = re.split(r'\n\n+', srt_content.strip())
        events = []
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue
            times = lines[1].split(' --> ')
            if len(times) != 2:
                continue
            start = srt_time_to_ass(times[0].strip())
            end = srt_time_to_ass(times[1].strip())
            text = '\\N'.join(lines[2:])
            if pos_override:
                cx, cy = pos_override
                text = '{\\an2\\pos(' + str(cx) + ',' + str(cy) + ')}' + text
            events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
        
        ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        ass_content += '\n'.join(events)
        
        tmp = tempfile.NamedTemporaryFile(suffix='.ass', delete=False, mode='w', encoding='utf-8')
        tmp.write(ass_content)
        tmp.close()
        return tmp.name

    # Build ASS style lines (Name,Font,Size,PrimaryColour,...,MarginV,Encoding)
    def _ass_style_line(fontname, fontsize, primary_colour, outline_colour, outline, alignment, margin_v):
        return (f"{fontname},{fontsize},{primary_colour},&H000000FF&,{outline_colour},&H00000000&,"
                f"0,0,0,0,100,100,0,0,1,{outline},0,{alignment},10,10,{margin_v},1")

    video_w = video_dimensions['width'] if video_dimensions else 1920
    video_h = video_dimensions['height'] if video_dimensions else 1080

    zh_ass_style = _ass_style_line(
        fontname=config.font_name_zh or config.font_name_en,
        fontsize=zh_fontsize,
        primary_colour='&H0000FFFF&',   # yellow
        outline_colour='&H00000000&',
        outline=1,
        alignment=zh_alignment,
        margin_v=margin_v_zh
    )
    en_ass_style = _ass_style_line(
        fontname=config.font_name_en,
        fontsize=en_fontsize,
        primary_colour='&H00FFFFFF&',   # white
        outline_colour='&H00000000&',
        outline=1,
        alignment=2,
        margin_v=margin_v_en
    )

    # For single-soft-subtitle modes, use absolute pos override in PlayRes coords.
    zh_pos_override = None
    en_pos_override = None
    if hard_subtitle_mask:
        mask_positions = _compute_mask_text_positions(
            mask_x=int(hard_subtitle_mask["x"]),
            mask_y=int(hard_subtitle_mask["y"]),
            mask_w=int(hard_subtitle_mask["w"]),
            mask_h=int(hard_subtitle_mask["h"]),
            video_h=video_h,
            en_fontsize=en_fontsize,
            zh_fontsize=zh_fontsize,
            inter_gap=layout["inter_gap"],
        )
        center_x = mask_positions["center_x"]
        en_pos_override = (center_x, mask_positions["en_bottom"])
        zh_pos_override = (center_x, mask_positions["zh_bottom"])
    elif has_en_hard_subtitle:
        en_top_px = int(subtitle_boundary * video_h)  # e.g. 0.8333 * 1080 = 899px
        gap_px = max(4, round(zh_fontsize * 0.12))
        zh_bottom_target = en_top_px - gap_px
        zh_pos_override = (video_w // 2, zh_bottom_target)
    elif has_zh_hard_subtitle:
        zh_top_px = int(subtitle_boundary * video_h)
        gap_px = max(4, round(en_fontsize * 0.12))
        # Horizontal samples proved more sensitive: aggressive downward shifts
        # overlap with the existing ZH hard subtitles. Keep them conservative.
        # Vertical samples still benefit from a tighter visual gap.
        if is_vertical_video:
            en_bottom_target = zh_top_px + round(en_fontsize * 0.18) - gap_px
        else:
            en_bottom_target = zh_top_px - gap_px
        en_pos_override = (video_w // 2, en_bottom_target)

    zh_ass_path = _srt_to_ass(zh_srt_path, zh_ass_style, video_w, video_h, pos_override=zh_pos_override)
    en_ass_path = _srt_to_ass(en_srt_path, en_ass_style, video_w, video_h, pos_override=en_pos_override)

    en_filter = f"ass={esc(en_ass_path)}"
    zh_filter = f"ass={esc(zh_ass_path)}"

    overlay_path = None
    if hard_subtitle_mask:
        mask_x = int(hard_subtitle_mask["x"])
        mask_y = int(hard_subtitle_mask["y"])
        mask_w = int(hard_subtitle_mask["w"])
        mask_h = int(hard_subtitle_mask["h"])
        mask_radius = int(hard_subtitle_mask.get("radius_px", 0))
        mask_feather = int(hard_subtitle_mask.get("feather_px", 0))
        mask_color = hard_subtitle_mask.get("color", config.subtitle_hard_mask_color)
        import tempfile
        overlay_image = _build_hard_subtitle_mask_overlay_image(
            video_width=video_w,
            video_height=video_h,
            hard_subtitle_mask=hard_subtitle_mask,
            color=mask_color,
        )
        overlay_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        overlay_path = overlay_tmp.name
        overlay_tmp.close()
        overlay_image.save(overlay_path)
        logging.info(
            "  Applying rounded hard-subtitle mask: x=%d y=%d w=%d h=%d radius=%d feather=%d color=%s.",
            mask_x,
            mask_y,
            mask_w,
            mask_h,
            mask_radius,
            mask_feather,
            mask_color,
        )

    # When source already has a hard subtitle track and we are preserving it: burn only the missing language.
    if has_en_hard_subtitle and not hard_subtitle_mask:
        vf = zh_filter
        logging.info(
            "  EN hard subtitle detected: burning ZH only above boundary %.4f (target_bottom_px=%s).",
            subtitle_boundary,
            zh_pos_override[1] if zh_pos_override else "n/a",
        )
    elif has_zh_hard_subtitle and not hard_subtitle_mask:
        vf = en_filter
        logging.info(
            "  ZH hard subtitle detected: burning EN only above boundary %.4f (target_bottom_px=%s).",
            subtitle_boundary,
            en_pos_override[1] if en_pos_override else "n/a",
        )
    else:
        if hard_subtitle_mask:
            vf = f"[0:v][1:v]overlay=0:0,{en_filter},{zh_filter}[v]"
        else:
            vf = ",".join([en_filter, zh_filter])
        if hard_subtitle_mask:
            logging.info(
                "  Hard subtitle replacement mode: burning unified EN+ZH dual subtitles inside mask "
                "(en_bottom_px=%s, zh_bottom_px=%s).",
                en_pos_override[1] if en_pos_override else "n/a",
                zh_pos_override[1] if zh_pos_override else "n/a",
            )

    # Use imageio_ffmpeg binary because Homebrew FFmpeg often lacks libass/subtitles filter
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    if hard_subtitle_mask and overlay_path:
        cmd = [
            ffmpeg_exe, "-y",
            "-i", video_path,
            "-i", overlay_path,
            "-filter_complex", vf,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            output_path
        ]
    else:
        cmd = [
            ffmpeg_exe, "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            output_path
        ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3600
        )
        if result.returncode != 0:
            logging.debug(f"[FFmpeg subtitles] Error: {result.stderr[-500:]}")
            return False
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False
        logging.info("  (bilingual subtitles burned via FFmpeg subtitles filter)")
        return True
    except Exception as e:
        logging.debug(f"[FFmpeg subtitles] Exception: {e}")
        return False
    finally:
        if overlay_path and os.path.exists(overlay_path):
            try:
                os.unlink(overlay_path)
            except Exception:
                pass


def _hard_burn_bilingual(video_path: str, en_srt_path: str, zh_srt_path: str,
                         output_path: str,
                         hard_subtitle_mask: Optional[Dict[str, Any]] = None) -> bool:
    """
    Hard-burn bilingual subtitles into video.

    Fast path: FFmpeg subtitles filter (seconds/minutes for long video).
    Fallback:  moviepy + Pillow frame rendering (slow, but no libass needed).

    Layout:
        ┌────────────────────────────────┐
        │                                │
        │          video frame           │
        │                                │
        │   English subtitle (line 1)    │  ← white
        │   中文字幕 (line 2)            │  ← yellow
        └────────────────────────────────┘
    """
    # ── Fast path: FFmpeg subtitles filter ──────────────────────────────
    logging.info("  Trying FFmpeg subtitles filter (fast path)...")
    if _hard_burn_bilingual_ffmpeg(video_path, en_srt_path, zh_srt_path, output_path):
        return True

    logging.warning("  FFmpeg subtitles filter failed (libass may be missing), falling back to moviepy")

    # ── Slow fallback: moviepy + Pillow ──────────────────────────────────
    try:
        from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
        import numpy as np
    except ImportError:
        logging.error("moviepy is required for fallback burn. Install: pip install moviepy")
        return False

    en_entries = _parse_srt(en_srt_path) if os.path.exists(en_srt_path) else []
    zh_entries = _parse_srt(zh_srt_path) if os.path.exists(zh_srt_path) else []

    if not en_entries and not zh_entries:
        logging.error("No subtitle entries found in either SRT file")
        return False

    config = get_config()
    font_path = _find_cjk_font(config)
    if not font_path:
        logging.warning("No CJK font found, Chinese text may not render correctly")

    try:
        video = VideoFileClip(video_path)
        w, h = video.size

        # Build bilingual subtitle pairs
        max_len = max(len(en_entries), len(zh_entries))
        subtitle_clips = []
        if hard_subtitle_mask:
            import numpy as np
            mask_x = int(hard_subtitle_mask["x"])
            mask_y = int(hard_subtitle_mask["y"])
            mask_w = int(hard_subtitle_mask["w"])
            mask_h = int(hard_subtitle_mask["h"])
            mask_radius = int(hard_subtitle_mask.get("radius_px", 0))
            mask_feather = int(hard_subtitle_mask.get("feather_px", 0))
            logging.info(
                "  Applying rounded hard-subtitle mask in moviepy fallback: x=%d y=%d w=%d h=%d radius=%d feather=%d.",
                mask_x,
                mask_y,
                mask_w,
                mask_h,
                mask_radius,
                mask_feather,
            )
            mask_color = hard_subtitle_mask.get("color", config.subtitle_hard_mask_color)
            mask_img = _build_hard_subtitle_mask_overlay_image(
                video_width=w,
                video_height=h,
                hard_subtitle_mask=hard_subtitle_mask,
                color=mask_color,
            )
            mask_clip = (
                ImageClip(np.array(mask_img), transparent=True)
                .with_start(0)
                .with_duration(video.duration)
            )
            subtitle_clips.append(mask_clip)

        video_dimensions = _get_video_dimensions(video_path)
        is_vertical_video = False
        if video_dimensions:
            is_vertical_video = video_dimensions['height'] > video_dimensions['width']

        for i in range(max_len):
            en = en_entries[i] if i < len(en_entries) else None
            zh = zh_entries[i] if i < len(zh_entries) else None

            if en and zh:
                start_s = min(en["start"], zh["start"])
                end_s = max(en["end"], zh["end"])
            elif en:
                start_s, end_s = en["start"], en["end"]
            else:
                start_s, end_s = zh["start"], zh["end"]

            en_text = en["text"].strip() if en else ""
            zh_text = zh["text"].strip() if zh else ""

            if not en_text and not zh_text:
                continue

            frame = _make_subtitle_frame(
                w,
                h,
                en_text,
                zh_text,
                font_path,
                config,
                is_vertical_video,
                hard_subtitle_mask=hard_subtitle_mask,
            )
            clip = (ImageClip(frame, transparent=True)
                    .with_start(start_s)
                    .with_duration(end_s - start_s))
            subtitle_clips.append(clip)

        final = CompositeVideoClip([video] + subtitle_clips)
        final.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            logger=None,
            threads=4
        )

        video.close()
        final.close()

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False

        logging.info("  (bilingual subtitles hard-burned via moviepy fallback)")
        return True

    except Exception as e:
        logging.error(f"Hard burn failed: {e}")
        return False



# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def embed_subtitle(video_path: str, subtitle_path: str, output_path: str) -> bool:
    """
    Embed a single-language subtitle into video (soft embed).
    Kept for backward compatibility.
    """
    if _try_soft_embed(video_path, subtitle_path, output_path):
        return True
    logging.error("Soft subtitle embedding failed")
    return False


def embed_subtitles_batch(clips_dir: str, subtitles_dir: str, output_dir: str,
                          language: str = "zh", burn: bool = False,
                          subtitle_status: str = "auto",
                          asr_segments: list = None,
                          clips_data: list = None) -> dict:
    """
    Batch embed subtitles into all clips.

    Args:
        clips_dir:     Directory containing video clips (used to locate actual video files)
        subtitles_dir: Directory containing subtitle files
        output_dir:    Output directory
        language:      Subtitle language to embed ('zh', 'en', 'original')
        burn:          If True, hard-burn bilingual subtitles (EN top + ZH bottom)
                       If False, soft-embed single language as subtitle track
        asr_segments:  Optional full-video ASR segments (fallback)
        clips_data:    Optional translation metadata with clip-specific ASR subsets
    """
    mode = "hard-burn bilingual" if burn else "soft-embed"
    logging.info("=" * 70)
    logging.info(f"Embedding Subtitles into Video Clips  [mode: {mode}]")
    logging.info("=" * 70)
    logging.info(f"Clips directory: {clips_dir}")
    logging.info(f"Subtitles directory: {subtitles_dir}")
    logging.info(f"Output directory: {output_dir}")

    if not os.path.exists(clips_dir):
        logging.error(f"Clips directory not found: {clips_dir}")
        return {"total_processed": 0, "successful": 0, "failed": 0, "videos": [], "status": "error", "message": f"Clips directory not found: {clips_dir}"}
    if not os.path.exists(subtitles_dir):
        logging.error(f"Subtitles directory not found: {subtitles_dir}")
        return {"total_processed": 0, "successful": 0, "failed": 0, "videos": [], "status": "error", "message": f"Subtitles directory not found: {subtitles_dir}"}

    os.makedirs(output_dir, exist_ok=True)

    clips_to_process_source = []
    clips_metadata_path = os.path.join(clips_dir, "clips_metadata.json")
    metadata_payload: Dict[str, Any] = {"clips": []}
    if clips_data is not None:
        clips_to_process_source = clips_data
        logging.info("Using provided clips_data for processing.")
        if os.path.exists(clips_metadata_path):
            try:
                metadata_payload = json.loads(Path(clips_metadata_path).read_text(encoding="utf-8"))
            except Exception:
                metadata_payload = {"clips": clips_to_process_source}
        else:
            metadata_payload = {"clips": clips_to_process_source}
    else:
        if not os.path.exists(clips_metadata_path):
            logging.error(f"clips_metadata.json not found in {clips_dir}")
            return {"total_processed": 0, "successful": 0, "failed": 0, "videos": [], "status": "error", "message": f"clips_metadata.json not found in {clips_dir}"}
        
        with open(clips_metadata_path, 'r') as f:
            metadata_payload = json.load(f)
        clips_to_process_source = metadata_payload.get('clips', [])
        logging.info(f"Using clips from {clips_metadata_path} for processing.")

    if not clips_to_process_source:
        logging.warning("No clips found to process.")
        return {"total_processed": 0, "successful": 0, "failed": 0, "videos": []}


    logging.info(f"\nTargeting {len(clips_to_process_source)} video clips for subtitle processing")

    results = []
    successful = 0
    failed = 0

    for i, clip_info in enumerate(clips_to_process_source, 1):
        video_file = os.path.basename(clip_info.get('clip_path')) # Get filename from clip_info
        video_path = os.path.join(clips_dir, video_file) # Construct full video path
        video_basename = os.path.splitext(video_file)[0]

        # Ensure video file exists before proceeding
        if not os.path.exists(video_path):
            logging.warning(f"  Video file not found for clip '{video_file}' at '{video_path}', skipping.")
            failed += 1
            continue

        # Determine ASR segments for this specific clip
        clip_asr = asr_segments  # Default to full ASR if provided
        # If translations are available in clip_info, use its 'en' translation segments (which are translated/shifted ASR)
        # These contain 'words' because we preserved them in Translator
        clip_asr = clip_info.get('translations', {}).get('en', clip_asr)


        logging.info(f"\nProcessing {i}/{len(clips_to_process_source)}: {video_file}")

        if burn:
            # Hard burn: need both EN and ZH subtitle files
            # These paths should now come from clip_info directly if available, or fall back to conventions
            en_srt_path = clip_info.get('subtitle_files', {}).get('en') or os.path.join(subtitles_dir, f"{video_basename}_en.srt")
            zh_srt_path = clip_info.get('subtitle_files', {}).get('zh') or os.path.join(subtitles_dir, f"{video_basename}_zh.srt")
            zh_aligned_srt_path = clip_info.get('subtitle_files', {}).get('zh_aligned') or os.path.join(subtitles_dir, f"{video_basename}_zh_aligned.srt")

            if not os.path.exists(en_srt_path) and not os.path.exists(zh_srt_path):
                # Fallback: try original, though for bilingual burn we need both
                orig_srt_path = clip_info.get('subtitle_files', {}).get('original') or os.path.join(subtitles_dir, f"{video_basename}_original.srt")
                if os.path.exists(orig_srt_path):
                    logging.warning(f"  Only original SRT found, but bilingual burn requires EN/ZH. Skipping {video_file}")
                    failed += 1
                    continue
                else:
                    logging.warning(f"  No EN/ZH/original subtitle files found for {video_file} in clip_info or {subtitles_dir}")
                    failed += 1
                    continue


            output_file = f"{video_basename}_bilingual.mp4"
            output_path = os.path.join(output_dir, output_file)

            logging.info(f"  EN: {os.path.basename(en_srt_path) if os.path.exists(en_srt_path) else '(none)'}")
            logging.info(f"  ZH: {os.path.basename(zh_srt_path) if os.path.exists(zh_srt_path) else '(none)'}")
            logging.info(f"  Output: {output_file}")

            burn_decision: Dict[str, Any] = {}
            success = _hard_burn_bilingual_auto(video_path, en_srt_path, zh_srt_path, output_path,
                                                subtitle_status=subtitle_status,
                                                asr_segments=clip_asr,
                                                zh_aligned_srt_path=zh_aligned_srt_path,
                                                decision_meta=burn_decision)
            clip_info["subtitle_burn"] = burn_decision
            logging.info("  Burn decision: %s", json.dumps(burn_decision, ensure_ascii=False))
        else:
            # Soft embed: single language
            subtitle_file = f"{video_basename}_{language}.srt"
            subtitle_path = clip_info.get('subtitle_files', {}).get(language) or os.path.join(subtitles_dir, subtitle_file)

            if not os.path.exists(subtitle_path):
                logging.warning(f"  Subtitle not found: {subtitle_file}")
                failed += 1
                continue

            output_file = f"{video_basename}_with_{language}_subtitles.mp4"
            output_path = os.path.join(output_dir, output_file)

            logging.info(f"  Subtitle: {subtitle_file}")
            logging.info(f"  Output: {output_file}")

            success = embed_subtitle(video_path, subtitle_path, output_path)

        if success:
            file_size = os.path.getsize(output_path) / (1024 * 1024)
            logging.info(f"  ✅ Success ({file_size:.2f} MB)")
            successful += 1
            results.append({
                "video": video_file,
                "output": output_path,
                "size_mb": file_size,
                "subtitle_burn": clip_info.get("subtitle_burn"),
            })
        else:
            logging.error(f"  ❌ Failed")
            failed += 1

    if clips_to_process_source:
        try:
            metadata_payload["clips"] = clips_to_process_source
            Path(clips_metadata_path).write_text(
                json.dumps(metadata_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logging.info("Updated clips metadata with subtitle burn decisions: %s", clips_metadata_path)
        except Exception as e:
            logging.warning("Failed to persist subtitle burn metadata to %s: %s", clips_metadata_path, e)

    logging.info("\n" + "=" * 70)
    logging.info("Embedding Complete!")
    logging.info("=" * 70)
    logging.info(f"Total processed: {len(clips_to_process_source)}")
    logging.info(f"Successful: {successful}")
    logging.info(f"Failed: {failed}")

    return {
        "total_processed": len(clips_to_process_source),
        "successful": successful,
        "failed": failed,
        "videos": results
    }


def main():
    parser = argparse.ArgumentParser(
        description='Embed subtitles into video clips',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Soft embed (subtitle track, fast):
  python embed_subtitles.py

  # Hard burn bilingual (EN top + ZH bottom, burned into video):
  python embed_subtitles.py --burn

  # Custom directories:
  python embed_subtitles.py --burn --input output/clips --output output/burned
        """
    )

    parser.add_argument('--input', default='output/clips',
                        help='Input clips directory (default: output/clips)')
    parser.add_argument('--output', default='output/clips_with_subtitles',
                        help='Output directory (default: output/clips_with_subtitles)')
    parser.add_argument('--subtitles', default='output/subtitles',
                        help='Subtitles directory (default: output/subtitles)')
    parser.add_argument('--language', default='zh', choices=['zh', 'en', 'original'],
                        help='Subtitle language for soft embed (default: zh)')
    parser.add_argument('--burn', action='store_true',
                        help='Hard-burn bilingual subtitles (EN top + ZH bottom) into video frames')

    args = parser.parse_args()

    result = embed_subtitles_batch(
        clips_dir=args.input,
        subtitles_dir=args.subtitles,
        output_dir=args.output,
        language=args.language,
        burn=args.burn
    )

    if result and result['successful'] > 0:
        logging.info(f"\n✅ Embedding successful!")
        logging.info(f"Output directory: {args.output}/")
        return 0
    else:
        logging.error(f"\n❌ Embedding failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
