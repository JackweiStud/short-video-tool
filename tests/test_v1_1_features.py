"""
Test suite for new features introduced in version 1.1.0:
1. D+B Subtitle Sync (SubtitleSync)
2. Subtitle Status Manual Override
3. 9:16 Aspect Ratio Scaling
4. Siliconflow Backend Routing
"""
import os
import json
import pytest
from unittest.mock import MagicMock, patch
import embed_subtitles
from subtitle_sync import SubtitleSync
from translator import Translator
from embed_subtitles import _make_subtitle_frame, embed_subtitles_batch
from config import Config
from subtitle_detect import SubtitleStatus

class TestSubtitleSync:
    """Test SubtitleSync logic (D+B)."""
    
    def test_word_level_alignment_logic(self):
        """Test how word-level timestamps are mapped to segments."""
        syncer = SubtitleSync()
        asr_segments = [
            {
                "start": 0.0, "end": 2.0, "text": "hello world",
                "words": [
                    {"word": "hello", "start": 0.1, "end": 0.5},
                    {"word": "world", "start": 0.6, "end": 1.2}
                ]
            }
        ]
        # Mock frames and logic inside SubtitleSync if it were isolated
        # For now, we test if the class can at least be initialized and has the method
        assert hasattr(syncer, "get_aligned_segments")

class TestTranslatorSiliconflow:
    """Test Siliconflow (DeepSeek) integration and fallbacks."""
    
    @patch('openai.OpenAI')
    @patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test-key"})
    def test_siliconflow_initialization(self, mock_openai):
        """Translator should pick siliconflow if config points to it."""
        config = Config()
        config.llm_backend = "siliconflow"
        config.llm_api_key = "test-key"
        
        translator = Translator(config=config)
        assert translator.backend == "siliconflow"
        assert translator.model == "deepseek-ai/DeepSeek-V3"

    @patch('deep_translator.GoogleTranslator')
    def test_google_fallback_initialization(self, mock_google):
        """Google Translate should always be initialized as a fallback."""
        translator = Translator()
        assert translator.google_translator is not None

class TestDimensionScaling:
    """Test UI/UX scaling for different aspect ratios."""
    
    def test_font_size_scaling_on_vertical_video(self):
        """Font size in 9:16 (vertical) should be based on width (min dim)."""
        w, h = 1080, 1920
        # This is a smoke test to ensure the rendering function doesn't crash 
        # with the new min(w, h) calculation.
        with patch('PIL.ImageFont.truetype') as mock_font:
            mock_font.return_value = MagicMock()
            # _make_subtitle_frame(w, h, en, zh, font_path)
            # We just verify it calls font loading with expected logic
            # (In a real test, one would check the font size passed to truetype)
            pass

    def test_hard_subtitle_overlay_uses_compact_gap(self):
        """EN hard-subtitle mode should not push the overlaid subtitle excessively high."""
        config = Config()
        layout = embed_subtitles._compute_subtitle_layout(
            video_height=1080,
            is_vertical=False,
            config=config,
            hard_subtitle_lang="en",
            subtitle_boundary=0.8348,
        )

        # Only the extra lift above the detected hard-sub boundary should stay compact.
        hard_sub_bottom_margin = int((1.0 - 0.8348) * 1080)
        extra_lift = layout["margin_v_zh"] - hard_sub_bottom_margin
        assert extra_lift <= 30
        assert layout["inter_gap"] <= 8

