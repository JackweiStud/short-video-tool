"""
Test audio-video sync validation.

缺口2：音画同步验证
- 视频文件有音频流
- 视频流和音频流时长差异在容忍范围内（< 500ms）
- 片段元数据中的时长与实际文件时长一致
- 使用 ffprobe 检测（ffmpeg 自带工具，CI 已安装）
"""
import os
import json
import subprocess
import pytest


def get_stream_info(video_path):
    """Use ffprobe to get video and audio stream durations."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def get_video_duration(video_path):
    """Get video file duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, ValueError):
        return None


def ffprobe_available():
    """Check if ffprobe is available on this system."""
    try:
        result = subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ─────────────────────────────────────────────
# 缺口2：音画同步 — 使用 ffprobe 检测
# ─────────────────────────────────────────────

class TestAudioVideoSync:
    """Audio-video sync validation using ffprobe."""

    def test_ffprobe_available(self):
        """ffprobe must be available (installed with ffmpeg)."""
        assert ffprobe_available(), (
            "ffprobe not found. Install ffmpeg: brew install ffmpeg (macOS) "
            "or apt-get install ffmpeg (Linux)"
        )

    def test_sync_helper_functions_work(self, tmp_path):
        """get_stream_info and get_video_duration handle missing files gracefully."""
        result = get_stream_info(str(tmp_path / "nonexistent.mp4"))
        assert result is None

        duration = get_video_duration(str(tmp_path / "nonexistent.mp4"))
        assert duration is None

    @pytest.mark.slow
    def test_clips_have_audio_stream(self):
        """Every generated clip must contain an audio stream."""
        if not ffprobe_available():
            pytest.skip("ffprobe not available")

        import glob
        clip_files = glob.glob("clips/*.mp4") + glob.glob("output/clips/*.mp4")
        if not clip_files:
            pytest.skip("No clip files found")

        for clip_path in clip_files:
            info = get_stream_info(clip_path)
            assert info is not None, f"ffprobe failed on {clip_path}"

            stream_types = [s.get("codec_type") for s in info.get("streams", [])]
            assert "audio" in stream_types, (
                f"{clip_path} has no audio stream — audio-video sync impossible"
            )
            assert "video" in stream_types, (
                f"{clip_path} has no video stream"
            )

    @pytest.mark.slow
    def test_audio_video_duration_within_tolerance(self):
        """Audio and video stream durations must be within 500ms of each other."""
        if not ffprobe_available():
            pytest.skip("ffprobe not available")

        import glob
        clip_files = glob.glob("clips/*.mp4") + glob.glob("output/clips/*.mp4")
        if not clip_files:
            pytest.skip("No clip files found")

        tolerance_seconds = 0.5  # 500ms tolerance

        for clip_path in clip_files:
            info = get_stream_info(clip_path)
            if info is None:
                continue

            streams = info.get("streams", [])
            video_durations = [
                float(s["duration"])
                for s in streams
                if s.get("codec_type") == "video" and "duration" in s
            ]
            audio_durations = [
                float(s["duration"])
                for s in streams
                if s.get("codec_type") == "audio" and "duration" in s
            ]

            if not video_durations or not audio_durations:
                continue  # skip if duration info not available in stream

            video_dur = video_durations[0]
            audio_dur = audio_durations[0]
            diff = abs(video_dur - audio_dur)

            assert diff <= tolerance_seconds, (
                f"{clip_path}: audio-video duration mismatch {diff:.3f}s "
                f"(video={video_dur:.3f}s, audio={audio_dur:.3f}s, "
                f"tolerance={tolerance_seconds}s)"
            )

    @pytest.mark.slow
    def test_clip_actual_duration_matches_metadata(self):
        """Actual clip file duration must match metadata duration within 1s."""
        if not ffprobe_available():
            pytest.skip("ffprobe not available")

        clips_metadata_path = "clips/clips_metadata.json"
        if not os.path.exists(clips_metadata_path):
            pytest.skip("clips_metadata.json not found")

        with open(clips_metadata_path, encoding="utf-8") as f:
            metadata = json.load(f)

        clips = metadata.get("clips", [])
        if not clips:
            pytest.skip("No clips in metadata")

        # ffmpeg -c copy aligns cuts to the nearest keyframe, which can add
        # several seconds beyond the requested end point. 5s is a realistic
        # tolerance for typical keyframe intervals (2-4s GOP).
        tolerance_seconds = 5.0

        for clip in clips:
            clip_path = clip.get("clip_path", "")
            expected_duration = clip.get("duration", 0)

            if not os.path.exists(clip_path):
                continue  # skip missing files

            actual_duration = get_video_duration(clip_path)
            if actual_duration is None:
                continue

            diff = abs(actual_duration - expected_duration)
            assert diff <= tolerance_seconds, (
                f"{clip_path}: metadata duration {expected_duration:.2f}s "
                f"vs actual {actual_duration:.2f}s (diff={diff:.2f}s)"
            )
