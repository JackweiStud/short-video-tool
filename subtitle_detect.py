import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from enum import Enum
from typing import Tuple, List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Configure logging to console and file (similar to step7_full_video.py)
_PROJECT_ROOT = Path(__file__).resolve().parent # This should point to short-video-tool
_LOG_DIR = _PROJECT_ROOT / 'logs'
_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Define regions to scan (y_start_frac, y_end_frac)
SCAN_REGIONS = [(0.8, 1.0), (0.4, 0.6), (0.0, 0.2)] # Bottom, Middle, Top 20%

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(_LOG_DIR / 'subtitle_detect.log'), encoding='utf-8'),
    ],
    force=True,
)


class SubtitleStatus(str, Enum):
    NONE = "none"
    EN = "en"
    ZH = "zh"
    BILINGUAL = "bilingual"
    UNCERTAIN = "uncertain"


EN_STREAM_SCORE_THRESHOLD = 6
EN_FALLBACK_SCORE_THRESHOLD = 6
OCR_DEFAULT_CONFIDENCE = 0.95
OCR_FALLBACK_EN_CONFIDENCE = 0.72
UI_HEAVY_HIT_THRESHOLD = 3
UI_KEYWORDS = {
    "gallery",
    "toolbar",
    "sidebar",
    "button",
    "buttons",
    "screen",
    "browser",
    "console",
    "terminal",
    "command",
    "install",
    "installer",
    "download",
    "upload",
    "output",
    "settings",
    "editor",
    "panel",
    "preview",
    "widget",
    "parklistscreen",
    "enableedgetoedge",
    "edgegallery",
}


def _collect_ui_keyword_hits(texts: List[str]) -> Tuple[int, List[str], bool]:
    tokens: List[str] = []
    for text in texts:
        tokens.extend(re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()))

    matched = [token for token in tokens if token in UI_KEYWORDS]
    unique_matches = sorted(set(matched))
    total_hits = len(matched)
    penalty = min(6, total_hits * 2)
    ui_heavy = len(unique_matches) >= UI_HEAVY_HIT_THRESHOLD or total_hits >= 4
    return penalty, unique_matches[:6], ui_heavy


def _estimate_en_subtitle_confidence(
    score: int,
    content_frames: int,
    change_ratio: float,
    ui_penalty: int,
    used_fallback: bool,
) -> float:
    confidence = 0.68 if used_fallback else 0.82
    if score >= 8:
        confidence += 0.08
    elif score >= 7:
        confidence += 0.04
    if content_frames >= 4:
        confidence += 0.03
    if change_ratio >= 0.60:
        confidence += 0.02
    if ui_penalty > 0:
        confidence -= 0.08
    ceiling = 0.79 if used_fallback else OCR_DEFAULT_CONFIDENCE
    return max(0.0, min(ceiling, confidence))


def _normalize_status_value(status: Any) -> str:
    if isinstance(status, SubtitleStatus):
        return status.value
    return str(status)


def _dominant_subtitle_language_from_text(text: str) -> SubtitleStatus:
    chinese_chars = sum(1 for char in text if '\u4e00' <= char <= '\u9fff')
    english_chars = sum(1 for char in text if ('a' <= char <= 'z') or ('A' <= char <= 'Z'))
    if chinese_chars == 0 and english_chars == 0:
        return SubtitleStatus.UNCERTAIN
    if chinese_chars >= english_chars:
        return SubtitleStatus.ZH
    return SubtitleStatus.EN


def _text_contains_chinese(text: str) -> bool:
    return any('\u4e00' <= char <= '\u9fff' for char in text)


def _dominant_subtitle_language_from_frames(frame_texts: List[str]) -> SubtitleStatus:
    """
    Downgrade a non-confident bilingual OCR candidate to the dominant subtitle language
    based on per-frame subtitle-candidate-region language, not merged character totals.

    Product intent:
    - If the subtitle candidate region repeatedly contains Chinese subtitle text and some
      unrelated English words/UI noise, treat it as ZH.
    - Only downgrade to EN when the same candidate region is consistently English-dominant.
    """
    texts_with_content = [text.strip() for text in frame_texts if text.strip()]
    if not texts_with_content:
        return SubtitleStatus.UNCERTAIN

    zh_like_frames = 0
    en_frames = 0
    for text in texts_with_content:
        status = _detect_language_from_text(text)
        if status == SubtitleStatus.ZH:
            zh_like_frames += 1
        elif status == SubtitleStatus.BILINGUAL:
            if _text_contains_chinese(text):
                zh_like_frames += 1
            else:
                en_frames += 1
        elif status == SubtitleStatus.EN:
            en_frames += 1

    if zh_like_frames == 0 and en_frames == 0:
        return SubtitleStatus.UNCERTAIN
    if zh_like_frames >= en_frames:
        return SubtitleStatus.ZH
    return SubtitleStatus.EN


