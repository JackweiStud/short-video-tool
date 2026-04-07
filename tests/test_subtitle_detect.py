import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
import json
from pathlib import Path

# Adjust import path if necessary based on your project structure
from subtitle_detect import (
    SubtitleStatus,
    _build_ocr_sample_timestamps,
    _detect_language_from_text,
    _detect_language_from_ocr_regions,
    _get_video_duration,
    _extract_frame,
    _transcribe_frame_region_with_whisper,
    _detect_hard_subtitle_regions,
    _get_soft_subtitle_tracks,
    detect_subtitle_status
)

# Mock _transcribe_frame_region_with_whisper for predictable results in tests
@pytest.fixture
def mock_whisper_transcribe():
    with patch('subtitle_detect._transcribe_frame_region_with_whisper') as mock:
        yield mock

# Fixture for a dummy video file
@pytest.fixture
def dummy_video_path():
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"dummy video content")
        dummy_path = Path(f.name)
    yield dummy_path
    os.unlink(dummy_path)

# Mock _extract_frame to simulate frame extraction without actual ffmpeg call
@pytest.fixture(autouse=True)
def mock_extract_frame():
    with patch('subtitle_detect._extract_frame') as mock:
        mock.return_value = True # Assume frame extraction is always successful
        # Create a dummy image file for PIL to open
        mock_image_path = Path(tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name)
        from PIL import Image
        img = Image.new('RGB', (1920, 1080), color = 'red')
        img.save(mock_image_path)
        mock.side_effect = lambda video_path, timestamp, output_path: Path(output_path).write_bytes(mock_image_path.read_bytes())
        yield mock
        os.unlink(mock_image_path)

# --- Test _detect_language_from_text ---

class TestDetectLanguageFromText:
    def test_pure_chinese(self):
        assert _detect_language_from_text("你好世界") == SubtitleStatus.ZH

    def test_pure_english(self):
        assert _detect_language_from_text("Hello World") == SubtitleStatus.EN

    def test_bilingual(self):
        assert _detect_language_from_text("你好 Hello 世界 World") == SubtitleStatus.BILINGUAL

    def test_empty_string(self):
        assert _detect_language_from_text("") == SubtitleStatus.EN # Default fallback

    def test_boundary_60_percent_zh(self):
        # 6 Chinese chars, 4 English chars -> 60% Chinese
        text = "中中中中中中eeee"
        assert _detect_language_from_text(text) == SubtitleStatus.ZH

    def test_boundary_59_percent_bilingual(self):
        # 5 Chinese chars, 4 English chars -> 55.5% Chinese (approx) -> Bilingual
        text = "中中中中中eeee"
        assert _detect_language_from_text(text) == SubtitleStatus.BILINGUAL

    def test_boundary_10_percent_en(self):
        # 1 Chinese char, 9 English chars -> 10% Chinese
        text = "中eeeeeeeee"
        assert _detect_language_from_text(text) == SubtitleStatus.EN

    def test_boundary_11_percent_bilingual(self):
        # 1 Chinese char, 8 English chars -> 11.1% Chinese (approx) -> Bilingual
        text = "中eeeeeeee"
        assert _detect_language_from_text(text) == SubtitleStatus.BILINGUAL

    def test_punctuation_and_numbers(self):
        assert _detect_language_from_text("你好, 123! Hello, 456.") == SubtitleStatus.BILINGUAL

    def test_mixed_uncommon_chars(self):
        # Mostly English with some other unicode, still mostly English
        assert _detect_language_from_text("Hello World £€¥αβγ") == SubtitleStatus.EN

# --- Test _get_video_duration ---

class TestGetVideoDuration:
    @patch('subprocess.run')
    def test_success(self, mock_run, dummy_video_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="120.0\n")
        assert _get_video_duration(str(dummy_video_path)) == 120.0

    @patch('subprocess.run')
    def test_failure(self, mock_run, dummy_video_path):
        mock_run.return_value = MagicMock(returncode=1, stderr="Error\n")
        assert _get_video_duration(str(dummy_video_path)) == 0.0

