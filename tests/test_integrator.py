"""
Test cases for Integrator module
"""
import os
import pytest
import json
from integrator import Integrator


class TestIntegratorInitialization:
    """Test Integrator initialization"""
    
    def test_integrator_can_be_initialized(self, temp_dir):
        """Test integrator can be initialized"""
        integrator = Integrator(output_dir=temp_dir)
        assert integrator is not None


class TestIntegratorValidation:
    """Test Integrator input validation"""
    
    def test_missing_video_path(self, temp_dir):
        """Test with missing video path"""
        integrator = Integrator(output_dir=temp_dir)
        result = integrator.integrate(
            video_path="nonexistent.mp4",
            analysis_result_path="analysis.json",
            clips_metadata_path="clips.json",
            translations_metadata_path="translations.json"
        )
        assert result is None
    
    def test_missing_analysis_result(self, temp_dir):
        """Test with missing analysis result"""
        # Create a dummy video file
        video_path = os.path.join(temp_dir, "video.mp4")
        with open(video_path, 'w') as f:
            f.write("dummy")
        
        integrator = Integrator(output_dir=temp_dir)
        result = integrator.integrate(
            video_path=video_path,
            analysis_result_path="nonexistent.json",
            clips_metadata_path="clips.json",
            translations_metadata_path="translations.json"
        )
        assert result is None


class TestIntegratorFunctionality:
    """Test Integrator functionality"""
    
    @pytest.mark.slow
    def test_integrate_success(self, temp_dir):
        """Test successful integration"""
        # Check if all required files exist
        video_path = "downloads/Julian Goldie SEO - OpenClaw just dropped an update that is honestly kind of scary.  It c....mp4"
        analysis_path = "analysis_results/analysis_result.json"
        clips_path = "clips/clips_metadata.json"
        translations_path = "subtitles/translations_metadata.json"
        
        if not all([
            os.path.exists(video_path),
            os.path.exists(analysis_path),
            os.path.exists(clips_path),
            os.path.exists(translations_path)
        ]):
            pytest.skip("Required files not available")
        
        integrator = Integrator(output_dir=temp_dir)
        result = integrator.integrate(
            video_path=video_path,
            analysis_result_path=analysis_path,
            clips_metadata_path=clips_path,
            translations_metadata_path=translations_path
        )
        
        assert result is not None
        assert 'video_title' in result
        assert 'clips' in result
        assert 'statistics' in result
    
    @pytest.mark.slow
    def test_output_directory_structure(self, temp_dir):
        """Test output directory structure is created"""
        video_path = "downloads/Julian Goldie SEO - OpenClaw just dropped an update that is honestly kind of scary.  It c....mp4"
        analysis_path = "analysis_results/analysis_result.json"
        clips_path = "clips/clips_metadata.json"
        translations_path = "subtitles/translations_metadata.json"
        
        if not all([
            os.path.exists(video_path),
            os.path.exists(analysis_path),
            os.path.exists(clips_path),
            os.path.exists(translations_path)
        ]):
            pytest.skip("Required files not available")
        
        integrator = Integrator(output_dir=temp_dir)
        result = integrator.integrate(
            video_path=video_path,
            analysis_result_path=analysis_path,
            clips_metadata_path=clips_path,
            translations_metadata_path=translations_path
        )
        
        if result:
            assert os.path.exists(os.path.join(temp_dir, "original"))
            assert os.path.exists(os.path.join(temp_dir, "clips"))
            assert os.path.exists(os.path.join(temp_dir, "subtitles"))
            assert os.path.exists(os.path.join(temp_dir, "analysis"))

    def test_summary_contains_final_subtitle_strategy(self, temp_dir):
        """Summary should explicitly surface the resolved subtitle strategy."""
        integrator = Integrator(output_dir=temp_dir)
        video_path = os.path.join(temp_dir, "sample.mp4")
        with open(video_path, "wb") as f:
            f.write(b"sample")
        result = {
            "video_title": "Sample Video",
            "original_video": video_path,
            "analysis_result": "/tmp/analysis.json",
            "clips": [
                {
                    "clip_number": 1,
                    "clip_path": "/tmp/clip.mp4",
                    "start_time": 0.0,
                    "end_time": 10.0,
                    "duration": 10.0,
                    "score": 1.0,
                    "subtitle_files": {
                        "en": "/tmp/sample_en.srt",
                        "zh": "/tmp/sample_zh.srt",
                    },
                    "subtitle_burn": {
                        "subtitle_burn_policy_summary": "EN=replace / ZH=skip / BILINGUAL=replace",
                        "auto_final_action": "mask_existing_hard_subtitles_and_burn_dual_subtitles",
                        "burn_renderer": "ffmpeg",
                    },
                }
            ],
            "statistics": {
                "total_clips": 1,
                "total_subtitles": 2,
                "processing_time": 1.23,
                "timestamp": "2026-04-07T00:00:00",
            }
        }
        analysis_result = {"video_path": video_path}

        summary_path = integrator._generate_summary(result, analysis_result)
        with open(summary_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "最终策略" in content
        assert "EN=replace / ZH=skip / BILINGUAL=replace" in content
        assert "最终动作" in content
        assert "渲染器" in content