def _is_confident_bilingual_subtitle_stream(
    frame_texts: List[str],
    region_key: str,
    sample_count: int,
) -> bool:
    """
    Only treat OCR result as BILINGUAL when the same subtitle candidate region
    behaves like a continuous bilingual subtitle stream across multiple frames.

    Note:
    - Product semantics are "same subtitle candidate region", not "must be bottom".
    - The current implementation is intentionally conservative and only trusts
      lower-half regions for BILINGUAL, to avoid reintroducing UI / title /
      screen-content false positives from upper regions.
    """
    texts_with_content = [text.strip() for text in frame_texts if text.strip()]
    if len(texts_with_content) < 3:
        return False

    try:
        region_start = float(region_key.split("-")[0])
    except (ValueError, IndexError):
        return False

    # Conservative implementation: only trust lower-half subtitle candidate regions
    # for BILINGUAL, because upper regions are currently much more likely to be UI chrome.
    if region_start < 0.5:
        return False

    per_frame_statuses = [_detect_language_from_text(text) for text in texts_with_content]
    bilingual_frames = sum(1 for status in per_frame_statuses if status == SubtitleStatus.BILINGUAL)
    if bilingual_frames < max(2, len(texts_with_content) // 2):
        return False

    pairs = list(zip(texts_with_content, texts_with_content[1:]))
    if not pairs:
        return False
    import difflib
    change_pairs = sum(
        1 for a, b in pairs
        if difflib.SequenceMatcher(None, a, b).ratio() < 0.85
    )
    change_ratio = change_pairs / len(pairs)
    return change_ratio >= 0.40


def _build_ocr_sample_timestamps(duration: float, sample_count: int) -> List[float]:
    """
    Build OCR sampling timestamps.

    - Short videos (< 20s): uniform sampling to avoid overlap.
    - Longer videos with small sample counts (<= 5): symmetric two-ended strategy.
    - Longer videos with denser sample counts: uniform sampling across the full duration.
    """
    if duration <= 0 or sample_count <= 0:
        return []

    # Short video fallback: uniform sampling
    if duration < 20:
        return [
            round(duration * (i + 1) / (sample_count + 1), 2)
            for i in range(sample_count)
        ]

    # Dense sampling across the whole timeline is better for auto hard-sub detection,
    # where subtitles may appear only in the middle of the video.
    if sample_count > 5:
        return [
            round(duration * (i + 1) / (sample_count + 1), 2)
            for i in range(sample_count)
        ]

    half = sample_count // 2
    # Front: 5s, 10s, 15s, ...
    front = [5.0 * (i + 1) for i in range(half)]
    # Back: duration-5s, duration-10s, ...
    back = [duration - 5.0 * (i + 1) for i in range(half)]
    # Odd count: add midpoint
    mid = [duration / 2] if sample_count % 2 == 1 else []

    timestamps = sorted(set(front + back + mid))
    # Clamp to valid range
    timestamps = [t for t in timestamps if 0.1 <= t <= duration - 0.1]
    return timestamps


def _detect_language_from_text(text: str) -> SubtitleStatus:
    """
    Analyzes the given text to determine its primary language based on character distribution.
    - >= 70% Chinese characters (Unicode \u4e00-\u9fff) -> ZH
    - 30% <= Chinese characters < 70% -> BILINGUAL
    - < 30% Chinese characters (but some Chinese present) -> BILINGUAL (EN primary)
    - No significant Chinese characters -> EN
    """
    if not text:
        return SubtitleStatus.EN # Default to EN for empty text or minimal content

    chinese_chars = 0
    total_linguistic_chars = 0 # Only count Chinese and English alphabetical chars for ratio
    has_chinese = False

    for char in text:
        # Unicode range for common Chinese characters
        if '\u4e00' <= char <= '\u9fff':
            chinese_chars += 1
            total_linguistic_chars += 1
            has_chinese = True
        elif 'a' <= char <= 'z' or 'A' <= char <= 'Z': # English alphabets
            total_linguistic_chars += 1
        # Ignore spaces, punctuation, numbers, etc.

    if total_linguistic_chars == 0:
        if has_chinese: # If there were Chinese characters, but no linguistic chars, assume ZH
            return SubtitleStatus.ZH
        return SubtitleStatus.UNCERTAIN

    chinese_ratio = chinese_chars / total_linguistic_chars

    if chinese_ratio >= 0.6:  # High Chinese content -> mostly Chinese (matches original threshold)
        return SubtitleStatus.ZH
    elif 0.11 <= chinese_ratio < 0.6: # Mixed content -> bilingual (>~11% Chinese chars)
        return SubtitleStatus.BILINGUAL
    elif chinese_ratio < 0.11 and has_chinese: # Very low Chinese (<11%) -> treat as EN (UI noise)
        return SubtitleStatus.EN
    else: # No Chinese content (chinese_chars == 0) -> English
        return SubtitleStatus.EN


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0: # Explicit check
        logging.warning(f"ffprobe failed for {video_path}: {result.stderr}")
        return 0.0
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        logging.warning(f"Could not parse duration from ffprobe output: {result.stdout.strip()}")
        return 0.0


def _extract_frame(video_path: str, timestamp: float, output_path: str) -> bool:
    """Extract a single frame from video at given timestamp."""
    cmd = [
        "ffmpeg", "-y", "-ss", str(timestamp),
        "-i", video_path,
        "-frames:v", "1",
        "-update", "1",
        "-q:v", "2", # Good quality for OCR
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.warning(f"FFmpeg failed to extract frame: {result.stderr}")
    return result.returncode == 0 and os.path.exists(output_path)


def _detect_language_from_audio(video_path: str, duration: float, whisper_model: str = "tiny") -> str:
    """
    Extract a 30s audio clip from the middle of the video and detect language.
    Apple Silicon prefers mlx-whisper; other environments use faster-whisper.
    Returns the detected language code (e.g. 'en', 'zh') or '' on failure.
    """
    try:
        import platform

        mid = max(0.0, duration / 2 - 15)
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "sample.wav")
            cmd = [
                "ffmpeg", "-y", "-ss", str(mid), "-t", "30",
                "-i", video_path,
                "-ac", "1", "-ar", "16000", "-vn",
                audio_path
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(audio_path):
                logging.warning(f"Audio extraction failed: {r.stderr[-200:]}")
                return ""

            if platform.system() == "Darwin" and platform.machine() == "arm64":
                try:
                    import mlx_whisper

                    result = mlx_whisper.transcribe(
                        audio_path,
                        language=None,
                        word_timestamps=False,
                    )
                    lang = ""
                    if isinstance(result, dict):
                        lang = result.get("language", "") or ""
                    if lang:
                        logging.info(
                            f"mlx-whisper detected language: '{lang}' for {video_path}"
                        )
                        return lang
                    logging.warning(
                        "mlx-whisper did not return a language; falling back to faster-whisper for language detection"
                    )
                except ImportError:
                    logging.info(
                        "mlx-whisper not installed on Apple Silicon; falling back to faster-whisper for language detection"
                    )
                except Exception as e:
                    logging.warning(f"mlx-whisper language detection failed: {e}")

            from faster_whisper import WhisperModel

            model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                audio_path,
                language=None,
                beam_size=1,
                vad_filter=False,
                word_timestamps=False,
            )
            _ = list(segments)
            lang = getattr(info, "language", "") or ""
            logging.info(f"faster-whisper detected language: '{lang}' for {video_path}")
            return lang
    except Exception as e:
        logging.warning(f"_detect_language_from_audio failed: {e}")
        return ""


def _transcribe_frame_region_with_whisper(frame_path: str, x_start: int, y_start: int, x_end: int, y_end: int) -> str:
    """
    Kept for interface compatibility with _detect_hard_subtitle_regions.
    Real language detection is done via _ocr_region_with_vision at a higher level.
    """
    return ""


def _ocr_region_with_vision(image_path: str) -> str:
    """
    Use Apple Vision Framework to OCR an image file.
    Supports Chinese (Simplified/Traditional) and English.
    Returns the recognized text, or empty string on failure.
    Must be called from a script file (not -c) to avoid pyobjc SIGTERM.
    """
    try:
        import Vision  # pyobjc-framework-Vision
        from Foundation import NSURL

        url = NSURL.fileURLWithPath_(image_path)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)
        # Must specify languages; without this Vision defaults to Latin-script only
        request.setRecognitionLanguages_(['zh-Hans', 'zh-Hant', 'en-US'])

        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        success, error = handler.performRequests_error_([request], None)
        if error:
            logging.warning(f"Vision OCR error: {error}")
            return ""

        results = []
        for obs in (request.results() or []):
            # Filter low-confidence results to reduce UI text / background noise
            # Real subtitles typically have confidence >= 0.5
            if obs.confidence() < 0.5:
                continue
            candidates = obs.topCandidates_(1)
            if candidates and len(candidates) > 0:
                results.append(str(candidates[0].string()))
        return ' '.join(results)
    except Exception as e:
        logging.warning(f"_ocr_region_with_vision failed: {e}")
        return ""


