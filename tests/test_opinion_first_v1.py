"""
Unit tests for Opinion-first Clipping V1
Covers:
  1. _normalize_topic_segments() preserves opinion fields
  2. _segment_by_topic() returns 3-tuple with segmentation_meta
  3. fallback paths return correct meta
  4. _segments_from_topics() passes opinion fields to candidates
  5. analyze_video() segmentation_meta in result
"""
import sys
import os
import types
import json
import re
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Helpers to build minimal Analyzer without heavy imports
# ---------------------------------------------------------------------------

def make_analyzer():
    """Import Analyzer and return a minimally configured instance."""
    from analyzer import Analyzer
    cfg = MagicMock()
    cfg.llm_api_key = "fake-key"
    cfg.llm_api_url = "https://fake.llm/v1/chat/completions"
    cfg.llm_model = "gpt-4"
    cfg.llm_max_tokens = 4096
    cfg.llm_temperature = 0.3
    cfg.topic_segment_min_duration = 30
    cfg.enable_topic_segmentation = True
    cfg.whisper_model = "base"
    cfg.asr_language = "en"
    cfg.min_clip_duration = 120
    cfg.max_clip_duration = 200
    cfg.audio_climax_top_n = 5
    cfg.scene_detection_threshold = 0.3
    cfg.output_dir = "/tmp/test_output"
    a = Analyzer.__new__(Analyzer)
    a.config = cfg
    a.llm_api_key = "fake-key"
    a.llm_base_url = "https://fake.llm/v1"
    a.llm_provider = "openai"
    a.llm_model = "gpt-4"
    a.llm_max_tokens = 4096
    a.llm_temperature = 0.3
    a.llm_timeout = 60
    a.topic_segment_min_duration = 30
    a.topic_segment_chunk_duration = 1500
    a.topic_segment_chunk_overlap_seconds = 180
    a.topic_segment_max_workers = 4
    a.enable_topic_segmentation = True
    a.whisper_model = "base"
    a.asr_language = "en"
    a.audio_climax_top_n = 5
    a.scene_detection_threshold = 0.3
    a.default_output_dir = "/tmp/test_output"
    return a


DUMMY_SEGMENTS = [
    {
        "start": 0.0, "end": 45.2,
        "topic": "AI Will Replace Jobs",
        "summary": "Speaker argues AI eliminates routine work.",
        "score": 94,
        "reason": "Strong contrarian claim.",
        "conclusion_clarity": 9,
        "self_contained": 8,
        "info_density": 7,
        "viral_fit": 9,
        "stance": "AI will eliminate 40% of routine jobs.",
        "key_sentences": ["AI will eliminate 40% of routine jobs."],
        "publishability": 88,
    },
    {
        "start": 45.2, "end": 120.5,
        "topic": "How to Adapt",
        "summary": "Practical steps for workers.",
        "score": 82,
        "reason": "Actionable advice.",
        "conclusion_clarity": 8,
        "self_contained": 9,
        "info_density": 8,
        "viral_fit": 7,
        "stance": "Learn to work alongside AI tools.",
        "key_sentences": ["Learn to work alongside AI tools."],
        "publishability": 80,
    },
]


# ---------------------------------------------------------------------------
# Test 1: _normalize_topic_segments preserves opinion fields
# ---------------------------------------------------------------------------

def test_normalize_preserves_opinion_fields():
    a = make_analyzer()
    segs, summaries = a._normalize_topic_segments(
        DUMMY_SEGMENTS, total_duration=120.5, min_duration=15
    )
    assert len(segs) == 2, f"Expected 2 segments, got {len(segs)}"
    seg = segs[0]
    assert seg["conclusion_clarity"] == 9
    assert seg["self_contained"] == 8
    assert seg["info_density"] == 7
    assert seg["viral_fit"] == 9
    assert seg["stance"] == "AI will eliminate 40% of routine jobs."
    assert seg["key_sentences"] == ["AI will eliminate 40% of routine jobs."]
    assert seg["publishability"] == 88
    print("PASS: test_normalize_preserves_opinion_fields")


