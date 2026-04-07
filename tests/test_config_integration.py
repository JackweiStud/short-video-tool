"""
Focused regression tests for config.py integration.
"""

import os
import subprocess
import sys
import types
from pathlib import Path

import analyzer as analyzer_module
import clipper as clipper_module
import downloader as downloader_module
import config as config_module
import main as main_module
from config import Config
import requests
from analyzer import Analyzer
from clipper import Clipper
from downloader import Downloader
from integrator import Integrator
from translator import Translator


class StubConfig:
    def __init__(self, base_dir: str):
        self.downloads_dir = os.path.join(base_dir, "downloads_cfg")
        self.output_dir = os.path.join(base_dir, "output_cfg")
        self.analysis_dir = os.path.join(base_dir, "analysis_cfg")
        self.clips_dir = os.path.join(base_dir, "clips_cfg")
        self.subtitles_dir = os.path.join(base_dir, "subtitles_cfg")

        self.min_clip_duration = 15
        self.max_clip_duration = 60
        self.max_clips = 2

        self.whisper_model = "base"
        self.whisper_word_timestamps = False
        self.asr_language = "zh"
        self.audio_climax_top_n = 3
        self.scene_detection_threshold = 19.5
        self.topic_segment_min_duration = 20

        self.openai_api_key = None
        self.openai_model = "gpt-4.1-mini"
        self.translation_backend = "googletrans"

        self.video_quality = "720p"
        self.download_retries = 7
        self.ytdlp_cookies_browser = "chrome"
        self.ytdlp_youtube_player_client = "tv"

        self.ffmpeg_timeout = 123

        # ASR chunking config (added for faster-whisper / chunked CLI fallback)
        self.asr_chunk_duration = 600
        self.asr_overlap_seconds = 5
        self.asr_segment_timeout = 600
        self.asr_cache_dir = os.path.join(base_dir, "cache", "asr")
        self.asr_vad_filter = True
        self.asr_vad_min_duration_threshold = 3600.0

        self.enable_topic_segmentation = False
        self.llm_provider = "openai"
        self.llm_model = "gpt-3.5-turbo"
        self.llm_base_url = None
        self.llm_api_key = None
        self.llm_timeout = 30
        self.enable_gpu = False
        self.log_level = "INFO"
        self.log_file = str(Path(__file__).resolve().parent.parent / 'logs' / 'main.log')


def test_downloader_uses_config_retries(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)
    captured_opts = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=True):
            return None

        def prepare_filename(self, info):
            return os.path.join(cfg.downloads_dir, "unused.mp4")

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    downloader = Downloader(config=cfg)
    result = downloader.download_video("https://example.com/video")

    assert result is None
    assert downloader.output_dir == cfg.downloads_dir
    assert captured_opts["retries"] == cfg.download_retries
    assert captured_opts["fragment_retries"] == cfg.download_retries
    assert captured_opts["cookiesfrombrowser"] == (cfg.ytdlp_cookies_browser,)
    assert captured_opts["extractor_args"] == {
        "youtube": {"player_client": [cfg.ytdlp_youtube_player_client]}
    }