# --- Test _get_soft_subtitle_tracks ---

class TestGetSoftSubtitleTracks:
    @patch('subprocess.run')
    def test_no_tracks(self, mock_run, dummy_video_path):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"streams": []}')
        assert _get_soft_subtitle_tracks(str(dummy_video_path)) == []

    @patch('subprocess.run')
    def test_english_track(self, mock_run, dummy_video_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({
            "streams": [{"codec_type": "subtitle", "tags": {"language": "eng", "title": "English"}}]
        }))
        tracks = _get_soft_subtitle_tracks(str(dummy_video_path))
        assert len(tracks) == 1
        assert tracks[0]["tags"]["language"] == "eng"

    @patch('subprocess.run')
    def test_chinese_track(self, mock_run, dummy_video_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({
            "streams": [{"codec_type": "subtitle", "tags": {"language": "zho", "title": "Chinese"}}]
        }))
        tracks = _get_soft_subtitle_tracks(str(dummy_video_path))
        assert len(tracks) == 1
        assert tracks[0]["tags"]["language"] == "zho"

    @patch('subprocess.run')
    def test_bilingual_tracks(self, mock_run, dummy_video_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({
            "streams": [
                {"codec_type": "subtitle", "tags": {"language": "eng", "title": "English"}},
                {"codec_type": "subtitle", "tags": {"language": "zho", "title": "Chinese"}}
            ]
        }))
        tracks = _get_soft_subtitle_tracks(str(dummy_video_path))
        assert len(tracks) == 2

    @patch('subprocess.run')
    def test_json_error(self, mock_run, dummy_video_path):
        mock_run.return_value = MagicMock(returncode=0, stdout='invalid json')
        assert _get_soft_subtitle_tracks(str(dummy_video_path)) == []

# --- Test _detect_hard_subtitle_regions ---

class TestDetectHardSubtitleRegions:
    @patch('subtitle_detect._get_video_duration', return_value=100.0)
    def test_all_regions_english_by_default(self, mock_duration, mock_whisper_transcribe, mock_extract_frame, dummy_video_path):
        mock_whisper_transcribe.return_value = "" # Simulate no text found, falls back to EN
        regions = _detect_hard_subtitle_regions(str(dummy_video_path), sample_count=1)
        assert len(regions) == 3 # All 3 regions have content due to red image, and "" -> EN
        assert all(r[3] == SubtitleStatus.EN for r in regions)

    @patch('subtitle_detect._get_video_duration', return_value=100.0)
    def test_single_region_english(self, mock_duration, mock_whisper_transcribe, mock_extract_frame, dummy_video_path):
        def side_effect(frame_path, x1, y1, x2, y2):
            if y1 / 1080 >= 0.8: return "Hello World" # Bottom
            return "" # Middle, Top (will resolve to EN)
        mock_whisper_transcribe.side_effect = side_effect
        regions = _detect_hard_subtitle_regions(str(dummy_video_path), sample_count=1)
        assert len(regions) == 3 # All 3 regions detected due to red image
        assert regions[0][3] == SubtitleStatus.EN # Bottom (Hello World)
        assert regions[1][3] == SubtitleStatus.EN # Middle (empty string)
        assert regions[2][3] == SubtitleStatus.EN # Top (empty string)

    @patch('subtitle_detect._get_video_duration', return_value=100.0)
    def test_single_region_chinese(self, mock_duration, mock_whisper_transcribe, mock_extract_frame, dummy_video_path):
        def side_effect(frame_path, x1, y1, x2, y2):
            if y1 / 1080 >= 0.8: return "你好世界" # Bottom
            return "" # Middle, Top (will resolve to EN)
        mock_whisper_transcribe.side_effect = side_effect
        regions = _detect_hard_subtitle_regions(str(dummy_video_path), sample_count=1)
        assert len(regions) == 3 # All 3 regions detected due to red image
        assert regions[0][3] == SubtitleStatus.ZH # Bottom (你好世界)
        assert regions[1][3] == SubtitleStatus.EN # Middle (empty string)
        assert regions[2][3] == SubtitleStatus.EN # Top (empty string)

    @patch('subtitle_detect._get_video_duration', return_value=100.0)
    def test_multiple_regions_bilingual(self, mock_duration, mock_whisper_transcribe, mock_extract_frame, dummy_video_path):
        def side_effect(frame_path, x1, y1, x2, y2):
            if y1 / 1080 >= 0.8: return "你好" # Bottom (ZH)
            if y1 / 1080 >= 0.4: return "Hello" # Middle (EN)
            return "" # Top (empty string, defaults to EN)
        mock_whisper_transcribe.side_effect = side_effect
        regions = _detect_hard_subtitle_regions(str(dummy_video_path), sample_count=1)
        assert len(regions) == 3 # Bottom, Middle, Top due to red image
        assert {r[3] for r in regions} == {SubtitleStatus.ZH, SubtitleStatus.EN}

    @patch('subtitle_detect._get_video_duration', return_value=0.0)
    def test_zero_duration_video(self, mock_duration, mock_whisper_transcribe, mock_extract_frame, dummy_video_path):
        regions = _detect_hard_subtitle_regions(str(dummy_video_path), sample_count=1)
        assert len(regions) == 0