# ---------------------------------------------------------------------------
# Test 2: _segment_by_topic returns 3-tuple
# ---------------------------------------------------------------------------

def test_segment_by_topic_returns_three_tuple():
    a = make_analyzer()
    # Patch out the HTTP call
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"segments": DUMMY_SEGMENTS})}}]
    }
    asr = [
        {"start": 0.0, "end": 45.2, "text": "AI will eliminate jobs."},
        {"start": 45.2, "end": 120.5, "text": "Learn to adapt."},
    ]
    with patch("requests.post", return_value=fake_response):
        result = a._segment_by_topic(asr, clip_strategy="opinion")
    assert isinstance(result, tuple) and len(result) == 3, \
        f"Expected 3-tuple, got {type(result)} len={len(result) if isinstance(result, tuple) else 'N/A'}"
    segs, summaries, meta = result
    assert isinstance(segs, list)
    assert isinstance(meta, dict)
    assert meta["clip_strategy_used"] == "opinion"
    assert meta["segmentation_effective"] is True
    assert meta["fallback_reason"] == ""
    print("PASS: test_segment_by_topic_returns_three_tuple")


# ---------------------------------------------------------------------------
# Test 3: fallback path on empty asr
# ---------------------------------------------------------------------------

def test_segment_by_topic_fallback_no_asr():
    a = make_analyzer()
    segs, summaries, meta = a._segment_by_topic([], clip_strategy="opinion")
    assert segs == []
    assert summaries == []
    assert meta["segmentation_effective"] is False
    assert meta["fallback_reason"] == "no_asr_result"
    print("PASS: test_segment_by_topic_fallback_no_asr")


# ---------------------------------------------------------------------------
# Test 4: fallback on invalid JSON from LLM
# ---------------------------------------------------------------------------

def test_segment_by_topic_fallback_invalid_json():
    a = make_analyzer()
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "not json at all"}}]
    }
    asr = [{"start": 0.0, "end": 10.0, "text": "hello"}]
    with patch("requests.post", return_value=fake_response):
        segs, summaries, meta = a._segment_by_topic(asr, clip_strategy="opinion")
    assert segs == []
    assert meta["segmentation_effective"] is False
    assert meta["fallback_reason"] == "llm_invalid_json"
    print("PASS: test_segment_by_topic_fallback_invalid_json")


# ---------------------------------------------------------------------------
# Test 5: _segments_from_topics passes opinion fields to candidates
# ---------------------------------------------------------------------------

def test_segments_from_topics_passes_opinion_fields():
    from clipper import Clipper
    cfg = MagicMock()
    cfg.min_clip_duration = 15
    cfg.max_clip_duration = 120
    cfg.max_clips = 10
    c = Clipper.__new__(Clipper)
    c.min_duration = 15
    c.max_duration = 120
    c.max_clips = 10

    # Patch technical fit scorer and splitter
    c._score_topic_clip_fit = MagicMock(return_value=61.0)
    c._split_topic_segment = MagicMock(return_value=[(0.0, 45.2)])
    c._coerce_topic_score = MagicMock(return_value=94.0)

    topic_segs = [
        {
            "start": 0.0, "end": 45.2,
            "topic": "AI Will Replace Jobs",
            "summary": "Speaker argues AI eliminates routine work.",
            "score": 94,
            "reason": "Strong contrarian claim.",
            "conclusion_clarity": 9,
            "self_contained": 8,
            "info_density": 7,
            "viral_fit": 9,
            "stance": "AI will eliminate 40% of routine jobs.",
            "key_sentences": ["AI will eliminate 40% of routine jobs."],
            "publishability": 88,
        }
    ]

    candidates = c._segments_from_topics(topic_segs, [], [])
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand["score"] == 94.0
    assert cand["clip_fit_score"] == 61.0
    assert cand["stance"] == "AI will eliminate 40% of routine jobs."
    assert cand["publishability"] == 88
    assert cand["self_contained"] == 8
    assert cand["key_sentences"] == ["AI will eliminate 40% of routine jobs."]
    assert cand["conclusion_clarity"] == 9
    assert cand["viral_fit"] == 9
    print("PASS: test_segments_from_topics_passes_opinion_fields")