def test_downloader_uses_configurable_ytdlp_settings(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)
    cfg.ytdlp_cookies_browser = "firefox"
    cfg.ytdlp_youtube_player_client = "web"
    captured_opts = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=True):
            return None

        def prepare_filename(self, info):
            return os.path.join(cfg.downloads_dir, "unused.mp4")

    monkeypatch.setattr(downloader_module.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    downloader = Downloader(config=cfg)
    result = downloader.download_video("https://example.com/video")

    assert result is None
    assert captured_opts["cookiesfrombrowser"] == ("firefox",)
    assert captured_opts["extractor_args"] == {
        "youtube": {"player_client": ["web"]}
    }


def test_analyzer_uses_config_for_ffmpeg_timeout(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)
    analyzer = Analyzer(config=cfg)
    observed = {"timeout": None}

    def fake_run(cmd, stdout=None, stderr=None, text=None, timeout=None):
        if cmd[:2] == ["ffmpeg", "-version"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")

        observed["timeout"] = timeout
        output_audio_path = cmd[-1]
        with open(output_audio_path, "wb") as f:
            f.write(b"fake-audio")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    audio_path = analyzer._extract_audio("dummy_input.mp4", temp_dir)
    assert audio_path is not None
    assert observed["timeout"] == cfg.ffmpeg_timeout


def test_analyzer_topic_segmentation_uses_llm_and_normalizes_segments(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)
    cfg.enable_topic_segmentation = True
    cfg.llm_api_key = "test-key"
    analyzer = Analyzer(config=cfg)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": """
                            {
                              "segments": [
                                {"start": 0, "end": 8, "topic": "Intro", "summary": "Opening"},
                                {"start": 8, "end": 42, "topic": "Main idea", "summary": "Core"},
                                {"start": 42, "end": 55, "topic": "Wrap up", "summary": "Close"}
                              ]
                            }
                            """
                        }
                    }
                ]
            }

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    topic_segments, topic_summaries, _seg_meta = analyzer._segment_by_topic(
        [
            {"start": 0.0, "end": 15.0, "text": "Intro text"},
            {"start": 15.0, "end": 35.0, "text": "Core text"},
            {"start": 35.0, "end": 56.0, "text": "Ending text"},
        ]
    )

    assert captured["url"].endswith("/chat/completions")
    assert captured["timeout"] == cfg.llm_timeout
    assert "short-video clipping" in captured["json"]["messages"][1]["content"]
    assert topic_segments[0]["start"] == 0.0
    assert topic_segments[0]["end"] >= cfg.topic_segment_min_duration
    assert "summary" in topic_segments[0]
    assert "score" in topic_segments[0]
    assert "reason" in topic_segments[0]
    assert len(topic_segments) == len(topic_summaries)
    assert topic_summaries[0]["summary"]
    assert topic_summaries[0]["score"] >= 0
    assert topic_summaries[0]["reason"]


def test_config_defaults_enable_topic_segmentation(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ENABLE_TOPIC_SEGMENTATION", raising=False)
    cfg = Config()
    assert cfg.enable_topic_segmentation is True
    assert cfg.topic_segment_min_duration == 20
    assert cfg.llm_api_key is None


def test_config_loads_dotenv_from_project_root(monkeypatch, temp_dir):
    env_path = Path(temp_dir) / ".env"
    env_path.write_text(
        'export LLM_API_KEY="dotenv-key"\n'
        "ENABLE_TOPIC_SEGMENTATION=false # disable LLM fallback for this test\n"
        "TOPIC_SEGMENT_MIN_DURATION=25\n",
        encoding="utf-8",
    )

    sandbox_environ = os.environ.copy()
    monkeypatch.setattr(config_module.os, "environ", sandbox_environ, raising=False)
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", Path(temp_dir))

    config_module._load_project_env(force=True)
    cfg = Config()

    assert cfg.llm_api_key == "dotenv-key"
    assert cfg.enable_topic_segmentation is False
    assert cfg.topic_segment_min_duration == 25


def test_clipper_uses_config_for_ffmpeg_timeout_and_max_clips(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)
    clipper = Clipper(config=cfg)

    observed = {"timeout": None}

    def fake_run(cmd, stdout=None, stderr=None, text=None, timeout=None):
        observed["timeout"] = timeout
        output_path = cmd[-1]
        with open(output_path, "wb") as f:
            f.write(b"fake-video")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(clipper_module.subprocess, "run", fake_run)

    out_path = os.path.join(temp_dir, "clip.mp4")
    assert clipper._clip_with_ffmpeg("input.mp4", out_path, 0.0, 5.0) is True
    assert observed["timeout"] == cfg.ffmpeg_timeout

    scene_changes = [0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 120.0]
    segments = clipper._segments_from_scene_changes(scene_changes)
    assert len(segments) <= cfg.max_clips


def test_integrator_uses_config_output_dir(temp_dir):
    cfg = StubConfig(temp_dir)
    integrator = Integrator(config=cfg)

    assert integrator.output_dir == cfg.output_dir
    assert integrator.original_dir == os.path.join(cfg.output_dir, "original")
    assert integrator.clips_dir == os.path.join(cfg.output_dir, "clips")


def test_translator_uses_config_model_backend(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)

    fake_module = types.ModuleType("deep_translator")

    class FakeGoogleTranslator:
        def __init__(self, source=None, target=None):
            self.source = source
            self.target = target

        def translate(self, text):
            return text

    fake_module.GoogleTranslator = FakeGoogleTranslator
    monkeypatch.setitem(__import__("sys").modules, "deep_translator", fake_module)

    translator = Translator(config=cfg)

    assert translator.model == cfg.openai_model
    assert translator.backend == cfg.translation_backend
    assert translator.default_output_dir == cfg.subtitles_dir


def test_analyzer_resolves_whisper_cli_from_environment(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)

    whisper_cli = Path(temp_dir) / "whisper"
    whisper_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    whisper_cli.chmod(0o755)

    monkeypatch.setenv("WHISPER_CLI_PATH", str(whisper_cli))
    monkeypatch.setattr(analyzer_module.shutil, "which", lambda name: None)

    analyzer = Analyzer(config=cfg)

    assert analyzer.whisper_cli == str(whisper_cli)


def test_main_propagates_config_video_quality_to_downloader(monkeypatch, temp_dir):
    cfg = StubConfig(temp_dir)
    cfg.video_quality = "1080p"
    cfg.log_file = os.path.join(temp_dir, "main-test.log")

    captured = {}

    class FakeDownloader:
        def __init__(self, output_dir=None, config=None):
            self.output_dir = output_dir
            self.config = config

        def download_video(self, url, quality="best"):
            captured["url"] = url
            captured["quality"] = quality
            return {"filepath": os.path.join(temp_dir, "downloaded.mp4")}

    class FakeAnalyzer:
        def __init__(self, config=None):
            self.config = config

        def analyze_video(self, video_path, output_dir=None, **kwargs):
            return {"scene_changes": [0.0], "audio_climax_points": [], "asr_result": []}

    class FakeClipper:
        def __init__(self, min_duration=None, max_duration=None, max_clips=None, config=None):
            self.config = config

        def clip_video(self, video_path, analysis_result, output_dir=None):
            return {"clips": [{"clip_path": "clip-1.mp4"}]}

    class FakeTranslator:
        def __init__(self, config=None):
            self.config = config

        def translate_clips(self, clips_metadata_path, output_dir=None):
            return {"clips": [{"subtitle_path": "clip-1.srt"}]}

    class FakeIntegrator:
        def __init__(self, output_dir=None, config=None):
            self.output_dir = output_dir
            self.config = config

        def integrate(self, video_path, analysis_result_path, clips_metadata_path, translations_metadata_path):
            return {"summary": "ok"}

    monkeypatch.setattr(main_module, "get_config", lambda: cfg)
    monkeypatch.setattr(main_module, "_configure_logging", lambda log_level, log_file: None)
    monkeypatch.setattr(main_module, "_acquire_lock", lambda: None)
    monkeypatch.setattr(main_module, "Downloader", FakeDownloader)
    monkeypatch.setattr(main_module, "Analyzer", FakeAnalyzer)
    monkeypatch.setattr(main_module, "Clipper", FakeClipper)
    monkeypatch.setattr(main_module, "Translator", FakeTranslator)
    monkeypatch.setattr(main_module, "Integrator", FakeIntegrator)
    monkeypatch.setattr(sys, "argv", ["main.py", "--url", "https://example.com/video"])

    exit_code = main_module.main()

    assert exit_code == 0
    assert captured["quality"] == "1080p"