# --- Test detect_subtitle_status (Integration-like) ---

class TestDetectSubtitleStatus:
    def test_video_not_found(self):
        status, confidence, _, _detected_boundary = detect_subtitle_status("non_existent_video.mp4")
        assert status == SubtitleStatus.UNCERTAIN
        assert confidence == 0.0

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.NONE, None, None))
    def test_no_subtitles_detected(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.NONE
        assert confidence == 0.0

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks')
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.NONE, None, None))
    def test_soft_english_subtitles(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        mock_soft.return_value = [{"codec_type": "subtitle", "tags": {"language": "eng"}}]
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.EN
        assert confidence == 1.0
        mock_ocr.assert_not_called()  # Should short-circuit after soft sub detection

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks')
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.NONE, None, None))
    def test_soft_chinese_subtitles(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        mock_soft.return_value = [{"codec_type": "subtitle", "tags": {"language": "zho"}}]
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.ZH
        assert confidence == 1.0
        mock_ocr.assert_not_called()

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks')
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.NONE, None, None))
    def test_soft_bilingual_subtitles(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        mock_soft.return_value = [
            {"codec_type": "subtitle", "tags": {"language": "eng"}},
            {"codec_type": "subtitle", "tags": {"language": "zho"}}
        ]
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.BILINGUAL
        assert confidence == 1.0
        mock_ocr.assert_not_called()

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[{"codec_type": "subtitle", "tags": {"language": "unknown"}}])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.EN, 'en', None))
    def test_soft_uncertain_falls_back_to_hard_english(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        # Unknown soft subtitle lang → falls through to OCR which detects EN
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.EN
        assert confidence > 0
        mock_ocr.assert_called_once()

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.EN, 'en', None))
    def test_hard_english_subtitles_only(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.EN
        assert confidence > 0

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.ZH, 'zh', None))
    def test_hard_chinese_subtitles_only(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.ZH
        assert confidence > 0

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.BILINGUAL, 'zh', None))
    def test_hard_bilingual_subtitles(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        # OCR detects BILINGUAL hard subtitles
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.BILINGUAL
        assert confidence > 0

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.UNCERTAIN, None, None))
    def test_hard_uncertain_subtitles(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        # OCR returns UNCERTAIN → falls through to audio Whisper (which also fails on dummy) → NONE
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status in (SubtitleStatus.UNCERTAIN, SubtitleStatus.NONE)

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.NONE, None, None))
    def test_hard_low_density_no_subtitle(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        # OCR finds no persistent subtitle regions
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.NONE


class TestSubtitleOcrSampling:
    def test_ocr_sampling_prioritizes_early_video(self):
        timestamps = _build_ocr_sample_timestamps(221.0, 5)
        assert timestamps[0] <= 5.1
        assert any(timestamp <= 30.0 for timestamp in timestamps)
        assert timestamps == sorted(timestamps)

    @patch('subtitle_detect._get_video_duration', return_value=221.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.NONE, None, None))
    @patch('subtitle_detect._detect_language_from_audio', return_value='en')
    def test_detect_subtitle_status_forwards_sample_count_to_ocr(
        self,
        mock_audio,
        mock_ocr,
        mock_soft,
        mock_dur,
        dummy_video_path,
    ):
        detect_subtitle_status(str(dummy_video_path), sample_count=7)
        mock_ocr.assert_called_once()
        _, kwargs = mock_ocr.call_args
        assert kwargs["sample_count"] == 7


