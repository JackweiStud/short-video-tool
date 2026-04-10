"""
Test cases for Analyzer module
"""
import json
import os
import sys
import types
import pytest
import tempfile
from unittest.mock import patch
from pathlib import Path
import analyzer as analyzer_module
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

    def test_merge_asr_segments_deduplicates_overlapping_duplicates(self):
        analyzer = Analyzer()
        segments = [
            {
                "start": 295.000,
                "end": 296.935,
                "text": "you need to think through all these things, not just the product.",
            },
            {
                "start": 295.639,
                "end": 297.132,
                "text": "you need to think through all these things, not just the product.",
            },
            {
                "start": 297.400,
                "end": 298.000,
                "text": "If it were.",
            },
        ]

        merged = analyzer._merge_asr_segments(segments)

        assert len(merged) == 2
        assert merged[0]["text"] == "you need to think through all these things, not just the product."
        assert merged[1]["text"] == "If it were."

    def test_resolve_asr_initial_prompt_only_for_zh(self):
        analyzer = Analyzer()
        analyzer.config.asr_initial_prompt_enabled = True
        analyzer.config.asr_initial_prompt_text = "中文 prompt"

        assert analyzer._resolve_asr_initial_prompt("zh") == "中文 prompt"
        assert analyzer._resolve_asr_initial_prompt("zh-CN") == "中文 prompt"
        assert analyzer._resolve_asr_initial_prompt("en") is None

    def test_mlx_whisper_passes_initial_prompt(self, temp_dir, monkeypatch):
        analyzer = Analyzer()
        analyzer.config.asr_initial_prompt_enabled = True
        analyzer.config.asr_initial_prompt_text = "中文 prompt"
        analyzer.config.mlx_whisper_local_model_dir = ""
        analyzer.asr_cache_dir = Path(temp_dir) / "cache"
        analyzer.asr_cache_dir.mkdir(parents=True, exist_ok=True)

        captured = {}

        def fake_run(cmd, stdout=None, stderr=None, timeout=None):
            if cmd and cmd[0] == analyzer.ffmpeg_bin:
                with open(cmd[-1], "wb") as f:
                    f.write(b"fake-wav")
                return analyzer_module.subprocess.CompletedProcess(cmd, 0, b"", b"")
            return analyzer_module.subprocess.CompletedProcess(cmd, 0, b"", b"")

        class FakeMLXWhisper:
            @staticmethod
            def transcribe(*args, **kwargs):
                captured["kwargs"] = kwargs
                return {
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "text": "测试",
                            "words": [],
                        }
                    ]
                }

        monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)
        monkeypatch.setitem(sys.modules, "mlx_whisper", FakeMLXWhisper)
        monkeypatch.setattr(analyzer, "_get_audio_duration", lambda _: 1.0)
        monkeypatch.setattr(analyzer, "_build_asr_chunk_windows", lambda duration: [(0, 0.0, 1.0)])
        monkeypatch.setattr(
            analyzer,
            "_build_asr_chunk_cache_file",
            lambda cache_key_prefix, chunk_idx: analyzer.asr_cache_dir / f"{cache_key_prefix}_{chunk_idx}.json",
        )

        result = analyzer._run_asr_mlx_whisper(
            "/tmp/input.wav",
            model="medium",
            language="zh",
            cache_source_path="/tmp/input.wav",
        )

        assert result
        assert captured["kwargs"]["initial_prompt"] == "中文 prompt"
        assert captured["kwargs"]["language"] == "zh"

    def test_summary_only_analysis_skips_other_steps(self, temp_dir):
        analyzer = Analyzer()
        video_path = os.path.join(temp_dir, "sample.mp4")
        audio_path = os.path.join(temp_dir, "audio.wav")
        with open(video_path, "wb") as f:
            f.write(b"video")
        with open(audio_path, "wb") as f:
            f.write(b"audio")

        with patch.object(analyzer, "_extract_audio", return_value=audio_path), \
             patch.object(analyzer, "_run_asr", return_value=[{"start": 0.0, "end": 1.0, "text": "hello"}]) as run_asr, \
             patch.object(analyzer, "_analyze_audio") as analyze_audio, \
             patch.object(analyzer, "_detect_scenes") as detect_scenes:
            result = analyzer.analyze_video_for_summary(video_path, output_dir=temp_dir)

        assert result is not None
        assert result["asr_result"] == [{"start": 0.0, "end": 1.0, "text": "hello"}]
        run_asr.assert_called_once()
        analyze_audio.assert_not_called()
        detect_scenes.assert_not_called()

    def test_generate_video_summary_writes_markdown(self, temp_dir, monkeypatch, sample_analysis_result):
        analyzer = Analyzer()
        analyzer.llm_api_key = "test-key"
        analyzer.llm_base_url = "https://example.com/v1"
        analyzer.llm_model = "test-model"

        summary_json = {
            "title": "测试视频总结",
            "one_sentence_summary": "这是一个示例视频的核心总结。",
            "core_points": ["核心观点一", "核心观点二", "核心观点三"],
            "evidence_points": ["明确依据一", "明确依据二"],
            "insights": ["启示一", "启示二", "启示三"],
            "actionable_takeaways": ["建议一", "建议二", "建议三"],
            "caveats": ["边界一", "边界二"],
            "best_for": ["产品经理", "内容创作者"],
            "keywords": ["AI", "总结", "视频"],
            "x_post_copy_zh": "Sam Altman 把创业讲得很直接：好想法、好产品、好团队、好执行。更重要的是，真正值得做的公司，往往一开始并不被看好。如果你在想怎么判断一个创意值不值得做，这支视频值得看。",
            "x_post_copy_en": "Sam Altman breaks down startup success into four things: a good idea, a great product, a strong team, and solid execution. The best ideas often look bad at first. If you're trying to judge whether a startup idea is worth pursuing, this video is worth your time.",
        }

        def fake_post(*args, **kwargs):
            content = json.dumps(summary_json, ensure_ascii=False)

            class FakeResponse:
                status_code = 200
                text = "ok"

                def json(self_inner):
                    return {
                        "choices": [
                            {"message": {"content": content}}
                        ]
                    }

            return FakeResponse()

        fake_requests = types.SimpleNamespace(post=fake_post, RequestException=Exception)
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        summary_path = analyzer.generate_video_summary(
            sample_analysis_result,
            output_dir=temp_dir,
            video_path="videos/demo.mp4",
        )

        assert summary_path is not None
        assert Path(summary_path).name == "demo_video_summary.md"
        assert os.path.exists(summary_path)
        with open(summary_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "# 测试视频总结" in content
        assert "## 一句话概括" in content
        assert "这是一个示例视频的核心总结。" in content
        assert "## 关键依据" in content
        assert "明确依据一" in content
        assert "## 对用户的启示与价值" in content
        assert "## 适用边界" in content
        assert "## X Post 文案" in content
        assert "### 中文" in content
        assert "### English" in content
        assert "Sam Altman 把创业讲得很直接" in content
        assert "Sam Altman breaks down startup success" in content