def _ocr_region_with_vision_bbox(image_path: str) -> list:
    """
    Like _ocr_region_with_vision but also returns bounding boxes.
    Returns list of (text, y_min_norm, y_max_norm, x_left_norm, x_right_norm) where
    coords are normalized 0-1 from TOP-LEFT of image.
    (Vision uses bottom-origin coordinates, so y is flipped.)
    Callers that only need (text, y_top, y_bot) can still unpack the first 3 elements.
    """
    try:
        import Vision
        from Foundation import NSURL
        url = NSURL.fileURLWithPath_(image_path)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)
        request.setRecognitionLanguages_(['zh-Hans', 'zh-Hant', 'en-US'])
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        handler.performRequests_error_([request], None)
        results = []
        for obs in (request.results() or []):
            if obs.confidence() < 0.5:
                continue
            candidates = obs.topCandidates_(1)
            if not candidates or len(candidates) == 0:
                continue
            text = str(candidates[0].string())
            bbox = obs.boundingBox()  # CGRect: origin=(x,y_bottom_norm), size=(w,h)
            # Vision y=0 is bottom; flip to top-origin
            y_bottom_vision = bbox.origin.y
            h_norm = bbox.size.height
            x_left_norm = bbox.origin.x
            w_norm = bbox.size.width
            y_top_norm = 1.0 - (y_bottom_vision + h_norm)   # top in top-origin coords
            y_bot_norm = 1.0 - y_bottom_vision               # bottom in top-origin coords
            x_right_norm = x_left_norm + w_norm
            results.append((text, y_top_norm, y_bot_norm, x_left_norm, x_right_norm))
        return results
    except Exception as e:
        logging.warning(f"_ocr_region_with_vision_bbox failed: {e}")
        return []


def _is_subtitle_geometry(x_left: float, x_right: float, min_center_margin: float = 0.15) -> bool:
    """
    Return True if a text block is horizontally centered enough to be a subtitle.

    Subtitles are typically centered: x_left and x_right are both within
    `min_center_margin` of symmetric margins. Watermarks / channel logos tend
    to hug one edge (x_left < 0.1 or x_right > 0.9 with narrow width).

    Args:
        x_left: normalized left edge of bbox (0-1)
        x_right: normalized right edge of bbox (0-1)
        min_center_margin: minimum required margin on each side for centered text.
            Default 0.15 means the text must start after 15% from left and end
            before 85% from right — OR span wide enough (> 25% width) to be a
            real subtitle line rather than a corner logo.

    Returns:
        True  → geometry looks like a subtitle (pass)
        False → geometry looks like a corner watermark (filter out)
    """
    width = x_right - x_left
    # Wide text blocks (> 25% of frame width) are almost certainly subtitles
    if width > 0.25:
        return True
    # Narrow block: must be centered (not hugging either edge)
    left_margin = x_left
    right_margin = 1.0 - x_right
    centered = left_margin >= min_center_margin and right_margin >= min_center_margin
    if not centered:
        logging.debug(
            "[SubtitleFilter] 排除：几何不居中 x_left=%.3f x_right=%.3f width=%.3f "
            "left_margin=%.3f right_margin=%.3f (min_center_margin=%.2f)",
            x_left, x_right, width, left_margin, right_margin, min_center_margin,
        )
    return centered


