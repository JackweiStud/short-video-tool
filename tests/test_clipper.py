"""
Test cases for Clipper module
"""
import os
import pytest
import json
from clipper import Clipper


class TestClipperExceptions:
    """Test exception handling in Clipper"""
    
    def test_nonexistent_video(self, sample_analysis_result):
        """Test with non-existent video file"""
        clipper = Clipper()
        result = clipper.clip_video("nonexistent_video.mp4", sample_analysis_result)
        assert result is None
    
    def test_empty_analysis_result(self, temp_dir):
        """Test with empty analysis result"""
        dummy_video = os.path.join(temp_dir, "dummy.mp4")
        with open(dummy_video, 'w') as f:
            f.write("dummy")
        
        clipper = Clipper()
        result = clipper.clip_video(dummy_video, None)
        assert result is None
    
    def test_missing_required_fields(self, temp_dir):
        """Test with missing required fields in analysis result"""
        dummy_video = os.path.join(temp_dir, "dummy.mp4")
        with open(dummy_video, 'w') as f:
            f.write("dummy")
        
        # Missing 'scene_changes' field
        incomplete_result = {
            "audio_climax_points": [{"time": 10.0, "score": 2.0}],
            "asr_result": []
        }
        
        clipper = Clipper()
        result = clipper.clip_video(dummy_video, incomplete_result)
        assert result is None
    
    def test_no_data(self, temp_dir):
        """Test with no climax points and no scene changes"""
        dummy_video = os.path.join(temp_dir, "dummy.mp4")
        with open(dummy_video, 'w') as f:
            f.write("dummy")
        
        empty_data = {
            "audio_climax_points": [],
            "scene_changes": [],
            "asr_result": []
        }
        
        clipper = Clipper()
        result = clipper.clip_video(dummy_video, empty_data)
        assert result is not None
        assert result.get('clips') == []


class TestClipperFunctionality:
    """Test Clipper functionality"""
    
    @pytest.mark.slow
    def test_clip_video_success(self, sample_video_path, temp_dir):
        """Test successful video clipping"""
        if sample_video_path is None:
            pytest.skip("Sample video not available")
        
        # Load real analysis result
        analysis_path = "analysis_results/analysis_result.json"
        if not os.path.exists(analysis_path):
            pytest.skip("Analysis result not available")
        
        with open(analysis_path, 'r', encoding='utf-8') as f:
            analysis_result = json.load(f)
        
        clipper = Clipper(min_duration=15, max_duration=60)
        result = clipper.clip_video(sample_video_path, analysis_result, output_dir=temp_dir)
        
        assert result is not None
        assert 'clips' in result
        assert len(result['clips']) > 0
    
    @pytest.mark.slow
    def test_clip_duration_range(self, sample_video_path, temp_dir):
        """Test clip duration is within range"""
        if sample_video_path is None:
            pytest.skip("Sample video not available")
        
        analysis_path = "analysis_results/analysis_result.json"
        if not os.path.exists(analysis_path):
            pytest.skip("Analysis result not available")
        
        with open(analysis_path, 'r', encoding='utf-8') as f:
            analysis_result = json.load(f)
        
        clipper = Clipper(min_duration=15, max_duration=60)
        result = clipper.clip_video(sample_video_path, analysis_result, output_dir=temp_dir)
        
        if result and result['clips']:
            for clip in result['clips']:
                assert 15 <= clip['duration'] <= 60
    
    @pytest.mark.slow
    def test_asr_subset_extraction(self, sample_video_path, temp_dir):
        """Test ASR subset extraction for clips"""
        if sample_video_path is None:
            pytest.skip("Sample video not available")
        
        analysis_path = "analysis_results/analysis_result.json"
        if not os.path.exists(analysis_path):
            pytest.skip("Analysis result not available")
        
        with open(analysis_path, 'r', encoding='utf-8') as f:
            analysis_result = json.load(f)
        
        clipper = Clipper(min_duration=15, max_duration=60)
        result = clipper.clip_video(sample_video_path, analysis_result, output_dir=temp_dir)
        
        if result and result['clips']:
            for clip in result['clips']:
                assert 'asr_subset' in clip
                assert isinstance(clip['asr_subset'], list)
    
    @pytest.mark.slow
    def test_fallback_to_scene_changes(self, sample_video_path, temp_dir):
        """Test fallback to scene changes when no climax points"""
        if sample_video_path is None:
            pytest.skip("Sample video not available")
        
        analysis_path = "analysis_results/analysis_result.json"
        if not os.path.exists(analysis_path):
            pytest.skip("Analysis result not available")
        
        with open(analysis_path, 'r', encoding='utf-8') as f:
            analysis_result = json.load(f)
        
        # Remove climax points to test fallback
        analysis_result['audio_climax_points'] = []
        
        clipper = Clipper(min_duration=15, max_duration=60)
        result = clipper.clip_video(sample_video_path, analysis_result, output_dir=temp_dir)
        
        assert result is not None
        assert len(result['clips']) > 0


