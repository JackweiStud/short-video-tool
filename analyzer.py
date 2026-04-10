import os
import json
import logging
import signal
import subprocess
import time
import hashlib
import math
import re
import shutil
from datetime import datetime
from multiprocessing import Queue, set_start_method
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, List, Dict
from difflib import SequenceMatcher

import librosa
import numpy as np
from scenedetect import detect, ContentDetector, AdaptiveDetector

from config import Config, get_config

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class Analyzer:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.default_output_dir = self.config.analysis_dir
        self.whisper_model = self.config.whisper_model
        self.whisper_word_timestamps = self.config.whisper_word_timestamps

        # ASR chunking config
        self.asr_chunk_duration = self.config.asr_chunk_duration
        self.asr_overlap_seconds = self.config.asr_overlap_seconds
        self.asr_segment_timeout = self.config.asr_segment_timeout
        self.asr_cache_dir = Path(self.config.asr_cache_dir)
        if not self.asr_cache_dir.is_absolute():
            self.asr_cache_dir = Path(__file__).resolve().parent / self.asr_cache_dir
        self.asr_vad_filter = self.config.asr_vad_filter
        self.asr_vad_min_duration = self.config.asr_vad_min_duration_threshold

        self.whisper_cli = self._resolve_whisper_cli()

        try:
            set_start_method("fork", force=True)
        except RuntimeError:
            pass  # Already set

        self.asr_language = self.config.asr_language
        self.audio_climax_top_n = self.config.audio_climax_top_n
        self.scene_detection_threshold = self.config.scene_detection_threshold
        self.ffmpeg_timeout = self.config.ffmpeg_timeout

        # Topic segmentation config
        self.enable_topic_segmentation = self.config.enable_topic_segmentation
        self.llm_provider = self.config.llm_provider
        self.llm_model = self.config.llm_model
        self.llm_base_url = self.config.llm_base_url
        self.llm_api_key = self.config.llm_api_key
        self.topic_segment_min_duration = self.config.topic_segment_min_duration
        self.llm_timeout = self.config.llm_timeout
        self.ffmpeg_bin = self._resolve_tool_bin(
            "FFMPEG_PATH",
            "ffmpeg",
            [
                "/opt/homebrew/bin/ffmpeg",  # Common macOS Homebrew location
                "/usr/local/bin/ffmpeg",     # Common Intel macOS/Linux location
                "/usr/bin/ffmpeg",           # Common Linux location
            ],
        )
        self.ffprobe_bin = self._resolve_tool_bin(
            "FFPROBE_PATH",
            "ffprobe",
            [
                "/opt/homebrew/bin/ffprobe",
                "/usr/local/bin/ffprobe",
                "/usr/bin/ffprobe",
            ],
        )

    def _resolve_tool_bin(
        self,
        env_var: str,
        tool_name: str,
        fallback_paths: list[str],
    ) -> str:
        """Resolve a tool executable from env, PATH, or common fallback locations."""
        candidates = [
            os.getenv(env_var),
            shutil.which(tool_name),
            *fallback_paths,
        ]

        for candidate in candidates:
            if not candidate:
                continue
            candidate_path = Path(candidate).expanduser()
            if candidate_path.is_file() and os.access(candidate_path, os.X_OK):
                logging.debug(f"Resolved {tool_name} to: {candidate_path}")
                return str(candidate_path)

        # Final fallback to the command name and hope for the best if nothing found
        return tool_name

    @staticmethod
    def _format_duration_mmss(seconds: float) -> str:
        """Format seconds as MM:SS or HH:MM:SS for chunk progress logs."""
        total_seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _build_asr_chunk_windows(self, duration: float) -> list:
        """Build overlapping chunk windows for ASR processing."""
        chunk_duration = self.asr_chunk_duration
        overlap = self.asr_overlap_seconds
        chunks = []
        start = 0.0
        chunk_idx = 0
        while start < duration:
            end = min(start + chunk_duration, duration)
            chunks.append((chunk_idx, start, end))
            chunk_idx += 1
            if end >= duration:
                break
            start = end - overlap
        return chunks

    def _build_asr_cache_key_prefix(
        self,
        engine_prefix: str,
        audio_path: str,
        model: str,
        language: str,
        cache_source_path: Optional[str] = None,
    ) -> str:
        """Build the cache key prefix for one ASR engine."""
        cache_md5_source = cache_source_path or audio_path
        source_md5 = self._get_file_md5(cache_md5_source)
        if engine_prefix:
            return f"{engine_prefix}_{source_md5}_{model}_{language}"
        return f"{source_md5}_{model}_{language}"

    def _build_asr_chunk_cache_file(self, cache_key_prefix: str, chunk_idx: int) -> Path:
        """Return the cache file path for a specific ASR chunk."""
        return self.asr_cache_dir / f"{cache_key_prefix}_chunk{chunk_idx:03d}.json"

    def _resolve_asr_initial_prompt(self, language: str) -> Optional[str]:
        """Return a configured initial prompt for Chinese ASR, or None."""
        enabled = getattr(self.config, "asr_initial_prompt_enabled", False)
        if not enabled:
            return None

        if not (language or "").lower().startswith("zh"):
            return None

        prompt = getattr(self.config, "asr_initial_prompt_text", "").strip()
        return prompt or None

    def _read_asr_chunk_cache(
        self,
        engine_name: str,
        cache_file: Path,
        chunk_label: str,
        chunk_start: float,
        chunk_duration: float,
    ) -> Optional[list]:
        """Read a cached ASR chunk if it exists and looks valid."""
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, list) and len(cached) > 0:
                cached = self._normalize_cached_asr_segments(
                    cached,
                    chunk_start=chunk_start,
                    chunk_duration=chunk_duration,
                )
                logging.info(
                    f"[{engine_name}] {chunk_label}: cache hit {cache_file.name} ({len(cached)} segments)"
                )
                return cached

            logging.warning(
                f"[{engine_name}] {chunk_label}: cache invalid ({cache_file.name}), re-processing"
            )
        except Exception as e:
            logging.warning(
                f"[{engine_name}] {chunk_label}: cache read failed: {e}, re-processing"
            )

        cache_file.unlink(missing_ok=True)
        return None

    def _write_asr_chunk_cache(
        self,
        engine_name: str,
        cache_file: Path,
        chunk_label: str,
        segments: list,
    ) -> None:
        """Write normalized ASR chunk data to cache."""
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(segments, f, ensure_ascii=False, indent=2)
            logging.info(
                f"[{engine_name}] {chunk_label}: cached to {cache_file.name}"
            )
        except Exception as e:
            logging.warning(
                f"[{engine_name}] {chunk_label}: cache write failed: {e}"
            )

    def _run_cached_asr_chunk(
        self,
        engine_name: str,
        cache_file: Path,
        chunk_label: str,
        chunk_start: float,
        chunk_duration: float,
        process_chunk,
    ) -> Optional[list]:
        """Run a chunk with cache hit/read/write handling."""
        cached = self._read_asr_chunk_cache(
            engine_name,
            cache_file,
            chunk_label,
            chunk_start,
            chunk_duration,
        )
        if cached is not None:
            return cached

        processed = process_chunk()
        if processed is None:
            return None

        chunk_segments, cache_segments = processed
        if cache_segments is not None:
            self._write_asr_chunk_cache(
                engine_name,
                cache_file,
                chunk_label,
                cache_segments,
            )
        return chunk_segments

    def _resolve_whisper_cli(self) -> Optional[str]:
        """Resolve the whisper CLI executable from env, PATH, or common installs."""
        candidates = [
            os.getenv("WHISPER_CLI_PATH"),
            os.getenv("WHISPER_BIN"),
            shutil.which("whisper"),
            "/opt/homebrew/bin/whisper",
            "/usr/local/bin/whisper",
        ]

        for candidate in candidates:
            if not candidate:
                continue
            candidate_path = Path(candidate).expanduser()
            if candidate_path.is_file() and os.access(candidate_path, os.X_OK):
                return str(candidate_path)
        return None

    def _require_whisper_cli(self) -> Optional[str]:
        """Return the resolved whisper CLI or log a clear error if none exists."""
        if self.whisper_cli:
            return self.whisper_cli

        logging.error(
            "Whisper CLI not found. Set WHISPER_CLI_PATH or install 'whisper' on PATH."
        )
        return None

    def analyze_video(
        self,
        video_path: str,
        output_dir: Optional[str] = None,
        clip_strategy: str = "opinion",
    ) -> dict:
        """
        Analyze video: ASR, audio features, scene detection.

        Args:
            video_path: Path to video file
            output_dir: Directory to save analysis results

        Returns:
            dict: {
                "asr_result": [...],
                "audio_climax_points": [...],
                "scene_changes": [...]
            }
            None if analysis fails
        """
        output_dir = output_dir or self.default_output_dir

        logging.info(f"Starting analysis for video: {video_path}")

        # Validation: Check if file exists
        if not os.path.exists(video_path):
            logging.error(f"Video file not found: {video_path}")
            return None

        # Validation: Check if file is readable
        if not os.access(video_path, os.R_OK):
            logging.error(f"Video file is not readable: {video_path}")
            return None

        # Validation: Check file size (must be > 0)
        file_size = os.path.getsize(video_path)
        if file_size == 0:
            logging.error(f"Video file is empty: {video_path}")
            return None

        logging.info(f"Video file size: {file_size / (1024 * 1024):.2f} MB")

        # Validation: Check if file is a valid video (basic check)
        valid_extensions = [".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"]
        file_ext = os.path.splitext(video_path)[1].lower()
        if file_ext not in valid_extensions:
            logging.warning(
                f"File extension '{file_ext}' may not be a valid video format"
            )

        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create output directory: {e}")
            return None

        # Extract audio from video
        audio_path = self._extract_audio(video_path, output_dir)
        if not audio_path:
            logging.error("Failed to extract audio from video")
            return None

        # Check if audio file was created and has content
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            logging.error("Extracted audio file is empty or missing")
            return None

        # F2.1: ASR - Speech to Text
        logging.info("Running ASR (Speech to Text)...")
        asr_result = self._run_asr(
            audio_path,
            model=self.whisper_model,
            language=self.asr_language,
            cache_source_path=video_path,
        )

        run_audio_analysis, run_scene_detection, run_topic_segmentation = (
            self._resolve_analysis_plan(clip_strategy)
        )

        audio_climax_points = []
        if run_audio_analysis:
            logging.info("Analyzing audio features...")
            audio_climax_points = self._analyze_audio(
                audio_path,
                top_n=self.audio_climax_top_n,
            )
        else:
            logging.info(
                "Skipping audio feature analysis for clip strategy '%s'",
                clip_strategy,
            )

        scene_changes = []
        if run_scene_detection:
            logging.info("Detecting scene changes...")
            scene_changes = self._detect_scenes(
                video_path,
                threshold=self.scene_detection_threshold,
            )
        else:
            logging.info(
                "Skipping scene detection for clip strategy '%s'",
                clip_strategy,
            )

        # F2.4: Topic Segmentation (if enabled)
        topic_segments = []
        topic_summaries = []
        segmentation_meta = {
            "clip_strategy_used": clip_strategy,
            "segmentation_effective": False,
            "fallback_reason": "topic_segmentation_disabled",
        }
        if run_topic_segmentation and self.enable_topic_segmentation and asr_result:
            logging.info("Running topic segmentation...")
            topic_segments, topic_summaries, segmentation_meta = self._segment_by_topic(
                asr_result, clip_strategy=clip_strategy
            )
        elif not run_topic_segmentation:
            segmentation_meta["fallback_reason"] = "topic_segmentation_bypassed_by_strategy"

        # Combine results
        analysis_result = {
            "video_path": video_path,
            "asr_result": asr_result,
            "audio_climax_points": audio_climax_points,
            "scene_changes": scene_changes,
            "topic_segments": topic_segments,
            "topic_summaries": topic_summaries,
            "segmentation_meta": segmentation_meta,
        }

        # Save to JSON
        output_file = os.path.join(output_dir, "analysis_result.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(analysis_result, f, ensure_ascii=False, indent=2)
        logging.info(f"Analysis result saved to: {output_file}")

        return analysis_result

    def analyze_video_for_summary(
        self, video_path: str, output_dir: Optional[str] = None
    ) -> dict:
        """
        Minimal analysis pipeline for summary generation.
        Only extracts audio and runs ASR; skips audio feature analysis, scene
        detection, clipping, translation, and integration.
        """
        output_dir = output_dir or self.default_output_dir

        logging.info(f"Starting summary-only analysis for video: {video_path}")

        if not os.path.exists(video_path):
            logging.error(f"Video file not found: {video_path}")
            return None

        if not os.access(video_path, os.R_OK):
            logging.error(f"Video file is not readable: {video_path}")
            return None

        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create output directory: {e}")
            return None

        audio_path = self._extract_audio(video_path, output_dir)
        if not audio_path:
            logging.error("Failed to extract audio from video")
            return None

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            logging.error("Extracted audio file is empty or missing")
            return None

        logging.info("Running ASR for summary-only mode...")
        asr_result = self._run_asr(
            audio_path,
            model=self.whisper_model,
            language=self.asr_language,
            cache_source_path=video_path,
        )
        if not asr_result:
            logging.error("Failed to generate ASR result for summary-only mode")
            return None

        analysis_result = {
            "video_path": video_path,
            "asr_result": asr_result,
            "audio_climax_points": [],
            "scene_changes": [],
            "topic_segments": [],
            "topic_summaries": [],
            "segmentation_meta": {
                "clip_strategy_used": "summary-only",
                "segmentation_effective": False,
                "fallback_reason": "summary_only_mode",
            },
        }

        output_file = os.path.join(output_dir, "analysis_result.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(analysis_result, f, ensure_ascii=False, indent=2)
        logging.info(f"Summary-only analysis saved to: {output_file}")

        return analysis_result

    def generate_video_summary(
        self,
        analysis_result: dict,
        output_dir: Optional[str] = None,
        video_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate a human-readable Markdown summary for one video.
        """
        output_dir = output_dir or self.default_output_dir
        os.makedirs(output_dir, exist_ok=True)

        if not analysis_result:
            logging.error("No analysis result available for video summary")
            return None

        asr_result = analysis_result.get("asr_result", [])
        if not asr_result:
            logging.error("No ASR result available for video summary")
            return None

        api_key = self.llm_api_key or getattr(self.config, "openai_api_key", None)
        if not api_key:
            logging.error("LLM API key not configured; cannot generate video summary")
            return None

        base_url = self.llm_base_url or "https://api.openai.com/v1"
        video_path = video_path or analysis_result.get("video_path", "")
        video_name = os.path.basename(video_path) if video_path else "video"

        summary_data = self._build_video_summary_data(
            analysis_result=analysis_result,
            api_key=api_key,
            base_url=base_url,
            video_name=video_name,
        )
        if not summary_data:
            return None

        summary_path = os.path.join(
            output_dir, self._build_video_summary_filename(video_path)
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(
                self._render_video_summary_markdown(
                    summary_data=summary_data,
                    analysis_result=analysis_result,
                    video_name=video_name,
                )
            )

        logging.info(f"Video summary saved to: {summary_path}")
        return summary_path

    @staticmethod
    def _build_video_summary_filename(video_path: Optional[str]) -> str:
        """Build a stable summary filename prefixed by the source video stem."""
        if not video_path:
            return "video_summary.md"

        stem = Path(video_path).stem.strip() or "video"
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
        if not sanitized:
            sanitized = "video"
        return f"{sanitized}_video_summary.md"

    @staticmethod
    def _filter_video_summary_best_for(items: list, transcript: str) -> list:
        """Drop unsupported audience labels that are too generic or age-based."""
        if not items:
            return []

        transcript_lower = (transcript or "").lower()
        banned_terms = [
            "年轻人",
            "年轻创业者",
            "普通人",
            "上班族",
            "宝妈",
            "学生党",
        ]
        filtered = []
        for item in items:
            text = str(item).strip()
            if not text:
                continue
            if any(term in text for term in banned_terms):
                matched = False
                for term in banned_terms:
                    if term in text and term.lower() in transcript_lower:
                        matched = True
                        break
                if not matched:
                    continue
            filtered.append(text)
        return filtered

    def _resolve_analysis_plan(self, clip_strategy: str) -> Tuple[bool, bool, bool]:
        """Return (audio_analysis, scene_detection, topic_segmentation) flags."""
        strategy = (clip_strategy or "opinion").lower()
        if strategy == "hybrid":
            return True, True, True
        if strategy in {"opinion", "topic"}:
            return False, False, True
        logging.warning(
            "Unknown clip strategy '%s', defaulting to hybrid analysis plan",
            clip_strategy,
        )
        return True, True, True

    def _extract_audio(self, video_path: str, output_dir: str) -> str:
        """Extract audio from video using ffmpeg."""
        audio_path = os.path.join(output_dir, "extracted_audio.wav")

        try:
            # Check if ffmpeg is available
            ffmpeg_check = subprocess.run(
                [self.ffmpeg_bin, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if ffmpeg_check.returncode != 0:
                logging.error(f"ffmpeg ({self.ffmpeg_bin}) is not installed or not accessible")
                return None

            cmd = [
                self.ffmpeg_bin,
                "-i",
                video_path,
                "-vn",  # No video
                "-acodec",
                "pcm_s16le",  # PCM 16-bit
                "-ar",
                "16000",  # 16kHz sample rate
                "-ac",
                "1",  # Mono
                "-y",  # Overwrite
                audio_path,
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.ffmpeg_timeout,
            )

            if result.returncode != 0:
                logging.error(f"FFmpeg error: {result.stderr}")
                # Check for specific error patterns
                if "Invalid data found" in result.stderr:
                    logging.error("Video file appears to be corrupted or invalid")
                elif "No such file or directory" in result.stderr:
                    logging.error("Video file path is invalid")
                elif "does not contain any stream" in result.stderr:
                    logging.error(
                        "Video file does not contain any audio or video streams"
                    )
                return None

            # Verify audio file was created
            if not os.path.exists(audio_path):
                logging.error("Audio file was not created")
                return None

            # Check if audio file has content
            audio_size = os.path.getsize(audio_path)
            if audio_size == 0:
                logging.error(
                    "Extracted audio file is empty (video may have no audio track)"
                )
                return None

            logging.info(
                f"Audio extracted to: {audio_path} ({audio_size / (1024 * 1024):.2f} MB)"
            )
            return audio_path

        except subprocess.TimeoutExpired:
            logging.error(
                "Audio extraction timed out (video may be too long or corrupted)"
            )
            return None
        except FileNotFoundError:
            logging.error("ffmpeg command not found. Please install ffmpeg.")
            return None
        except Exception as e:
            logging.error(f"Failed to extract audio: {e}")
            return None

    def _run_asr(
        self,
        audio_path: str,
        model: str = "medium",
        language: str = "en",
        cache_source_path: Optional[str] = None,
    ) -> list:
        """
        Run ASR using mlx-whisper (Apple Silicon), faster-whisper, or fallback to whisper CLI.
        """
        import platform
        # 1. Priority: mlx-whisper for Mac M-series (Apple Silicon GPU acceleration)
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            try:
                import mlx_whisper  # noqa: F401
                logging.info("[ASR] mlx-whisper available on Apple Silicon, using native MLX engine for massive speedup")
                return self._run_asr_mlx_whisper(
                    audio_path,
                    model,
                    language,
                    cache_source_path=cache_source_path,
                )
            except ImportError:
                logging.warning(
                    "[ASR] Apple Silicon detected but mlx-whisper not installed. "
                    "Run `pip install mlx-whisper` for massive hardware-accelerated speedup."
                )

        # 2. Priority: faster-whisper
        try:
            from faster_whisper import WhisperModel  # noqa: F401

            logging.info("[ASR] faster-whisper available, using faster-whisper engine")
            return self._run_asr_faster_whisper(audio_path, model, language)
        except ImportError:
            logging.warning(
                "[ASR] faster-whisper not available, falling back to whisper CLI"
            )
            return self._run_asr_whisper_cli(audio_path, model, language)

    def _remove_repetitive_segments(self, segments: List[Dict], similarity_threshold: float = 0.85) -> List[Dict]:
        """
        Remove repetitive ASR segments caused by Whisper hallucination.

        Strategy: When detecting a repetition zone, merge all repetitive segments
        into a single segment covering the entire time range. This preserves
        timeline coverage while removing redundant text.

        Args:
            segments: List of ASR segments with 'text', 'start', 'end'
            similarity_threshold: Text similarity ratio (0-1) above which segments are considered repetitive

        Returns:
            Filtered list of segments with repetitions merged
        """
        if not segments:
            return segments

        filtered = []
        prev_text = None
        repetition_zone_segments = []  # Collect segments in repetition zone
        in_repetition_zone = False

        for seg in segments:
            text = seg.get('text', '').strip()

            if not text:
                continue

            # Check if this segment contains internal repetition (hallucination pattern)
            has_internal_rep = self._has_internal_repetition(text)

            # Calculate similarity with previous segment
            similarity = 0.0
            if prev_text:
                similarity = SequenceMatcher(None, prev_text.lower(), text.lower()).ratio()

            # Check if we're in or entering a repetition zone
            is_repetitive = has_internal_rep or (similarity >= similarity_threshold and prev_text)

            if is_repetitive:
                if not in_repetition_zone:
                    # Entering repetition zone - start collecting
                    in_repetition_zone = True
                    repetition_zone_segments = [seg]
                    logging.debug(f"Entering repetition zone at {seg['start']:.1f}s")
                else:
                    # Already in repetition zone - add to collection
                    repetition_zone_segments.append(seg)
            else:
                # Not repetitive
                if in_repetition_zone:
                    # Exiting repetition zone - merge collected segments
                    if repetition_zone_segments:
                        merged = self._merge_repetitive_segments(repetition_zone_segments)
                        filtered.append(merged)
                        logging.warning(
                            f"Merged {len(repetition_zone_segments)} repetitive segments "
                            f"into one ({merged['start']:.1f}-{merged['end']:.1f}s)"
                        )
                    repetition_zone_segments = []
                    in_repetition_zone = False

                # Add normal segment
                filtered.append(seg)

            prev_text = text

        # Handle case where we end in a repetition zone
        if in_repetition_zone and repetition_zone_segments:
            merged = self._merge_repetitive_segments(repetition_zone_segments)
            filtered.append(merged)
            logging.warning(
                f"Merged {len(repetition_zone_segments)} repetitive segments at end "
                f"into one ({merged['start']:.1f}-{merged['end']:.1f}s)"
            )

        removed_count = len(segments) - len(filtered)
        if removed_count > 0:
            logging.info(f"[ASR Post-processing] Merged {removed_count} repetitive segments")

        return filtered

    def _merge_repetitive_segments(self, segments: List[Dict]) -> Dict:
        """
        Merge multiple repetitive segments into one.

        Takes the text from the first segment (usually the cleanest),
        and spans the time range from first start to last end.

        Args:
            segments: List of segments to merge

        Returns:
            Single merged segment
        """
        if not segments:
            return {}

        if len(segments) == 1:
            return segments[0]

        # Use the first segment's text (usually least corrupted)
        first_seg = segments[0]
        last_seg = segments[-1]

        # Extract the non-repetitive part of the text
        text = first_seg['text'].strip()

        # Try to clean up internal repetition by taking only the first occurrence
        # Split by common sentence endings
        sentences = []
        current = []
        for char in text:
            current.append(char)
            if char in '.!?':
                sentence = ''.join(current).strip()
                if sentence and sentence not in sentences:
                    sentences.append(sentence)
                current = []

        if current:
            sentence = ''.join(current).strip()
            if sentence and sentence not in sentences:
                sentences.append(sentence)

        # If we found distinct sentences, use them; otherwise use original
        if sentences:
            cleaned_text = ' '.join(sentences)
        else:
            cleaned_text = text

        merged = {
            'start': first_seg['start'],
            'end': last_seg['end'],
            'text': cleaned_text,
        }

        # Preserve any additional keys from first segment
        for key in first_seg:
            if key not in merged:
                merged[key] = first_seg[key]

        return merged

    def _has_internal_repetition(self, text: str, min_phrase_length: int = 15) -> bool:
        """
        Detect if a text segment contains repetitive phrases (hallucination pattern).

        Args:
            text: Text to check
            min_phrase_length: Minimum character length for a phrase to be considered

        Returns:
            True if text contains significant internal repetition
        """
        # Method 1: Check for repeated complete sentences
        sentences = []
        current = []
        for char in text:
            current.append(char)
            if char in '.!?':
                sentence = ''.join(current).strip()
                if len(sentence) >= min_phrase_length:
                    sentences.append(sentence)
                current = []

        # Add remaining text as a sentence
        if current:
            sentence = ''.join(current).strip()
            if len(sentence) >= min_phrase_length:
                sentences.append(sentence)

        # Check for repeated sentences
        if len(sentences) >= 3:
            from collections import Counter
            sentence_counts = Counter(sentences)

            # If any sentence appears 3+ times, it's likely hallucination
            for sentence, count in sentence_counts.items():
                if count >= 3:
                    logging.debug(f"Found repeated sentence ({count}x): {sentence[:40]}...")
                    return True

        # Method 2: Check for repeated phrases (sliding window)
        # Look for phrases that appear multiple times in the text
        words = text.split()
        if len(words) < 10:
            return False

        # Try different phrase lengths (4-8 words)
        max_repetition_ratio = 0.0
        for phrase_len in range(4, 9):
            if len(words) < phrase_len * 2:
                continue

            phrase_counts = {}
            for i in range(len(words) - phrase_len + 1):
                phrase = ' '.join(words[i:i + phrase_len]).lower()
                phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

            # Calculate repetition ratio: (repeated words) / (total words)
            repeated_words = 0
            for phrase, count in phrase_counts.items():
                if count >= 2:
                    # Each repetition beyond the first adds phrase_len words
                    repeated_words += phrase_len * (count - 1)

            repetition_ratio = repeated_words / len(words)
            max_repetition_ratio = max(max_repetition_ratio, repetition_ratio)

            # If any phrase appears 3+ times, it's definitely hallucination
            for phrase, count in phrase_counts.items():
                if count >= 3:
                    logging.debug(f"Found repeated phrase ({count}x, {phrase_len} words): {phrase[:40]}...")
                    return True

        # If more than 40% of the text is repetitive, it's likely hallucination
        if max_repetition_ratio > 0.4:
            logging.debug(f"High repetition ratio: {max_repetition_ratio:.1%}")
            return True

        return False

    def _run_asr_mlx_whisper(
        self,
        audio_path: str,
        model: str = "medium",
        language: str = "en",
        cache_source_path: Optional[str] = None,
    ) -> list:
        """
        Chunked MLX Whisper ASR with segment-level cache and overlap merging.
        Mirrors the faster-whisper / whisper-cli chunk cache flow, but uses an
        isolated MLX cache key prefix to avoid collisions with other engines.
        """
        import mlx_whisper
        import tempfile
        import threading
        import shutil
        import os

        initial_prompt = self._resolve_asr_initial_prompt(language)

        duration = self._get_audio_duration(audio_path)
        logging.info(
            f"[mlx-whisper] Audio duration: {duration:.1f}s ({duration / 60:.1f} min)"
        )

        # Setup cache
        self.asr_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key_prefix = self._build_asr_cache_key_prefix(
            "mlx",
            audio_path,
            model,
            language,
            cache_source_path=cache_source_path,
        )

        # Build chunks using the same overlap policy as the other engines
        chunks = self._build_asr_chunk_windows(duration)

        total = len(chunks)
        logging.info(
            f"[mlx-whisper] Processing {total} chunks (chunk_duration={self.asr_chunk_duration}s, overlap={self.asr_overlap_seconds}s)"
        )

        # Check for local model first
        mlx_local_base = getattr(self.config, 'mlx_whisper_local_model_dir', None)
        if mlx_local_base:
            local_model_path = os.path.join(os.path.expanduser(mlx_local_base), f"whisper-{model}-mlx")
            if os.path.isdir(local_model_path):
                mlx_model_repo = local_model_path
                logging.info(f"[mlx-whisper] Using local model: {mlx_model_repo}")
            else:
                mlx_model_repo = f"mlx-community/whisper-{model}-mlx"
                logging.info(f"[mlx-whisper] Local model not found, using HF repo: {mlx_model_repo}")
        else:
            # Default fallback to mlx-community repo structures for whisper:
            mlx_model_repo = f"mlx-community/whisper-{model}-mlx"
            logging.info(f"[mlx-whisper] Transcribing audio with Apple GPU via: {mlx_model_repo}")

        try:
            all_segments = []
            failed_chunks = []
            tmp_dir = tempfile.mkdtemp(prefix="mlx_asr_chunks_")

            try:
                for idx, chunk_start, chunk_end in chunks:
                    cache_file = self._build_asr_chunk_cache_file(cache_key_prefix, idx)
                    chunk_span = chunk_end - chunk_start
                    chunk_label = (
                        f"chunk {idx + 1}/{total} "
                        f"[{self._format_duration_mmss(chunk_start)}"
                        f" -> {self._format_duration_mmss(chunk_end)}, "
                        f"len={self._format_duration_mmss(chunk_span)}]"
                    )

                    chunk_wav = os.path.join(tmp_dir, f"chunk_{idx:03d}.wav")
                    def _process_chunk():
                        logging.info(
                            f"[mlx-whisper] {chunk_label}: extracting audio for transcription"
                        )

                        ffmpeg_cmd = [
                            self.ffmpeg_bin,
                            "-y",
                            "-ss",
                            str(chunk_start),
                            "-t",
                            str(chunk_end - chunk_start),
                            "-i",
                            audio_path,
                            "-ar",
                            "16000",
                            "-ac",
                            "1",
                            "-f",
                            "wav",
                            chunk_wav,
                        ]
                        try:
                            ffmpeg_result = subprocess.run(
                                ffmpeg_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                timeout=120,
                            )
                            if ffmpeg_result.returncode != 0:
                                logging.warning(
                                    f"[mlx-whisper] {chunk_label}: ffmpeg extraction failed, skipping"
                                )
                                return None
                        except subprocess.TimeoutExpired:
                            logging.warning(
                                f"[mlx-whisper] {chunk_label}: ffmpeg timeout, skipping"
                            )
                            return None

                        logging.info(
                            f"[mlx-whisper] {chunk_label}: starting transcription "
                            f"(timeout={self.asr_segment_timeout}s, word_timestamps={self.whisper_word_timestamps}, "
                            f"initial_prompt={'on' if initial_prompt else 'off'})"
                        )

                        result_container = []
                        error_container = []

                        def _transcribe():
                            try:
                                result_container.append(
                                    mlx_whisper.transcribe(
                                        chunk_wav,
                                        path_or_hf_repo=mlx_model_repo,
                                        word_timestamps=self.whisper_word_timestamps,
                                        language=language,
                                        initial_prompt=initial_prompt,
                                        temperature=0.0,
                                        compression_ratio_threshold=2.4,
                                        logprob_threshold=-1.0,
                                        no_speech_threshold=0.6,
                                        condition_on_previous_text=False,
                                        hallucination_silence_threshold=0.5,
                                    )
                                )
                            except Exception as e:
                                error_container.append(e)

                        t_chunk_start = time.time()
                        t = threading.Thread(target=_transcribe, daemon=True)
                        t.start()
                        t.join(timeout=self.asr_segment_timeout)
                        elapsed = time.time() - t_chunk_start

                        try:
                            if t.is_alive():
                                logging.error(
                                    f"[mlx-whisper] {chunk_label}: timeout after {self.asr_segment_timeout}s"
                                )
                                return None

                            if error_container:
                                logging.error(
                                    f"[mlx-whisper] {chunk_label}: error: {error_container[0]}"
                                )
                                return None

                            if not result_container:
                                logging.error(f"[mlx-whisper] {chunk_label}: no result")
                                return None

                            result = result_container[0]
                            raw_segments = []
                            for seg in result.get("segments", []):
                                words = []
                                for w in seg.get("words", []):
                                    words.append(
                                        {
                                            "word": w.get("word", "").strip(),
                                            "start": round(w.get("start", 0.0), 3),
                                            "end": round(w.get("end", 0.0), 3),
                                        }
                                    )
                                raw_segments.append(
                                    {
                                        "start": round(seg.get("start", 0.0), 3),
                                        "end": round(seg.get("end", 0.0), 3),
                                        "text": seg.get("text", "").strip(),
                                        "words": words,
                                    }
                                )

                            if not raw_segments:
                                logging.warning(
                                    f"[mlx-whisper] {chunk_label}: no segments returned"
                                )
                                return None

                            chunk_segments = []
                            for seg in raw_segments:
                                shifted = dict(seg)
                                shifted["start"] = round(
                                    shifted.get("start", 0.0) + chunk_start, 3
                                )
                                shifted["end"] = round(
                                    shifted.get("end", 0.0) + chunk_start, 3
                                )
                                words = shifted.get("words", [])
                                if isinstance(words, list):
                                    shifted_words = []
                                    for word in words:
                                        if not isinstance(word, dict):
                                            continue
                                        shifted_word = dict(word)
                                        shifted_word["start"] = round(
                                            shifted_word.get("start", 0.0) + chunk_start, 3
                                        )
                                        shifted_word["end"] = round(
                                            shifted_word.get("end", 0.0) + chunk_start, 3
                                        )
                                        shifted_words.append(shifted_word)
                                    shifted["words"] = shifted_words
                                chunk_segments.append(shifted)

                            logging.info(
                                f"[mlx-whisper] {chunk_label}: completed with {len(chunk_segments)} segments, elapsed {elapsed:.1f}s"
                            )
                            return chunk_segments, raw_segments
                        finally:
                            try:
                                os.remove(chunk_wav)
                            except Exception:
                                pass
                            try:
                                os.remove(os.path.splitext(chunk_wav)[0] + ".json")
                            except Exception:
                                pass

                    chunk_segments = self._run_cached_asr_chunk(
                        "mlx-whisper",
                        cache_file,
                        chunk_label,
                        chunk_start,
                        chunk_end - chunk_start,
                        _process_chunk,
                    )
                    if chunk_segments is None:
                        failed_chunks.append((idx, chunk_start, chunk_end))
                        continue

                    all_segments.extend(chunk_segments)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

            if failed_chunks:
                logging.warning(
                    f"[mlx-whisper] {len(failed_chunks)} failed chunks: {[c[0] for c in failed_chunks]}"
                )
                logging.warning(
                    "[mlx-whisper] Re-run to resume - completed chunks are cached"
                )

            merged = self._merge_asr_segments(all_segments)
            merged = self._remove_repetitive_segments(merged)
            logging.info(f"[mlx-whisper] ASR complete: {len(merged)} segments total")
            return merged

        except Exception as e:
            logging.error(f"[mlx-whisper] Failed to run MLX transcription: {e}")
            logging.warning("[mlx-whisper] Falling back to faster-whisper chunks...")
            return self._run_asr_faster_whisper(audio_path, model, language)

    def _run_asr_faster_whisper(
        self, audio_path: str, model: str = "medium", language: str = "en"
    ) -> list:
        """
        Chunked faster-whisper ASR with segment-level cache, timeout, and overlap merging.
        Splits audio into chunks via ffmpeg, runs faster-whisper per chunk, merges results.
        Cache key prefixed with 'fw_' to avoid collision with whisper-cli cache.
        """
        import tempfile
        import shutil
        initial_prompt = self._resolve_asr_initial_prompt(language)

        duration = self._get_audio_duration(audio_path)
        logging.info(
            f"[faster-whisper] Audio duration: {duration:.1f}s ({duration / 60:.1f} min)"
        )

        # Setup cache
        self.asr_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key_prefix = self._build_asr_cache_key_prefix(
            "fw", audio_path, model, language
        )

        # Build chunks (same logic as whisper-cli)
        chunks = self._build_asr_chunk_windows(duration)

        total = len(chunks)
        logging.info(
            f"[faster-whisper] Processing {total} chunks (chunk_duration={self.asr_chunk_duration}s, overlap={self.asr_overlap_seconds}s)"
        )

        import os

        # Prefer local model directory to avoid HuggingFace download
        _fw_local_base = self.config.faster_whisper_local_model_dir
        _fw_base_dir = os.path.expanduser(_fw_local_base) if _fw_local_base else ""
        local_model_path = (
            os.path.join(_fw_base_dir, f"faster-whisper-{model}")
            if _fw_base_dir
            else ""
        )
        if (
            local_model_path
            and os.path.isdir(local_model_path)
            and os.path.exists(os.path.join(local_model_path, "model.bin"))
        ):
            model_id = local_model_path
            logging.info(f"[faster-whisper] Using local model: {model_id}")
        else:
            model_id = model
            logging.info(
                f"[faster-whisper] Local model not found at {local_model_path or '(disabled)'}, will download '{model}'"
            )

        all_segments = []
        failed_chunks = []
        tmp_dir = tempfile.mkdtemp(prefix="fw_asr_chunks_")

        try:
            for idx, chunk_start, chunk_end in chunks:
                cache_file = self._build_asr_chunk_cache_file(cache_key_prefix, idx)
                chunk_span = chunk_end - chunk_start
                chunk_label = (
                    f"chunk {idx + 1}/{total} "
                    f"[{self._format_duration_mmss(chunk_start)}"
                    f" -> {self._format_duration_mmss(chunk_end)}, "
                    f"len={self._format_duration_mmss(chunk_span)}]"
                )
                chunk_wav = os.path.join(tmp_dir, f"chunk_{idx:03d}.wav")
                def _process_chunk():
                    logging.info(
                        f"[faster-whisper] {chunk_label}: extracting audio for transcription"
                    )

                    ffmpeg_cmd = [
                        self.ffmpeg_bin,
                        "-y",
                        "-ss",
                        str(chunk_start),
                        "-t",
                        str(chunk_end - chunk_start),
                        "-i",
                        audio_path,
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-f",
                        "wav",
                        chunk_wav,
                    ]
                    try:
                        subprocess.run(
                            ffmpeg_cmd, capture_output=True, timeout=120, check=True
                        )
                    except Exception as e:
                        logging.error(
                            f"[faster-whisper] {chunk_label}: ffmpeg failed: {e}"
                        )
                        return None

                    import threading

                    t_chunk_start = time.time()
                    result_container = []
                    error_container = []

                    def _transcribe():
                        try:
                            from faster_whisper import WhisperModel

                            device_type = (
                                "cuda"
                                if getattr(self.config, "enable_gpu", False)
                                else "auto"
                            )
                            compute_type = "default"

                            fw_model = WhisperModel(
                                model_id,
                                device=device_type,
                                compute_type=compute_type,
                                cpu_threads=max(1, os.cpu_count() - 2)
                                if os.cpu_count()
                                else 4,
                            )
                            segs, info = fw_model.transcribe(
                                chunk_wav,
                                language=language,
                                word_timestamps=self.whisper_word_timestamps,
                                vad_filter=self.asr_vad_filter,
                                initial_prompt=initial_prompt,
                            )
                            result_container.append(list(segs))
                        except Exception as e:
                            error_container.append(e)

                    logging.info(
                        f"[faster-whisper] {chunk_label}: starting transcription "
                        f"(timeout={self.asr_segment_timeout}s, vad_filter={self.asr_vad_filter}, "
                        f"initial_prompt={'on' if initial_prompt else 'off'}, isolated_process=False)"
                    )
                    t = threading.Thread(target=_transcribe, daemon=True)
                    t.start()
                    t.join(timeout=self.asr_segment_timeout)
                    elapsed = time.time() - t_chunk_start

                    try:
                        if t.is_alive():
                            logging.error(
                                f"[faster-whisper] {chunk_label}: timeout after {self.asr_segment_timeout}s"
                            )
                            return None

                        if error_container:
                            logging.error(
                                f"[faster-whisper] {chunk_label}: error: {error_container[0]}"
                            )
                            return None

                        if not result_container:
                            logging.error(f"[faster-whisper] {chunk_label}: no result")
                            return None

                        raw_segs = result_container[0]

                        chunk_segments = []
                        cache_segments = []
                        for seg in raw_segs:
                            words = []
                            cache_words = []
                            if seg.words:
                                for w in seg.words:
                                    words.append(
                                        {
                                            "word": w.word,
                                            "start": round(w.start + chunk_start, 3),
                                            "end": round(w.end + chunk_start, 3),
                                        }
                                    )
                                    cache_words.append(
                                        {
                                            "word": w.word,
                                            "start": round(w.start, 3),
                                            "end": round(w.end, 3),
                                        }
                                    )
                            chunk_segments.append(
                                {
                                    "start": round(seg.start + chunk_start, 3),
                                    "end": round(seg.end + chunk_start, 3),
                                    "text": seg.text.strip(),
                                    "words": words,
                                }
                            )
                            cache_segments.append(
                                {
                                    "start": round(seg.start, 3),
                                    "end": round(seg.end, 3),
                                    "text": seg.text.strip(),
                                    "words": cache_words,
                                }
                            )

                        logging.info(
                            f"[faster-whisper] {chunk_label}: completed with {len(chunk_segments)} segments, elapsed {elapsed:.1f}s"
                        )
                        return chunk_segments, cache_segments
                    finally:
                        try:
                            os.remove(chunk_wav)
                        except Exception:
                            pass

                chunk_segments = self._run_cached_asr_chunk(
                    "faster-whisper",
                    cache_file,
                    chunk_label,
                    chunk_start,
                    chunk_end - chunk_start,
                    _process_chunk,
                )
                if chunk_segments is None:
                    failed_chunks.append((idx, chunk_start, chunk_end))
                    continue

                all_segments.extend(chunk_segments)

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if failed_chunks:
            logging.warning(
                f"[faster-whisper] {len(failed_chunks)} chunks failed: {[c[0] for c in failed_chunks]}"
            )

        merged = self._merge_asr_segments(all_segments)
        logging.info(f"[faster-whisper] ASR complete: {len(merged)} segments total")
        return merged

    def _merge_asr_segments(self, segments: list) -> list:
        """
        Merge ASR segments from multiple chunks, deduplicating overlap regions.
        Strategy: sort by start time and drop near-duplicate segments that
        overlap heavily with the previous kept segment. When two segments have
        almost identical text, keep the one with broader coverage.
        """
        if not segments:
            return []

        def _norm_text(text: str) -> str:
            return re.sub(r"[\s\W_]+", "", (text or "").lower(), flags=re.UNICODE)

        sorted_segs = sorted(
            [seg for seg in segments if isinstance(seg, dict)],
            key=lambda s: (float(s.get("start", 0.0)), float(s.get("end", 0.0))),
        )

        deduped = []
        for seg in sorted_segs:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue

            current = dict(seg)
            current_start = float(current.get("start", 0.0))
            current_end = float(current.get("end", 0.0))
            current_norm = _norm_text(text)

            if not deduped:
                deduped.append(current)
                continue

            prev = deduped[-1]
            prev_text = str(prev.get("text", "")).strip()
            prev_start = float(prev.get("start", 0.0))
            prev_end = float(prev.get("end", 0.0))
            prev_norm = _norm_text(prev_text)
            overlap = min(prev_end, current_end) - max(prev_start, current_start)
            gap = current_start - prev_end
            similarity = (
                SequenceMatcher(None, prev_norm, current_norm).ratio()
                if prev_norm and current_norm
                else 0.0
            )

            if prev_norm and current_norm and prev_norm == current_norm and (overlap > 0 or gap <= 1.0):
                prev_dur = max(0.0, prev_end - prev_start)
                curr_dur = max(0.0, current_end - current_start)
                if curr_dur > prev_dur + 0.15:
                    deduped[-1] = current
                continue

            if similarity >= 0.97 and overlap > 0 and gap <= 1.0:
                prev_dur = max(0.0, prev_end - prev_start)
                curr_dur = max(0.0, current_end - current_start)
                if curr_dur > prev_dur + 0.25:
                    deduped[-1] = current
                continue

            deduped.append(current)

        return deduped

    @staticmethod
    def _normalize_cached_asr_segments(
        segments: list, chunk_start: float, chunk_duration: float
    ) -> list:
        """
        Normalize cached ASR segments to absolute timestamps.

        Historical cache files stored chunk-relative timestamps. When those
        caches are reused, the timestamps must be shifted back into the global
        audio timeline or the merged transcript collapses to the first chunk.
        """
        if not segments:
            return []

        valid_segments = [seg for seg in segments if isinstance(seg, dict)]
        if not valid_segments:
            return []

        try:
            max_end = max(float(seg.get("end", 0.0)) for seg in valid_segments)
            min_start = min(float(seg.get("start", 0.0)) for seg in valid_segments)
        except Exception:
            return segments

        looks_relative = (
            chunk_start > 0 and max_end <= chunk_duration + 1.0 and min_start >= -1.0
        )
        if not looks_relative:
            return segments

        normalized = []
        for seg in valid_segments:
            shifted = dict(seg)
            shifted["start"] = round(float(shifted.get("start", 0.0)) + chunk_start, 3)
            shifted["end"] = round(float(shifted.get("end", 0.0)) + chunk_start, 3)
            words = shifted.get("words")
            if isinstance(words, list):
                shifted_words = []
                for word in words:
                    if not isinstance(word, dict):
                        continue
                    shifted_word = dict(word)
                    shifted_word["start"] = round(
                        float(shifted_word.get("start", 0.0)) + chunk_start, 3
                    )
                    shifted_word["end"] = round(
                        float(shifted_word.get("end", 0.0)) + chunk_start, 3
                    )
                    shifted_words.append(shifted_word)
                shifted["words"] = shifted_words
            normalized.append(shifted)

        return normalized

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        try:
            result = subprocess.run(
                [
                    self.ffprobe_bin,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_streams",
                    audio_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if "duration" in stream:
                    return float(stream["duration"])
        except Exception as e:
            logging.warning(f"ffprobe failed: {e}, estimating duration from file size")
        try:
            size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            return size_mb * 60
        except Exception:
            return 3600.0

    def _get_file_md5(self, filepath: str) -> str:
        """Get MD5 hash of first 1MB of file for cache key (fast)."""
        h = hashlib.md5()
        try:
            with open(filepath, "rb") as f:
                h.update(f.read(1024 * 1024))
        except Exception:
            h.update(filepath.encode())
        return h.hexdigest()[:16]

    @staticmethod
    def _transcribe_chunk_whisper_cli(
        chunk_wav: str,
        model: str,
        language: str,
        whisper_bin: str,
        chunk_idx: int,
        total: int,
        whisper_word_timestamps: bool,
        initial_prompt: Optional[str],
        tmp_dir: str,
        result_queue: Queue,
    ) -> None:
        """
        Worker function: runs in a separate Process.
        Executes whisper CLI on a single chunk WAV, puts result list into result_queue.
        Uses Popen + setsid so the whisper process group can be killed on timeout.
        Puts (pgid, segments_or_None) into result_queue.
        """
        import subprocess, json, os, logging, signal

        if not whisper_bin:
            logging.error(
                f"[Chunk {chunk_idx + 1}/{total}] Whisper CLI executable not configured"
            )
            result_queue.put((-1, []))
            return

        whisper_cmd = [
            whisper_bin,
            chunk_wav,
            "--model",
            model,
            "--language",
            language,
            "--output_format",
            "json",
            "--output_dir",
            tmp_dir,
        ]
        if whisper_word_timestamps:
            whisper_cmd += ["--word_timestamps", "True"]
        if initial_prompt:
            whisper_cmd += ["--initial_prompt", initial_prompt]
        # chunk_json: whisper writes {stem}.json into output_dir
        chunk_stem = os.path.splitext(os.path.basename(chunk_wav))[0]
        chunk_json = os.path.join(tmp_dir, chunk_stem + ".json")
        try:
            proc = subprocess.Popen(
                whisper_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # creates new process group
            )
            pgid = os.getpgid(proc.pid)
            # Signal parent the pgid so it can kill on timeout
            result_queue.put((pgid, None))
            stdout, stderr = proc.communicate()
            if proc.returncode == 0 and os.path.exists(chunk_json):
                with open(chunk_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                segments = data.get("segments", [])
                logging.info(
                    f"[Chunk {chunk_idx + 1}/{total}] Got {len(segments)} segments"
                )
                result_queue.put((pgid, segments))
            else:
                logging.error(
                    f"[Chunk {chunk_idx + 1}/{total}] whisper failed rc={proc.returncode} "
                    f"json_exists={os.path.exists(chunk_json)} stderr={stderr.decode(errors='replace')[:200]}"
                )
                result_queue.put((pgid, []))
        except Exception as e:
            logging.error(f"[Chunk {chunk_idx + 1}/{total}] Exception in worker: {e}")
            result_queue.put((-1, []))

    def _run_transcription_process_with_timeout(
        self,
        target_func,  # kept for API compatibility but not used
        args: tuple,  # (chunk_wav, model, language, whisper_bin, idx, total, whisper_word_timestamps, initial_prompt, tmp_dir)
        timeout: int,
        chunk_display_idx: int,
        total_chunks: int,
    ) -> list | None:
        """
        Runs whisper CLI directly via subprocess.Popen with threading.Timer for timeout.
        Replaces multiprocessing approach which suffered from spawn-crash / orphan-process bugs
        on macOS Python 3.14 (worker Process crashed on import, leaving whisper as orphan).
        """
        import os, signal, threading, subprocess, json

        # Unpack args: (chunk_wav, model, language, whisper_bin, idx, total, whisper_word_timestamps, initial_prompt, tmp_dir)
        chunk_wav, model, language, whisper_bin, idx, total, whisper_word_timestamps, initial_prompt, tmp_dir = args
        whisper_bin = whisper_bin or self._require_whisper_cli()
        if not whisper_bin:
            return None

        whisper_cmd = [
            whisper_bin,
            chunk_wav,
            "--model",
            model,
            "--language",
            language,
            "--output_format",
            "json",
            "--output_dir",
            tmp_dir,
        ]
        if whisper_word_timestamps:
            whisper_cmd += ["--word_timestamps", "True"]
        if initial_prompt:
            whisper_cmd += ["--initial_prompt", initial_prompt]

        chunk_stem = os.path.splitext(os.path.basename(chunk_wav))[0]
        chunk_json = os.path.join(tmp_dir, chunk_stem + ".json")

        timed_out = threading.Event()

        def _kill_proc(proc):
            timed_out.set()
            logging.warning(
                f"[Chunk {chunk_display_idx}/{total_chunks}] Timeout after {timeout}s, killing whisper pid={proc.pid}"
            )
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
                logging.info(
                    f"[Chunk {chunk_display_idx}/{total_chunks}] Killed whisper pgid={pgid}"
                )
            except Exception as e:
                logging.warning(
                    f"[Chunk {chunk_display_idx}/{total_chunks}] killpg failed: {e}"
                )
                try:
                    proc.kill()
                except Exception:
                    pass

        try:
            proc = subprocess.Popen(
                whisper_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            timer = threading.Timer(timeout, _kill_proc, args=(proc,))
            timer.start()
            try:
                stdout, stderr = proc.communicate()
            finally:
                timer.cancel()

            if timed_out.is_set():
                return None

            if proc.returncode == 0 and os.path.exists(chunk_json):
                with open(chunk_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                segments = data.get("segments", [])
                logging.info(
                    f"[Chunk {chunk_display_idx}/{total_chunks}] Got {len(segments)} segments"
                )
                return segments
            else:
                logging.error(
                    f"[Chunk {chunk_display_idx}/{total_chunks}] whisper failed rc={proc.returncode} "
                    f"json_exists={os.path.exists(chunk_json)} stderr={stderr.decode(errors='replace')[:200]}"
                )
                return []
        except Exception as e:
            logging.error(
                f"[Chunk {chunk_display_idx}/{total_chunks}] Exception running whisper: {e}"
            )
            return None

    def _run_asr_whisper_cli(
        self, audio_path: str, model: str = "medium", language: str = "en"
    ) -> list:
        """
        Chunked whisper CLI fallback with segment-level cache, timeout, and overlap merging.
        Splits audio into chunks via ffmpeg, runs whisper per chunk, merges results.
        Uses multiprocessing.Process per chunk for reliable timeout enforcement.
        Used when faster-whisper is not available.
        """
        import tempfile
        import shutil

        whisper_bin = self._require_whisper_cli()
        if not whisper_bin:
            return []
        initial_prompt = self._resolve_asr_initial_prompt(language)

        duration = self._get_audio_duration(audio_path)
        logging.info(
            f"[whisper-cli] Audio duration: {duration:.1f}s ({duration / 60:.1f} min)"
        )

        # Setup cache
        self.asr_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key_prefix = self._build_asr_cache_key_prefix(
            "", audio_path, model, language
        )

        # Build chunks
        chunks = self._build_asr_chunk_windows(duration)

        total = len(chunks)
        logging.info(
            f"[whisper-cli] Processing {total} chunks (chunk_duration={self.asr_chunk_duration}s, overlap={self.asr_overlap_seconds}s)"
        )

        all_segments = []
        failed_chunks = []
        tmp_dir = tempfile.mkdtemp(prefix="asr_chunks_")

        try:
            for idx, chunk_start, chunk_end in chunks:
                cache_file = self._build_asr_chunk_cache_file(cache_key_prefix, idx)
                chunk_span = chunk_end - chunk_start
                chunk_label = (
                    f"chunk {idx + 1}/{total} "
                    f"[{self._format_duration_mmss(chunk_start)}"
                    f" -> {self._format_duration_mmss(chunk_end)}, "
                    f"len={self._format_duration_mmss(chunk_span)}]"
                )
                chunk_wav = os.path.join(tmp_dir, f"chunk_{idx:03d}.wav")
                def _process_chunk():
                    logging.info(
                        f"[whisper-cli] {chunk_label}: extracting audio for transcription"
                    )

                    ffmpeg_cmd = [
                        self.ffmpeg_bin,
                        "-y",
                        "-ss",
                        str(chunk_start),
                        "-t",
                        str(chunk_end - chunk_start),
                        "-i",
                        audio_path,
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        chunk_wav,
                    ]
                    try:
                        ffmpeg_result = subprocess.run(
                            ffmpeg_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            timeout=120,
                        )
                        if ffmpeg_result.returncode != 0:
                            logging.warning(
                                f"[whisper-cli] {chunk_label}: ffmpeg extraction failed, skipping"
                            )
                            return None
                    except subprocess.TimeoutExpired:
                        logging.warning(
                            f"[whisper-cli] {chunk_label}: ffmpeg timeout, skipping"
                        )
                        return None

                    logging.info(
                        f"[whisper-cli] {chunk_label}: starting transcription "
                        f"(timeout={self.asr_segment_timeout}s, word_timestamps={self.whisper_word_timestamps}, "
                        f"initial_prompt={'on' if initial_prompt else 'off'})"
                    )
                    chunk_segments_raw = self._run_transcription_process_with_timeout(
                        target_func=Analyzer._transcribe_chunk_whisper_cli,
                        args=(
                            chunk_wav,
                            model,
                            language,
                            whisper_bin,
                            idx + 1,
                            total,
                            self.whisper_word_timestamps,
                            initial_prompt,
                            tmp_dir,
                        ),
                        timeout=self.asr_segment_timeout,
                        chunk_display_idx=idx + 1,
                        total_chunks=total,
                    )

                    try:
                        if chunk_segments_raw is None:
                            logging.error(
                                f"[whisper-cli] {chunk_label}: transcription failed or timed out"
                            )
                            return None

                        chunk_segments = []
                        cache_segments = []
                        for seg in chunk_segments_raw:
                            cache_words = []
                            abs_words = []
                            for w in seg.get("words", []):
                                if not isinstance(w, dict):
                                    continue
                                cache_words.append(
                                    {
                                        "word": w.get("word", ""),
                                        "start": round(w.get("start", 0), 3),
                                        "end": round(w.get("end", 0), 3),
                                    }
                                )
                                abs_words.append(
                                    {
                                        "word": w.get("word", ""),
                                        "start": round(w.get("start", 0) + chunk_start, 3),
                                        "end": round(w.get("end", 0) + chunk_start, 3),
                                    }
                                )

                            cache_segments.append(
                                {
                                    "start": round(seg.get("start", 0), 3),
                                    "end": round(seg.get("end", 0), 3),
                                    "text": seg.get("text", ""),
                                    "words": cache_words,
                                }
                            )
                            chunk_segments.append(
                                {
                                    "start": round(seg.get("start", 0) + chunk_start, 3),
                                    "end": round(seg.get("end", 0) + chunk_start, 3),
                                    "text": seg.get("text", ""),
                                    "words": abs_words,
                                }
                            )

                        logging.info(
                            f"[whisper-cli] {chunk_label}: transcription successful, prepared {len(chunk_segments)} segments"
                        )
                        return chunk_segments, cache_segments
                    finally:
                        chunk_json = os.path.splitext(chunk_wav)[0] + ".json"
                        for tmp_f in [chunk_wav, chunk_json]:
                            try:
                                os.remove(tmp_f)
                            except Exception:
                                pass

                chunk_segments = self._run_cached_asr_chunk(
                    "whisper-cli",
                    cache_file,
                    chunk_label,
                    chunk_start,
                    chunk_end - chunk_start,
                    _process_chunk,
                )
                if chunk_segments is None:
                    failed_chunks.append((idx, chunk_start, chunk_end))
                    continue

                all_segments.extend(chunk_segments)
                logging.info(
                    f"[whisper-cli] {chunk_label}: completed with {len(chunk_segments)} segments"
                )

        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

        if failed_chunks:
            logging.warning(
                f"[whisper-cli] {len(failed_chunks)} failed chunks: {[(c[0], c[1], c[2]) for c in failed_chunks]}"
            )
            logging.warning(
                "[whisper-cli] Re-run to resume — completed chunks are cached"
            )

        merged = self._merge_asr_segments(all_segments)
        logging.info(f"[whisper-cli] ASR complete: {len(merged)} segments total")
        return merged

    def _run_asr_srt_fallback(self, audio_path: str, model: str, language: str) -> list:
        """Fallback: run Whisper with SRT output (no word timestamps)."""
        try:
            whisper_bin = self._require_whisper_cli()
            if not whisper_bin:
                return []

            cmd = [
                whisper_bin,
                audio_path,
                "--model",
                model,
                "--language",
                language,
                "--output_format",
                "srt",
                "--output_dir",
                os.path.dirname(audio_path),
            ]
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if result.returncode != 0:
                logging.error(f"Whisper SRT also failed: {result.stderr[-200:]}")
                return []
            srt_path = audio_path.replace(".wav", ".srt")
            if os.path.exists(srt_path):
                return self._parse_srt(srt_path)
            return []
        except Exception as e:
            logging.error(f"ASR SRT fallback failed: {e}")
            return []

    def _parse_whisper_json(self, json_path: str) -> list:
        """
        Parse Whisper JSON output into segments with optional word-level data.

        Returns segments with 'words' field when word_timestamps=True.
        """
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            segments = []
            for seg in data.get("segments", []):
                entry = {
                    "start": float(seg["start"]),
                    "end": float(seg["end"]),
                    "text": seg["text"].strip(),
                }
                # Include word-level timestamps if available
                if "words" in seg and seg["words"]:
                    entry["words"] = [
                        {
                            "word": w.get("word", "").strip(),
                            "start": float(w.get("start", seg["start"])),
                            "end": float(w.get("end", seg["end"])),
                        }
                        for w in seg["words"]
                    ]
                segments.append(entry)

            return segments
        except Exception as e:
            logging.error(f"Failed to parse Whisper JSON: {e}")
            return []

    def extract_soft_subtitle(self, video_path: str) -> list:
        """
        Strategy D: Try to extract embedded soft subtitle track from video.

        Works for videos that have a subtitle track muxed in (common with
        YouTube downloads). Returns empty list if no soft subtitle found.

        Returns:
            list: [{"start": 0.0, "end": 2.5, "text": "Hello world"}, ...]
                  or [] if no soft subtitle track found
        """
        try:
            # Check if video has subtitle tracks
            probe_cmd = [
                self.ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=index,codec_name",
                "-of",
                "json",
                video_path,
            ]
            result = subprocess.run(
                probe_cmd, capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return []

            probe_data = json.loads(result.stdout)
            streams = probe_data.get("streams", [])
            if not streams:
                logging.info(
                    "No subtitle tracks found in video (hard-burned or no subtitle)"
                )
                return []

            logging.info(f"Found {len(streams)} subtitle track(s), extracting...")

            # Extract first subtitle track to SRT
            srt_path = f"/tmp/soft_subtitle_{os.path.basename(video_path)}.srt"
            extract_cmd = [self.ffmpeg_bin, "-y", "-i", video_path, "-map", "0:s:0", srt_path]
            result = subprocess.run(
                extract_cmd, capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0 or not os.path.exists(srt_path):
                logging.warning("Failed to extract subtitle track")
                return []

            segments = self._parse_srt(srt_path)
            logging.info(f"Soft subtitle extracted: {len(segments)} segments")

            try:
                os.remove(srt_path)
            except:
                pass

            return segments

        except Exception as e:
            logging.warning(f"Soft subtitle extraction failed: {e}")
            return []

    def _parse_srt(self, srt_path: str) -> list:
        """Parse SRT file to extract timestamps and text."""
        segments = []

        try:
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Split by double newline (segment separator)
            blocks = content.strip().split("\n\n")

            for block in blocks:
                lines = block.strip().split("\n")
                if len(lines) >= 3:
                    # Line 0: index
                    # Line 1: timestamp
                    # Line 2+: text
                    timestamp_line = lines[1]
                    text = " ".join(lines[2:])

                    # Parse timestamp: "00:00:00,000 --> 00:00:02,500"
                    if " --> " in timestamp_line:
                        start_str, end_str = timestamp_line.split(" --> ")
                        start_sec = self._srt_time_to_seconds(start_str)
                        end_sec = self._srt_time_to_seconds(end_str)

                        segments.append(
                            {"start": start_sec, "end": end_sec, "text": text.strip()}
                        )

            return segments

        except Exception as e:
            logging.error(f"Failed to parse SRT: {e}")
            return []

    def _srt_time_to_seconds(self, time_str: str) -> float:
        """Convert SRT timestamp to seconds: '00:00:02,500' -> 2.5"""
        try:
            time_str = time_str.replace(",", ".")
            parts = time_str.split(":")
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        except:
            return 0.0

    def _analyze_audio(self, audio_path: str, top_n: int = 5) -> list:
        """
        Analyze audio to detect climax points based on energy and spectral features.

        Returns:
            list: [{"time": 10.5, "score": 0.85}, ...]
        """
        try:
            # Load audio
            y, sr = librosa.load(audio_path, sr=None)

            # Calculate frame-level features
            hop_length = 512
            frame_length = 2048

            # 1. RMS Energy (volume)
            rms = librosa.feature.rms(
                y=y, frame_length=frame_length, hop_length=hop_length
            )[0]

            # 2. Spectral Centroid (brightness)
            spectral_centroid = librosa.feature.spectral_centroid(
                y=y, sr=sr, hop_length=hop_length
            )[0]

            # 3. Zero Crossing Rate (speech activity)
            zcr = librosa.feature.zero_crossing_rate(
                y, frame_length=frame_length, hop_length=hop_length
            )[0]

            # Normalize features
            rms_norm = (rms - np.mean(rms)) / (np.std(rms) + 1e-8)
            centroid_norm = (spectral_centroid - np.mean(spectral_centroid)) / (
                np.std(spectral_centroid) + 1e-8
            )
            zcr_norm = (zcr - np.mean(zcr)) / (np.std(zcr) + 1e-8)

            # Combine features (weighted sum)
            climax_score = 0.5 * rms_norm + 0.3 * centroid_norm + 0.2 * zcr_norm

            # Convert frame indices to time
            times = librosa.frames_to_time(
                np.arange(len(climax_score)), sr=sr, hop_length=hop_length
            )

            # Find peaks
            from scipy.signal import find_peaks

            peaks, properties = find_peaks(
                climax_score, height=1.0, distance=sr // hop_length * 2
            )  # At least 2 seconds apart

            # Get top N peaks
            if len(peaks) > 0:
                peak_heights = properties["peak_heights"]
                top_indices = np.argsort(peak_heights)[-top_n:][::-1]

                climax_points = []
                for idx in top_indices:
                    if idx < len(peaks):
                        peak_idx = peaks[idx]
                        climax_points.append(
                            {
                                "time": float(times[peak_idx]),
                                "score": float(climax_score[peak_idx]),
                            }
                        )

                # Sort by time
                climax_points.sort(key=lambda x: x["time"])
                logging.info(f"Detected {len(climax_points)} audio climax points")
                return climax_points
            else:
                logging.warning("No significant audio climax points detected")
                return []

        except Exception as e:
            logging.error(f"Audio analysis failed: {e}")
            return []

    def _detect_scenes(self, video_path: str, threshold: float = 27.0) -> list:
        """
        Detect scene changes using PySceneDetect.

        Returns:
            list: [0.0, 10.5, 25.3, ...]  (timestamps in seconds)
        """
        try:
            # Use ContentDetector for scene change detection
            scene_list = detect(video_path, ContentDetector(threshold=threshold))

            # Extract timestamps
            scene_changes = [0.0]  # Always start at 0
            for scene in scene_list:
                start_time = scene[0].get_seconds()
                scene_changes.append(start_time)

            logging.info(f"Detected {len(scene_changes)} scene changes")
            return scene_changes

        except Exception as e:
            logging.error(f"Scene detection failed: {e}")
            return [0.0]

    def _segment_by_topic(
        self, asr_result: list, clip_strategy: str = "opinion"
    ) -> tuple:
        """
        Segment transcript by topic using LLM.

        Args:
            asr_result: ASR segments with timestamps and text
            clip_strategy: "opinion" (default) prioritises strong viewpoints/conclusions;
                           "topic" uses pure chapter/topic segmentation;
                           "hybrid" blends both signals.

        Returns:
            tuple: (topic_segments, topic_summaries, segmentation_meta)
                topic_segments: [{"start": 0.0, "end": 45.2, "topic": "Introduction", ...}, ...]
                topic_summaries: [{"topic": "Introduction", "summary": "..."}, ...]
                segmentation_meta: {"clip_strategy_used": str, "segmentation_effective": bool, "fallback_reason": str}
        """
        _fallback_meta = {
            "clip_strategy_used": clip_strategy,
            "segmentation_effective": False,
            "fallback_reason": "",
        }

        if not asr_result:
            logging.warning("No ASR result for topic segmentation")
            _fallback_meta["fallback_reason"] = "no_asr_result"
            return [], [], _fallback_meta

        if not self.llm_api_key:
            logging.warning("LLM API key not configured, skipping topic segmentation")
            _fallback_meta["fallback_reason"] = "no_llm_api_key"
            return [], [], _fallback_meta

        try:
            total_duration = max(
                (float(seg.get("end", 0.0)) for seg in asr_result), default=0.0
            )
            windows = self._build_topic_segmentation_windows(asr_result)
            if not windows:
                logging.warning("No usable transcript windows for topic segmentation")
                _fallback_meta["fallback_reason"] = "no_segmentation_windows"
                return [], [], _fallback_meta

            max_workers = max(
                1, min(int(getattr(self, "topic_segment_max_workers", 4)), len(windows))
            )
            logging.info(
                "Running topic segmentation on %s overlapping window(s) (chunk=%ss, overlap=%ss, workers=%s)",
                len(windows),
                getattr(self, "topic_segment_chunk_duration", 1500),
                getattr(self, "topic_segment_chunk_overlap_seconds", 180),
                max_workers,
            )

            window_results = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(
                        self._segment_topic_window,
                        window,
                        clip_strategy,
                        total_duration,
                        asr_result,
                        len(windows),
                    ): window
                    for window in windows
                }
                for future in as_completed(future_map):
                    window = future_map[future]
                    try:
                        window_result = future.result()
                    except Exception as e:
                        logging.exception(
                            "Topic segmentation window failed: %s - %s",
                            window.get("start"),
                            window.get("end"),
                        )
                        window_result = {
                            "window_index": window.get("index", 0),
                            "window_start": window.get("start", 0.0),
                            "window_end": window.get("end", 0.0),
                            "segments": [],
                            "has_opinion_fields": False,
                            "fallback_reason": f"window_exception: {e}",
                        }
                    window_results.append(window_result)

            window_results.sort(
                key=lambda item: (
                    item.get("window_start", 0.0),
                    item.get("window_end", 0.0),
                )
            )
            merged_segments, merged_summaries = self._merge_chunked_topic_segments(
                window_results,
                total_duration=total_duration,
                clip_strategy=clip_strategy,
            )
            merged_segments, merged_summaries = self._filter_strategy_segments(
                merged_segments,
                merged_summaries,
                clip_strategy=clip_strategy,
            )

            successful_windows = [
                item for item in window_results if item.get("segments")
            ]
            failed_windows = [
                item for item in window_results if not item.get("segments")
            ]
            has_opinion_fields = any(
                item.get("has_opinion_fields") for item in window_results
            )

            if not merged_segments:
                failure_reasons = [
                    str(item.get("fallback_reason", "")).strip()
                    for item in window_results
                    if str(item.get("fallback_reason", "")).strip()
                ]
                if failure_reasons and len(set(failure_reasons)) == 1:
                    fallback_reason = failure_reasons[0]
                elif successful_windows:
                    fallback_reason = "llm_empty_segments"
                else:
                    fallback_reason = "all_topic_windows_failed"
                _fallback_meta["fallback_reason"] = fallback_reason
                logging.warning(
                    "Topic segmentation produced no usable segments (%s)",
                    fallback_reason,
                )
                return [], [], _fallback_meta

            segmentation_effective = clip_strategy == "topic" or (
                clip_strategy in ("opinion", "hybrid") and has_opinion_fields
            )
            fallback_reason = (
                "" if segmentation_effective else "opinion_fields_missing_from_llm"
            )
            if not segmentation_effective:
                logging.warning(
                    "Opinion fields missing from chunked LLM response for strategy='%s'; segmentation_effective=False",
                    clip_strategy,
                )

            meta = {
                "clip_strategy_used": clip_strategy,
                "segmentation_effective": segmentation_effective,
                "fallback_reason": fallback_reason,
                "chunk_count": len(windows),
                "successful_chunk_count": len(successful_windows),
                "failed_chunk_count": len(failed_windows),
                "chunk_duration_seconds": getattr(
                    self, "topic_segment_chunk_duration", 1500
                ),
                "chunk_overlap_seconds": getattr(
                    self, "topic_segment_chunk_overlap_seconds", 180
                ),
            }
            logging.info(
                "Topic segmentation completed: %s segments from %s chunk(s) [strategy=%s, effective=%s]",
                len(merged_segments),
                len(windows),
                clip_strategy,
                segmentation_effective,
            )
            return merged_segments, merged_summaries, meta

        except Exception as e:
            logging.error(f"Topic segmentation failed: {e}")
            _fallback_meta["fallback_reason"] = f"exception: {e}"
            return [], [], _fallback_meta

    def _build_topic_segmentation_windows(self, asr_result: list) -> list:
        """Build overlapping transcript windows for LLM topic segmentation."""
        total_duration = max(
            (float(seg.get("end", 0.0)) for seg in asr_result), default=0.0
        )
        if total_duration <= 0:
            return []

        chunk_duration = float(getattr(self, "topic_segment_chunk_duration", 1500))
        overlap = float(getattr(self, "topic_segment_chunk_overlap_seconds", 180))
        step = max(1.0, chunk_duration - overlap)

        windows = []
        start = 0.0
        index = 0
        while start < total_duration:
            end = min(start + chunk_duration, total_duration)
            windows.append(
                {
                    "index": index,
                    "start": round(start, 3),
                    "end": round(end, 3),
                }
            )
            if end >= total_duration:
                break
            start = end - overlap
            if start <= windows[-1]["start"]:
                start = windows[-1]["start"] + step
            index += 1

        return windows

    def _build_topic_window_transcript(
        self, asr_result: list, window_start: float, window_end: float
    ) -> str:
        """Return a compact transcript snippet for one topic window."""
        transcript_lines = []
        for seg in asr_result:
            seg_start = float(seg.get("start", 0.0))
            seg_end = float(seg.get("end", 0.0))
            if seg_end <= window_start or seg_start >= window_end:
                continue
            transcript_lines.append(
                f"[{seg_start:.1f}s - {seg_end:.1f}s] {seg.get('text', '').strip()}"
            )
        return "\n".join(line for line in transcript_lines if line.strip())

    def _build_topic_window_prompt(
        self,
        window_start: float,
        window_end: float,
        window_index: int,
        window_total: int,
        transcript: str,
        clip_strategy: str,
        total_duration: float,
    ) -> str:
        strategy = (clip_strategy or "opinion").lower()
        final_min_duration, final_max_duration = self._get_final_clip_duration_bounds()
        config_obj = getattr(self, "config", None)
        brand_keywords_source = (
            getattr(config_obj, "PROFILE_BRAND_KEYWORDS", None)
            or getattr(config_obj, "profile_brand_keywords", None)
            or []
        )
        brand_keywords = (
            "、".join(brand_keywords_source)
            if brand_keywords_source
            else "赛道相关关键词"
        )
        product = (
            getattr(config_obj, "PROFILE_PRODUCT", None)
            or getattr(config_obj, "profile_product", None)
            or "目标产品"
        )
        market = (
            getattr(config_obj, "PROFILE_MARKET", None)
            or getattr(config_obj, "profile_market", None)
            or "目标市场"
        )
        audience = (
            getattr(config_obj, "PROFILE_AUDIENCE", None)
            or getattr(config_obj, "profile_audience", None)
            or "目标受众"
        )
        opinion_block = ""
        if strategy in ("opinion", "hybrid"):
            opinion_block = """
## Opinion-first scoring (apply for each segment):
For each segment also provide these opinion-intelligence fields:
- `conclusion_clarity` (0-10): How clear and explicit is the core conclusion or claim?
- `self_contained` (0-10): Can a viewer understand this clip WITHOUT watching the rest of the video?
- `info_density` (0-10): How information-dense is the segment (facts, data, examples per second)?
- `viral_fit` (0-10): How well does this segment fit short-video virality (hook, punchline, controversy, novelty)?
- `stance`: One sentence capturing the speaker's core viewpoint or claim in this segment (empty string if none).
- `key_sentences`: List of 1-2 verbatim sentences (from the transcript) that best represent the opinion/insight.
- `publishability` (0-100): Overall publishability score combining the above four dimensions.
  Formula hint: publishability ≈ (conclusion_clarity*2.5 + self_contained*2.5 + info_density*2.0 + viral_fit*3.0)

For `score`, weight opinion signals heavily when clip_strategy=opinion:
  score ≈ 0.4*publishability + 0.3*self_contained*10 + 0.3*base_topic_quality
"""

        strategy_guidance = self._build_strategy_guidance(strategy)
        strategy_field_notes = self._build_strategy_field_notes(strategy)
        strategy_example = self._build_strategy_json_example(strategy)
        strategy_target_guidance = self._build_strategy_target_guidance(
            strategy=strategy,
            window_span=max(window_end - window_start, 0.0),
            final_min_duration=final_min_duration,
            final_max_duration=final_max_duration,
        )

        estimated_segments = max(
            1,
            round(
                max(window_end - window_start, 1.0)
                / max((final_min_duration + final_max_duration) / 2.0, 60.0)
            ),
        )

        return f"""Analyze the following video transcript window and identify the best segments for short-video clipping.

Clip strategy: {strategy}
{strategy_guidance}

Window context:
- Window index: {window_index + 1}/{window_total}
- Window time span: {window_start:.1f}s - {window_end:.1f}s
- Total video duration: approximately {total_duration:.1f} seconds
- Overlap context is already included in neighboring windows

Brand information:
- Product: {product}
- Market: {market}
- Audience: {audience}
- Keywords: {brand_keywords}

For each segment, provide ALL of the following fields:
1. `start`: Start timestamp (seconds)
2. `end`: End timestamp (seconds)
3. `topic`: Concise title (3-8 words)
4. `summary`: 1-2 sentence description
5. `score`: 0-100 clip-worthiness score
6. `reason`: Why this is a good clip boundary
{opinion_block}
{strategy_field_notes}
Transcript:
{transcript}

Target guidance:
- Total video duration: approximately {total_duration:.1f} seconds
- This window would typically support up to about {estimated_segments} publishable candidates if there are genuine semantic transitions
- Minimum segment duration: {self.topic_segment_min_duration} seconds
- Prefer segments that are complete ideas, examples, steps, or strong conclusions
- Preferred final clip range after downstream repair: {final_min_duration:.0f}-{final_max_duration:.0f} seconds
- You are proposing semantic candidate segments; final publication length will be adjusted downstream
- Avoid tiny fragments unless there is a genuine hard transition
{strategy_target_guidance}

Return ONLY valid JSON in this exact format:
Example note:
- The JSON example below demonstrates schema only.
- Do NOT imitate its exact timestamps, spacing, count, or coverage pattern.
- Choose boundaries only from the transcript's real semantic transitions.

{{
  "segments": [
    {strategy_example}
  ]
}}

Rules:
- Segments must be chronological and non-overlapping
- Do not create segments shorter than the minimum duration unless unavoidable
- When opinion fields are requested, all opinion fields must be present; use 0 / empty string / empty list as defaults if the segment has no strong opinion
- `score` should reflect clip-worthiness for short-video publishing
- Do not include any text outside the JSON structure"""

    def _build_segmentation_system_prompt(self, clip_strategy: str) -> str:
        strategy = (clip_strategy or "opinion").lower()
        if strategy == "topic":
            return (
                "You are a careful video topic segmentation assistant. "
                "Identify coherent chapter-like segments with clean semantic boundaries. "
                "Do not partition a transcript window into evenly sized chunks unless the topic transitions truly support it. "
                "Return only valid JSON."
            )
        if strategy == "hybrid":
            return (
                "You are a careful short-video segmentation assistant. "
                "Balance topic coherence with strong standalone clip potential. "
                "Return only valid JSON."
            )
        return (
            "You are an opinion-first short-video clipping assistant. "
            "Find the strongest standalone claims, conclusions, and publishable moments. "
            "Return only valid JSON."
        )

    def _build_strategy_guidance(self, strategy: str) -> str:
        if strategy == "topic":
            return """- "topic": Prioritise coherent chapter/topic boundaries.
- Return only the strongest distinct themes that are worth clipping as standalone topic blocks.
- Keep semantically complete units, even if they are less punchy than viral highlights.
- Partial coverage is acceptable: skip weak setup, filler, repetition, and low-value transitions.
- Do not dedicate a standalone topic segment to a brief opening hook, housekeeping, sales pitch, or generic outro unless it contains a complete high-value theme by itself.
- Never split a coherent chapter into uniform subsegments just to increase segment count."""
        if strategy == "hybrid":
            return """- "hybrid": Balance both opinion strength and chapter coherence.
- Prefer segments that are both self-contained and structurally meaningful.
- Avoid weak filler, but do not sacrifice topic completeness for punchiness alone."""
        return """- "opinion": Prioritise segments with strong viewpoints, clear conclusions, and viral potential.
- Build each candidate as a viewpoint arc: include the core claim plus the nearby setup, support, or punchline that makes it publishable.
- A segment does NOT need to represent the full chapter; it should represent the strongest publishable claim.
- It is acceptable to skip low-signal spans entirely if they do not contain clear opinions or conclusions.
- Do not break one argumentative arc into consecutive 20-40 second microsegments unless the transcript truly pivots to a new claim."""

    def _build_strategy_field_notes(self, strategy: str) -> str:
        if strategy == "topic":
            return """
Additional topic guidance:
- Let `score` reflect topic completeness, clarity, and usefulness as a standalone chapter clip.
- Prefer fewer, more complete theme blocks over many small evenly spaced windows.
- If the window only contains 3 strong themes, return 3 strong themes; do not invent a filler fourth chapter just to use more clip slots.
- A strong 150-190s block is better than two adjacent 70-90s subchapters that express the same broader idea.
- Score low-value intros, housekeeping, transitions, and repetitive recap lower instead of forcing them into a topic segment.
- When two adjacent spans are clearly one broader theme, return them as one topic block instead of two sequential mini-chapters.
- Do not invent opinion-only metadata unless the transcript clearly contains it.
"""
        if strategy == "hybrid":
            return """
Additional hybrid guidance:
- Let `score` balance topic completeness and standalone publishability.
- When opinion signals exist, include the opinion fields. When they are weak, still keep topic coherence high.
"""
        return """
Additional opinion guidance:
- Let `score` strongly prefer explicit claims, conclusions, unique insights, and strong hooks.
- Prefer a smaller set of stronger publishable arcs over many tiny adjacent claims.
- If two adjacent transcript spans support the same core claim, keep them in one candidate instead of splitting them.
- Do not reward transitional exposition unless it contains a standalone claim worth publishing.
"""

    def _build_strategy_json_example(self, strategy: str) -> str:
        if strategy == "topic":
            return (
                '{"start": 41.3, "end": 186.9, "topic": "Why Traditional Learning Wastes Time", '
                '"summary": "The speaker builds a complete argument that books and long videos often repeat low-value information before introducing a better workflow.", '
                '"score": 88, "reason": "A full topic arc with setup, explanation, and a clean pivot to the next subject."},\n'
                '    {"start": 266.5, "end": 418.7, "topic": "AI Workflow for Faster Research", '
                '"summary": "The speaker outlines a repeatable AI-assisted workflow for compressing research and learning with concrete examples.", '
                '"score": 91, "reason": "A natural chapter block with practical steps and no need for further subdivision."}'
            )
        if strategy == "hybrid":
            return (
                '{"start": 12.0, "end": 86.0, "topic": "Books Are Mostly Redundant", '
                '"summary": "The speaker argues that modern AI tools can remove most repetitive content from books and videos.", '
                '"score": 92, "reason": "Strong thesis with clear chapter coherence and broad standalone value.", '
                '"conclusion_clarity": 9, "self_contained": 8, "info_density": 8, "viral_fit": 8, '
                '"stance": "Most books are too repetitive for modern learners.", '
                '"key_sentences": ["Most books repeat one good idea for 300 pages."], "publishability": 86},\n'
                '    {"start": 86.0, "end": 165.0, "topic": "Building the AI Study Pipeline", '
                '"summary": "The speaker turns the thesis into a step-by-step workflow using AI summarisation and note-taking.", '
                '"score": 87, "reason": "Useful how-to chapter with clear follow-through and moderate publishability.", '
                '"conclusion_clarity": 7, "self_contained": 8, "info_density": 8, "viral_fit": 6, '
                '"stance": "Use AI to compress and structure knowledge before studying deeply.", '
                '"key_sentences": ["You should let AI compress the material before you read it."], "publishability": 77}'
            )
        return (
            '{"start": 58.4, "end": 192.6, "topic": "Books Waste Your Time", '
            '"summary": "The speaker makes a direct claim that most books are bloated and inefficient for learning.", '
            '"score": 95, "reason": "Strong standalone claim with a clear hook and memorable framing.", '
            '"conclusion_clarity": 9, "self_contained": 9, "info_density": 8, "viral_fit": 9, '
            '"stance": "Most books are ten times longer than the value they contain.", '
            '"key_sentences": ["Most books are ten times longer than the value they contain."], "publishability": 89},\n'
            '    {"start": 327.1, "end": 457.9, "topic": "AI Makes Research Instant", '
            '"summary": "The speaker claims AI can compress days of research into minutes when used with the right workflow.", '
            '"score": 90, "reason": "Bold conclusion with practical relevance and strong short-video appeal.", '
            '"conclusion_clarity": 8, "self_contained": 8, "info_density": 9, "viral_fit": 8, '
            '"stance": "AI can compress days of research into minutes.", '
            '"key_sentences": ["AI can compress days of research into minutes."], "publishability": 84}'
        )

    def _get_final_clip_duration_bounds(self) -> tuple[float, float]:
        config_obj = getattr(self, "config", None)
        min_duration = (
            getattr(config_obj, "min_clip_duration", None)
            if config_obj is not None
            else None
        )
        max_duration = (
            getattr(config_obj, "max_clip_duration", None)
            if config_obj is not None
            else None
        )

        try:
            min_duration = float(min_duration)
        except Exception:
            min_duration = float(max(30, getattr(self, "topic_segment_min_duration", 30)))

        try:
            max_duration = float(max_duration)
        except Exception:
            max_duration = max(min_duration, min_duration + 60.0)

        if max_duration < min_duration:
            max_duration = min_duration

        return min_duration, max_duration

    def _build_strategy_target_guidance(
        self,
        strategy: str,
        window_span: float,
        final_min_duration: float,
        final_max_duration: float,
    ) -> str:
        target = max(
            final_min_duration,
            min(final_max_duration, (final_min_duration + final_max_duration) / 2.0),
        )
        if strategy == "topic":
            return (
                f"- Prefer major topic blocks that can naturally yield clips around {target:.0f}s without forced equal partitioning\n"
                "- It is acceptable to return fewer segments when one topic stays coherent for a long stretch\n"
                "- Gaps are acceptable; you do not need to cover the entire window end-to-end\n"
                "- Avoid spending one of the limited clip slots on pure intro, housekeeping, or CTA material unless it is genuinely one of the strongest themes\n"
                "- Do not create evenly sized chapter slices just to cover the full window"
            )
        if strategy == "hybrid":
            return (
                f"- Prefer candidates that can survive downstream repair into roughly {final_min_duration:.0f}-{final_max_duration:.0f}s without losing their main point\n"
                "- Keep strong topic pivots and strong publishable moments; skip filler instead of backfilling uniform windows"
            )
        return (
            f"- Prefer standalone claim-driven candidates that can be repaired into roughly {final_min_duration:.0f}-{final_max_duration:.0f}s clips\n"
            "- Prefer 3-6 strong opinion arcs across a long window rather than many micro-claims when the transcript allows it\n"
            "- Do not cover the whole window for completeness; only return moments with clear publishable value"
        )

    def _build_video_summary_transcript(
        self, asr_result: list, max_chars: int = 24000
    ) -> tuple[str, bool]:
        """Build a compact transcript snippet for summary generation."""
        transcript_lines = []
        total_chars = 0
        truncated = False

        for seg in asr_result:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            line = f"[{start:.1f}s - {end:.1f}s] {text}"
            line_len = len(line) + 1
            if transcript_lines and total_chars + line_len > max_chars:
                truncated = True
                break
            transcript_lines.append(line)
            total_chars += line_len

        transcript = "\n".join(transcript_lines)
        if truncated:
            transcript += "\n...[truncated]"
        return transcript, truncated

    def _build_video_summary_prompt(
        self,
        analysis_result: dict,
        video_name: str,
        transcript: str,
        transcript_truncated: bool,
    ) -> str:
        """Build the LLM prompt for a Chinese video summary."""
        asr_result = analysis_result.get("asr_result", [])
        topic_summaries = analysis_result.get("topic_summaries", [])
        topic_segments = analysis_result.get("topic_segments", [])
        audio_climax_points = analysis_result.get("audio_climax_points", [])
        scene_changes = analysis_result.get("scene_changes", [])

        compact_topic_summaries = []
        if isinstance(topic_summaries, list):
            for item in topic_summaries[:8]:
                if not isinstance(item, dict):
                    continue
                compact_topic_summaries.append(
                    {
                        "topic": str(item.get("topic", "")).strip(),
                        "summary": str(item.get("summary", "")).strip(),
                    }
                )

        compact_topic_segments = []
        if isinstance(topic_segments, list):
            for item in topic_segments[:8]:
                if not isinstance(item, dict):
                    continue
                compact_topic_segments.append(
                    {
                        "start": round(float(item.get("start", 0.0)), 1),
                        "end": round(float(item.get("end", 0.0)), 1),
                        "topic": str(item.get("topic", "")).strip(),
                        "summary": str(item.get("summary", "")).strip(),
                    }
                )

        compact_audio_climax_points = []
        if isinstance(audio_climax_points, list):
            for item in audio_climax_points[:6]:
                if not isinstance(item, dict):
                    continue
                compact_audio_climax_points.append(
                    {
                        "time": round(float(item.get("time", 0.0)), 1),
                        "score": round(float(item.get("score", 0.0)), 3),
                    }
                )

        compact_scene_changes = []
        if isinstance(scene_changes, list):
            for item in scene_changes[:12]:
                try:
                    compact_scene_changes.append(round(float(item), 1))
                except (TypeError, ValueError):
                    continue

        return f"""你是一名擅长把视频内容提炼成高密度中文总结的助手。

任务：
基于提供的 transcript 和结构化分析信息，输出一个可被程序渲染为 Markdown 的严格 JSON 总结。

要求：
- 只输出严格 JSON，不要输出 Markdown，不要输出额外解释
- 用中文写作，面向希望高效理解视频核心内容的读者
- 所有结论都必须以 transcript 或提供的结构化信息为依据，不要脑补
- 不要复述整段字幕，要提炼主线、结论、方法、启示、价值和适用边界
- 如果内容偏知识/教程，强调方法、步骤、注意事项和实践价值
- 如果内容偏观点/评论，强调核心立场、主要论据和适用边界
- 如果存在 topic_summaries 或 topic_segments，可以把它们当作结构线索，但不能替代 transcript
- audio_climax_points 和 scene_changes 只作弱参考，不要据此推断语义重点
- 如果 transcript 已截断，只能基于现有内容总结，不要假设视频全貌
- 如果某类信息证据不足，可以少写，但不要编造
- `best_for` 只能写 transcript 能支持的人群或场景，不要补充无依据的人物画像
- `best_for` 优先写“适合什么问题/场景的人看”，不要写“年轻人”“普通人”“上班族”这类泛化标签，除非 transcript 明确提到
- `actionable_takeaways` 必须来自 transcript 中明确出现的方法、判断标准、原则或建议
- `core_points` 必须写成“视频真正展开过的关键判断或方法”，不要写目录式标题
- `evidence_points` 应提炼 2-4 条视频里明确表达过的关键判断、标准、例子或论据，不要写空泛套话
- `caveats` 用于总结这套观点成立的前提、边界或未覆盖的问题；如果证据不足可以为空数组
- `x_post_copy_zh` 用一小段可直接发到 X 的中文文案总结这支视频，要求有吸引力但不标题党，不要编造，不要使用 emoji，不要堆砌 hashtags
- `x_post_copy_en` 用一段对应的地道美式英文翻译，语气自然，避免直译腔
- 如果视频里没有足够证据支持“启示”或“建议”，宁可少写，也不要用通用创业鸡汤或常识性废话补齐

视频文件名: {video_name}
ASR 段数: {len(asr_result)}
主题摘要数量: {len(topic_summaries) if isinstance(topic_summaries, list) else 0}
主题分段数量: {len(topic_segments) if isinstance(topic_segments, list) else 0}
音频高潮点数量: {len(audio_climax_points) if isinstance(audio_climax_points, list) else 0}
场景切换点数量: {len(scene_changes) if isinstance(scene_changes, list) else 0}
Transcript 是否截断: {"yes" if transcript_truncated else "no"}

可参考的结构信息：
主题摘要: {json.dumps(compact_topic_summaries, ensure_ascii=False)}
主题分段: {json.dumps(compact_topic_segments, ensure_ascii=False)}
音频高潮点: {json.dumps(compact_audio_climax_points, ensure_ascii=False)}
场景切换点: {json.dumps(compact_scene_changes, ensure_ascii=False)}

Transcript:
{transcript}

请返回以下 JSON 结构：
{{
  "title": "准确、克制、不标题党的标题",
  "one_sentence_summary": "一句话概括视频核心内容",
  "core_points": ["核心观点1", "核心观点2", "核心观点3"],
  "evidence_points": ["视频里明确提到的依据1", "视频里明确提到的依据2"],
  "insights": ["对用户的启示1", "对用户的启示2", "对用户的启示3"],
  "actionable_takeaways": ["可执行建议1", "可执行建议2", "可执行建议3"],
  "caveats": ["适用边界1", "适用边界2"],
  "best_for": ["适合的人群1", "适合的人群2"],
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "x_post_copy_zh": "一段可直接发布到 X 的中文短文",
  "x_post_copy_en": "A natural American English version of the same post"
}}

规则：
- core_points 2-5 条，优先写最关键的内容
- evidence_points 2-4 条，尽量具体，优先写视频中真实展开过的判断、标准、例子或论据
- insights 1-4 条；如果证据不足，不要强行凑数
- actionable_takeaways 1-4 条；如果视频缺少明确方法论，不要编造建议
- caveats 0-3 条；没有明确边界信息时可以为空数组
- best_for 1-3 条；只能写视频内容直接支持的受众或使用场景，不要猜年龄、身份阶段或职业画像
- keywords 3-8 个
- x_post_copy_zh 1 段，建议 80-180 字，像一条真人会发的高质量 X 帖子；要有信息密度和传播性，但不能夸张失真
- x_post_copy_en 1 段，与中文内容含义一致，但不要求逐字直译，优先自然流畅
- title 不要过长，不要夸张，不要营销腔
- 只输出 JSON
"""

    def _build_video_summary_data(
        self,
        analysis_result: dict,
        api_key: str,
        base_url: str,
        video_name: str,
    ) -> Optional[dict]:
        """Call the LLM and parse summary JSON."""
        import requests

        asr_result = analysis_result.get("asr_result", [])
        transcript, transcript_truncated = self._build_video_summary_transcript(
            asr_result
        )
        prompt = self._build_video_summary_prompt(
            analysis_result=analysis_result,
            video_name=video_name,
            transcript=transcript,
            transcript_truncated=transcript_truncated,
        )

        system_prompt = (
            "你是一个严谨、克制、重证据的视频总结助手。"
            "你必须只返回严格 JSON，避免夸张、营销腔和无依据推断。"
        )
        payload = {
            "model": self.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 16000,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        response = None
        last_error = None
        for attempt in range(1, 3):
            try:
                response = requests.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.llm_timeout,
                )
                break
            except requests.RequestException as e:
                last_error = e
                logging.warning(
                    "Video summary request attempt %s failed for %s: %s",
                    attempt,
                    video_name,
                    e,
                )
                if attempt < 2:
                    time.sleep(1.5 * attempt)

        if response is None:
            logging.error("Video summary request failed: %s", last_error)
            return None

        if response.status_code != 200:
            logging.error(
                "Video summary API error for %s: %s - %s",
                video_name,
                response.status_code,
                response.text,
            )
            return None

        try:
            result = response.json()
            content = result.get("choices", [])[0].get("message", {}).get(
                "content", ""
            )
        except Exception as e:
            logging.error("Failed to read video summary response: %s", e)
            return None

        parsed = self._extract_json_object(content)
        if not parsed:
            logging.error("No valid JSON found in video summary response for %s", video_name)
            return None

        return {
            "title": str(parsed.get("title", "")).strip() or video_name,
            "one_sentence_summary": str(parsed.get("one_sentence_summary", "")).strip(),
            "core_points": [
                str(item).strip()
                for item in parsed.get("core_points", [])
                if str(item).strip()
            ],
            "evidence_points": [
                str(item).strip()
                for item in parsed.get("evidence_points", [])
                if str(item).strip()
            ],
            "insights": [
                str(item).strip()
                for item in parsed.get("insights", [])
                if str(item).strip()
            ],
            "actionable_takeaways": [
                str(item).strip()
                for item in parsed.get("actionable_takeaways", [])
                if str(item).strip()
            ],
            "caveats": [
                str(item).strip()
                for item in parsed.get("caveats", [])
                if str(item).strip()
            ],
            "best_for": [
                str(item).strip()
                for item in self._filter_video_summary_best_for(
                    parsed.get("best_for", []), transcript
                )
                if str(item).strip()
            ],
            "keywords": [
                str(item).strip()
                for item in parsed.get("keywords", [])
                if str(item).strip()
            ],
            "x_post_copy_zh": str(
                parsed.get("x_post_copy_zh", parsed.get("x_post_copy", ""))
            ).strip(),
            "x_post_copy_en": str(parsed.get("x_post_copy_en", "")).strip(),
        }

    def _render_video_summary_markdown(
        self,
        summary_data: dict,
        analysis_result: dict,
        video_name: str,
    ) -> str:
        """Render the summary JSON as Markdown."""
        video_path = analysis_result.get("video_path", "")
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def _bullets(items: list) -> str:
            if not items:
                return "- 无"
            return "\n".join(f"- {item}" for item in items)

        md = [
            f"# {summary_data.get('title') or '视频总结'}",
            "",
            f"- **视频文件**: `{video_name}`",
            f"- **源路径**: `{video_path}`" if video_path else "- **源路径**: 未提供",
            f"- **生成时间**: {generated_at}",
            "",
            "## 一句话概括",
            "",
            summary_data.get("one_sentence_summary", "").strip() or "暂无",
            "",
            "## 核心内容",
            "",
            _bullets(summary_data.get("core_points", [])),
            "",
            "## 关键依据",
            "",
            _bullets(summary_data.get("evidence_points", [])),
            "",
            "## 对用户的启示与价值",
            "",
            _bullets(summary_data.get("insights", [])),
            "",
            "## 可执行建议",
            "",
            _bullets(summary_data.get("actionable_takeaways", [])),
            "",
            "## 适用边界",
            "",
            _bullets(summary_data.get("caveats", [])),
            "",
            "## 适合谁看",
            "",
            _bullets(summary_data.get("best_for", [])),
            "",
            "## 关键词",
            "",
            ", ".join(summary_data.get("keywords", [])) or "无",
        ]

        topic_summaries = analysis_result.get("topic_summaries", [])
        if isinstance(topic_summaries, list) and topic_summaries:
            md.extend(
                [
                    "",
                    "## 结构线索",
                    "",
                ]
            )
            for idx, item in enumerate(topic_summaries[:8], 1):
                topic = str(item.get("topic", "")).strip()
                summary = str(item.get("summary", "")).strip()
                if topic or summary:
                    md.append(f"{idx}. {topic or '主题'} - {summary or '无'}")

        md.extend(
            [
                "",
                "## X Post 文案",
                "",
                "### 中文",
                "",
                summary_data.get("x_post_copy_zh", "").strip() or "暂无",
                "",
                "### English",
                "",
                summary_data.get("x_post_copy_en", "").strip() or "暂无",
            ]
        )

        return "\n".join(md).strip() + "\n"

    def _segment_topic_window(
        self,
        window: dict,
        clip_strategy: str,
        total_duration: float,
        asr_result: list,
        window_total: int,
    ) -> dict:
        """Run one LLM request for a transcript window."""
        import requests

        window_start = float(window.get("start", 0.0))
        window_end = float(window.get("end", 0.0))
        window_index = int(window.get("index", 0))
        transcript = self._build_topic_window_transcript(
            asr_result, window_start, window_end
        )
        prompt = self._build_topic_window_prompt(
            window_start=window_start,
            window_end=window_end,
            window_index=window_index,
            window_total=window_total,
            transcript=transcript,
            clip_strategy=clip_strategy,
            total_duration=total_duration,
        )

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": self._build_segmentation_system_prompt(clip_strategy),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": int(0.95 * 8192),  # DeepSeek-V3 max output is 8K, use 90%
        }

        max_attempts = 4
        response = None
        parsed = None
        last_error = None
        last_failure_reason = "llm_request_failed"

        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(
                    f"{self.llm_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.llm_timeout,
                )
            except requests.RequestException as e:
                last_error = e
                last_failure_reason = "llm_request_failed"
                logging.warning(
                    "LLM request attempt %s failed for window %s-%s: %s",
                    attempt,
                    window_start,
                    window_end,
                    e,
                )
                if attempt < max_attempts:
                    time.sleep(1.5 * (2 ** (attempt - 1)))
                continue

            if response.status_code != 200:
                if response.status_code in {429} or response.status_code >= 500:
                    last_error = RuntimeError(
                        f"LLM API error {response.status_code}: {response.text}"
                    )
                    last_failure_reason = f"llm_api_error_{response.status_code}"
                    logging.warning(
                        "LLM API attempt %s returned retryable status for window %s-%s: %s - %s",
                        attempt,
                        window_start,
                        window_end,
                        response.status_code,
                        response.text,
                    )
                    if attempt < max_attempts:
                        time.sleep(1.5 * (2 ** (attempt - 1)))
                    continue

                logging.error(
                    "LLM API error for window %s-%s: %s - %s",
                    window_start,
                    window_end,
                    response.status_code,
                    response.text,
                )
                return {
                    "window_index": window_index,
                    "window_start": window_start,
                    "window_end": window_end,
                    "segments": [],
                    "has_opinion_fields": False,
                    "fallback_reason": f"llm_api_error_{response.status_code}",
                }

            try:
                result = response.json()
                content = result.get("choices", [])[0].get("message", {}).get("content", "")
            except (ValueError, IndexError, AttributeError, TypeError) as e:
                last_error = e
                last_failure_reason = "llm_invalid_json"
                logging.warning(
                    "LLM response decode attempt %s failed for window %s-%s: %s",
                    attempt,
                    window_start,
                    window_end,
                    e,
                )
                if attempt < max_attempts:
                    time.sleep(1.5 * (2 ** (attempt - 1)))
                continue

            parsed = self._extract_json_object(content)
            if parsed:
                break

            parsed = None
            last_error = RuntimeError("LLM invalid JSON content")
            last_failure_reason = "llm_invalid_json"
            logging.warning(
                "No valid JSON found in LLM response for window %s-%s (attempt %s/%s)",
                window_start,
                window_end,
                attempt,
                max_attempts,
            )
            if attempt < max_attempts:
                time.sleep(1.5 * (2 ** (attempt - 1)))
            continue

        if parsed is None:
            logging.error(
                "LLM request failed for window %s-%s after %s attempts: %s",
                window_start,
                window_end,
                max_attempts,
                last_error,
            )
            return {
                "window_index": window_index,
                "window_start": window_start,
                "window_end": window_end,
                "segments": [],
                "has_opinion_fields": False,
                "fallback_reason": last_failure_reason,
            }

        segments_data = parsed.get("segments", [])
        if not isinstance(segments_data, list):
            segments_data = []

        has_opinion_fields = any(
            seg.get("stance") is not None or seg.get("publishability") is not None
            for seg in segments_data
            if isinstance(seg, dict)
        )

        return {
            "window_index": window_index,
            "window_start": window_start,
            "window_end": window_end,
            "segments": segments_data,
            "has_opinion_fields": has_opinion_fields,
            "fallback_reason": "",
        }

    def _merge_chunked_topic_segments(
        self,
        window_results: list,
        total_duration: float,
        clip_strategy: str,
    ) -> tuple:
        """Merge overlapping window outputs into one chronological segment list."""
        flattened = []
        for window in window_results:
            window_start = float(window.get("window_start", 0.0))
            window_end = float(window.get("window_end", 0.0))
            for seg in window.get("segments", []):
                if not isinstance(seg, dict):
                    continue
                parsed = self._parse_topic_segment_record(seg, total_duration)
                if not parsed:
                    continue
                parsed["source_windows"] = [(window_start, window_end)]
                flattened.append(parsed)

        if not flattened:
            return [], []

        flattened.sort(key=lambda item: (item["start"], item["end"], -item["score"]))

        clusters = []
        for seg in flattened:
            if clusters and self._should_merge_topic_segment_records(
                clusters[-1][-1], seg
            ):
                clusters[-1].append(seg)
            else:
                clusters.append([seg])

        merged = [
            self._merge_topic_segment_cluster(cluster, clip_strategy)
            for cluster in clusters
        ]
        merged = [seg for seg in merged if seg["end"] > seg["start"]]
        merged.sort(key=lambda item: (item["start"], item["end"]))

        normalized_segments, normalized_summaries = self._normalize_topic_segments(
            merged,
            total_duration=total_duration,
            min_duration=self.topic_segment_min_duration,
        )
        return normalized_segments, normalized_summaries

    def _filter_strategy_segments(
        self,
        segments: list,
        summaries: list,
        clip_strategy: str,
    ) -> tuple[list, list]:
        """Apply strategy-owned candidate filtering before clipper consumes segments."""
        if not segments:
            return segments, summaries

        strategy = (clip_strategy or "opinion").lower()
        if strategy != "topic":
            return segments, summaries

        filtered_segments = self._merge_adjacent_topic_strategy_segments(segments)
        max_clips = self._get_strategy_max_clips()
        threshold = None
        if len(filtered_segments) > max_clips:
            ranked_scores = sorted(
                (float(item.get("score", 0.0)) for item in filtered_segments),
                reverse=True,
            )
            buffer_index = min(len(ranked_scores) - 1, max_clips)
            threshold = max(80.0, ranked_scores[buffer_index] - 1.0)

            filtered_segments = [
                item
                for item in filtered_segments
                if float(item.get("score", 0.0)) >= threshold
            ]

        if not filtered_segments:
            return segments, summaries

        if len(filtered_segments) != len(segments):
            if threshold is None:
                logging.info(
                    "Topic strategy semantic consolidation: %s → %s segments",
                    len(segments),
                    len(filtered_segments),
                )
            else:
                logging.info(
                    "Topic strategy pre-filter: %s → %s segments (threshold=%.1f)",
                    len(segments),
                    len(filtered_segments),
                    threshold,
                )

        filtered_summaries = [
            {
                "topic": str(segment.get("topic", "")).strip() or "Untitled Topic",
                "summary": str(segment.get("summary", "")).strip()
                or str(segment.get("topic", "")).strip()
                or "Untitled Topic",
                "score": float(segment.get("score", 70.0)),
                "reason": str(segment.get("reason", "")).strip()
                or str(segment.get("summary", "")).strip()
                or str(segment.get("topic", "")).strip()
                or "Untitled Topic",
            }
            for segment in filtered_segments
        ]

        return filtered_segments, filtered_summaries

    def _merge_adjacent_topic_strategy_segments(self, segments: list) -> list:
        """Merge adjacent topic mini-chapters when they are really one broader theme."""
        if len(segments) < 2:
            return segments

        _, final_max_duration = self._get_final_clip_duration_bounds()
        ordered = sorted(segments, key=lambda item: (item.get("start", 0.0), item.get("end", 0.0)))
        merged = [dict(ordered[0])]
        merge_gap_limit = 2.0
        min_merge_duration = max(75.0, self.topic_segment_min_duration * 2.5)
        complete_theme_duration = max(45.0, self.topic_segment_min_duration * 1.5)

        for current_raw in ordered[1:]:
            current = dict(current_raw)
            previous = merged[-1]
            gap = float(current.get("start", 0.0)) - float(previous.get("end", 0.0))
            combined_duration = float(current.get("end", 0.0)) - float(previous.get("start", 0.0))
            if gap > merge_gap_limit or combined_duration > final_max_duration:
                merged.append(current)
                continue

            continuity = max(
                self._topic_similarity(previous.get("topic", ""), current.get("topic", "")),
                self._topic_similarity(previous.get("summary", ""), current.get("summary", "")),
            )
            previous_duration = float(previous.get("end", 0.0)) - float(previous.get("start", 0.0))
            current_duration = float(current.get("end", 0.0)) - float(current.get("start", 0.0))
            both_are_complete_themes = (
                previous_duration >= complete_theme_duration
                and current_duration >= complete_theme_duration
            )
            weaker_score = min(
                float(previous.get("score", 0.0)),
                float(current.get("score", 0.0)),
            )
            adjacent_short_pair = (
                gap <= 1.0
                and combined_duration <= final_max_duration
                and continuity >= 0.72
                and (
                    previous_duration < min_merge_duration
                    or current_duration < min_merge_duration
                )
                and weaker_score < 86.0
            )

            should_merge = (
                continuity >= 0.68
                and (
                    not both_are_complete_themes
                    and (
                        previous_duration < min_merge_duration
                        or current_duration < min_merge_duration
                        or weaker_score < 84.0
                    )
                )
            ) or continuity >= 0.90 or adjacent_short_pair

            # Two already-complete themes should not be merged just because they
            # are adjacent and loosely related. Reserve merges for near-duplicates
            # or clearly subordinate short subthemes.
            if both_are_complete_themes and continuity < 0.90:
                should_merge = False

            if not should_merge:
                merged.append(current)
                continue

            merged_segment = self._merge_topic_segment_cluster([previous, current], "topic")
            logging.info(
                "  ↳ Topic semantic merge: %.1f-%.1fs + %.1f-%.1fs → %.1f-%.1fs (continuity=%.2f)",
                float(previous.get("start", 0.0)),
                float(previous.get("end", 0.0)),
                float(current.get("start", 0.0)),
                float(current.get("end", 0.0)),
                float(merged_segment.get("start", 0.0)),
                float(merged_segment.get("end", 0.0)),
                continuity,
            )
            merged[-1] = merged_segment

        return merged

    def _get_strategy_max_clips(self) -> int:
        config_obj = getattr(self, "config", None)
        raw_value = getattr(config_obj, "max_clips", None) if config_obj else None
        try:
            value = int(raw_value)
        except Exception:
            value = 4
        return max(1, value)

    def _parse_topic_segment_record(
        self, seg: dict, total_duration: float
    ) -> dict | None:
        """Parse and clamp a single segment record returned by the LLM."""
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except Exception:
            return None

        if end <= start:
            return None

        topic = str(seg.get("topic", "")).strip() or "Untitled Topic"
        summary = str(seg.get("summary", "")).strip() or topic
        reason = str(seg.get("reason", "")).strip() or summary
        score = self._parse_topic_score(seg.get("score"))

        if total_duration > 0:
            start = max(0.0, min(start, total_duration))
            end = max(start, min(end, total_duration))

        if end <= start:
            return None

        key_sentences = seg.get("key_sentences", [])
        if not isinstance(key_sentences, list):
            key_sentences = []

        return {
            "start": start,
            "end": end,
            "topic": topic,
            "summary": summary,
            "score": score,
            "reason": reason,
            "conclusion_clarity": seg.get("conclusion_clarity"),
            "self_contained": seg.get("self_contained"),
            "info_density": seg.get("info_density"),
            "viral_fit": seg.get("viral_fit"),
            "stance": str(seg.get("stance", "")).strip(),
            "key_sentences": key_sentences,
            "publishability": seg.get("publishability"),
        }

    @staticmethod
    def _text_tokens(value: str) -> set:
        import re

        return {
            token
            for token in re.findall(r"[A-Za-z0-9]+|[一-鿿]", str(value).lower())
            if token
        }

    def _topic_similarity(self, left: str, right: str) -> float:
        left_tokens = self._text_tokens(left)
        right_tokens = self._text_tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _should_merge_topic_segment_records(
        self, previous: dict, current: dict
    ) -> bool:
        overlap = min(previous["end"], current["end"]) - max(
            previous["start"], current["start"]
        )
        gap = current["start"] - previous["end"]
        topic_similarity = self._topic_similarity(
            previous.get("topic", ""), current.get("topic", "")
        )
        summary_similarity = self._topic_similarity(
            previous.get("summary", ""), current.get("summary", "")
        )

        if overlap > 0:
            if topic_similarity >= 0.2 or summary_similarity >= 0.2:
                return True
            if (
                overlap
                >= min(
                    previous["end"] - previous["start"],
                    current["end"] - current["start"],
                )
                * 0.5
            ):
                return True

        if (
            0
            <= gap
            <= max(
                3.0,
                float(getattr(self, "topic_segment_chunk_overlap_seconds", 180)) * 0.1,
            )
        ):
            return topic_similarity >= 0.65 or summary_similarity >= 0.65

        return False

    @staticmethod
    def _merge_text_fragments(values: list[str], max_parts: int = 3) -> str:
        import re

        parts = []
        seen = set()
        for value in values:
            if not value:
                continue
            for fragment in re.split(r"(?<=[。！？.!?])\s+", str(value).strip()):
                fragment = fragment.strip(" \t\r\n。；;，,")
                if not fragment or fragment in seen:
                    continue
                seen.add(fragment)
                parts.append(fragment)
                if len(parts) >= max_parts:
                    return " ".join(parts).strip()
        return " ".join(parts).strip()

    @staticmethod
    def _merge_unique_strings(
        existing: list[str], incoming: list[str], limit: int = 3
    ) -> list[str]:
        merged = []
        seen = set()
        for value in list(existing or []) + list(incoming or []):
            value = str(value).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
            if len(merged) >= limit:
                break
        return merged

    def _merge_topic_segment_opinion_fields(self, target: dict, source: dict) -> None:
        """Merge opinion-first fields while preserving the strongest evidence."""
        numeric_fields = [
            "conclusion_clarity",
            "self_contained",
            "info_density",
            "viral_fit",
            "publishability",
        ]
        for field in numeric_fields:
            left = target.get(field)
            right = source.get(field)
            values = [
                value for value in (left, right) if isinstance(value, (int, float))
            ]
            if values:
                target[field] = max(values)

        source_stance = str(source.get("stance", "")).strip()
        target_stance = str(target.get("stance", "")).strip()
        if source_stance and (
            not target_stance or len(source_stance) > len(target_stance)
        ):
            target["stance"] = source_stance

        target["key_sentences"] = self._merge_unique_strings(
            target.get("key_sentences", []),
            source.get("key_sentences", []),
            limit=3,
        )

    def _score_merged_topic_segment(
        self, segment: dict, cluster_size: int, clip_strategy: str
    ) -> float:
        """Re-score a merged segment with opinion-first weighting across all chunks."""
        score = self._coerce_topic_score(segment.get("score", 70.0))

        opinion_scores = []
        publishability = segment.get("publishability")
        if isinstance(publishability, (int, float)):
            opinion_scores.append(float(publishability))

        numeric_fields = [
            segment.get(field)
            for field in (
                "conclusion_clarity",
                "self_contained",
                "info_density",
                "viral_fit",
            )
        ]
        numeric_values = [
            float(value) for value in numeric_fields if isinstance(value, (int, float))
        ]
        if numeric_values:
            opinion_scores.append(sum(numeric_values) / len(numeric_values) * 10.0)

        if segment.get("stance"):
            opinion_scores.append(
                min(100.0, 50.0 + len(str(segment.get("stance")).split()) * 4.0)
            )

        if segment.get("key_sentences"):
            opinion_scores.append(
                min(100.0, 55.0 + len(segment.get("key_sentences", [])) * 8.0)
            )

        if opinion_scores:
            score = max(score, max(opinion_scores))

        support_bonus = max(
            0.0,
            min(
                8.0, (cluster_size - 1) * (2.0 if clip_strategy == "opinion" else 1.25)
            ),
        )
        score = min(100.0, score + support_bonus)
        return round(max(0.0, score), 2)

    def _merge_topic_segment_cluster(self, cluster: list, clip_strategy: str) -> dict:
        """Merge a cluster of overlapping topic segments into one record."""
        cluster = sorted(
            cluster, key=lambda item: (item["start"], item["end"], -item["score"])
        )
        best = max(
            cluster, key=lambda item: (item["score"], item["end"] - item["start"])
        )
        merged = {
            "start": min(item["start"] for item in cluster),
            "end": max(item["end"] for item in cluster),
            "topic": best.get("topic", "Untitled Topic"),
            "summary": best.get("summary", ""),
            "score": best.get("score", 70.0),
            "reason": best.get("reason", ""),
            "conclusion_clarity": best.get("conclusion_clarity"),
            "self_contained": best.get("self_contained"),
            "info_density": best.get("info_density"),
            "viral_fit": best.get("viral_fit"),
            "stance": best.get("stance", ""),
            "key_sentences": list(best.get("key_sentences", [])),
            "publishability": best.get("publishability"),
        }

        for item in cluster:
            if item is best:
                continue
            self._merge_topic_segment_opinion_fields(merged, item)

        merged["summary"] = self._merge_text_fragments(
            [item.get("summary", "") for item in cluster if item.get("summary")],
            max_parts=3,
        )
        if not merged["summary"]:
            merged["summary"] = merged["topic"]
        merged["reason"] = self._merge_text_fragments(
            [item.get("reason", "") for item in cluster if item.get("reason")],
            max_parts=3,
        )
        if not merged["reason"]:
            merged["reason"] = merged["summary"]

        merged["score"] = self._score_merged_topic_segment(
            merged, len(cluster), clip_strategy
        )
        return merged

    def _parse_topic_score(self, raw_score) -> float:
        """
        Parse a topic quality score from the LLM output.

        Supports 0-1, 0-10 and 0-100 style outputs by normalizing them into a
        0-100 range.
        """
        try:
            score = float(raw_score)
        except Exception:
            return 70.0

        if score <= 0:
            return 0.0
        if score <= 1.0:
            score *= 100.0
        elif score <= 10.0:
            score *= 10.0

        return max(0.0, min(score, 100.0))

    def _target_topic_duration(self) -> float:
        """Return the ideal duration for a topic-based clip."""
        return max(
            float(self.min_duration),
            min(
                float(self.max_duration),
                (float(self.min_duration) + float(self.max_duration)) / 2.0,
            ),
        )

    def _coerce_topic_score(self, raw_score) -> float:
        """Normalize LLM topic scores into a 0-100 range."""
        try:
            score = float(raw_score)
        except Exception:
            return 70.0

        if score <= 0:
            return 0.0
        if score <= 1.0:
            score *= 100.0
        elif score <= 10.0:
            score *= 10.0

        return max(0.0, min(score, 100.0))

    def _score_topic_candidate(
        self,
        base_score: float,
        start: float,
        end: float,
        summary: str,
        reason: str,
        asr_result: List[Dict],
    ) -> float:
        """
        Combine topic quality with duration fit and ASR coverage.

        The returned score is used for ranking, so higher means the clip is a
        stronger candidate for publication.
        """
        duration = max(end - start, 0.0)
        if duration <= 0:
            return 0.0

        target = self._target_topic_duration()
        duration_fit = 1.0 - min(1.0, abs(duration - target) / max(target, 1.0))
        duration_score = duration_fit * 100.0

        summary_bonus = 0.0
        if summary.strip():
            summary_bonus += min(12.0, 3.0 + len(summary.split()) * 0.7)
        if reason.strip():
            summary_bonus += min(10.0, 2.0 + len(reason.split()) * 0.5)

        speech_bonus = 0.0
        if asr_result:
            overlap_segments = self._extract_asr_subset(asr_result, start, end)
            if overlap_segments:
                covered_duration = sum(
                    max(0.0, float(seg["end"]) - float(seg["start"]))
                    for seg in overlap_segments
                )
                speech_ratio = min(1.0, covered_duration / duration)
                speech_bonus = speech_ratio * 100.0

        final_score = (
            base_score * 0.55
            + duration_score * 0.20
            + speech_bonus * 0.15
            + summary_bonus
        )

        return round(max(0.0, min(final_score, 100.0)), 2)

    def _extract_json_object(self, content: str) -> dict:
        """Extract the first JSON object from model output.

        Handles:
        1. Markdown code blocks: ```json ... ``` or ``` ... ```
        2. Raw JSON objects
        3. Nested/complex JSON with proper brace matching
        """
        import re

        # Step 1: strip markdown code fences if present
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()

        # Step 2: try direct parse first (content might already be clean JSON)
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # Step 3: find the outermost JSON object using brace matching
        start = content.find("{")
        if start == -1:
            return {}

        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(content[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = content[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        pass
                    break

        return {}

    def _normalize_topic_segments(
        self,
        segments_data: list,
        total_duration: float,
        min_duration: int,
    ) -> tuple:
        """
        Normalize LLM topic segments so downstream clipping gets clean boundaries.

        This removes invalid ranges, sorts segments, clamps the final window to the
        transcript duration, and merges segments that are shorter than the minimum
        duration when possible.
        """
        cleaned = []
        for seg in segments_data:
            try:
                start = float(seg["start"])
                end = float(seg["end"])
                topic = str(seg.get("topic", "")).strip() or "Untitled Topic"
                summary = str(seg.get("summary", "")).strip() or topic
                score = self._parse_topic_score(seg.get("score"))
                reason = str(seg.get("reason", "")).strip() or summary
            except Exception:
                continue

            if end <= start:
                continue

            start = max(0.0, min(start, total_duration or start))
            end = max(start, min(end, total_duration or end))
            if end <= start:
                continue

            # Extract opinion fields with safe defaults (None = LLM did not return)
            conclusion_clarity = seg.get("conclusion_clarity")
            self_contained_val = seg.get("self_contained")
            info_density = seg.get("info_density")
            viral_fit = seg.get("viral_fit")
            stance = str(seg.get("stance", "")).strip()
            key_sentences = seg.get("key_sentences", [])
            if not isinstance(key_sentences, list):
                key_sentences = []
            publishability = seg.get("publishability")

            cleaned.append(
                {
                    "start": start,
                    "end": end,
                    "topic": topic,
                    "summary": summary,
                    "score": score,
                    "reason": reason,
                    "conclusion_clarity": conclusion_clarity,
                    "self_contained": self_contained_val,
                    "info_density": info_density,
                    "viral_fit": viral_fit,
                    "stance": stance,
                    "key_sentences": key_sentences,
                    "publishability": publishability,
                }
            )

        if not cleaned:
            return [], []

        cleaned.sort(key=lambda x: (x["start"], x["end"]))

        normalized = []
        for seg in cleaned:
            if not normalized:
                normalized.append(seg)
                continue

            previous = normalized[-1]

            if seg["start"] < previous["end"]:
                seg["start"] = previous["end"]

            if seg["end"] <= seg["start"]:
                continue

            if seg["end"] - seg["start"] < float(min_duration) and normalized:
                previous["end"] = max(previous["end"], seg["end"])
                if seg["topic"] not in previous["topic"]:
                    previous["topic"] = f"{previous['topic']} / {seg['topic']}"
                if seg["summary"] not in previous["summary"]:
                    previous["summary"] = (
                        f"{previous['summary']} {seg['summary']}".strip()
                    )
                previous["score"] = max(
                    float(previous.get("score", 0.0)), float(seg.get("score", 0.0))
                )
                if seg.get("reason") and seg["reason"] not in previous.get(
                    "reason", ""
                ):
                    previous["reason"] = (
                        f"{previous.get('reason', '').strip()} {seg['reason']}".strip()
                    )
                self._merge_topic_segment_opinion_fields(previous, seg)
                continue

            normalized.append(seg)

        if len(normalized) >= 2:
            first = normalized[0]
            if first["end"] - first["start"] < float(min_duration):
                next_seg = normalized[1]
                next_seg["start"] = first["start"]
                if first["topic"] not in next_seg["topic"]:
                    next_seg["topic"] = f"{first['topic']} / {next_seg['topic']}"
                if first["summary"] not in next_seg["summary"]:
                    next_seg["summary"] = (
                        f"{first['summary']} {next_seg['summary']}".strip()
                    )
                next_seg["score"] = max(
                    float(next_seg.get("score", 0.0)), float(first.get("score", 0.0))
                )
                if first.get("reason") and first["reason"] not in next_seg.get(
                    "reason", ""
                ):
                    next_seg["reason"] = (
                        f"{first.get('reason', '').strip()} {next_seg['reason']}".strip()
                    )
                self._merge_topic_segment_opinion_fields(next_seg, first)
                normalized.pop(0)

        if len(normalized) >= 2:
            last = normalized[-1]
            if last["end"] - last["start"] < float(min_duration):
                previous = normalized[-2]
                previous["end"] = max(previous["end"], last["end"])
                if last["topic"] not in previous["topic"]:
                    previous["topic"] = f"{previous['topic']} / {last['topic']}"
                if last["summary"] not in previous["summary"]:
                    previous["summary"] = (
                        f"{previous['summary']} {last['summary']}".strip()
                    )
                previous["score"] = max(
                    float(previous.get("score", 0.0)), float(last.get("score", 0.0))
                )
                if last.get("reason") and last["reason"] not in previous.get(
                    "reason", ""
                ):
                    previous["reason"] = (
                        f"{previous.get('reason', '').strip()} {last['reason']}".strip()
                    )
                self._merge_topic_segment_opinion_fields(previous, last)
                normalized.pop()

        topic_segments = [
            {
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "topic": seg["topic"],
                "summary": seg["summary"],
                "score": float(seg.get("score", 70.0)),
                "reason": seg.get("reason", seg.get("summary", seg["topic"])),
                # opinion fields — None if LLM did not return them
                "conclusion_clarity": seg.get("conclusion_clarity"),
                "self_contained": seg.get("self_contained"),
                "info_density": seg.get("info_density"),
                "viral_fit": seg.get("viral_fit"),
                "stance": seg.get("stance", ""),
                "key_sentences": seg.get("key_sentences", []),
                "publishability": seg.get("publishability"),
            }
            for seg in normalized
        ]
        topic_summaries = [
            {
                "topic": seg["topic"],
                "summary": seg["summary"],
                "score": float(seg.get("score", 70.0)),
                "reason": seg.get("reason", seg.get("summary", seg["topic"])),
            }
            for seg in normalized
        ]

        return topic_segments, topic_summaries


if __name__ == "__main__":
    # Test with downloaded video
    analyzer = Analyzer()

    # Use the YouTube video we downloaded earlier
    video_path = "downloads/COSTA RICA IN 4K 60fps HDR (ULTRA HD).mp4"

    if os.path.exists(video_path):
        logging.info(f"\n{'=' * 60}")
        logging.info("Testing Analyzer with YouTube video")
        logging.info(f"{'=' * 60}\n")

        result = analyzer.analyze_video(video_path)

        if result:
            logging.info(f"\n{'=' * 60}")
            logging.info("Analysis Complete!")
            logging.info(f"{'=' * 60}")
            logging.info(f"\nASR Segments: {len(result['asr_result'])}")
            logging.info(f"Audio Climax Points: {len(result['audio_climax_points'])}")
            logging.info(f"Scene Changes: {len(result['scene_changes'])}")

            # Print sample results
            if result["asr_result"]:
                logging.info(f"\nSample ASR (first 3 segments):")
                for seg in result["asr_result"][:3]:
                    logging.info(
                        f"  [{seg['start']:.2f}s - {seg['end']:.2f}s]: {seg['text']}"
                    )

            if result["audio_climax_points"]:
                logging.info(f"\nAudio Climax Points:")
                for point in result["audio_climax_points"]:
                    logging.info(
                        f"  Time: {point['time']:.2f}s, Score: {point['score']:.2f}"
                    )

            if result["scene_changes"]:
                logging.info(f"\nScene Changes (first 10):")
                for time in result["scene_changes"][:10]:
                    logging.info(f"  {time:.2f}s")
        else:
            logging.error("Analysis failed!")
    else:
        logging.error(f"Video file not found: {video_path}")
        logging.error("Please run downloader.py first to download a test video.")