def _is_fixed_watermark(
    frame_texts: List[str], # This is the raw list, possibly with empty strings
    similarity_threshold: float = 0.9,
    min_frames_for_similarity: int = 3,
    occupancy_threshold: float = 0.8
) -> bool:
    """
    Return True if the OCR texts across frames are suspiciously identical,
    indicating a fixed UI watermark rather than real subtitles.

    Real subtitles change between frames or appear transiently; watermarks stay the same and are pervasive.
    We use difflib.SequenceMatcher to handle minor OCR noise.

    Args:
        frame_texts: list of OCR text strings, one per sampled frame (can contain empty strings).
        similarity_threshold: if average pairwise similarity >= this value, treat as fixed watermark. Default 0.9.
        min_frames_for_similarity: Minimum number of non-empty frames needed to perform similarity check.
                                   If fewer, it's not considered a fixed watermark. Default 3.
        occupancy_threshold: Minimum ratio of non-empty frames to total sampled frames.
                             If (non-empty frames / total frames) < this value, it's not a fixed watermark. Default 0.8.

    Returns:
        True  → looks like a fixed watermark (filter out)
        False → looks like real subtitles (pass)
    """
    original_full_sample_count = len(frame_texts) # Renamed to be explicit
    texts_with_content = [t for t in frame_texts if t.strip()]

    # Check if enough content frames to perform similarity check
    if len(texts_with_content) < min_frames_for_similarity:
        logging.debug(
            f"[SubtitleFilter] 跨帧过滤跳过：有效文本帧数 {len(texts_with_content)} < {min_frames_for_similarity} (最小相似度检查帧数)"
        )
        return False

    # Compare consecutive pairs for similarity
    import difflib
    if len(texts_with_content) < 2:
        return False # Only one non-empty frame, cannot determine similarity reliably

    similarities = []
    for a, b in zip(texts_with_content, texts_with_content[1:]):
        ratio = difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()
        similarities.append(ratio)

    avg_similarity = sum(similarities) / len(similarities)
    is_watermark = avg_similarity >= similarity_threshold
    if is_watermark:
        logging.debug(
            "[SubtitleFilter] 排除固定水印：跨帧相似度 %.2f >= %.2f，有效文本帧=%r",
            avg_similarity, similarity_threshold, texts_with_content[:3],
        )
    return is_watermark