# ---------------------------------------------------------------------------
# Test 6: segmentation_meta with opinion_fields_missing
# ---------------------------------------------------------------------------

def test_segment_by_topic_opinion_fields_missing_marks_ineffective():
    a = make_analyzer()
    segs_no_opinion = [
        {"start": 0.0, "end": 45.2, "topic": "Intro", "summary": "Intro", "score": 70, "reason": "ok"},
        {"start": 45.2, "end": 90.0, "topic": "Body", "summary": "Body", "score": 75, "reason": "ok"},
    ]
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"segments": segs_no_opinion})}}]
    }
    asr = [
        {"start": 0.0, "end": 45.2, "text": "intro"},
        {"start": 45.2, "end": 90.0, "text": "body"},
    ]
    with patch("requests.post", return_value=fake_response):
        segs, summaries, meta = a._segment_by_topic(asr, clip_strategy="opinion")
    assert meta["segmentation_effective"] is False
    assert meta["fallback_reason"] == "opinion_fields_missing_from_llm"
    print("PASS: test_segment_by_topic_opinion_fields_missing_marks_ineffective")


def test_strategy_prompts_are_meaningfully_different():
    a = make_analyzer()
    transcript = "[0.0s - 10.0s] AI can compress days of research into minutes."

    opinion_prompt = a._build_topic_window_prompt(
        0.0, 120.0, 0, 1, transcript, "opinion", 120.0
    )
    topic_prompt = a._build_topic_window_prompt(
        0.0, 120.0, 0, 1, transcript, "topic", 120.0
    )
    hybrid_prompt = a._build_topic_window_prompt(
        0.0, 120.0, 0, 1, transcript, "hybrid", 120.0
    )

    assert "Opinion-first scoring" in opinion_prompt
    assert "Opinion-first scoring" not in topic_prompt
    assert "coherent chapter/topic boundaries" in topic_prompt
    assert "skip low-signal spans entirely" in opinion_prompt
    assert "Balance both opinion strength and chapter coherence" in hybrid_prompt
    assert "final publication length will be adjusted downstream" in topic_prompt
    assert "Do not create evenly sized chapter slices" in topic_prompt
    assert "Preferred final clip range after downstream repair: 120-200 seconds" in topic_prompt
    assert "Do not cover the whole window for completeness" in opinion_prompt
    assert "The JSON example below demonstrates schema only" in topic_prompt
    assert "Do NOT imitate its exact timestamps" in opinion_prompt
    assert "Partial coverage is acceptable" in topic_prompt
    assert "viewpoint arc" in opinion_prompt
    assert "Prefer 3-6 strong opinion arcs" in opinion_prompt
    print("PASS: test_strategy_prompts_are_meaningfully_different")


def test_strategy_system_prompts_are_split():
    a = make_analyzer()

    opinion_system = a._build_segmentation_system_prompt("opinion")
    topic_system = a._build_segmentation_system_prompt("topic")
    hybrid_system = a._build_segmentation_system_prompt("hybrid")

    assert "opinion-first" in opinion_system.lower()
    assert "topic segmentation assistant" in topic_system.lower()
    assert "balance topic coherence" in hybrid_system.lower()
    assert "do not partition a transcript window into evenly sized chunks" in topic_system.lower()
    print("PASS: test_strategy_system_prompts_are_split")


