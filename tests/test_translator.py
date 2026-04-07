"""
Test cases for Translator module
"""
import os
import pytest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from translator import (
    Translator,
    _extract_json_array_payload,
    _split_long_segments,
    _translation_has_language_drift,
    _translation_has_meta_output,
)


class TestSplitLongSegments:
    """Unit tests for _split_long_segments helper"""

    def test_short_en_segment_unchanged(self):
        """Segments within limit should pass through as-is"""
        segs = [{'start': 0.0, 'end': 2.0, 'text': 'Hello world'}]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang="en")
        assert len(result) == 1
        assert result[0]['text'] == 'Hello world'
        assert result[0]['start'] == 0.0
        assert result[0]['end'] == 2.0

    def test_long_en_segment_split(self):
        """Long EN segment should be split into multiple short segments"""
        text = 'This is a really long sentence that definitely exceeds forty two characters in length.'
        segs = [{'start': 0.0, 'end': 4.0, 'text': text}]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang="en")
        assert len(result) > 1
        for seg in result:
            assert len(seg['text']) <= 42 + 10  # allow small overshoot on single words

    def test_time_span_preserved(self):
        """Total time span of split segments should equal original"""
        text = 'First sentence here. Second sentence there. Third sentence everywhere.'
        segs = [{'start': 1.0, 'end': 7.0, 'text': text}]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang="en")
        assert abs(result[0]['start'] - 1.0) < 0.001
        assert abs(result[-1]['end'] - 7.0) < 0.001

    def test_timestamps_monotonic(self):
        """Timestamps must be strictly non-decreasing"""
        text = 'Word one. Word two. Word three. Word four. Word five. Word six. Word seven.'
        segs = [{'start': 0.0, 'end': 6.0, 'text': text}]
        result = _split_long_segments(segs, max_en_chars=20, max_zh_chars=22, lang="en")
        for i in range(len(result) - 1):
            assert result[i]['end'] <= result[i + 1]['start'] + 0.001

    def test_short_zh_segment_unchanged(self):
        """Short ZH segment should pass through unchanged"""
        segs = [{'start': 0.0, 'end': 2.0, 'text': '这是短句'}]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang="zh")
        assert len(result) == 1
        assert result[0]['text'] == '这是短句'

    def test_long_zh_segment_split(self):
        """Long ZH segment should be split"""
        text = '这是一个很长的中文字幕，超过了二十二个字符的限制，需要被正确地断开。'
        segs = [{'start': 0.0, 'end': 3.0, 'text': text}]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang="zh")
        assert len(result) > 1
        for seg in result:
            assert len(seg['text']) <= 22 + 5  # allow small overshoot

    def test_multiple_segments_each_processed(self):
        """Each segment in a list should be independently processed"""
        segs = [
            {'start': 0.0, 'end': 2.0, 'text': 'Short text'},
            {'start': 2.0, 'end': 6.0, 'text': 'This is a much longer sentence that needs splitting up properly.'},
        ]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang="en")
        assert len(result) >= 2
        # First segment should still be there unchanged
        assert result[0]['text'] == 'Short text'

    def test_empty_text_skipped(self):
        """Segments with empty text should be preserved as-is"""
        segs = [{'start': 0.0, 'end': 1.0, 'text': ''}]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang="en")
        assert len(result) == 1

    def test_split_prefers_punctuation_boundary(self):
        """Split should prefer punctuation boundaries over arbitrary word breaks"""
        text = 'First clause, second clause. Third clause here.'
        segs = [{'start': 0.0, 'end': 3.0, 'text': text}]
        result = _split_long_segments(segs, max_en_chars=30, max_zh_chars=22, lang="en")
        # At least one chunk should end with punctuation
        ends_with_punct = any(seg['text'].rstrip()[-1] in '.!?,;' for seg in result if seg['text'].rstrip())
        assert ends_with_punct