def _detect_language_from_ocr_regions(
    video_path: str,
    duration: float,
    sample_count: int = 5
) -> Tuple[SubtitleStatus, Optional[str], Optional[float], float]:
    """
    Sample frames from the video, crop subtitle zones, run Vision OCR with bbox,
    return (status, ocr_lang, subtitle_top_ratio, confidence).
    subtitle_top_ratio: normalized y position (0-1 from top) of detected subtitle top edge.
    None if not detected.
    """
    if duration <= 0:
        return SubtitleStatus.UNCERTAIN, None, None, 0.0

    import math
    import difflib

    sample_count = max(sample_count, 3)

    def _score_subtitle_stream(
        frame_texts: List[str], region_key: str, sample_count: int
    ) -> Tuple[int, bool, Dict[str, Any]]:
        """
        计算一个 region 是否是「连续变化字幕流」的可信度分数。
        满分 9，再叠加 UI/广告负向特征扣分。
        返回 (score, has_content_signal, meta)。
        """
        texts_with_content = [t for t in frame_texts if t.strip()]
        score = 0
        reasons = []
        has_content_signal = False
        change_ratio = 0.0
        avg_chars = 0.0
        wordy_ratio = 0.0

        if len(texts_with_content) >= 2:
            pairs = list(zip(texts_with_content, texts_with_content[1:]))
            change_pairs = sum(
                1 for a, b in pairs
                if difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio() < 0.85
            )
            change_ratio = change_pairs / len(pairs) if pairs else 0.0
            if change_ratio >= 0.40:
                score += 3
                has_content_signal = True
                reasons.append(f"S1+3(change_ratio={change_ratio:.2f})")
            else:
                reasons.append(f"S1+0(change_ratio={change_ratio:.2f})")
        else:
            reasons.append("S1+0(too_few_frames)")

        if texts_with_content:
            avg_chars = sum(len(t.strip()) for t in texts_with_content) / len(texts_with_content)
            if avg_chars >= 15:
                score += 2
                has_content_signal = True
                reasons.append(f"S2+2(avg_chars={avg_chars:.1f})")
            else:
                reasons.append(f"S2+0(avg_chars={avg_chars:.1f})")
        else:
            reasons.append("S2+0(no_content)")

        try:
            y_start = float(region_key.split("-")[0])
            if y_start >= 0.5:
                score += 2
                reasons.append("S3+2(bottom_region)")
            else:
                reasons.append("S3+0(not_bottom)")
        except (ValueError, IndexError):
            reasons.append("S3+0(parse_error)")

        if texts_with_content:
            wordy_frames = sum(1 for text in texts_with_content if len(text.split()) >= 3)
            wordy_ratio = wordy_frames / len(texts_with_content)
            if wordy_ratio >= 0.50:
                score += 1
                has_content_signal = True
                reasons.append(f"S4+1(wordy_ratio={wordy_ratio:.2f})")
            else:
                reasons.append(f"S4+0(wordy_ratio={wordy_ratio:.2f})")
        else:
            reasons.append("S4+0(no_content)")

        if len(texts_with_content) >= 3:
            score += 1
            reasons.append(f"S5+1(content_frames={len(texts_with_content)})")
        else:
            reasons.append(f"S5+0(content_frames={len(texts_with_content)})")

        ui_penalty, ui_keywords, ui_heavy = _collect_ui_keyword_hits(texts_with_content)
        if ui_penalty > 0:
            score = max(0, score - ui_penalty)
            reasons.append(
                f"S6-{ui_penalty}(ui_keywords={','.join(ui_keywords) if ui_keywords else 'none'})"
            )
        else:
            reasons.append("S6-0(no_ui_keywords)")
        if ui_heavy:
            reasons.append("S7(block_ui_heavy)")

        meta = {
            "change_ratio": change_ratio,
            "avg_chars": avg_chars,
            "wordy_ratio": wordy_ratio,
            "content_frames": len(texts_with_content),
            "ui_penalty": ui_penalty,
            "ui_keywords": ui_keywords,
            "ui_heavy": ui_heavy,
        }
        logging.info(
            "[ScoreFilter] region=%s sample_count=%d score=%d/9 content_signal=%s details=%s",
            region_key, sample_count, score, has_content_signal, " ".join(reasons)
        )
        return score, has_content_signal, meta

    sample_timestamps = _build_ocr_sample_timestamps(duration, sample_count)
    logging.info(
        "OCR sample timestamps: %s",
        ", ".join(f"{timestamp:.1f}s" for timestamp in sample_timestamps),
    )

    # Scan regions for OCR: focus on subtitle zones (top/bottom edges).
    # LIMITATION: only top-20% and bottom-20% are scanned; middle 60% is NOT covered.
    # subtitle_top_ratio is computed ONLY from bottom regions (y_frac_start >= 0.5).
    # Rationale: top region is used for language detection only — in screen-recording
    # videos, top text is typically UI chrome (browser tabs, app menus) that persists
    # across frames and passes the consistency filter, but does NOT represent subtitle
    # position. If a video has genuine top-positioned subtitles, subtitle_top_ratio
    # will return None (not detected), which is a known gap.
    ocr_regions = [
        (0.0, 0.20),   # top 20% — language detection only (may contain UI chrome)
        (0.70, 1.0),   # bottom 30% — subtitle zone (used for position calculation)
    ]

    # region_frame_texts: dict[region_key] -> list of texts per frame
    # This list will always have sample_count length, with empty strings for frames without text.
    region_frame_texts: dict[str, List[str]] = {}
    # Track subtitle top positions per region across frames
    region_subtitle_tops: dict[str, List[float]] = {}
    bottom_region_texts_for_fallback: List[str] = [] # Raw OCR text from bottom regions that pass geometry, for fallback if no persistent regions
    bottom_region_keys_for_fallback: List[str] = [] # Corresponding keys for bottom_region_texts_for_fallback

    # Initialize all region_frame_texts lists with empty strings
    for y_frac_start, y_frac_end in ocr_regions:
        region_key = f"{y_frac_start:.2f}-{y_frac_end:.2f}"
        region_frame_texts[region_key] = [''] * len(sample_timestamps) # Pre-fill with empty strings


    with tempfile.TemporaryDirectory() as tmpdir:
        for i, timestamp in enumerate(sample_timestamps):
            frame_path = os.path.join(tmpdir, f"frame_{i}.jpg")
            if not _extract_frame(video_path, timestamp, frame_path):
                continue

            try:
                from PIL import Image
                img = Image.open(frame_path).convert('RGB')
                w, h = img.size

                for y_frac_start, y_frac_end in ocr_regions:
                    region_key = f"{y_frac_start:.2f}-{y_frac_end:.2f}"
                    y1 = int(h * y_frac_start)
                    y2 = int(h * y_frac_end)
                    region_crop_path = os.path.join(tmpdir, f"region_{i}_{int(y_frac_start*100)}.jpg")
                    img.crop((0, y1, w, y2)).save(region_crop_path)
                    # Use bbox version to get precise positions
                    bbox_results = _ocr_region_with_vision_bbox(region_crop_path)
                    
                    combined_text_for_frame = "" # Default to empty for this frame and region
                    if bbox_results:
                        # Filter by geometry
                        filtered_results = []
                        for item in bbox_results:
                            t, y_top, y_bot, x_left, x_right = item
                            if _is_subtitle_geometry(x_left, x_right):
                                filtered_results.append(item)
                            else:
                                logging.debug(
                                    "[SubtitleFilter] 排除：几何过滤 t=%.1fs region=%s text=%r",
                                    timestamp, region_key, t[:40],
                                )
                        
                        if filtered_results:
                            combined_text_for_frame = ' '.join([item[0] for item in filtered_results])
                            logging.info(f"OCR t={timestamp:.1f}s region={region_key}: {repr(combined_text_for_frame[:80])}")
                            
                            # For bottom region raw texts (for potential fallback)
                            if y_frac_start >= 0.5:
                                bottom_region_texts_for_fallback.append(combined_text_for_frame)
                                if region_key not in bottom_region_keys_for_fallback:
                                    bottom_region_keys_for_fallback.append(region_key)

                            # Record bbox y positions in full-video coordinate space
                            for item in filtered_results:
                                _, y_top_in_region, _, _, _ = item
                                y_top_abs = y_frac_start + y_top_in_region * (y_frac_end - y_frac_start)
                                if region_key not in region_subtitle_tops:
                                    region_subtitle_tops[region_key] = []
                                region_subtitle_tops[region_key].append(y_top_abs)
                    
                    # Always store the text (or empty string) for the current frame 'i'
                    region_frame_texts[region_key][i] = combined_text_for_frame

            except Exception as e:
                logging.warning(f"Frame OCR failed (frame {i}): {e}")
                # For any failure, ensure all region_frame_texts are filled with empty string for this frame
                for rk in region_frame_texts.keys(): # Iterate over keys to update all regions
                    region_frame_texts[rk][i] = '' # Ensure we don't have gaps for this failed frame
                continue

    # Check if any OCR text was captured at all across all regions/frames
    if all(all(not text for text in frame_texts_list) for frame_texts_list in region_frame_texts.values()):
        logging.info("OCR returned no text from any region or all texts were filtered by geometry.")
        return SubtitleStatus.NONE, None, None, 0.0

    final_eligible_regions_data: List[str] = []
    final_eligible_region_keys: List[str] = []
    final_en_region_confidences: List[float] = []

    min_frames_for_persistence = max(2, math.ceil(sample_count * 0.4))

    for region_key, frame_texts_list in region_frame_texts.items():
        texts_with_content_in_region = [t for t in frame_texts_list if t.strip()]

        # Persistence filter
        if len(texts_with_content_in_region) < min_frames_for_persistence:
            logging.debug(
                f"[SubtitleFilter] Region {region_key}: 排除（持久性过滤），"
                f"有效文本帧数 {len(texts_with_content_in_region)} < {min_frames_for_persistence}"
            )
            continue

        # Fixed watermark filter — only for persistent regions, only if video >= 30s
        # frame_texts_list has length == sample_count (pre-filled with empty strings)
        # so occupancy_threshold check in _is_fixed_watermark uses the correct denominator
        if duration >= 30.0 and _is_fixed_watermark(frame_texts_list):
            logging.debug(
                f"[SubtitleFilter] Region {region_key}: 排除（固定水印过滤），跨帧相似度过高或内容覆盖率不足"
            )
            continue

        region_lang = _detect_language_from_text(" ".join(texts_with_content_in_region))
        if region_lang == SubtitleStatus.EN:
            stream_score, _, stream_meta = _score_subtitle_stream(frame_texts_list, region_key, sample_count)
            if stream_score < EN_STREAM_SCORE_THRESHOLD or stream_meta["ui_heavy"]:
                logging.info(
                    "[ScoreFilter] Region %s: 排除（score=%d < %d 或 ui_heavy=%s）",
                    region_key,
                    stream_score,
                    EN_STREAM_SCORE_THRESHOLD,
                    stream_meta["ui_heavy"],
                )
                continue
            final_en_region_confidences.append(
                _estimate_en_subtitle_confidence(
                    score=stream_score,
                    content_frames=stream_meta["content_frames"],
                    change_ratio=stream_meta["change_ratio"],
                    ui_penalty=stream_meta["ui_penalty"],
                    used_fallback=False,
                )
            )

        final_eligible_regions_data.extend(texts_with_content_in_region)
        final_eligible_region_keys.append(region_key)
        logging.info(
            f"Region {region_key}: 通过持久性和水印过滤，"
            f"{len(texts_with_content_in_region)}/{len(frame_texts_list)} 帧有文本"
        )

    # Fallback: if no region passed both filters, use geometry-filtered bottom texts
    if not final_eligible_regions_data:
        if bottom_region_texts_for_fallback:
            # Filter out fixed watermarks from fallback regions too
            non_watermark_fallback_texts: List[str] = []
            fallback_region_scores: dict[str, int] = {}
            fallback_region_has_signal: dict[str, bool] = {}
            fallback_region_meta: dict[str, Dict[str, Any]] = {}
            for region_key in bottom_region_keys_for_fallback:
                region_texts = region_frame_texts.get(region_key, [])
                if duration >= 30.0 and _is_fixed_watermark(region_texts):
                    logging.info(
                        f"[SubtitleFilter] Fallback region {region_key}: 排除（固定水印过滤）"
                    )
                    continue
                score, has_content_signal, meta = _score_subtitle_stream(
                    region_texts, region_key, sample_count
                )
                fallback_region_scores[region_key] = score
                fallback_region_has_signal[region_key] = has_content_signal
                fallback_region_meta[region_key] = meta
                non_watermark_fallback_texts.extend([t for t in region_texts if t.strip()])
            if not non_watermark_fallback_texts:
                logging.info("Fallback: 所有底部区域均为固定水印，跳过。")
                return SubtitleStatus.NONE, None, None, 0.0
            merged_fallback = " ".join(non_watermark_fallback_texts)
            lang_fallback = _detect_language_from_text(merged_fallback)
            if lang_fallback == SubtitleStatus.EN:
                eligible_fallback_keys = [
                    region_key for region_key in bottom_region_keys_for_fallback
                    if fallback_region_scores.get(region_key, 0) >= EN_FALLBACK_SCORE_THRESHOLD
                    and fallback_region_has_signal.get(region_key, False)
                    and fallback_region_meta.get(region_key, {}).get("content_frames", 0) >= 3
                    and fallback_region_meta.get(region_key, {}).get("change_ratio", 0.0) >= 0.50
                    and not fallback_region_meta.get(region_key, {}).get("ui_heavy", False)
                ]
                eligible_fallback_region = bool(eligible_fallback_keys)
                if not eligible_fallback_region:
                    logging.info(
                        "[ScoreFilter] Fallback: EN 候选区域未满足严格阈值（score/content_frames/change_ratio/ui_heavy），返回 NONE"
                    )
                    return SubtitleStatus.NONE, None, None, 0.0
            if lang_fallback == SubtitleStatus.BILINGUAL:
                bilingual_keys = [
                    region_key for region_key in bottom_region_keys_for_fallback
                    if _is_confident_bilingual_subtitle_stream(
                        region_frame_texts.get(region_key, []),
                        region_key,
                        sample_count,
                    )
                ]
                if not bilingual_keys:
                    downgraded = _dominant_subtitle_language_from_frames(non_watermark_fallback_texts)
                    if downgraded == SubtitleStatus.UNCERTAIN:
                        downgraded = _dominant_subtitle_language_from_text(merged_fallback)
                    logging.info(
                        "Fallback: bilingual OCR candidate is not a confident bilingual subtitle stream; downgrading to %s",
                        downgraded.value,
                    )
                    lang_fallback = downgraded

            if lang_fallback not in (SubtitleStatus.NONE, SubtitleStatus.UNCERTAIN):
                logging.info(
                    f"Fallback: 底部区域文本通过几何过滤，lang={lang_fallback.value}: "
                    f"{repr(merged_fallback[:120])}"
                )
                fallback_tops = []
                for region_key in bottom_region_keys_for_fallback:
                    try:
                        rstart = float(region_key.split("-")[0])
                    except (ValueError, IndexError):
                        rstart = 0.0
                    if rstart >= 0.5:
                        fallback_tops.extend(region_subtitle_tops.get(region_key, []))
                subtitle_top_ratio = min(fallback_tops) if fallback_tops else None
                if subtitle_top_ratio is not None:
                    logging.info(f"Detected subtitle top ratio: {subtitle_top_ratio:.4f} (fallback, {len(fallback_tops)} observations)")
                if lang_fallback == SubtitleStatus.EN:
                    first_key = eligible_fallback_keys[0]
                    meta = fallback_region_meta[first_key]
                    confidence = _estimate_en_subtitle_confidence(
                        score=fallback_region_scores[first_key],
                        content_frames=meta["content_frames"],
                        change_ratio=meta["change_ratio"],
                        ui_penalty=meta["ui_penalty"],
                        used_fallback=True,
                    )
                else:
                    confidence = 0.85
                ocr_lang = 'bilingual' if lang_fallback == SubtitleStatus.BILINGUAL else lang_fallback.value
                return lang_fallback, ocr_lang, subtitle_top_ratio, confidence
            else:
                logging.info("Fallback bottom texts did not indicate a clear language.")
                return SubtitleStatus.NONE, None, None, 0.0
        else:
            logging.info("No eligible subtitle regions found — likely no subtitles.")
            return SubtitleStatus.NONE, None, None, 0.0

    # Compute subtitle_top_ratio from final eligible bottom regions only
    subtitle_top_ratio = None
    all_tops = []
    for region_key in final_eligible_region_keys:
        try:
            region_start = float(region_key.split("-")[0])
        except (ValueError, IndexError):
            region_start = 0.0
        if region_start < 0.5:
            logging.info(f"Skipping top region {region_key} for subtitle_top_ratio (UI chrome, not subtitle)")
            continue
        tops = region_subtitle_tops.get(region_key, [])
        all_tops.extend(tops)
    if all_tops:
        subtitle_top_ratio = min(all_tops)
        logging.info(f"Detected subtitle top ratio: {subtitle_top_ratio:.4f} (from {len(all_tops)} observations)")

    merged = " ".join(final_eligible_regions_data)
    lang_status = _detect_language_from_text(merged)
    logging.info(f"OCR merged text lang={lang_status.value}: {repr(merged[:120])}")

    if lang_status == SubtitleStatus.ZH:
        return SubtitleStatus.ZH, 'zh', subtitle_top_ratio, OCR_DEFAULT_CONFIDENCE
    elif lang_status == SubtitleStatus.EN:
        confidence = (
            sum(final_en_region_confidences) / len(final_en_region_confidences)
            if final_en_region_confidences
            else 0.82
        )
        return SubtitleStatus.EN, 'en', subtitle_top_ratio, confidence
    elif lang_status == SubtitleStatus.BILINGUAL:
        confident_bilingual_regions = [
            region_key for region_key in final_eligible_region_keys
            if _is_confident_bilingual_subtitle_stream(
                region_frame_texts.get(region_key, []),
                region_key,
                sample_count,
            )
        ]
        if confident_bilingual_regions:
            return SubtitleStatus.BILINGUAL, 'bilingual', subtitle_top_ratio, OCR_DEFAULT_CONFIDENCE

        bottom_region_texts = []
        for region_key in final_eligible_region_keys:
            try:
                region_start = float(region_key.split("-")[0])
            except (ValueError, IndexError):
                region_start = 0.0
            if region_start >= 0.5:
                bottom_region_texts.extend(region_frame_texts.get(region_key, []))
        downgraded = _dominant_subtitle_language_from_frames(bottom_region_texts)
        if downgraded == SubtitleStatus.UNCERTAIN:
            downgraded = _dominant_subtitle_language_from_text(" ".join(bottom_region_texts) or merged)
        logging.info(
            "OCR merged text is bilingual but not a confident bilingual subtitle stream; downgrading to %s",
            downgraded.value,
        )
        return downgraded, downgraded.value, subtitle_top_ratio, OCR_DEFAULT_CONFIDENCE
    else:
        return SubtitleStatus.UNCERTAIN, None, None, 0.0