class TestSubtitleStreamScoreFilter:
    @patch("subtitle_detect._build_ocr_sample_timestamps", return_value=[5.0, 10.0, 15.0])
    @patch("subtitle_detect._ocr_region_with_vision_bbox", return_value=[])
    def test_ocr_region_detection_enforces_minimum_three_samples(
        self,
        mock_ocr_bbox,
        mock_timestamps,
        dummy_video_path,
    ):
        status, lang, subtitle_top_ratio, confidence = _detect_language_from_ocr_regions(
            str(dummy_video_path), duration=18.0, sample_count=1
        )

        mock_timestamps.assert_called_once_with(18.0, 3)
        assert status == SubtitleStatus.NONE
        assert lang is None
        assert subtitle_top_ratio is None
        assert confidence == 0.0

    @patch("subtitle_detect._build_ocr_sample_timestamps", return_value=[5.0, 10.0, 15.0, 20.0, 25.0])
    @patch("subtitle_detect._ocr_region_with_vision_bbox")
    def test_fixed_short_en_watermark_is_filtered_to_none(
        self,
        mock_ocr_bbox,
        mock_timestamps,
        dummy_video_path,
    ):
        mock_ocr_bbox.side_effect = [
            [],
            [("ALL-IN", 0.10, 0.18, 0.35, 0.65)],
            [],
            [("ALL-IN", 0.10, 0.18, 0.35, 0.65)],
            [],
            [("ALL-IN", 0.10, 0.18, 0.35, 0.65)],
            [],
            [("ALL-IN", 0.10, 0.18, 0.35, 0.65)],
            [],
            [("ALL-IN", 0.10, 0.18, 0.35, 0.65)],
        ]

        status, lang, subtitle_top_ratio, confidence = _detect_language_from_ocr_regions(
            str(dummy_video_path), duration=20.0, sample_count=5
        )

        assert status == SubtitleStatus.NONE
        assert lang is None
        assert subtitle_top_ratio is None
        assert confidence == 0.0

    @patch("subtitle_detect._build_ocr_sample_timestamps", return_value=[5.0, 10.0, 15.0, 20.0, 25.0])
    @patch("subtitle_detect._ocr_region_with_vision_bbox")
    def test_changing_english_subtitle_stream_is_detected_as_en(
        self,
        mock_ocr_bbox,
        mock_timestamps,
        dummy_video_path,
    ):
        subtitle_lines = [
            "We have to move faster than the market today",
            "This quarter the team shipped a major update",
            "Every investor is watching the margin story now",
            "The product roadmap is finally starting to land",
            "Execution matters more than promises at this stage",
        ]

        side_effect = []
        for line in subtitle_lines:
            side_effect.extend([
                [],
                [(line, 0.10, 0.18, 0.20, 0.80)],
            ])
        mock_ocr_bbox.side_effect = side_effect

        status, lang, subtitle_top_ratio, confidence = _detect_language_from_ocr_regions(
            str(dummy_video_path), duration=20.0, sample_count=5
        )

        assert status == SubtitleStatus.EN
        assert lang == "en"
        assert subtitle_top_ratio is not None
        assert confidence >= 0.85

    @patch("subtitle_detect._build_ocr_sample_timestamps", return_value=[5.0, 10.0, 15.0, 20.0, 25.0])
    @patch("subtitle_detect._ocr_region_with_vision_bbox")
    def test_ui_heavy_english_bottom_text_is_rejected(
        self,
        mock_ocr_bbox,
        mock_timestamps,
        dummy_video_path,
    ):
        ui_lines = [
            "Google AI Edge Gallery output preview screen",
            "Install button open gallery output panel",
            "Preview screen output in edge gallery panel",
            "Gallery install output screen preview",
            "Open the output panel in the edge gallery",
        ]

        side_effect = []
        for line in ui_lines:
            side_effect.extend([
                [],
                [(line, 0.10, 0.18, 0.20, 0.80)],
            ])
        mock_ocr_bbox.side_effect = side_effect

        status, lang, subtitle_top_ratio, confidence = _detect_language_from_ocr_regions(
            str(dummy_video_path), duration=20.0, sample_count=5
        )

        assert status == SubtitleStatus.NONE
        assert lang is None
        assert subtitle_top_ratio is None
        assert confidence == 0.0

    @patch("subtitle_detect._build_ocr_sample_timestamps", return_value=[5.0, 10.0, 15.0, 20.0, 25.0])
    @patch("subtitle_detect._ocr_region_with_vision_bbox")
    def test_bilingual_candidate_without_continuous_bottom_stream_downgrades_to_zh(
        self,
        mock_ocr_bbox,
        mock_timestamps,
        dummy_video_path,
    ):
        side_effect = []
        bottom_lines = [
            "你觉得我是吗",
            "What's your name 你叫什么",
            "她说这个名字",
            "My name is Debbie 我叫 Debbie",
            "你还记得吗",
        ]
        for line in bottom_lines:
            side_effect.extend([
                [],
                [(line, 0.10, 0.18, 0.20, 0.80)],
            ])
        mock_ocr_bbox.side_effect = side_effect

        status, lang, subtitle_top_ratio, confidence = _detect_language_from_ocr_regions(
            str(dummy_video_path), duration=20.0, sample_count=5
        )

        assert status == SubtitleStatus.ZH
        assert lang == "zh"
        assert subtitle_top_ratio is not None
        assert confidence > 0

    @patch("subtitle_detect._build_ocr_sample_timestamps", return_value=[5.0, 10.0, 15.0, 20.0, 25.0])
    @patch("subtitle_detect._ocr_region_with_vision_bbox")
    def test_continuous_bottom_bilingual_stream_is_kept_as_bilingual(
        self,
        mock_ocr_bbox,
        mock_timestamps,
        dummy_video_path,
    ):
        side_effect = []
        bilingual_lines = [
            "你好 我叫 Debbie 你今天叫什么名字 What's your name today",
            "你好 我是 Debbie 你今天感觉如何 How are you today",
            "你好 Debbie 你来自哪里 where are you from today",
            "你好 Debbie 你再说一次名字 tell me your name today",
            "你好 Debbie 你今天能重复一下吗 can you repeat that today",
        ]
        for line in bilingual_lines:
            side_effect.extend([
                [],
                [(line, 0.10, 0.18, 0.20, 0.80)],
            ])
        mock_ocr_bbox.side_effect = side_effect

        status, lang, subtitle_top_ratio, confidence = _detect_language_from_ocr_regions(
            str(dummy_video_path), duration=20.0, sample_count=5
        )

        assert status == SubtitleStatus.BILINGUAL
        assert lang == "bilingual"
        assert subtitle_top_ratio is not None
        assert confidence > 0

    @patch('subtitle_detect._get_video_duration', return_value=60.0)
    @patch('subtitle_detect._get_soft_subtitle_tracks', return_value=[])
    @patch('subtitle_detect._detect_language_from_ocr_regions', return_value=(SubtitleStatus.EN, 'en', 0.88, 0.72))
    def test_hard_english_subtitles_preserve_ocr_confidence(self, mock_ocr, mock_soft, mock_dur, dummy_video_path):
        status, confidence, _, _detected_boundary = detect_subtitle_status(str(dummy_video_path))
        assert status == SubtitleStatus.EN
        assert confidence == pytest.approx(0.72)
