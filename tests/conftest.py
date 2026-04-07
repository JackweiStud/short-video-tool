"""
Pytest configuration and fixtures
"""
import os
import sys
import pytest
import tempfile
import shutil

# Add parent directory to path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs"""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    # Cleanup
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path)


@pytest.fixture
def sample_video_path():
    """Return path to a sample video file (if exists)"""
    video_path = "downloads/Julian Goldie SEO - OpenClaw just dropped an update that is honestly kind of scary.  It c....mp4"
    if os.path.exists(video_path):
        return video_path
    return None


@pytest.fixture
def sample_analysis_result():
    """Return a sample analysis result"""
    return {
        "video_path": "downloads/test_video.mp4",
        "audio_climax_points": [
            {"time": 10.0, "score": 2.5},
            {"time": 30.0, "score": 2.3},
            {"time": 50.0, "score": 2.1}
        ],
        "scene_changes": [0.0, 15.0, 35.0, 55.0, 70.0],
        "asr_result": [
            {"start": 0.0, "end": 5.0, "text": "Sample text 1"},
            {"start": 5.0, "end": 10.0, "text": "Sample text 2"},
            {"start": 10.0, "end": 15.0, "text": "Sample text 3"}
        ]
    }


@pytest.fixture
def sample_clips_metadata():
    """Return sample clips metadata"""
    return {
        "clips": [
            {
                "clip_path": "clips/test_clip_1.mp4",
                "start_time": 0.0,
                "end_time": 30.0,
                "duration": 30.0,
                "score": 2.5,
                "asr_subset": [
                    {"start": 0.0, "end": 5.0, "text": "Sample text 1"},
                    {"start": 5.0, "end": 10.0, "text": "Sample text 2"}
                ]
            }
        ]
    }
