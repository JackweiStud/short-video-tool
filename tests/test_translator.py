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
    _snap_chunk_intervals_to_word_boundaries,
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

    def test_split_with_words_snaps_to_word_boundary(self):
        """Real 0:03-0:12 opening case: split point must land on a word.end,
        not in the silent gap between 'coming.' (7.44s) and 'My' (9.72s)."""
        seg = {
            'start': 3.20,
            'end': 12.30,
            'text': "Hello. Hey everyone. How's it going? Thanks for coming. My name is Mahesh and I'm a product",
            'words': [
                {'word': 'Hello.', 'start': 3.20, 'end': 3.76},
                {'word': 'Hey', 'start': 3.76, 'end': 4.32},
                {'word': 'everyone.', 'start': 4.32, 'end': 4.62},
                {'word': "How's", 'start': 4.88, 'end': 5.12},
                {'word': 'it', 'start': 5.12, 'end': 5.22},
                {'word': 'going?', 'start': 5.22, 'end': 5.40},
                {'word': 'Thanks', 'start': 6.64, 'end': 6.88},
                {'word': 'for', 'start': 6.88, 'end': 7.10},
                {'word': 'coming.', 'start': 7.10, 'end': 7.44},
                {'word': 'My', 'start': 9.72, 'end': 10.08},
                {'word': 'name', 'start': 10.08, 'end': 10.26},
                {'word': 'is', 'start': 10.26, 'end': 10.46},
                {'word': 'Mahesh', 'start': 10.46, 'end': 10.88},
                {'word': 'and', 'start': 10.88, 'end': 11.54},
                {'word': "I'm", 'start': 11.54, 'end': 11.88},
                {'word': 'a', 'start': 11.88, 'end': 12.08},
                {'word': 'product', 'start': 12.08, 'end': 12.30},
            ],
        }
        # Use the same horizontal-video budget the production pipeline uses
        # so the segment splits into the same 2 chunks the user observed.
        result = _split_long_segments([seg], max_en_chars=65, max_zh_chars=45, lang='en')
        assert len(result) == 2, f"expected 2 chunks (production split), got {len(result)}"

        # Every split end must equal some word.end (within rounding)
        word_ends = {round(w['end'], 3) for w in seg['words']}
        word_ends.add(round(seg['end'], 3))
        for r in result:
            assert round(r['end'], 3) in word_ends, (
                f"end={r['end']} did not snap to any word boundary {sorted(word_ends)}"
            )
        # First chunk text ends with 'coming.' → its end must snap to 'coming.' (7.44),
        # NOT into the silent gap 7.44-9.72 (old buggy behaviour was 8.76).
        assert abs(result[0]['end'] - 7.44) < 0.01, (
            f"first chunk end should snap to 'coming.' (7.44), got {result[0]['end']}"
        )
        # Second chunk start follows the first chunk end and must not fall before
        # the next spoken word ('My' starts at 9.72).
        assert result[1]['start'] == result[0]['end']

    def test_split_without_words_falls_back_to_proportional(self):
        """No `words` field → behavior identical to old character-proportional split."""
        text = 'This is a really long sentence that definitely exceeds forty two characters in length.'
        segs = [{'start': 0.0, 'end': 4.0, 'text': text}]
        result = _split_long_segments(segs, max_en_chars=42, max_zh_chars=22, lang='en')
        assert abs(result[0]['start'] - 0.0) < 0.001
        assert abs(result[-1]['end'] - 4.0) < 0.001
        # monotonic timestamps
        for i in range(len(result) - 1):
            assert result[i]['end'] <= result[i + 1]['start'] + 0.001

    def test_split_preserves_words_field_for_downstream(self):
        """The `words` field should remain on every split chunk (downstream uses it)."""
        seg = {
            'start': 0.0,
            'end': 4.0,
            'text': 'short clause. another short clause here.',
            'words': [
                {'word': 'short', 'start': 0.0, 'end': 0.4},
                {'word': 'clause.', 'start': 0.4, 'end': 1.0},
                {'word': 'another', 'start': 1.5, 'end': 2.0},
                {'word': 'short', 'start': 2.0, 'end': 2.4},
                {'word': 'clause', 'start': 2.4, 'end': 3.0},
                {'word': 'here.', 'start': 3.0, 'end': 4.0},
            ],
        }
        result = _split_long_segments([seg], max_en_chars=20, max_zh_chars=22, lang='en')
        for r in result:
            assert 'words' in r


