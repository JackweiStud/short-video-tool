"""
Test cases for Analyzer module
"""
import os
import pytest
import tempfile
from unittest.mock import patch
from analyzer import Analyzer


class TestAnalyzerExceptions:
    """Test exception handling in Analyzer"""
    
    def test_nonexistent_file(self):
        """Test with non-existent file"""
        analyzer = Analyzer()
        result = analyzer.analyze_video("nonexistent_video.mp4")
        assert result is None
    
    def test_empty_file(self, temp_dir):
        """Test with empty file"""
        empty_file = os.path.join(temp_dir, "empty.mp4")
        with open(empty_file, 'w') as f:
            pass
        
        analyzer = Analyzer()
        result = analyzer.analyze_video(empty_file)
        assert result is None
    
    def test_corrupted_file(self, temp_dir):
        """Test with corrupted/invalid video file"""
        invalid_file = os.path.join(temp_dir, "invalid.mp4")
        with open(invalid_file, 'w') as f:
            f.write("This is not a video file")
        
        analyzer = Analyzer()
        result = analyzer.analyze_video(invalid_file)
        assert result is None


class TestAnalyzerFunctionality:
    """Test Analyzer functionality with real video"""
    
    @pytest.mark.slow
    def test_analyze_video_success(self, sample_video_path, temp_dir):
        """Test successful video analysis"""
        if sample_video_path is None:
            pytest.skip("Sample video not available")
        
        analyzer = Analyzer()
        result = analyzer.analyze_video(sample_video_path, output_dir=temp_dir)
        
        assert result is not None
        assert 'asr_result' in result
        assert 'audio_climax_points' in result
        assert 'scene_changes' in result
        assert isinstance(result['asr_result'], list)
        assert isinstance(result['audio_climax_points'], list)
        assert isinstance(result['scene_changes'], list)
    
    @pytest.mark.slow
    def test_asr_output_format(self, sample_video_path, temp_dir):
        """Test ASR output format"""
        if sample_video_path is None:
            pytest.skip("Sample video not available")
        
        analyzer = Analyzer()
        result = analyzer.analyze_video(sample_video_path, output_dir=temp_dir)
        
        if result and result['asr_result']:
            asr_segment = result['asr_result'][0]
            assert 'start' in asr_segment
            assert 'end' in asr_segment
            assert 'text' in asr_segment
            assert isinstance(asr_segment['start'], (int, float))
            assert isinstance(asr_segment['end'], (int, float))
            assert isinstance(asr_segment['text'], str)

    def test_opinion_strategy_skips_audio_and_scene_analysis(self, temp_dir):
        analyzer = Analyzer()
        video_path = os.path.join(temp_dir, "sample.mp4")
        audio_path = os.path.join(temp_dir, "audio.wav")
        with open(video_path, "wb") as f:
            f.write(b"video")
        with open(audio_path, "wb") as f:
            f.write(b"audio")

        with patch.object(analyzer, "_extract_audio", return_value=audio_path), \
             patch("os.path.getsize", return_value=1024), \
             patch.object(analyzer, "_run_asr", return_value=[{"start": 0.0, "end": 1.0, "text": "hello"}]), \
             patch.object(analyzer, "_analyze_audio") as analyze_audio, \
             patch.object(analyzer, "_detect_scenes") as detect_scenes, \
             patch.object(analyzer, "_segment_by_topic", return_value=([{"start": 0.0, "end": 10.0, "score": 80}], [], {"clip_strategy_used": "opinion", "segmentation_effective": True, "fallback_reason": ""})):
            result = analyzer.analyze_video(video_path, output_dir=temp_dir, clip_strategy="opinion")

        assert result is not None
        assert result["audio_climax_points"] == []
        assert result["scene_changes"] == []
        analyze_audio.assert_not_called()
        detect_scenes.assert_not_called()

    def test_hybrid_strategy_runs_audio_and_scene_analysis(self, temp_dir):
        analyzer = Analyzer()
        video_path = os.path.join(temp_dir, "sample.mp4")
        audio_path = os.path.join(temp_dir, "audio.wav")
        with open(video_path, "wb") as f:
            f.write(b"video")
        with open(audio_path, "wb") as f:
            f.write(b"audio")

        with patch.object(analyzer, "_extract_audio", return_value=audio_path), \
             patch("os.path.getsize", return_value=1024), \
             patch.object(analyzer, "_run_asr", return_value=[{"start": 0.0, "end": 1.0, "text": "hello"}]), \
             patch.object(analyzer, "_analyze_audio", return_value=[{"time": 1.0, "score": 1.0}]) as analyze_audio, \
             patch.object(analyzer, "_detect_scenes", return_value=[0.0, 2.0]) as detect_scenes, \
             patch.object(analyzer, "_segment_by_topic", return_value=([], [], {"clip_strategy_used": "hybrid", "segmentation_effective": False, "fallback_reason": ""})):
            result = analyzer.analyze_video(video_path, output_dir=temp_dir, clip_strategy="hybrid")

        assert result is not None
        assert result["audio_climax_points"] == [{"time": 1.0, "score": 1.0}]
        assert result["scene_changes"] == [0.0, 2.0]
        analyze_audio.assert_called_once()
        detect_scenes.assert_called_once()
