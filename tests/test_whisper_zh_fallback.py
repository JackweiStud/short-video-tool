"""
Test: Whisper 检测到中文音频，但实际视频无任何字幕时的完整流程。

覆盖场景：
  1. Strategy 1 (ffprobe) 无软字幕
  2. Strategy 2 (OCR)    无硬字幕文字
  3. Strategy 3 (Whisper) 音频 = 中文 -> 返回 SubtitleStatus.ZH
  4. embed_subtitles      ZH 专用分支 -> 在源中文字幕之上叠加英文
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from subtitle_detect import SubtitleStatus, detect_subtitle_status


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def dummy_video(tmp_path):
    """占位视频文件，不需要真实可解码内容。"""
    p = tmp_path / "test_no_sub.mp4"
    p.write_bytes(b"\x00" * 64)
    return str(p)


# ─────────────────────────────────────────────────────────────
# Scenario 1: detect_subtitle_status
# Strategy 1 无软字幕, Strategy 2 OCR 无文字, Strategy 3 Whisper=zh
# 期望返回 SubtitleStatus.ZH
# ─────────────────────────────────────────────────────────────

class TestDetectSubtitleStatusWhisperZh:

    def test_returns_zh_when_audio_is_chinese_no_hard_sub(self, dummy_video):
        """
        无软字幕 + OCR 无结果 + Whisper 检测音频为中文
        -> 应返回 (SubtitleStatus.ZH, 0.8, 'zh', None)
        """
        with patch("subtitle_detect._get_soft_subtitle_tracks", return_value=[]), \
             patch("subtitle_detect._get_video_duration", return_value=60.0), \
             patch("subtitle_detect._detect_language_from_ocr_regions",
                   return_value=(SubtitleStatus.NONE, None, None)), \
             patch("subtitle_detect._detect_language_from_audio", return_value="zh"):

            result = detect_subtitle_status(dummy_video)

        assert len(result) == 4
        status, confidence, ocr_lang, top_ratio = result
        assert status == SubtitleStatus.ZH
        assert confidence == pytest.approx(0.8)
        assert ocr_lang == "zh"
        assert top_ratio is None

    def test_zh_status_overlays_english_above_existing_zh_hard_sub(self, dummy_video):
        """
        验证 embed_subtitles._hard_burn_bilingual_auto 对 ZH 状态会遮挡原硬字幕区域，
        然后统一烧录双语字幕。
        """
        # 动态导入避免顶层依赖失败
        from embed_subtitles import _hard_burn_bilingual_auto

        dummy_srt = tempfile.NamedTemporaryFile(suffix=".srt", delete=False, mode="w")
        dummy_srt.write(
            "1\n00:00:01,000 --> 00:00:03,000\nHello world\n\n"
        )
        dummy_srt.flush()
        en_srt = dummy_srt.name

        dummy_zh = tempfile.NamedTemporaryFile(suffix=".srt", delete=False, mode="w")
        dummy_zh.write(
            "1\n00:00:01,000 --> 00:00:03,000\n你好世界\n\n"
        )
        dummy_zh.flush()
        zh_srt = dummy_zh.name

        output_path = tempfile.mktemp(suffix=".mp4")

        try:
            with patch("embed_subtitles._hard_burn_bilingual_ffmpeg") as mock_ffmpeg, \
                 patch("embed_subtitles._hard_burn_overlay_english") as mock_overlay:
                result = _hard_burn_bilingual_auto(
                    video_path=dummy_video,
                    en_srt_path=en_srt,
                    zh_srt_path=zh_srt,
                    output_path=output_path,
                    subtitle_status="zh",
                )

            assert result is True
            mock_ffmpeg.assert_not_called()
            mock_overlay.assert_not_called()
            assert os.path.exists(output_path)
            assert os.path.getsize(output_path) > 0

        finally:
            os.unlink(en_srt)
            os.unlink(zh_srt)
            if os.path.exists(output_path):
                os.unlink(output_path)


# ─────────────────────────────────────────────────────────────
# Scenario 2: 对比 EN 误判（修复前）vs NONE（修复后）
# ─────────────────────────────────────────────────────────────

class TestWhisperEnBugFixed:

    def test_whisper_en_audio_returns_none_not_en(self, dummy_video):
        """
        修复验证：Whisper 检测到英文音频，但 OCR 无任何字幕文字
        -> 应返回 SubtitleStatus.NONE（修复后）
        -> 不应返回 SubtitleStatus.EN（修复前的 bug）
        """
        with patch("subtitle_detect._get_soft_subtitle_tracks", return_value=[]), \
             patch("subtitle_detect._get_video_duration", return_value=60.0), \
             patch("subtitle_detect._detect_language_from_ocr_regions",
                   return_value=(SubtitleStatus.NONE, None, None)), \
             patch("subtitle_detect._detect_language_from_audio", return_value="en"):

            result = detect_subtitle_status(dummy_video)

        status, confidence, ocr_lang, top_ratio = result
        assert status == SubtitleStatus.NONE, (
            f"修复后 Whisper=EN+无OCR 应返回 NONE，实际返回 {status}"
        )
        assert ocr_lang is None, "无 OCR 字幕时 ocr_lang 应为 None"

    def test_en_hard_sub_moviepy_fallback_without_asr_marks_plain_alignment(self, dummy_video):
        """
        EN 硬字幕场景下，若 FFmpeg 失败且没有 asr_segments，
        应使用带遮罩的双语 moviepy fallback，不应再走单语保留式路径。
        """
        from embed_subtitles import _hard_burn_bilingual_auto

        en_srt = tempfile.NamedTemporaryFile(suffix=".srt", delete=False, mode="w")
        en_srt.write("1\n00:00:01,000 --> 00:00:03,000\nHello world\n\n")
        en_srt.flush()

        zh_srt = tempfile.NamedTemporaryFile(suffix=".srt", delete=False, mode="w")
        zh_srt.write("1\n00:00:01,000 --> 00:00:03,000\n你好世界\n\n")
        zh_srt.flush()

        out = tempfile.mktemp(suffix=".mp4")
        decision_meta = {}

        try:
            with patch("embed_subtitles._hard_burn_bilingual_ffmpeg", return_value=False), \
                 patch("embed_subtitles._hard_burn_bilingual", return_value=True) as mock_fallback:
                ok = _hard_burn_bilingual_auto(
                    video_path=dummy_video,
                    en_srt_path=en_srt.name,
                    zh_srt_path=zh_srt.name,
                    output_path=out,
                    subtitle_status="en",
                    asr_segments=None,
                    decision_meta=decision_meta,
                )

            assert ok is True
            mock_fallback.assert_called_once()
            assert decision_meta["burn_renderer"] == "moviepy_overlay"
            assert decision_meta["subtitle_alignment_source"] == "shared_dual_timeline"
            assert decision_meta["chosen_burn_mode"] == "mask_existing_hard_subtitles_and_burn_dual_moviepy_fallback"
        finally:
            os.unlink(en_srt.name)
            os.unlink(zh_srt.name)


# ─────────────────────────────────────────────────────────────
# Scenario 3: 优先级验证 - OCR 有结果时不应走到 Whisper
# ─────────────────────────────────────────────────────────────

class TestStrategyPriority:

    def test_ocr_en_result_does_not_fall_through_to_whisper(self, dummy_video):
        """
        Strategy 2 OCR 检测到英文硬字幕时，
        不应该再调用 Whisper（Strategy 3）。
        """
        with patch("subtitle_detect._get_soft_subtitle_tracks", return_value=[]), \
             patch("subtitle_detect._get_video_duration", return_value=60.0), \
             patch("subtitle_detect._detect_language_from_ocr_regions",
                   return_value=(SubtitleStatus.EN, "en", 0.85)), \
             patch("subtitle_detect._detect_language_from_audio") as mock_whisper:

            result = detect_subtitle_status(dummy_video)

        mock_whisper.assert_not_called()
        status = result[0]
        assert status == SubtitleStatus.EN

    def test_soft_subtitle_en_does_not_fall_through_to_ocr_or_whisper(self, dummy_video):
        """
        Strategy 1 ffprobe 检测到英文软字幕时，
        OCR 和 Whisper 都不应被调用。
        """
        with patch("subtitle_detect._get_soft_subtitle_tracks",
                   return_value=[{"index": 0, "tags": {"language": "eng"}}]), \
             patch("subtitle_detect._get_video_duration", return_value=60.0), \
             patch("subtitle_detect._detect_language_from_ocr_regions") as mock_ocr, \
             patch("subtitle_detect._detect_language_from_audio") as mock_whisper:

            result = detect_subtitle_status(dummy_video)

        mock_ocr.assert_not_called()
        mock_whisper.assert_not_called()
        status = result[0]
        assert status == SubtitleStatus.EN