class TestSnapChunkIntervalsToWordBoundaries:
    """Unit tests for _snap_chunk_intervals_to_word_boundaries helper."""

    def test_snaps_to_nearest_word_end(self):
        words = [
            {'word': 'a', 'start': 0.0, 'end': 1.0},
            {'word': 'b', 'start': 1.0, 'end': 2.0},
            {'word': 'c', 'start': 2.0, 'end': 3.0},
            {'word': 'd', 'start': 3.0, 'end': 4.0},
        ]
        # Raw split says first chunk ends at 1.8 → should snap to 2.0 (word b)
        intervals = _snap_chunk_intervals_to_word_boundaries(
            chunks=['a b', 'c d'],
            raw_ends=[1.8, 4.0],
            seg_start=0.0,
            seg_end=4.0,
            words=words,
        )
        assert intervals[0] == (0.0, 2.0)
        assert intervals[1] == (2.0, 4.0)

    def test_no_words_uses_raw(self):
        intervals = _snap_chunk_intervals_to_word_boundaries(
            chunks=['a', 'b'],
            raw_ends=[1.5, 3.0],
            seg_start=0.0,
            seg_end=3.0,
            words=[],
        )
        assert intervals == [(0.0, 1.5), (1.5, 3.0)]

    def test_monotonic_no_overlap_when_snapping(self):
        words = [{'word': str(i), 'start': i, 'end': i + 0.5} for i in range(10)]
        intervals = _snap_chunk_intervals_to_word_boundaries(
            chunks=['0 1', '2 3', '4 5', '6 7', '8 9'],
            raw_ends=[2.0, 4.0, 6.0, 8.0, 9.5],
            seg_start=0.0,
            seg_end=9.5,
            words=words,
        )
        for i in range(len(intervals) - 1):
            assert intervals[i][1] <= intervals[i + 1][0] + 1e-6
        assert intervals[-1][1] == 9.5


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

    def test_siliconflow_chunk_packing_respects_char_budget(self):
        translator = Translator()
        texts = ["aaaa", "bbbb", "cccc", "dddd", "eeee"]

        with patch.object(
            Translator,
            "_get_siliconflow_translation_chunk_limits",
            return_value=(10, 100),
        ):
            chunks = translator._build_siliconflow_translation_chunks(texts, "zh")

        assert [len(chunk) for _, chunk in chunks] == [2, 2, 1]
        assert [idx for idx, _ in chunks] == [0, 2, 4]

    def test_siliconflow_chunk_packing_respects_max_items(self):
        translator = Translator()
        texts = ["a", "b", "c", "d", "e"]

        with patch.object(
            Translator,
            "_get_siliconflow_translation_chunk_limits",
            return_value=(100, 2),
        ):
            chunks = translator._build_siliconflow_translation_chunks(texts, "zh")

        assert [len(chunk) for _, chunk in chunks] == [2, 2, 1]
        assert [idx for idx, _ in chunks] == [0, 2, 4]

    def test_siliconflow_chunk_limits_are_more_conservative_for_zh(self):
        translator = Translator()

        en_limits = translator._get_siliconflow_translation_chunk_limits("en")
        zh_limits = translator._get_siliconflow_translation_chunk_limits("zh")

        assert zh_limits[0] < en_limits[0]
        assert zh_limits[1] <= en_limits[1]
        assert zh_limits == (800, 14)

    def test_siliconflow_chunk_packing_shrinks_for_long_texts(self):
        translator = Translator()
        texts = ["x" * 100 for _ in range(10)]

        with patch.object(
            Translator,
            "_get_siliconflow_translation_chunk_limits",
            return_value=(1000, 24),
        ):
            chunks = translator._build_siliconflow_translation_chunks(texts, "zh")

        assert [len(chunk) for _, chunk in chunks] == [8, 2]
        assert [idx for idx, _ in chunks] == [0, 8]


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

    def test_en_short_circuit_tolerates_one_short_fragment(self):
        """前 5 句里允许出现 1 条过短英文碎片，不应直接误判为非英文。"""
        translator = self._make_translator()
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Welcome to the show everyone."},
            {"start": 1.0, "end": 2.0, "text": "I think we should continue."},
            {"start": 2.0, "end": 3.0, "text": "AI."},
            {"start": 3.0, "end": 4.0, "text": "This is the main point."},
            {"start": 4.0, "end": 5.0, "text": "Let's move on."},
        ]

        with patch.object(Translator, "_batch_translate") as mock_batch:
            result = translator._translate_segments(segments, target_lang="en")

        mock_batch.assert_not_called()
        assert [s["text"] for s in result] == [s["text"] for s in segments]
    