class TestTranslatorFunctionality:
    """Test Translator functionality"""

    def test_translator_initialization(self):
        """Test translator can be initialized"""
        translator = Translator()
        assert translator is not None

    @pytest.mark.slow
    def test_translate_clips_success(self, sample_clips_metadata, temp_dir):
        """Test successful translation of clips"""
        # Create a temporary clips metadata file
        metadata_path = os.path.join(temp_dir, "clips_metadata.json")
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(sample_clips_metadata, f)

        translator = Translator()
        result = translator.translate_clips(metadata_path, output_dir=temp_dir)

        assert result is not None
        assert 'clips' in result
        assert len(result['clips']) > 0

    @pytest.mark.slow
    def test_subtitle_files_generated(self, temp_dir):
        """Test subtitle files are generated"""
        # Use real clips metadata if available
        clips_metadata_path = "clips/clips_metadata.json"
        if not os.path.exists(clips_metadata_path):
            pytest.skip("Clips metadata not available")

        translator = Translator()
        result = translator.translate_clips(clips_metadata_path, output_dir=temp_dir)

        if result and result['clips']:
            for clip in result['clips']:
                assert 'subtitle_files' in clip
                assert 'original' in clip['subtitle_files']
                assert 'zh' in clip['subtitle_files']
                assert 'en' in clip['subtitle_files']

    @pytest.mark.slow
    def test_subtitle_files_non_empty(self, temp_dir):
        """Test subtitle files are non-empty"""
        clips_metadata_path = "clips/clips_metadata.json"
        if not os.path.exists(clips_metadata_path):
            pytest.skip("Clips metadata not available")

        translator = Translator()
        result = translator.translate_clips(clips_metadata_path, output_dir=temp_dir)

        if result and result['clips']:
            for clip in result['clips']:
                for lang, path in clip['subtitle_files'].items():
                    if os.path.exists(path):
                        assert os.path.getsize(path) > 0

    def test_translate_clips_generates_aligned_zh_subtitles(self, sample_clips_metadata, temp_dir):
        """Hard-EN workflows need a Chinese subtitle file that keeps EN timestamps."""
        metadata_path = os.path.join(temp_dir, "clips_metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(sample_clips_metadata, f)

        def fake_translate(self, segments, target_lang):
            translated = []
            for idx, seg in enumerate(segments):
                new_seg = dict(seg)
                if target_lang == "en":
                    new_seg["text"] = f"EN segment {idx}"
                else:
                    new_seg["text"] = "中文字幕需要保持和英文完全一致的时间轴。" + ("非常长" * 8 if idx == 0 else "")
                translated.append(new_seg)
            return translated

        translator = Translator()
        with patch.object(Translator, "_translate_segments", fake_translate):
            result = translator.translate_clips(metadata_path, output_dir=temp_dir)

        assert result is not None
        clip = result["clips"][0]
        assert "zh_aligned" in clip["subtitle_files"]
        assert os.path.exists(clip["subtitle_files"]["zh_aligned"])
        assert os.path.exists(clip["subtitle_files"]["zh"])

        def read_srt_times(path):
            times = []
            for block in Path(path).read_text(encoding="utf-8").strip().split("\n\n"):
                lines = block.splitlines()
                if len(lines) >= 2:
                    times.append(lines[1])
            return times

        def read_srt_blocks(path):
            return [
                block.splitlines()
                for block in Path(path).read_text(encoding="utf-8").strip().split("\n\n")
                if block.strip()
            ]

        en_times = read_srt_times(clip["subtitle_files"]["en"])
        zh_aligned_times = read_srt_times(clip["subtitle_files"]["zh_aligned"])
        zh_times = read_srt_times(clip["subtitle_files"]["zh"])
        zh_aligned_blocks = read_srt_blocks(clip["subtitle_files"]["zh_aligned"])

        assert en_times == zh_aligned_times
        assert len(zh_times) >= len(zh_aligned_times)
        assert any(len(block) >= 4 for block in zh_aligned_blocks)

    def test_detects_language_drift_for_wrong_target_language(self):
        assert _translation_has_language_drift(["完全正确。", "我们开始吧。"], "en") is True
        assert _translation_has_language_drift(["Exactly right.", "Where does that go?"], "zh") is True
        assert _translation_has_language_drift(["NBA", "NVIDIA"], "zh") is False

    def test_extract_json_array_payload_accepts_fenced_json(self):
        raw = '```json\n["第一行", "第二行"]\n```'
        assert _extract_json_array_payload(raw) == ["第一行", "第二行"]

    def test_meta_output_detection_rejects_placeholder_notes(self):
        assert _translation_has_meta_output(["正常字幕", "（此处应为空行，因原文第19行无实质内容）"]) is True
        assert _translation_has_meta_output(["正常字幕", "继续翻译"]) is False

    def test_siliconflow_prefers_json_array_protocol(self):
        translator = Translator()
        translator.backend = "siliconflow"
        translator.model = "fake-model"

        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='["完全正确。", "我们开始吧。"]'))]
        )
        translator._openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: response
                )
            )
        )

        result = translator._batch_translate_siliconflow(
            ["That's exactly right.", "Let's begin."],
            target_lang="zh",
        )
        assert result == ["完全正确。", "我们开始吧。"]

    def test_siliconflow_falls_back_when_english_target_returns_chinese(self):
        translator = Translator()
        translator.backend = "siliconflow"
        translator.model = "fake-model"

        bad_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="1. 完全正确。\n2. 我们开始吧。"))]
        )
        translator._openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: bad_response
                )
            )
        )

        with patch.object(
            Translator,
            "_batch_translate_google",
            side_effect=lambda texts, target_lang: [
                "Exactly right." if "exactly" in texts[0].lower() else "Let's begin."
            ],
        ) as mock_google:
            result = translator._batch_translate_siliconflow(
                ["That's exactly right.", "Let's begin."],
                target_lang="en",
            )

        assert result == ["Exactly right.", "Let's begin."]
        assert mock_google.called

    def test_siliconflow_falls_back_when_chinese_target_returns_english(self):
        translator = Translator()
        translator.backend = "siliconflow"
        translator.model = "fake-model"

        bad_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="1. Exactly right.\n2. Where should that go?"))]
        )
        translator._openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: bad_response
                )
            )
        )

        with patch.object(
            Translator,
            "_batch_translate_google",
            side_effect=lambda texts, target_lang: [
                "完全正确。" if "exactly" in texts[0].lower() else "那个该放哪里？"
            ],
        ) as mock_google:
            result = translator._batch_translate_siliconflow(
                ["That's exactly right.", "Where should that go?"],
                target_lang="zh",
            )

        assert result == ["完全正确。", "那个该放哪里？"]
        assert mock_google.called

    def test_siliconflow_failed_chunk_degrades_to_smaller_chunks(self):
        translator = Translator()
        translator.backend = "siliconflow"
        translator.model = "fake-model"

        with patch.object(
            Translator,
            "_request_siliconflow_chunk",
            side_effect=[None, ["甲"], ["乙"]],
        ) as mock_request, patch.object(
            Translator,
            "_batch_translate_google",
            side_effect=lambda texts, target_lang: [f"google:{text}" for text in texts],
        ) as mock_google:
            result = translator._batch_translate_siliconflow(
                ["line-1", "line-2"],
                target_lang="zh",
            )

        assert result == ["甲", "乙"]
        assert mock_request.call_count == 3
        assert not mock_google.called

    def test_siliconflow_meta_output_falls_back_per_line(self):
        translator = Translator()
        translator.backend = "siliconflow"
        translator.model = "fake-model"

        with patch.object(
            Translator,
            "_request_siliconflow_chunk",
            side_effect=[None, None, None],
        ), patch.object(
            Translator,
            "_batch_translate_google",
            side_effect=lambda texts, target_lang: [f"google:{text}" for text in texts],
        ) as mock_google:
            result = translator._batch_translate_siliconflow(
                ["line-1", "line-2"],
                target_lang="zh",
            )

        assert result == ["google:line-1", "google:line-2"]
        assert mock_google.call_count == 2


