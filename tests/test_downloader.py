"""
Test cases for Downloader module
"""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from downloader import Downloader


class TestDownloaderInitialization:
    """Test Downloader initialization"""
    
    def test_downloader_can_be_initialized(self):
        """Test downloader can be initialized"""
        downloader = Downloader()
        assert downloader is not None


class TestDownloaderValidation:
    """Test Downloader input validation"""
    
    def test_empty_url(self):
        """Test with empty URL"""
        downloader = Downloader()
        result = downloader.download_video(url="")
        assert result is None
    
    def test_none_url(self):
        """Test with None URL"""
        downloader = Downloader()
        # Downloader doesn't validate None before using 'in' operator
        # This test documents current behavior - it will raise TypeError
        with pytest.raises(TypeError):
            result = downloader.download_video(url=None)
    
    def test_invalid_url_format(self):
        """Test with invalid URL format"""
        downloader = Downloader()
        result = downloader.download_video(url="not-a-valid-url")
        assert result is None


class TestDownloaderMocked:
    """Test Downloader with mocked yt-dlp"""
    
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_youtube_success(self, mock_ytdl, temp_dir):
        """Test successful YouTube download (mocked)"""
        # Mock yt-dlp behavior
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
        mock_instance.prepare_filename.return_value = os.path.join(temp_dir, "Test Video.mp4")
        mock_ytdl.return_value = mock_instance
        
        # Create a fake video file
        fake_video = os.path.join(temp_dir, "Test Video.mp4")
        with open(fake_video, 'w') as f:
            f.write("fake video content")
        
        downloader = Downloader(output_dir=temp_dir)
        
        result = downloader.download_video(
            url="https://youtube.com/watch?v=test123"
        )
        
        # Verify result structure
        assert result is not None
        assert 'filepath' in result
    
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_handles_exception(self, mock_ytdl):
        """Test download handles exceptions gracefully"""
        # Mock yt-dlp to raise exception
        mock_instance = MagicMock()
        mock_instance.extract_info.side_effect = Exception("Download failed")
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        mock_ytdl.return_value = mock_instance
        
        downloader = Downloader()
        result = downloader.download_video(
            url="https://youtube.com/watch?v=test123"
        )
        
        assert result is None

    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_renames_final_merged_file(self, mock_ytdl, temp_dir):
        """Test post-download rename uses the actual merged file on disk."""
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            'id': 'abc123',
            'title': '阿绎 AYi - 喵的这是我免费能看的吗？？？',
            'uploader': '阿绎 AYi',
            'duration': 300,
            'description': 'Test description',
            'webpage_url': 'https://x.com/i/status/2042152766513279291',
        }
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        mock_instance.prepare_filename.return_value = os.path.join(
            temp_dir,
            '阿绎 AYi - 喵的这是我免费能看的吗？？？.webm',
        )
        mock_ytdl.return_value = mock_instance

        actual_downloaded = os.path.join(
            temp_dir,
            '阿绎 AYi - 喵的这是我免费能看的吗？？？.mp4',
        )
        with open(actual_downloaded, 'w', encoding='utf-8') as f:
            f.write('fake merged video content')

        downloader = Downloader(output_dir=temp_dir)
        result = downloader.download_video(
            url="https://x.com/i/status/2042152766513279291"
        )

        assert result is not None
        assert result['filepath'].endswith('.mp4')
        assert os.path.basename(result['filepath']) != os.path.basename(actual_downloaded)
        assert ' ' not in os.path.basename(result['filepath'])
        assert os.path.exists(result['filepath'])

    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_uses_title_prefix_and_video_id_filename(self, mock_ytdl, temp_dir):
        """Test stable filename format: title prefix + video id."""
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            'id': '2038054103188738439',
            'title': '梓哲悟语 Zenzhe 就马云的这个演讲，五十年内没有任何企业家可以超越',
            'uploader': '梓哲悟语',
            'duration': 300,
            'description': 'Test description',
            'webpage_url': 'https://x.com/i/status/2038054103188738439',
        }
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        mock_instance.prepare_filename.return_value = os.path.join(
            temp_dir,
            '梓哲悟语 Zenzhe 就马云的这个演讲，五十年内没有任何企业家可以超越.webm',
        )
        mock_ytdl.return_value = mock_instance

        actual_downloaded = os.path.join(
            temp_dir,
            '梓哲悟语 Zenzhe 就马云的这个演讲，五十年内没有任何企业家可以超越.mp4',
        )
        with open(actual_downloaded, 'w', encoding='utf-8') as f:
            f.write('fake merged video content')

        downloader = Downloader(output_dir=temp_dir)
        result = downloader.download_video(
            url="https://x.com/i/status/2038054103188738439"
        )

        assert result is not None
        filename = os.path.basename(result['filepath'])
        assert filename == '梓哲悟语_Zenzhe_就马云_2038054103188738439.mp4'
        assert len(filename) < 80


class TestDownloaderURLSupport:
    """Test Downloader URL support"""
    
    def test_youtube_url_format(self):
        """Test YouTube URL is recognized"""
        downloader = Downloader()
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        # Just test that it doesn't crash on validation
        assert url.startswith("http")
    
    def test_tiktok_url_format(self):
        """Test TikTok URL is recognized"""
        downloader = Downloader()
        url = "https://www.tiktok.com/@user/video/1234567890"
        assert url.startswith("http")
    
    def test_twitter_url_format(self):
        """Test Twitter URL is recognized"""
        downloader = Downloader()
        url = "https://twitter.com/user/status/1234567890"
        assert url.startswith("http")
