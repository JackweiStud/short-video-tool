"""
End-to-end test cases for the complete video processing pipeline
"""
import os
import pytest
from unittest.mock import patch, MagicMock
from downloader import Downloader
from analyzer import Analyzer
from clipper import Clipper
from translator import Translator
from integrator import Integrator


class TestEndToEndPipeline:
    """Test complete pipeline from URL to final output"""
    
    @pytest.mark.slow
    @pytest.mark.e2e
    def test_complete_pipeline_with_real_data(self, temp_dir):
        """Test complete pipeline with real downloaded video"""
        # Check if we have a real video to work with
        video_path = "downloads/Julian Goldie SEO - OpenClaw just dropped an update that is honestly kind of scary.  It c....mp4"
        if not os.path.exists(video_path):
            pytest.skip("Real video not available for E2E test")
        
        # Step 1: Analyze (skip download since we have the video)
        analyzer = Analyzer()
        analysis_dir = os.path.join(temp_dir, "analysis")
        os.makedirs(analysis_dir, exist_ok=True)
        analysis_result = analyzer.analyze_video(video_path, output_dir=analysis_dir)
        assert analysis_result is not None, "Analysis failed"
        
        # Step 2: Clip
        clipper = Clipper(min_duration=15, max_duration=60)
        clips_dir = os.path.join(temp_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)
        clips_result = clipper.clip_video(video_path, analysis_result, output_dir=clips_dir)
        assert clips_result is not None, "Clipping failed"
        assert len(clips_result['clips']) > 0, "No clips created"
        
        # Step 3: Translate
        translator = Translator()
        subtitles_dir = os.path.join(temp_dir, "subtitles")
        os.makedirs(subtitles_dir, exist_ok=True)
        
        # Save clips metadata for translator
        clips_metadata_path = os.path.join(clips_dir, "clips_metadata.json")
        import json
        with open(clips_metadata_path, 'w', encoding='utf-8') as f:
            json.dump(clips_result, f, ensure_ascii=False, indent=2)
        
        translations_result = translator.translate_clips(clips_metadata_path, output_dir=subtitles_dir)
        assert translations_result is not None, "Translation failed"
        
        # Step 4: Integrate
        integrator = Integrator(output_dir=os.path.join(temp_dir, "output"))
        
        # Save analysis result for integrator
        analysis_result_path = os.path.join(analysis_dir, "analysis_result.json")
        with open(analysis_result_path, 'w', encoding='utf-8') as f:
            json.dump(analysis_result, f, ensure_ascii=False, indent=2)
        
        # Save translations metadata for integrator
        translations_metadata_path = os.path.join(subtitles_dir, "translations_metadata.json")
        with open(translations_metadata_path, 'w', encoding='utf-8') as f:
            json.dump(translations_result, f, ensure_ascii=False, indent=2)
        
        integration_result = integrator.integrate(
            video_path=video_path,
            analysis_result_path=analysis_result_path,
            clips_metadata_path=clips_metadata_path,
            translations_metadata_path=translations_metadata_path
        )
        assert integration_result is not None, "Integration failed"
        
        # Verify final output structure
        output_dir = os.path.join(temp_dir, "output")
        assert os.path.exists(os.path.join(output_dir, "original"))
        assert os.path.exists(os.path.join(output_dir, "clips"))
        assert os.path.exists(os.path.join(output_dir, "subtitles"))
        assert os.path.exists(os.path.join(output_dir, "analysis"))
        assert os.path.exists(os.path.join(output_dir, "summary.md"))
    
    @pytest.mark.e2e
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_pipeline_with_mocked_download(self, mock_ytdl, temp_dir):
        """Test pipeline with mocked download step"""
        # Mock download
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            'title': 'Test Video',
            'uploader': 'Test Channel',
            'duration': 300,
            'description': 'Test description',
            'webpage_url': 'https://youtube.com/watch?v=test123'
        }
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        mock_ytdl.return_value = mock_instance
        
        # Create a fake video file
        fake_video = os.path.join(temp_dir, "Test Video.mp4")
        with open(fake_video, 'wb') as f:
            f.write(b"fake video content" * 1000)  # Make it non-empty
        
        downloader = Downloader(output_dir=temp_dir)
        
        # Mock the file finding logic
        with patch('os.listdir', return_value=['Test Video.mp4']):
            with patch('os.path.exists', return_value=True):
                with patch('os.path.getsize', return_value=18000):
                    download_result = downloader.download_video(
                        url="https://youtube.com/watch?v=test123"
                    )
        
        # Verify download result structure
        assert download_result is not None
        assert 'filepath' in download_result
    
    @pytest.mark.e2e
    def test_pipeline_error_handling(self, temp_dir):
        """Test pipeline handles errors gracefully"""
        # Test with invalid video path
        analyzer = Analyzer()
        result = analyzer.analyze_video("nonexistent.mp4", output_dir=temp_dir)
        assert result is None
        
        # Test clipper with invalid analysis
        clipper = Clipper()
        result = clipper.clip_video("nonexistent.mp4", None, output_dir=temp_dir)
        assert result is None
        
        # Test integrator with missing files
        integrator = Integrator(output_dir=temp_dir)
        result = integrator.integrate(
            video_path="nonexistent.mp4",
            analysis_result_path="nonexistent.json",
            clips_metadata_path="nonexistent.json",
            translations_metadata_path="nonexistent.json"
        )
        assert result is None