def test_topic_strategy_filtering_happens_in_analyzer():
    a = make_analyzer()
    a.config.max_clips = 4
    segments = [
        {"start": 0.0, "end": 88.8, "score": 90.0, "topic": "A", "summary": "A", "reason": "A"},
        {"start": 88.8, "end": 182.7, "score": 85.0, "topic": "B", "summary": "B", "reason": "B"},
        {"start": 182.7, "end": 253.6, "score": 92.0, "topic": "C", "summary": "C", "reason": "C"},
        {"start": 253.6, "end": 363.1, "score": 88.0, "topic": "D", "summary": "D", "reason": "D"},
        {"start": 363.1, "end": 480.3, "score": 87.0, "topic": "E", "summary": "E", "reason": "E"},
        {"start": 480.3, "end": 557.3, "score": 75.0, "topic": "F", "summary": "F", "reason": "F"},
        {"start": 557.3, "end": 678.4, "score": 89.0, "topic": "G", "summary": "G", "reason": "G"},
        {"start": 678.4, "end": 797.9, "score": 80.0, "topic": "H", "summary": "H", "reason": "H"},
    ]
    summaries = [{"topic": seg["topic"], "summary": seg["summary"], "score": seg["score"], "reason": seg["reason"]} for seg in segments]

    filtered_segments, filtered_summaries = a._filter_strategy_segments(
        segments,
        summaries,
        clip_strategy="topic",
    )

    assert len(filtered_segments) == 5
    assert {item["topic"] for item in filtered_segments} == {"A", "C", "D", "E", "G"}
    assert {item["topic"] for item in filtered_summaries} == {"A", "C", "D", "E", "G"}


def test_topic_strategy_merges_adjacent_subthemes_in_analyzer():
    a = make_analyzer()
    a.config.max_clips = 4
    segments = [
        {
            "start": 0.0,
            "end": 32.0,
            "topic": "AI Learning Workflow",
            "summary": "The speaker introduces the AI learning workflow and its first phase.",
            "score": 90.0,
            "reason": "Strong theme opener",
        },
        {
            "start": 32.1,
            "end": 118.7,
            "topic": "AI Learning Workflow",
            "summary": "The speaker continues the same AI learning workflow with friction points in the same process.",
            "score": 85.0,
            "reason": "Supports the same broader theme",
        },
        {
            "start": 253.8,
            "end": 363.1,
            "topic": "Step-by-Step AI Workflow Demonstration",
            "summary": "A separate practical demo theme.",
            "score": 92.0,
            "reason": "Distinct workflow block",
        },
    ]

    filtered_segments, filtered_summaries = a._filter_strategy_segments(
        segments,
        summaries=[],
        clip_strategy="topic",
    )

    assert len(filtered_segments) == 2
    assert filtered_segments[0]["start"] == 0.0
    assert filtered_segments[0]["end"] == 118.7
    assert filtered_segments[1]["topic"] == "Step-by-Step AI Workflow Demonstration"
    assert len(filtered_summaries) == 2


def test_topic_strategy_keeps_adjacent_complete_themes_separate():
    a = make_analyzer()
    a.config.max_clips = 4
    segments = [
        {
            "start": 0.0,
            "end": 72.0,
            "topic": "Gemma 4 Model Family Breakdown",
            "summary": "Explains the 2B, 4B, 26B and 31B models and where each one runs best.",
            "score": 87.0,
            "reason": "A full standalone product-family theme.",
        },
        {
            "start": 72.8,
            "end": 138.0,
            "topic": "Gemma 4 Security and Trust",
            "summary": "Shifts to enterprise security posture, trust, and Google DeepMind safeguards.",
            "score": 86.0,
            "reason": "A distinct standalone trust theme.",
        },
    ]

    filtered_segments, filtered_summaries = a._filter_strategy_segments(
        segments,
        summaries=[],
        clip_strategy="topic",
    )

    assert len(filtered_segments) == 2
    assert filtered_segments[0]["topic"] == "Gemma 4 Model Family Breakdown"
    assert filtered_segments[1]["topic"] == "Gemma 4 Security and Trust"
    assert len(filtered_summaries) == 2


if __name__ == "__main__":
    test_normalize_preserves_opinion_fields()
    test_segment_by_topic_returns_three_tuple()
    test_segment_by_topic_fallback_no_asr()
    test_segment_by_topic_fallback_invalid_json()
    test_segments_from_topics_passes_opinion_fields()
    test_segment_by_topic_opinion_fields_missing_marks_ineffective()
    test_strategy_prompts_are_meaningfully_different()
    test_strategy_system_prompts_are_split()
    print("\nAll tests passed.")


# ---------------------------------------------------------------------------
# Test 7: chunked topic segmentation merges overlap duplicates
# ---------------------------------------------------------------------------