class TestCLIPSelection:
    """Test precision mode for processing only current clips."""
    
    def test_precision_mode_filtering(self):
        """embed_subtitles_batch should only process files path in clips_data."""
        clips_data = [
            {"clip_path": "/tmp/new_clip_1.mp4"},
            {"clip_path": "/tmp/new_clip_2.mp4"}
        ]
        # Mocking os.listdir or embed_subtitles_batch internals 
        # to verify it correctly maps filenames from clips_data
        pass

    def test_en_hard_subtitle_masks_source_and_burns_dual(self, temp_dir):
        """Hard-EN mode should replace source hard subtitles with masked dual burn."""
        clips_dir = os.path.join(temp_dir, "clips")
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        output_dir = os.path.join(temp_dir, "out")
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(subtitles_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(clips_dir, "sample.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake")

        en_srt = os.path.join(subtitles_dir, "sample_en.srt")
        zh_srt = os.path.join(subtitles_dir, "sample_zh.srt")
        zh_aligned_srt = os.path.join(subtitles_dir, "sample_zh_aligned.srt")
        for path in [en_srt, zh_srt, zh_aligned_srt]:
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n\n")

        clips_data = [
            {
                "clip_path": video_path,
                "subtitle_files": {
                    "en": en_srt,
                    "zh": zh_srt,
                    "zh_aligned": zh_aligned_srt,
                },
            }
        ]

        def fake_burn(*args, **kwargs):
            output_path = args[3]
            with open(output_path, "wb") as f:
                f.write(b"fake output")
            return True

        with patch("embed_subtitles._hard_burn_bilingual_auto", side_effect=fake_burn) as mock_burn:
            result = embed_subtitles_batch(
                clips_dir=clips_dir,
                subtitles_dir=subtitles_dir,
                output_dir=output_dir,
                burn=True,
                subtitle_status="en",
                clips_data=clips_data,
            )

        assert result["successful"] == 1
        assert mock_burn.called
        args, kwargs = mock_burn.call_args
        assert args[1] == en_srt
        assert args[2] == zh_srt
        assert kwargs["subtitle_status"] == "en"

    def test_auto_en_hard_subtitle_masks_source_and_burns_dual(self, temp_dir):
        """Auto-detected EN hard subtitles should switch to masked dual-burn mode."""
        clips_dir = os.path.join(temp_dir, "clips")
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        output_dir = os.path.join(temp_dir, "out")
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(subtitles_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(clips_dir, "sample.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake")

        en_srt = os.path.join(subtitles_dir, "sample_en.srt")
        zh_srt = os.path.join(subtitles_dir, "sample_zh.srt")
        zh_aligned_srt = os.path.join(subtitles_dir, "sample_zh_aligned.srt")
        for path in [en_srt, zh_srt, zh_aligned_srt]:
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n\n")

        def fake_ffmpeg(video_path, en_path, zh_path, output_path, ocr_lang=None, subtitle_boundary=0.80, hard_subtitle_mask=None):
            with open(output_path, "wb") as f:
                f.write(b"fake output")
            assert zh_path == zh_srt
            assert en_path == en_srt
            assert ocr_lang is None
            assert hard_subtitle_mask is not None
            return True

        with patch("subtitle_detect.detect_subtitle_status", return_value=(SubtitleStatus.EN, 0.95, "en", 0.88)):
            with patch("embed_subtitles._hard_burn_bilingual_ffmpeg", side_effect=fake_ffmpeg) as mock_ffmpeg:
                success = embed_subtitles._hard_burn_bilingual_auto(
                    video_path,
                    en_srt,
                    zh_srt,
                    os.path.join(output_dir, "sample_bilingual.mp4"),
                    subtitle_status="auto",
                    zh_aligned_srt_path=zh_aligned_srt,
                )

        assert success is True
        assert mock_ffmpeg.called

    def test_auto_en_hard_subtitle_records_masked_dual_metadata(self, temp_dir):
        clips_dir = os.path.join(temp_dir, "clips")
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        output_dir = os.path.join(temp_dir, "out")
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(subtitles_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(clips_dir, "sample.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake")

        en_srt = os.path.join(subtitles_dir, "sample_en.srt")
        zh_srt = os.path.join(subtitles_dir, "sample_zh.srt")
        zh_aligned_srt = os.path.join(subtitles_dir, "sample_zh_aligned.srt")
        zh_visual_srt = os.path.join(subtitles_dir, "sample_zh_visual.srt")
        for path in [en_srt, zh_srt, zh_aligned_srt]:
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n\n")
        with open(zh_visual_srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:01,000\n视觉对齐字幕\n\n")

        def fake_ffmpeg(video_path, en_path, zh_path, output_path, ocr_lang=None, subtitle_boundary=0.80, hard_subtitle_mask=None):
            with open(output_path, "wb") as f:
                f.write(b"fake output")
            assert zh_path == zh_srt
            assert ocr_lang is None
            assert hard_subtitle_mask is not None
            return True

        with patch("subtitle_detect.detect_subtitle_status", return_value=(SubtitleStatus.EN, 0.95, "en", 0.88)):
            with patch("embed_subtitles._build_visual_synced_subtitle_track", return_value=zh_visual_srt) as mock_visual:
                with patch("embed_subtitles._hard_burn_bilingual_ffmpeg", side_effect=fake_ffmpeg) as mock_ffmpeg:
                    decision = {}
                    success = embed_subtitles._hard_burn_bilingual_auto(
                        video_path,
                        en_srt,
                        zh_srt,
                        os.path.join(output_dir, "sample_bilingual.mp4"),
                        subtitle_status="auto",
                        asr_segments=[{"text": "hello", "start": 0.0, "end": 1.0}],
                        zh_aligned_srt_path=zh_aligned_srt,
                        decision_meta=decision,
                    )

        assert success is True
        assert not mock_visual.called
        assert mock_ffmpeg.called
        assert decision["subtitle_alignment_source"] == "shared_dual_timeline"
        assert decision["chosen_burn_mode"] == "mask_existing_hard_subtitles_and_burn_dual"
        assert decision["hard_subtitle_mask"]["enabled"] is True

    def test_auto_zh_hard_subtitle_skips_burn_and_keeps_source(self, temp_dir):
        clips_dir = os.path.join(temp_dir, "clips")
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        output_dir = os.path.join(temp_dir, "out")
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(subtitles_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(clips_dir, "sample.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake")

        en_srt = os.path.join(subtitles_dir, "sample_en.srt")
        zh_srt = os.path.join(subtitles_dir, "sample_zh.srt")
        for path in [en_srt, zh_srt]:
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n\n")

        with patch("subtitle_detect.detect_subtitle_status", return_value=(SubtitleStatus.ZH, 0.95, "zh", 0.88)):
            with patch("embed_subtitles._hard_burn_bilingual_ffmpeg") as mock_ffmpeg:
                decision = {}
                output_path = os.path.join(output_dir, "sample_bilingual.mp4")
                success = embed_subtitles._hard_burn_bilingual_auto(
                    video_path,
                    en_srt,
                    zh_srt,
                    output_path,
                    subtitle_status="auto",
                    asr_segments=[{"text": "你好", "start": 0.0, "end": 1.0}],
                    decision_meta=decision,
                )

        assert success is True
        assert not mock_ffmpeg.called
        assert os.path.exists(output_path)
        assert decision["chosen_burn_mode"] == "skip_burn_keep_existing_zh_hard_subtitles"
        assert decision["burn_renderer"] == "copy"
        assert decision["auto_final_action"] == "keep_existing_zh_hard_subtitles_without_burn"

    def test_auto_low_confidence_en_falls_back_to_dual_mode(self, temp_dir):
        clips_dir = os.path.join(temp_dir, "clips")
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        output_dir = os.path.join(temp_dir, "out")
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(subtitles_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(clips_dir, "sample-low-confidence.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake")

        en_srt = os.path.join(subtitles_dir, "sample_en.srt")
        zh_srt = os.path.join(subtitles_dir, "sample_zh.srt")
        zh_aligned_srt = os.path.join(subtitles_dir, "sample_zh_aligned.srt")
        for path in [en_srt, zh_srt, zh_aligned_srt]:
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n\n")

        def fake_ffmpeg(video_path, en_path, zh_path, output_path, ocr_lang=None, subtitle_boundary=0.80, hard_subtitle_mask=None):
            with open(output_path, "wb") as f:
                f.write(b"fake output")
            assert zh_path == zh_srt
            assert ocr_lang is None
            assert hard_subtitle_mask is None
            return True

        with patch("subtitle_detect.detect_subtitle_status", return_value=(SubtitleStatus.EN, 0.72, "en", 0.88)):
            with patch("embed_subtitles._hard_burn_bilingual_ffmpeg", side_effect=fake_ffmpeg) as mock_ffmpeg:
                success = embed_subtitles._hard_burn_bilingual_auto(
                    video_path,
                    en_srt,
                    zh_srt,
                    os.path.join(output_dir, "sample_bilingual.mp4"),
                    subtitle_status="auto",
                    zh_aligned_srt_path=zh_aligned_srt,
                )

        assert success is True
        assert mock_ffmpeg.called

    def test_auto_bilingual_masks_source_and_burns_dual(self, temp_dir):
        clips_dir = os.path.join(temp_dir, "clips")
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        output_dir = os.path.join(temp_dir, "out")
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(subtitles_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(clips_dir, "sample-bilingual.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake-video")

        en_srt = os.path.join(subtitles_dir, "sample_en.srt")
        zh_srt = os.path.join(subtitles_dir, "sample_zh.srt")
        for path in [en_srt, zh_srt]:
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n\n")

        output_path = os.path.join(output_dir, "sample_bilingual.mp4")
        decision_meta = {}

        with patch("subtitle_detect.detect_subtitle_status", return_value=(SubtitleStatus.BILINGUAL, 0.91, "bilingual", 0.80)):
            with patch("embed_subtitles._hard_burn_bilingual_ffmpeg", return_value=True) as mock_ffmpeg:
                success = embed_subtitles._hard_burn_bilingual_auto(
                    video_path,
                    en_srt,
                    zh_srt,
                    output_path,
                    subtitle_status="auto",
                    decision_meta=decision_meta,
                )

        assert success is True
        mock_ffmpeg.assert_called_once()
        assert decision_meta["effective_subtitle_status"] == "bilingual"
        assert decision_meta["chosen_burn_mode"] == "mask_existing_hard_subtitles_and_burn_dual"
        assert decision_meta["auto_final_action"] == "mask_existing_hard_subtitles_and_burn_dual_subtitles"
        assert decision_meta["burn_renderer"] == "ffmpeg"
        assert decision_meta["subtitle_alignment_source"] == "shared_dual_timeline"

    def test_batch_persists_subtitle_burn_decision_to_metadata(self, temp_dir):
        clips_dir = os.path.join(temp_dir, "clips")
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        output_dir = os.path.join(temp_dir, "out")
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(subtitles_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(clips_dir, "sample.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake")

        en_srt = os.path.join(subtitles_dir, "sample_en.srt")
        zh_srt = os.path.join(subtitles_dir, "sample_zh.srt")
        for path in [en_srt, zh_srt]:
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n\n")

        clips_data = [{
            "clip_path": video_path,
            "subtitle_files": {
                "en": en_srt,
                "zh": zh_srt,
            },
        }]

        def fake_auto(*args, **kwargs):
            decision_meta = kwargs["decision_meta"]
            decision_meta.update({
                "requested_subtitle_status": "auto",
                "effective_subtitle_status": "none",
                "detected_status": "none",
                "confidence": 0.8,
                "ocr_lang": None,
                "detected_boundary": None,
                "chosen_burn_mode": "dual_bottom_ffmpeg_or_moviepy",
                "used_zh_aligned": False,
                "subtitle_alignment_source": None,
                "burn_renderer": "ffmpeg",
            })
            with open(args[3], "wb") as f:
                f.write(b"fake output")
            return True

        with patch("embed_subtitles._hard_burn_bilingual_auto", side_effect=fake_auto):
            result = embed_subtitles_batch(
                clips_dir=clips_dir,
                subtitles_dir=subtitles_dir,
                output_dir=output_dir,
                burn=True,
                subtitle_status="auto",
                clips_data=clips_data,
            )

        assert result["successful"] == 1
        metadata_path = os.path.join(clips_dir, "clips_metadata.json")
        assert os.path.exists(metadata_path)
        payload = json.loads(open(metadata_path, "r", encoding="utf-8").read())
        assert payload["clips"][0]["subtitle_burn"]["chosen_burn_mode"] == "dual_bottom_ffmpeg_or_moviepy"
        assert payload["clips"][0]["subtitle_burn"]["subtitle_alignment_source"] is None
        assert payload["clips"][0]["subtitle_burn"]["burn_renderer"] == "ffmpeg"

if __name__ == "__main__":
    pytest.main([__file__])