class TestClipperTopicRanking:
    """Test topic-based ranking and splitting behavior."""

    def test_topic_segments_are_ranked_by_quality(self):
        clipper = Clipper(min_duration=15, max_duration=60, max_clips=2)

        topic_segments = [
            {"start": 0.0, "end": 58.0, "topic": "Warm-up", "score": 28, "reason": "Transitional setup"},
            {"start": 60.0, "end": 100.0, "topic": "Core insight", "score": 95, "reason": "Strong self-contained point"},
            {"start": 110.0, "end": 145.0, "topic": "Practical example", "score": 74, "reason": "Concrete example"},
        ]
        topic_summaries = [
            {"topic": "Warm-up", "summary": "Opening context", "score": 28, "reason": "Transitional setup"},
            {"topic": "Core insight", "summary": "Main point", "score": 95, "reason": "Strong self-contained point"},
            {"topic": "Practical example", "summary": "Example", "score": 74, "reason": "Concrete example"},
        ]

        ranked = clipper._segments_from_topics(topic_segments, topic_summaries, asr_result=[])
        selected = clipper._select_topic_candidates(ranked)

        assert len(selected) == 2
        assert selected[0]["topic"] == "Core insight"
        assert selected[0]["score"] >= selected[1]["score"]
        assert selected[1]["topic"] == "Practical example"

    def test_long_topic_segments_split_on_asr_boundaries(self):
        clipper = Clipper(min_duration=15, max_duration=60, max_clips=5)

        topic_segments = [
            {"start": 0.0, "end": 130.0, "topic": "Long chapter", "score": 90, "reason": "Extended explanation"},
        ]
        topic_summaries = [
            {"topic": "Long chapter", "summary": "Extended explanation", "score": 90, "reason": "Extended explanation"},
        ]
        asr_result = [
            {"start": 0.0, "end": 42.0, "text": "Part one"},
            {"start": 42.0, "end": 88.0, "text": "Part two"},
            {"start": 88.0, "end": 130.0, "text": "Part three"},
        ]

        ranked = clipper._segments_from_topics(topic_segments, topic_summaries, asr_result=asr_result)

        assert len(ranked) >= 3
        boundaries = {(seg["start"], seg["end"]) for seg in ranked}
        assert (0.0, 42.0) in boundaries
        assert (42.0, 88.0) in boundaries
        assert (88.0, 130.0) in boundaries

    def test_topic_split_preserves_min_duration_for_tail_with_asr_boundaries(self):
        clipper = Clipper(min_duration=30, max_duration=60, max_clips=5)
        asr_result = [
            {"start": 123.49, "end": 140.87, "text": "a"},
            {"start": 140.87, "end": 144.01, "text": "b"},
            {"start": 144.01, "end": 149.26, "text": "c"},
            {"start": 150.47, "end": 152.47, "text": "d"},
            {"start": 154.68, "end": 175.92, "text": "e"},
            {"start": 175.92, "end": 181.14, "text": "f"},
            {"start": 181.14, "end": 186.60, "text": "g"},
            {"start": 186.60, "end": 191.90, "text": "h"},
            {"start": 191.90, "end": 194.38, "text": "i"},
            {"start": 194.38, "end": 202.70, "text": "j"},
            {"start": 203.46, "end": 204.26, "text": "k"},
        ]

        chunks = clipper._split_topic_segment(123.50, 204.26, asr_result)

        assert len(chunks) == 2
        assert chunks[0][0] == pytest.approx(123.50)
        assert chunks[1][1] == pytest.approx(204.26)
        assert all(30 <= end - start <= 60 for start, end in chunks)

    def test_fixed_topic_split_preserves_min_duration_for_tail(self):
        clipper = Clipper(min_duration=30, max_duration=60, max_clips=5)

        chunks = clipper._split_topic_segment_fixed(0.0, 95.0)

        assert len(chunks) == 2
        assert chunks[0][0] == pytest.approx(0.0)
        assert chunks[1][1] == pytest.approx(95.0)
        assert all(30 <= end - start <= 60 for start, end in chunks)

    def test_topic_candidates_respect_duration_bounds_before_selection(self):
        clipper = Clipper(min_duration=120, max_duration=200, max_clips=4)
        candidates = [
            {"start": 0.0, "end": 52.2, "score": 100.0, "topic": "A", "summary": "A", "reason": "A"},
            {"start": 52.2, "end": 124.2, "score": 90.0, "topic": "B", "summary": "B", "reason": "B"},
            {"start": 124.2, "end": 193.2, "score": 95.0, "topic": "C", "summary": "C", "reason": "C"},
            {"start": 193.2, "end": 262.2, "score": 88.0, "topic": "D", "summary": "D", "reason": "D"},
            {"start": 262.2, "end": 381.7, "score": 98.0, "topic": "E", "summary": "E", "reason": "E"},
        ]

        normalized = clipper._normalize_topic_candidate_durations(candidates, "opinion")

        assert normalized
        assert all(120 <= seg["end"] - seg["start"] <= 200 for seg in normalized)

    def test_topic_strategy_topic_path_respects_duration_constraints(self, temp_dir, monkeypatch):
        clipper = Clipper(min_duration=120, max_duration=200, max_clips=4)
        dummy_video = os.path.join(temp_dir, "dummy.mp4")
        with open(dummy_video, "wb") as f:
            f.write(b"dummy-video")

        analysis_result = {
            "audio_climax_points": [],
            "scene_changes": [],
            "asr_result": [{"start": 0.0, "end": 10.0, "text": "x"}],
            "topic_segments": [
                {"start": 0.0, "end": 52.2, "score": 100.0, "topic": "A", "summary": "A", "reason": "A"},
                {"start": 52.2, "end": 124.2, "score": 90.0, "topic": "B", "summary": "B", "reason": "B"},
                {"start": 124.2, "end": 193.2, "score": 95.0, "topic": "C", "summary": "C", "reason": "C"},
                {"start": 193.2, "end": 262.2, "score": 88.0, "topic": "D", "summary": "D", "reason": "D"},
                {"start": 262.2, "end": 381.7, "score": 98.0, "topic": "E", "summary": "E", "reason": "E"},
            ],
            "topic_summaries": [],
            "segmentation_meta": {
                "clip_strategy_used": "topic",
                "segmentation_effective": True,
                "fallback_reason": "",
            },
        }

        monkeypatch.setattr(
            clipper,
            "_clip_with_ffmpeg",
            lambda video_path, clip_path, start_time, end_time: True,
        )

        result = clipper.clip_video(dummy_video, analysis_result, output_dir=temp_dir)

        assert result is not None
        assert result["clips"]
        assert all(120 <= clip["duration"] <= 200 for clip in result["clips"])

    def test_opinion_strategy_does_not_fallback_without_topic_segments(self, sample_analysis_result, temp_dir):
        clipper = Clipper(min_duration=15, max_duration=60, max_clips=2)
        dummy_video = os.path.join(temp_dir, "dummy.mp4")
        with open(dummy_video, "wb") as f:
            f.write(b"dummy-video")

        analysis_result = dict(sample_analysis_result)
        analysis_result["topic_segments"] = []
        analysis_result["segmentation_meta"] = {
            "clip_strategy_used": "opinion",
            "segmentation_effective": False,
            "fallback_reason": "llm_failed",
        }

        result = clipper.clip_video(dummy_video, analysis_result, output_dir=temp_dir)

        assert result is not None
        assert result["clips"] == []

    def test_hybrid_strategy_can_fallback_without_topic_segments(self, sample_analysis_result, temp_dir, monkeypatch):
        clipper = Clipper(min_duration=15, max_duration=60, max_clips=2)
        dummy_video = os.path.join(temp_dir, "dummy.mp4")
        with open(dummy_video, "wb") as f:
            f.write(b"dummy-video")

        analysis_result = dict(sample_analysis_result)
        analysis_result["topic_segments"] = []
        analysis_result["segmentation_meta"] = {
            "clip_strategy_used": "hybrid",
            "segmentation_effective": False,
            "fallback_reason": "topic_missing",
        }

        monkeypatch.setattr(
            clipper,
            "_clip_with_ffmpeg",
            lambda video_path, clip_path, start_time, end_time: True,
        )

        result = clipper.clip_video(dummy_video, analysis_result, output_dir=temp_dir)

        assert result is not None
        assert len(result["clips"]) > 0

    def test_topic_duration_merge_still_repairs_short_adjacent_segments(self):
        clipper = Clipper(min_duration=120, max_duration=200, max_clips=4)
        candidates = [
            {
                "start": 0.0,
                "end": 78.0,
                "score": 91.0,
                "topic": "Why Traditional Learning Wastes Time",
                "summary": "The speaker argues books repeat low-value information.",
                "reason": "Complete thesis block.",
                "stance": "",
            },
            {
                "start": 78.0,
                "end": 156.0,
                "score": 92.0,
                "topic": "Choosing Tech with AI Assistance",
                "summary": "The speaker pivots to buying hardware faster with AI support.",
                "reason": "New decision-making chapter.",
                "stance": "",
            },
        ]

        normalized = clipper._normalize_topic_candidate_durations(candidates, "topic")

        assert len(normalized) == 1
        assert normalized[0]["start"] == 0.0
        assert normalized[0]["end"] == 156.0

    def test_topic_duration_merge_does_not_force_valid_windows_toward_target(self):
        clipper = Clipper(min_duration=120, max_duration=200, max_clips=4)
        candidates = [
            {
                "start": 0.0,
                "end": 128.0,
                "score": 88.0,
                "topic": "AI Workflow for Faster Research",
                "summary": "Step one of the AI-assisted research workflow.",
                "reason": "Introduces the workflow.",
                "stance": "",
            },
            {
                "start": 128.0,
                "end": 258.0,
                "score": 90.0,
                "topic": "AI Workflow for Faster Research",
                "summary": "Step two of the AI-assisted research workflow with examples.",
                "reason": "Continues the same workflow.",
                "stance": "",
            },
        ]

        normalized = clipper._normalize_topic_candidate_durations(candidates, "topic")

        assert len(normalized) == 2
        assert normalized[0]["start"] == 0.0
        assert normalized[0]["end"] == 128.0
        assert normalized[1]["start"] == 128.0
        assert normalized[1]["end"] == 258.0

    def test_topic_padding_prefers_asr_boundaries(self):
        clipper = Clipper(min_duration=120, max_duration=200, max_clips=4)
        candidates = [
            {"start": 0.0, "end": 88.8, "score": 90.0, "topic": "A", "summary": "A", "reason": "A"},
            {"start": 182.7, "end": 378.0, "score": 92.0, "topic": "B", "summary": "B", "reason": "B"},
        ]
        asr_result = [
            {"start": 84.3, "end": 88.8, "text": "first"},
            {"start": 88.8, "end": 121.4, "text": "boundary extension"},
            {"start": 121.4, "end": 140.0, "text": "extra"},
        ]

        normalized = clipper._normalize_topic_candidate_durations(
            candidates,
            "topic",
            asr_result=asr_result,
        )

        assert normalized[0]["start"] == 0.0
        assert normalized[0]["end"] == 121.4

    def test_topic_padding_recomputes_remaining_duration_after_boundary_snap(self):
        clipper = Clipper(min_duration=120, max_duration=200, max_clips=4)
        candidates = [
            {"start": 363.1, "end": 480.3, "score": 87.0, "topic": "A", "summary": "A", "reason": "A"},
            {"start": 548.3, "end": 607.5, "score": 85.0, "topic": "B", "summary": "B", "reason": "B"},
        ]
        asr_result = [
            {"start": 480.3, "end": 488.7, "text": "bridge"},
            {"start": 488.7, "end": 507.0, "text": "topic starts"},
        ]

        normalized = clipper._normalize_topic_candidate_durations(
            candidates,
            "topic",
            asr_result=asr_result,
        )

        assert normalized[1]["start"] == 488.7
        assert normalized[1]["end"] == 608.7
        assert normalized[1]["end"] - normalized[1]["start"] == pytest.approx(120.0)
