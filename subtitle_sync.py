#!/usr/bin/env python3
"""
subtitle_sync.py — D + B Subtitle Sync Strategy

Provides precise bilingual subtitle alignment for videos with hard-burned
English subtitles where the original subtitle track is unavailable.

Strategy:
  D (first): Try to extract soft subtitle track via ffmpeg → perfect alignment
  B (fallback): Frame-difference detection + Whisper word timestamps → ±0.2s accuracy

Usage:
    from subtitle_sync import SubtitleSync
    sync = SubtitleSync()
    segments = sync.get_aligned_segments(video_path, asr_segments)
    # segments = [{"start": 0.0, "end": 3.2, "text": "Agents working across the land"}, ...]
"""

import logging
import os
import subprocess
import re
import tempfile
from typing import List, Dict, Optional, Tuple

from PIL import Image

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class SubtitleSync:
    """
    D+B subtitle synchronization engine.

    Detects when on-screen subtitles change using frame-difference analysis,
    then aligns Whisper word-level ASR output to those visual change points.
    Result: Chinese translation appears at exactly the same moment as the
    corresponding English subtitle — human-imperceptibly synchronized.
    """

    def __init__(self,
                 sample_fps: float = 5.0,
                 diff_threshold: float = 0.015,
                 min_segment_duration: float = 0.4,
                 min_merge_duration: float = 2.5,
                 max_merge_duration: float = 6.0,
                 subtitle_area: Tuple[float, float, float, float] = (0.2, 0.75, 0.8, 1.0)):
        """
        Args:
            sample_fps:            Frame sampling rate for diff detection (default 5fps = ±0.2s)
            diff_threshold:        Pixel difference ratio to declare a subtitle change (0.0-1.0)
            min_segment_duration:  Minimum raw window duration (filter noise from frame diff)
            min_merge_duration:    Minimum merged segment duration in seconds (default 1.5s).
                                   Lyric-style videos display word-by-word; merging prevents
                                   single-word segments with no translation context.
            max_merge_duration:    Maximum merged segment duration in seconds (default 6.0s).
            subtitle_area:         (x0, y0, x1, y1) relative crop area for subtitle region
                                   Default: center 60% width, bottom 25% height
        """
        self.sample_fps = sample_fps
        self.diff_threshold = diff_threshold
        self.min_segment_duration = min_segment_duration
        self.min_merge_duration = min_merge_duration
        self.max_merge_duration = max_merge_duration
        self.subtitle_area = subtitle_area

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def get_aligned_segments(self,
                             video_path: str,
                             asr_segments: List[Dict]) -> List[Dict]:
        """
        Main entry point: given a video and ASR word-level segments,
        return subtitle segments whose timing matches the visual subtitle changes.

        Strategy D → B:
          1. Try to extract soft subtitle track (ffmpeg) → use directly if found
          2. Detect visual subtitle change points (frame diff)
          3. Align ASR words to change points → precise segments

        Args:
            video_path:   Path to source video
            asr_segments: Whisper output with word-level timestamps
                          [{\"start\": 0.0, \"end\": 2.5, \"text\": ...,
                            \"words\": [{\"word\": \"Hello\", \"start\": 0.0, \"end\": 0.4}, ...]}]

        Returns:
            list: [{\"start\": float, \"end\": float, \"text\": str}, ...]
        """
        # ── Strategy D: Try soft subtitle extraction first ──
        soft_segments = self._extract_soft_subtitle(video_path)
        if soft_segments:
            logging.info(f"[SubtitleSync] Strategy D: Soft subtitle extracted ({len(soft_segments)} segments)")
            return soft_segments

        # ── Strategy B: Frame-diff detection + ASR alignment ──
        logging.info("[SubtitleSync] Strategy B: No soft subtitle found, using frame-diff + ASR alignment")

        if not asr_segments:
            logging.warning("[SubtitleSync] No ASR segments provided, cannot align")
            return []

        # Get video duration
        duration = self._get_video_duration(video_path)
        if duration is None:
            logging.error("[SubtitleSync] Cannot get video duration")
            return asr_segments  # Return raw ASR as fallback

        # Detect visual subtitle change points
        change_points = self._detect_subtitle_changes(video_path, duration)
        logging.info(f"[SubtitleSync] Detected {len(change_points)} visual subtitle change points")

        if len(change_points) < 2:
            logging.warning("[SubtitleSync] Not enough change points detected, using ASR directly")
            return self._flatten_asr_segments(asr_segments)

        # Build raw time-windows from change points
        raw_windows = self._build_windows(change_points, duration)
        logging.info(f"[SubtitleSync] Built {len(raw_windows)} raw windows from change points")

        # Merge micro-windows into natural sentence-length segments
        merged_windows = self._merge_windows(raw_windows)
        logging.info(f"[SubtitleSync] Merged into {len(merged_windows)} phrase windows "
                     f"(min={self.min_merge_duration}s, max={self.max_merge_duration}s)")

        # Extract word-level timeline from ASR
        words = self._extract_words(asr_segments)

        # Align words to merged windows
        aligned = self._align_words_to_windows(words, merged_windows)
        logging.info(f"[SubtitleSync] Aligned into {len(aligned)} subtitle segments")

        return aligned

    # ──────────────────────────────────────────────────────────────────────
    # Strategy D: Soft Subtitle Extraction
    # ──────────────────────────────────────────────────────────────────────

    def _extract_soft_subtitle(self, video_path: str) -> List[Dict]:
        """Extract embedded subtitle track via ffmpeg (Strategy D)."""
        try:
            # Probe for subtitle streams
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "s",
                 "-show_entries", "stream=index,codec_name",
                 "-of", "json", video_path],
                capture_output=True, text=True, timeout=10
            )
            if probe.returncode != 0:
                return []

            import json
            data = json.loads(probe.stdout)
            if not data.get('streams'):
                return []

            # Extract first subtitle stream to temp SRT
            srt_path = f"/tmp/soft_sub_{os.path.basename(video_path)}.srt"
            extract = subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, "-map", "0:s:0", srt_path],
                capture_output=True, text=True, timeout=30
            )
            if extract.returncode != 0 or not os.path.exists(srt_path):
                return []

            segments = self._parse_srt(srt_path)
            try:
                os.remove(srt_path)
            except Exception:
                pass

            return segments

        except Exception as e:
            logging.debug(f"[SubtitleSync] Soft subtitle extraction failed: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Strategy B: Frame-Difference Detection
    # ──────────────────────────────────────────────────────────────────────

    def _detect_subtitle_changes(self, video_path: str, duration: float) -> List[float]:
        """
        Extract frames at sample_fps and detect when subtitle region changes significantly.

        Algorithm:
          1. Extract frames via ffmpeg at sample_fps into a temp dir
          2. For each frame, crop the subtitle_area
          3. Compute normalized pixel difference vs. previous frame
          4. When diff > threshold → record as change point
          5. Filter out change points that are too close together
             (scene cuts cause full-frame changes that also affect subtitle area,
              but they differ from single-line text changes by their magnitude)

        Returns:
            Sorted list of timestamps (seconds) where subtitle text changes
        """
        x0, y0, x1, y1 = self.subtitle_area
        interval = 1.0 / self.sample_fps

        with tempfile.TemporaryDirectory(prefix="subtitle_sync_") as tmpdir:
            # Extract frames via ffmpeg at desired fps
            frame_pattern = os.path.join(tmpdir, "frame_%06d.jpg")
            cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-vf", f"fps={self.sample_fps}",
                "-q:v", "4",
                frame_pattern
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logging.warning(f"[SubtitleSync] Frame extraction failed: {result.stderr[-200:]}")
                return []

            # List extracted frames in order
            frames = sorted(
                [os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.endswith('.jpg')]
            )

            if not frames:
                return []

            change_points = [0.0]  # Always include start
            prev_region = None

            for i, frame_path in enumerate(frames):
                t = i * interval
                try:
                    img = Image.open(frame_path).convert("RGB")
                    w, h = img.size

                    # Crop subtitle region
                    region = img.crop((
                        int(w * x0), int(h * y0),
                        int(w * x1), int(h * y1)
                    ))

                    if prev_region is not None:
                        diff = self._pixel_diff(prev_region, region)

                        if diff > self.diff_threshold:
                            # Filter: scene cuts have very high diff across whole frame
                            # vs. subtitle changes which are more localized
                            # We cap at 0.7 to avoid triggering on hard cuts as
                            # "subtitle changes" (hard cuts reset the subtitle anyway)
                            if diff < 0.7 and t - change_points[-1] >= self.min_segment_duration:
                                change_points.append(round(t, 3))
                                logging.debug(f"  Change at {t:.2f}s (diff={diff:.3f})")

                    prev_region = region

                except Exception as e:
                    logging.debug(f"  Frame {i} skipped: {e}")
                    continue

        # Always include end
        change_points.append(round(duration, 3))
        return sorted(set(change_points))

    def _pixel_diff(self, img_a: Image.Image, img_b: Image.Image) -> float:
        """
        Compute normalized mean absolute difference between two PIL images.
        Returns 0.0 (identical) to 1.0 (completely different).
        """
        import numpy as np

        if img_a.size != img_b.size:
            img_b = img_b.resize(img_a.size)

        arr_a = np.array(img_a, dtype=np.float32) / 255.0
        arr_b = np.array(img_b, dtype=np.float32) / 255.0
        return float(np.mean(np.abs(arr_a - arr_b)))

    # ──────────────────────────────────────────────────────────────────────
    # Alignment: ASR Words → Visual Windows
    # ──────────────────────────────────────────────────────────────────────

    def _build_windows(self,
                       change_points: List[float],
                       duration: float) -> List[Tuple[float, float]]:
        """Convert raw change points into (start, end) time windows, filtering noise."""
        windows = []
        pts = sorted(change_points)
        for i in range(len(pts) - 1):
            s, e = pts[i], pts[i + 1]
            if e - s >= self.min_segment_duration:
                windows.append((s, e))
        return windows

    def _merge_windows(self,
                       windows: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """
        Greedily merge consecutive short windows into natural phrase-length segments.

        Handles lyric-style (word-by-word) subtitle videos:
          [0.0-0.4] "Demand"  [0.4-0.8] "so"  [0.8-3.0] "we solve the problem"
          -> merged to -> [0.0-3.0] "Demand so we solve the problem"

        Merge rules:
          - Keep merging forward until current window >= min_merge_duration
          - Stop if adding next window would exceed max_merge_duration
          - A gap > 2.5s signals a true lyric line break -> start a new group
        """
        if not windows:
            return []

        merged = []
        group_start, group_end = windows[0]

        for i in range(1, len(windows)):
            next_start, next_end = windows[i]
            gap = next_start - group_end
            merged_dur = group_end - group_start
            would_be_dur = next_end - group_start

            # True lyric line break: subtitle cleared for > 2.5s
            if gap > 2.5:
                merged.append((group_start, group_end))
                group_start, group_end = next_start, next_end
                continue

            # Merge if current group still too short AND fits within max
            if merged_dur < self.min_merge_duration and would_be_dur <= self.max_merge_duration:
                group_end = next_end
            else:
                merged.append((group_start, group_end))
                group_start, group_end = next_start, next_end

        merged.append((group_start, group_end))
        return merged

    def _extract_words(self, asr_segments: List[Dict]) -> List[Dict]:
        """
        Flatten all word-level entries from Whisper JSON segments.

        Falls back to segment-level if no word timestamps available.
        """
        words = []
        for seg in asr_segments:
            if 'words' in seg and seg['words']:
                for w in seg['words']:
                    if w.get('word', '').strip():
                        words.append({
                            'word':  w['word'].strip(),
                            'start': w['start'],
                            'end':   w['end'],
                        })
            else:
                # Segment-level fallback: treat whole segment as one "word"
                words.append({
                    'word':  seg['text'].strip(),
                    'start': seg['start'],
                    'end':   seg['end'],
                })
        return sorted(words, key=lambda x: x['start'])

    def _align_words_to_windows(self,
                                 words: List[Dict],
                                 windows: List[Tuple[float, float]]) -> List[Dict]:
        """
        Assign ASR words to visual subtitle windows.

        For each window (start, end), collect all words whose midpoint falls
        inside the window. This gives each subtitle segment exactly the words
        that were being spoken while that subtitle was on screen.
        """
        segments = []
        for win_start, win_end in windows:
            # Collect words whose midpoint is within this window
            window_words = [
                w for w in words
                if win_start <= (w['start'] + w['end']) / 2 < win_end
            ]
            if not window_words:
                continue

            text = ' '.join(w['word'] for w in window_words)
            # Clean up whitespace around punctuation
            text = re.sub(r'\s+([,.!?])', r'\1', text).strip()

            segments.append({
                'start': win_start,
                'end':   win_end,
                'text':  text,
            })

        return segments

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _flatten_asr_segments(self, asr_segments: List[Dict]) -> List[Dict]:
        """Return ASR segments without word-level data (simple fallback)."""
        return [
            {'start': s['start'], 'end': s['end'], 'text': s['text']}
            for s in asr_segments
        ]

    def _get_video_duration(self, video_path: str) -> Optional[float]:
        """Get video duration in seconds via ffprobe."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 video_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception:
            pass
        return None

    def _parse_srt(self, srt_path: str) -> List[Dict]:
        """Parse SRT file into list of {start, end, text} dicts."""
        segments = []
        try:
            with open(srt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            blocks = content.strip().split('\n\n')
            for block in blocks:
                lines = block.strip().split('\n')
                if len(lines) < 3:
                    continue
                m = re.match(
                    r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*'
                    r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})',
                    lines[1]
                )
                if not m:
                    continue
                g = m.groups()
                start = int(g[0])*3600 + int(g[1])*60 + int(g[2]) + int(g[3])/1000
                end   = int(g[4])*3600 + int(g[5])*60 + int(g[6]) + int(g[7])/1000
                text  = ' '.join(lines[2:]).strip()
                segments.append({'start': start, 'end': end, 'text': text})
        except Exception as e:
            logging.error(f"[SubtitleSync] SRT parse error: {e}")
        return segments


# ──────────────────────────────────────────────────────────────────────────
# Convenience function used by embed_subtitles.py
# ──────────────────────────────────────────────────────────────────────────

def sync_subtitles_for_video(video_path: str,
                              asr_segments: List[Dict],
                              sample_fps: float = 5.0) -> List[Dict]:
    """
    Top-level convenience wrapper for the D+B strategy.

    Args:
        video_path:   Path to source video (must have hard-burned English subtitle)
        asr_segments: Whisper output with word-level timestamps
        sample_fps:   Sampling rate for frame-diff detection (default 5fps)

    Returns:
        List of aligned English segments ready for translation & overlay.
    """
    syncer = SubtitleSync(sample_fps=sample_fps)
    return syncer.get_aligned_segments(video_path, asr_segments)