def _detect_hard_subtitle_regions(video_path: str, sample_count: int = 5) -> List[Tuple[float, float, float, SubtitleStatus]]:
    """
    Detects hard-burned subtitles by sampling frames and analyzing three regions:
    bottom 20%, middle 20%, top 20%. Uses Whisper transcript for language detection.
    Returns a list of (y_start_frac, y_end_frac, density, SubtitleStatus) for detected regions.
    """
    regions_data = []
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return []

    # Define regions to scan (y_start_frac, y_end_frac)


    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(sample_count):
            timestamp = duration * (i + 1) / (sample_count + 1)
            frame_path = os.path.join(tmpdir, f"frame_{i}.jpg")

            if not _extract_frame(video_path, timestamp, frame_path):
                continue

            try:
                from PIL import Image
                img = Image.open(frame_path).convert('RGB')
                width, height = img.size

                for y_frac_start, y_frac_end in SCAN_REGIONS:
                    y1 = int(height * y_frac_start)
                    y2 = int(height * y_frac_end)

                    region_img = img.crop((0, y1, width, y2))
                    pixels = list(region_img.getdata())
                    non_black_pixels = sum(1 for r, g, b in pixels if r > 30 or g > 30 or b > 30) # Simple non-black check
                    density = non_black_pixels / len(pixels) if len(pixels) > 0 else 0.0

                    if density >= 0.05: # Threshold for "has significant content"
                        transcribed_text = _transcribe_frame_region_with_whisper(
                            frame_path, 0, y1, width, y2
                        )
                        lang_status = _detect_language_from_text(transcribed_text)
                        
                        regions_data.append((y_frac_start, y_frac_end, density, lang_status))

            except Exception as e:
                logging.warning(f"Hard subtitle region analysis failed for frame {i}: {e}")
                continue
    return regions_data