class TestEnShortCircuit:
    """验证 _translate_segments 的 EN 短路逻辑：原文已是英文时不调用翻译后端。"""

    def _make_translator(self):
        t = Translator()
        t.backend = "siliconflow"
        t.model = "fake-model"
        return t

    def _make_segments(self, texts):
        return [
            {"start": i * 1.0, "end": i * 1.0 + 0.9, "text": text}
            for i, text in enumerate(texts)
        ]

    def test_en_source_skips_translation_call(self):
        """原文是英文句子，target_lang=en 时不应调用 _batch_translate。"""
        translator = self._make_translator()
        english_texts = [
            "This is the first subtitle line.",
            "Here comes the second line of text.",
            "And here is the third one for good measure.",
            "A fourth line to ensure the sample is solid.",
            "Finally the fifth line closes the sample.",
        ]
        segments = self._make_segments(english_texts)

        with patch.object(Translator, "_batch_translate") as mock_batch:
            result = translator._translate_segments(segments, target_lang="en")

        mock_batch.assert_not_called()
        assert [s["text"] for s in result] == english_texts

    def test_zh_source_does_not_skip_translation(self):
        """原文是中文，target_lang=en 时应正常走翻译调用。"""
        translator = self._make_translator()
        zh_texts = [
            "这是第一条字幕。",
            "这是第二条字幕内容。",
            "第三条字幕在这里。",
        ]
        segments = self._make_segments(zh_texts)
        expected = ["This is subtitle one.", "This is subtitle two.", "This is subtitle three."]

        with patch.object(Translator, "_batch_translate", return_value=expected) as mock_batch:
            result = translator._translate_segments(segments, target_lang="en")

        mock_batch.assert_called_once()
        assert [s["text"] for s in result] == expected

    def test_en_short_circuit_preserves_timestamps(self):
        """短路返回时时间戳必须与原 segments 一致。"""
        translator = self._make_translator()
        segments = [
            {"start": 0.0, "end": 1.5, "text": "Welcome to the show everyone."},
            {"start": 1.6, "end": 3.0, "text": "Today we have a special guest joining us."},
            {"start": 3.1, "end": 4.5, "text": "Let us get started with the interview."},
            {"start": 4.6, "end": 6.0, "text": "Please welcome our distinguished speaker now."},
            {"start": 6.1, "end": 7.5, "text": "Thank you all for being here tonight."},
        ]
        with patch.object(Translator, "_batch_translate"):
            result = translator._translate_segments(segments, target_lang="en")

        for orig, res in zip(segments, result):
            assert res["start"] == orig["start"]
            assert res["end"] == orig["end"]
            assert res["text"] == orig["text"]
    
