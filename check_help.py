import sys
from unittest.mock import MagicMock

# Define the mock modules list
MOCK_MODULES = [
    'librosa', 'moviepy', 'moviepy.editor', 'PIL', 'imageio_ffmpeg', 
    'subtitle_detect', 'subtitle_sync', 'analyzer', 'clipper', 
    'config', 'downloader', 'integrator', 'translator'
]

# Apply mocks before any imports
for mod_name in MOCK_MODULES:
    mock = MagicMock()
    sys.modules[mod_name] = mock

# Inject some defaults that main.py needs from config
class FakeConfig:
    def __init__(self):
        self.log_level = 'INFO'
        self.log_file = 'logs/main.log'
        self.video_quality = '1080p'
        self.output_dir = 'output'
        self.min_clip_duration = 15
        self.max_clip_duration = 60
        self.max_clips = 5
        self.asr_language = 'en'

config_mock = sys.modules['config']
config_mock.get_config.return_value = FakeConfig()
config_mock.VALID_VIDEO_QUALITIES = ['360p', '480p', '720p', '1080p', 'best']

# Mock the _acquire_lock to avoid PID lock issues during test
import os
import main
main._acquire_lock = lambda: None

if __name__ == "__main__":
    # Simulate calling main() with --help
    sys.argv = ['main.py', '--help']
    try:
        main.main()
    except SystemExit:
        pass