def _get_soft_subtitle_tracks(video_path: str) -> List[dict]:
    """
    Use ffprobe to detect soft subtitle tracks in the video.
    Returns list of subtitle track info dicts.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        logging.error(f"Failed to probe soft subtitle tracks: {result.stderr}")
        return []

    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
        return subtitle_streams
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse ffprobe JSON output: {e}")
        return []


def detect_subtitle_status(video_path: str, sample_count: int = 5) -> Tuple[SubtitleStatus, float, Optional[str], Optional[float]]:
    """
    Detect subtitle status of a video file, including language, supporting soft and hard subtitles.

    Args:
        video_path: Path to the video file.
        sample_count: Number of frames to sample for hard subtitle detection. (Default: 5)

    Returns:
        Tuple[SubtitleStatus, float, Optional[str], Optional[float]]:
            (status, confidence, ocr_lang, subtitle_top_ratio)
        status: One of SubtitleStatus Enum values (NONE, EN, ZH, BILINGUAL, UNCERTAIN)
        confidence: Float between 0.0 and 1.0.
        ocr_lang: Detected language from OCR for hard subtitles ('en', 'zh', 'bilingual') or None.
        subtitle_top_ratio: Normalized y position (0-1 from top) of detected subtitle top edge, or None.
    """
    if not Path(video_path).exists():
        logging.error(f"Video file not found: {video_path}")
        return SubtitleStatus.UNCERTAIN, 0.0, None, None

    # --- Strategy 1: Soft subtitle detection via ffprobe (before duration check) ---
    soft_tracks = _get_soft_subtitle_tracks(video_path)
    if soft_tracks:
        soft_languages_detected = set()
        for track in soft_tracks:
            tags = track.get("tags", {})
            lang = tags.get("language", "").lower()
            title = tags.get("title", "").lower()

            if lang == 'chi' or 'chinese' in title or 'zh' in lang:
                soft_languages_detected.add('zh')
            elif lang == 'eng' or 'english' in title or 'en' in lang:
                soft_languages_detected.add('en')
            else:
                logging.debug(f"Unknown soft subtitle language: {lang} / {title}")

        if 'zh' in soft_languages_detected and 'en' in soft_languages_detected:
            logging.info("Soft subtitles: Bilingual (EN+ZH) detected via ffprobe.")
            return SubtitleStatus.BILINGUAL, 1.0, 'bilingual', None
        elif 'zh' in soft_languages_detected:
            logging.info("Soft subtitles: Chinese detected via ffprobe.")
            return SubtitleStatus.ZH, 1.0, 'zh', None
        elif 'en' in soft_languages_detected:
            logging.info("Soft subtitles: English detected via ffprobe.")
            return SubtitleStatus.EN, 1.0, 'en', None

    # --- Strategy 2: Hard subtitle detection via Apple Vision OCR ---
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return SubtitleStatus.UNCERTAIN, 0.0, None, None
    ocr_result = _detect_language_from_ocr_regions(
        video_path,
        duration,
        sample_count=sample_count,
    )
    if len(ocr_result) == 4:
        ocr_status, ocr_text_lang_detected, subtitle_top_ratio, ocr_confidence = ocr_result
    else:
        ocr_status, ocr_text_lang_detected, subtitle_top_ratio = ocr_result
        ocr_confidence = OCR_DEFAULT_CONFIDENCE if ocr_status not in (SubtitleStatus.NONE, SubtitleStatus.UNCERTAIN) else 0.0
    if ocr_status != SubtitleStatus.NONE and ocr_status != SubtitleStatus.UNCERTAIN:
        logging.info(
            "Hard subtitle detected via OCR: %s (OCR Lang: %s, top_ratio: %s, confidence: %.2f)",
            ocr_status.value,
            ocr_text_lang_detected,
            subtitle_top_ratio,
            ocr_confidence,
        )
        return ocr_status, ocr_confidence, ocr_text_lang_detected, subtitle_top_ratio

    # --- Strategy 3: Audio language detection via mlx-whisper / faster-whisper (last resort) ---
    audio_lang = _detect_language_from_audio(video_path, duration)
    if audio_lang == 'zh':
        logging.info("Audio language: Chinese detected via mlx-whisper / faster-whisper.")
        return SubtitleStatus.ZH, 0.8, 'zh', None
    elif audio_lang == 'en':
        logging.info("Audio language: English detected via mlx-whisper / faster-whisper. No hard subtitle detected (OCR found nothing).")
        return SubtitleStatus.NONE, 0.8, None, None

    logging.info("No subtitles or discernible language detected.")
    return SubtitleStatus.NONE, 0.0, None, None