def test_segment_by_topic_merges_chunked_overlap_results():
    a = make_analyzer()
    a.topic_segment_chunk_duration = 1000
    a.topic_segment_chunk_overlap_seconds = 120
    a.topic_segment_max_workers = 3

    asr = [
        {"start": 0.0, "end": 500.0, "text": "intro"},
        {"start": 500.0, "end": 1000.0, "text": "boundary"},
        {"start": 1000.0, "end": 1500.0, "text": "core"},
        {"start": 1500.0, "end": 2100.0, "text": "wrap"},
    ]

    def build_response(segments):
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"segments": segments})}}]
        }
        return fake

    responses = {
        "0.0s - 1000.0s": build_response([
            {
                "start": 0.0,
                "end": 900.0,
                "topic": "Opening Thesis",
                "summary": "Sets up the premise.",
                "score": 68,
                "reason": "Strong opening context.",
                "conclusion_clarity": 6,
                "self_contained": 5,
                "info_density": 5,
                "viral_fit": 6,
                "stance": "The talk opens with a premise.",
                "key_sentences": ["The talk opens with a premise."],
                "publishability": 66,
            },
            {
                "start": 850.0,
                "end": 1250.0,
                "topic": "Boundary Insight",
                "summary": "Duplicate boundary segment from the first window.",
                "score": 72,
                "reason": "Touches the transition.",
                "conclusion_clarity": 7,
                "self_contained": 6,
                "info_density": 6,
                "viral_fit": 7,
                "stance": "The transition is important.",
                "key_sentences": ["The transition is important."],
                "publishability": 74,
            },
        ]),
        "880.0s - 1880.0s": build_response([
            {
                "start": 860.0,
                "end": 1300.0,
                "topic": "Boundary Insight",
                "summary": "Duplicate boundary segment from the middle window.",
                "score": 95,
                "reason": "Best version of the overlap.",
                "conclusion_clarity": 9,
                "self_contained": 9,
                "info_density": 8,
                "viral_fit": 9,
                "stance": "The transition is the key opinion.",
                "key_sentences": ["The transition is the key opinion."],
                "publishability": 92,
            },
            {
                "start": 1300.0,
                "end": 1780.0,
                "topic": "Actionable Follow-up",
                "summary": "Second distinct segment.",
                "score": 80,
                "reason": "Clear follow-up recommendation.",
                "conclusion_clarity": 8,
                "self_contained": 8,
                "info_density": 7,
                "viral_fit": 7,
                "stance": "This is the recommended next step.",
                "key_sentences": ["This is the recommended next step."],
                "publishability": 81,
            },
        ]),
        "1760.0s - 2100.0s": build_response([
            {
                "start": 1760.0,
                "end": 2100.0,
                "topic": "Actionable Follow-up",
                "summary": "Third window repeats the follow-up.",
                "score": 78,
                "reason": "Overlap confirmation.",
                "conclusion_clarity": 8,
                "self_contained": 7,
                "info_density": 7,
                "viral_fit": 7,
                "stance": "This is the recommended next step.",
                "key_sentences": ["This is the recommended next step."],
                "publishability": 79,
            }
        ]),
    }

    def fake_post(url, headers=None, json=None, timeout=None):
        prompt = json["messages"][1]["content"]
        match = re.search(r"Window time span: ([0-9.]+)s - ([0-9.]+)s", prompt)
        assert match, prompt
        key = f"{float(match.group(1)):.1f}s - {float(match.group(2)):.1f}s"
        return responses[key]

    with patch("requests.post", side_effect=fake_post):
        segs, summaries, meta = a._segment_by_topic(asr, clip_strategy="opinion")

    assert meta["segmentation_effective"] is True
    assert meta["chunk_count"] == 3
    assert len(segs) == 3
    assert segs[0]["topic"] == "Opening Thesis"
    assert segs[1]["topic"] == "Boundary Insight"
    assert segs[1]["score"] >= 95
    assert segs[1]["publishability"] == 92
    assert segs[2]["topic"] == "Actionable Follow-up"
    print("PASS: test_segment_by_topic_merges_chunked_overlap_results")
