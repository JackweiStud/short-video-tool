import os
import json
import logging
import subprocess
import re
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import Config, get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_TRANSLATION_BACKEND = "auto"

SILICONFLOW_BASE_URL  = "https://api.siliconflow.cn/v1"
SILICONFLOW_DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"

# Punctuation boundaries (prefer to break after these)
_EN_BREAK_PUNCT = set('.!?,;:')
_ZH_BREAK_PUNCT = set('。！？，；：、…')
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_LATIN_RE = re.compile(r'[A-Za-z]')


def _wrap_cjk_text(text: str, max_chars: int) -> str:
    """
    Wrap CJK-heavy text into display-friendly lines without changing timing.

    This is used for aligned subtitle tracks so they remain on the same
    timestamps as the source English subtitle while staying readable in
    narrow layouts such as 9:16 videos.
    """
    text = (text or "").strip()
    if not text or max_chars <= 0:
        return text

    if len(text) <= max_chars:
        return text

    lines: List[str] = []
    current: List[str] = []

    def flush() -> None:
        if current:
            lines.append("".join(current).strip())
            current.clear()

    for ch in text:
        current.append(ch)
        current_len = len(current)
        should_break = current_len >= max_chars

        if not should_break and ch in _ZH_BREAK_PUNCT and current_len >= max(8, int(max_chars * 0.65)):
            should_break = True

        if should_break:
            flush()

    flush()

    return "\n".join(line for line in lines if line)


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _latin_count(text: str) -> int:
    return len(_LATIN_RE.findall(text or ""))


def _looks_like_english_sentence(text: str) -> bool:
    """
    Detect obvious English sentence fragments while tolerating acronyms/names.
    """
    text = (text or "").strip()
    if not text or _contains_cjk(text):
        return False

    latin_count = _latin_count(text)
    if latin_count < 4:
        return False

    # Acronyms or model names like "NBA" / "NVIDIA" are acceptable inside ZH output.
    if " " not in text and latin_count <= 8 and text.upper() == text:
        return False

    return True


def _translation_has_language_drift(texts: List[str], target_lang: str) -> bool:
    """
    Detect chunk-level language drift that still preserves numbering/count.
    """
    normalized = [(text or "").strip() for text in texts if (text or "").strip()]
    if not normalized:
        return False

    if target_lang == "en":
        wrong = sum(1 for text in normalized if _contains_cjk(text))
        return wrong / len(normalized) > 0.15

    if target_lang == "zh":
        wrong = sum(1 for text in normalized if _looks_like_english_sentence(text))
        return wrong / len(normalized) > 0.15

    return False


_TRANSLATION_META_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"此处应为空行",
        r"原文第\d+行",
        r"占位",
        r"空行",
        r"do not translate",
        r"leave this line blank",
        r"placeholder",
        r"translator note",
        r"note:",
    )
]


def _translation_has_meta_output(texts: List[str]) -> bool:
    """Detect translator notes / placeholder text that should never appear in subtitles."""
    for text in texts:
        normalized = (text or "").strip()
        if not normalized:
            continue
        for pattern in _TRANSLATION_META_PATTERNS:
            if pattern.search(normalized):
                return True
    return False


def _get_video_dimensions(video_path: str) -> Optional[Dict]:
    """
    使用 ffprobe 获取视频的宽度和高度。
    """
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height',
            '-of', 'json',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if 'streams' in data and len(data['streams']) > 0:
            stream = data['streams'][0]
            return {'width': stream['width'], 'height': stream['height']}
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        logging.warning(f"无法获取视频尺寸 {video_path}: {e}")
    return None

def _snap_chunk_intervals_to_word_boundaries(
    chunks: List[str],
    raw_ends: List[float],
    seg_start: float,
    seg_end: float,
    words: List[Dict],
    min_dur: float = 0.05,
    chunk_end_word_indices: Optional[List[int]] = None,
) -> List[tuple]:
    """
    Replace character-proportional chunk timestamps with timestamps snapped to
    actual ASR word boundaries (word['end']).

    Without this, split points often land inside a silence gap (e.g. the
    speaker pauses for 2s after "Thanks for coming."): the first SRT line
    lingers into the gap and the next line appears before the next word is
    actually spoken, causing audible audio/subtitle drift.

    Two modes:
      1. **Exact mapping** (preferred). When `chunk_end_word_indices` is
         provided, chunk i ends at words[idx].end directly. This is the
         only correct way for EN where tokens map 1:1 to ASR words —
         distance-based snap can otherwise pick the *next* word when char
         proportion overshoots into a silent gap.
      2. **Distance snap** (fallback). For ZH or when index mapping is
         unavailable: for each non-last chunk, pick the nearest unused
         word.end to raw_ends[i] (subject to monotonicity).

    The last chunk always ends exactly at seg_end (no float drift).

    Returns: list of (start, end) tuples, len == len(chunks).
    """
    n = len(chunks)
    if n == 0:
        return []
    if n == 1:
        return [(seg_start, seg_end)]

    starts_fallback = [seg_start] + raw_ends[:-1]
    fallback = list(zip(starts_fallback, raw_ends))

    if not words:
        return fallback

    # --- Mode 1: exact mapping by word index ---
    if chunk_end_word_indices is not None and len(chunk_end_word_indices) == n:
        intervals = []
        prev_end = seg_start
        for i in range(n):
            if i == n - 1:
                intervals.append((prev_end, seg_end))
                break
            idx = chunk_end_word_indices[i]
            if idx is None or idx < 0 or idx >= len(words):
                # bad hint → fall back to raw_end for just this boundary
                snapped = max(raw_ends[i], prev_end + min_dur)
            else:
                w_end_raw = words[idx].get('end')
                if w_end_raw is None:
                    snapped = max(raw_ends[i], prev_end + min_dur)
                else:
                    snapped = float(w_end_raw)
                    if snapped <= prev_end:
                        snapped = prev_end + min_dur
                    if snapped >= seg_end:
                        snapped = seg_end - min_dur
            intervals.append((prev_end, snapped))
            prev_end = snapped
        return intervals

    # --- Mode 2: distance snap fallback ---
    candidates = sorted({
        float(w['end']) for w in words
        if isinstance(w, dict)
        and 'end' in w
        and seg_start < float(w['end']) < seg_end
    })
    if not candidates:
        return fallback

    intervals = []
    prev_end = seg_start
    remaining_cands = list(candidates)

    for i in range(n - 1):
        raw_end = raw_ends[i]
        viable = [
            c for c in remaining_cands
            if c >= prev_end + min_dur and c <= seg_end - min_dur
        ]
        if not viable:
            snapped = max(raw_end, prev_end + min_dur)
        else:
            snapped = min(viable, key=lambda c: abs(c - raw_end))
            remaining_cands = [c for c in remaining_cands if c != snapped]
        intervals.append((prev_end, snapped))
        prev_end = snapped

    intervals.append((prev_end, seg_end))
    return intervals


def _split_long_segments(
    segments: List[Dict],
    max_en_chars: int,
    max_zh_chars: int,
    lang: str = 'en',
    is_vertical_video: bool = False,
) -> List[Dict]:
    """
    Split ASR/translation segments whose text exceeds max_chars.

    Each oversized segment is broken into shorter chunks at punctuation
    boundaries first, then at word/character boundaries.

    Timestamp assignment:
      - If the source segment carries ASR word-level timestamps (`words`),
        each split point is snapped to the nearest word boundary so subtitle
        cuts land on actual speech edges (not in silence gaps).
      - Otherwise falls back to character-length proportional distribution.

    Args:
        segments:  List of dicts with 'start', 'end', 'text', optionally 'words'.
        max_en_chars: Maximum character count per output segment for English.
        max_zh_chars: Maximum character count per output segment for Chinese.
        lang:      'en' (space-separated words) or 'zh' (character-level).
        is_vertical_video: True if the video is vertical (height > width).

    Returns:
        New list of segments; short segments pass through unchanged.
    """
    is_zh = lang == 'zh'
    break_punct = _ZH_BREAK_PUNCT if is_zh else _EN_BREAK_PUNCT
    result: List[Dict] = []
    
    max_chars = max_zh_chars if is_zh else max_en_chars

    for seg in segments:
        text = seg.get('text', '').strip()
        start = seg['start']
        end = seg['end']
        duration = end - start

        # Sanitize zero-duration segments regardless of length
        if duration <= 0:
            fixed = dict(seg)
            fixed['end'] = round(start + 0.05, 3)
            result.append(fixed)
            continue

        # Fast path: already short enough
        if len(text) <= max_chars:
            result.append(seg)
            continue

        # Build a flat list of tokens (words for EN, characters for ZH).
        # For EN we also track each token's original index so we can map the
        # resulting chunks back to ASR words by index (1:1 with Whisper words).
        if is_zh:
            tokens = list(text)
        else:
            tokens = text.split(' ')

        # Greedily pack tokens into chunks, preferring to break after punct.
        # Alongside the text chunks, track the index (within `tokens`) of the
        # LAST token of each chunk — used downstream for exact word-boundary
        # mapping on the EN path.
        chunks: List[str] = []
        chunk_last_token_idx: List[int] = []
        curr_tokens: List[str] = []
        curr_token_indices: List[int] = []
        curr_len = 0

        for tok_idx, tok in enumerate(tokens):
            sep = '' if is_zh else ' '
            tok_len = len(tok) + (0 if is_zh else (1 if curr_tokens else 0))

            if curr_len + tok_len > max_chars and curr_tokens:
                # Prefer to flush at a punctuation boundary that is already
                # within the current chunk before appending this token
                flushed = False
                for split_idx in range(len(curr_tokens) - 1, -1, -1):
                    if curr_tokens[split_idx].rstrip()[-1:] in break_punct:
                        left = sep.join(curr_tokens[: split_idx + 1]).strip()
                        chunks.append(left)
                        chunk_last_token_idx.append(curr_token_indices[split_idx])
                        remainder = curr_tokens[split_idx + 1 :]
                        remainder_idx = curr_token_indices[split_idx + 1 :]
                        curr_tokens = remainder + [tok]
                        curr_token_indices = remainder_idx + [tok_idx]
                        curr_len = sum(len(t) + (0 if is_zh else 1) for t in curr_tokens)
                        flushed = True
                        break
                if not flushed:
                    chunks.append(sep.join(curr_tokens).strip())
                    chunk_last_token_idx.append(curr_token_indices[-1])
                    curr_tokens = [tok]
                    curr_token_indices = [tok_idx]
                    curr_len = len(tok)
            else:
                curr_tokens.append(tok)
                curr_token_indices.append(tok_idx)
                curr_len += tok_len

        if curr_tokens:
            chunks.append(('' if is_zh else ' ').join(curr_tokens).strip())
            chunk_last_token_idx.append(curr_token_indices[-1])

        # Remove empty chunks (and the matching index entries)
        kept = [(c, idx) for c, idx in zip(chunks, chunk_last_token_idx) if c]
        if not kept:
            result.append(seg)
            continue
        chunks = [c for c, _ in kept]
        chunk_last_token_idx = [idx for _, idx in kept]

        # Compute provisional chunk end times by character-length proportion.
        # These are then snapped to ASR word boundaries below (if available).
        MIN_DUR = 0.05
        total_chars = sum(len(c) for c in chunks)
        n_chunks = len(chunks)
        raw_durs = [
            duration * (len(c) / total_chars if total_chars > 0 else 1 / n_chunks)
            for c in chunks
        ]
        clamped = [max(d, MIN_DUR) for d in raw_durs]
        total_clamped = sum(clamped)
        if total_clamped > duration and duration > 0:
            scale = duration / total_clamped
            clamped = [d * scale for d in clamped]
        raw_ends: List[float] = []
        t = start
        for i, dur in enumerate(clamped):
            t = t + dur
            if i == n_chunks - 1:
                raw_ends.append(end)
            else:
                raw_ends.append(t)

        # For EN, if ASR words count matches token count we can do an exact
        # token-index → word-index mapping (no nearest-distance ambiguity).
        seg_words = seg.get('words') or []
        chunk_end_word_indices: Optional[List[int]] = None
        if (not is_zh) and seg_words and len(seg_words) == len(tokens):
            chunk_end_word_indices = list(chunk_last_token_idx)

        intervals = _snap_chunk_intervals_to_word_boundaries(
            chunks=chunks,
            raw_ends=raw_ends,
            seg_start=start,
            seg_end=end,
            words=seg_words,
            min_dur=MIN_DUR,
            chunk_end_word_indices=chunk_end_word_indices,
        )

        for chunk, (cs, ce) in zip(chunks, intervals):
            if ce <= cs:
                ce = cs + MIN_DUR
            new_seg = dict(seg)  # preserve any extra keys
            new_seg['text'] = chunk
            new_seg['start'] = round(cs, 3)
            new_seg['end'] = round(ce, 3)
            result.append(new_seg)

    return result


class Translator:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_OPENAI_MODEL,
        backend: str = DEFAULT_TRANSLATION_BACKEND,
        base_url: Optional[str] = None,
        config: Optional[Config] = None,
    ):
        """
        Initialize Translator.

        Args:
            api_key:  API key (OpenAI / Siliconflow / compatible)
            model:    Model name
            backend:  'siliconflow' | 'openai' | 'googletrans' | 'auto'
            base_url: API base URL (for OpenAI-compatible endpoints)
        """
        self.config = config or get_config()

        self.api_key  = api_key  if api_key  is not None else self.config.openai_api_key
        self.base_url = base_url if base_url is not None else self.config.llm_base_url
        self.model = (
            self.config.openai_model
            if model == DEFAULT_OPENAI_MODEL
            else model
        )
        self.backend = (
            self.config.translation_backend
            if backend == DEFAULT_TRANSLATION_BACKEND
            else backend
        )
        self.default_output_dir = self.config.subtitles_dir

        # ── Auto-detect backend priority:
        #    siliconflow (llm_api_key) > openai (openai_api_key) > googletrans
        if self.backend == "auto":
            if self.config.llm_api_key:
                self.backend  = "siliconflow"
                self.api_key  = self.config.llm_api_key
                self.model    = self.config.llm_model or SILICONFLOW_DEFAULT_MODEL
                self.base_url = self.config.llm_base_url or SILICONFLOW_BASE_URL
                logging.info(f"Using Siliconflow backend: {self.model}")
            elif self.api_key:
                self.backend = "openai"
                logging.info("Using OpenAI backend for translation")
            else:
                self.backend = "googletrans"
                logging.info("Using Google Translate backend (free)")

        # ── Always initialize Google Translate as a fallback ──
        try:
            from deep_translator import GoogleTranslator
            self.google_translator = GoogleTranslator
        except ImportError:
            logging.warning("deep-translator not available, fallback will fail")
            self.google_translator = None

        # ── Initialize chosen backend ──
        if self.backend == "siliconflow":
            if not self.api_key:
                raise ValueError("llm_api_key required for siliconflow backend")
            import openai as _openai
            self._openai_client = _openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or SILICONFLOW_BASE_URL,
                timeout=30.0,  # 30 second timeout per request
                max_retries=2,  # Retry failed requests twice
            )
            # Default model if not set
            if self.model == self.config.openai_model:
                self.model = SILICONFLOW_DEFAULT_MODEL
            logging.info(f"Siliconflow client ready (model={self.model}, url={self.base_url or SILICONFLOW_BASE_URL})")

        elif self.backend == "openai":
            if not self.api_key:
                raise ValueError("OpenAI API key required for openai backend")
            import openai
            openai.api_key = self.api_key
            self.openai = openai

        elif self.backend == "googletrans":
            if not self.google_translator:
                try:
                    from deep_translator import GoogleTranslator
                    self.google_translator = GoogleTranslator
                    logging.info("Google Translate (deep-translator) initialized successfully")
                except ImportError:
                    logging.error("deep-translator not installed. Installing...")
                    import subprocess
                    subprocess.check_call(["pip", "install", "deep-translator"])
                    from deep_translator import GoogleTranslator
                    self.google_translator = GoogleTranslator
            logging.info("Using Google Translate backend")
        
    def translate_clips(self, clips_metadata_path: str, output_dir: Optional[str] = None) -> dict:
        """
        Translate all clips and generate subtitle files.
        
        Args:
            clips_metadata_path: Path to clips_metadata.json from clipper module
            output_dir: Directory to save subtitle files
            
        Returns:
            dict: {
                "original_metadata": "...",
                "clips": [
                    {
                        "clip_path": "...",
                        "translations": {
                            "zh": [...],
                            "en": [...]
                        },
                        "subtitle_files": {
                            "original": "...",
                            "zh": "...",
                            "en": "..."
                        }
                    }
                ]
            }
        """
        output_dir = output_dir or self.default_output_dir

        logging.info(f"Starting translation for clips metadata: {clips_metadata_path}")
        
        # Validation
        if not os.path.exists(clips_metadata_path):
            logging.error(f"Clips metadata file not found: {clips_metadata_path}")
            return None
        
        # Load clips metadata
        try:
            with open(clips_metadata_path, 'r', encoding='utf-8') as f:
                clips_metadata = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load clips metadata: {e}")
            return None
        
        if 'clips' not in clips_metadata:
            logging.error("Invalid clips metadata: missing 'clips' field")
            return None
        
        clips = clips_metadata['clips']
        
        if not clips:
            logging.warning("No clips found in metadata")
            return {"clips": []}
        
        os.makedirs(output_dir, exist_ok=True)
        
        # Process each clip
        results = []
        
        for i, clip in enumerate(clips, 1):
            logging.info(f"\nProcessing clip {i}/{len(clips)}: {clip['clip_path']}")
            
            clip_name = os.path.splitext(os.path.basename(clip['clip_path']))[0]
            asr_subset = clip.get('asr_subset', [])
            
            if not asr_subset:
                logging.warning(f"Clip {i} has no ASR text, skipping")
                continue

            # 获取视频尺寸以判断长宽比
            video_dimensions = _get_video_dimensions(clip['clip_path'])
            is_vertical_video = False
            if video_dimensions:
                is_vertical_video = video_dimensions['height'] > video_dimensions['width']
                logging.info(f"Video {clip['clip_path']} dimensions: {video_dimensions['width']}x{video_dimensions['height']}, vertical: {is_vertical_video}")

            # 根据长宽比获取 max_chars 配置
            if is_vertical_video:
                max_en_chars = self.config.max_en_chars_vertical
                max_zh_chars = self.config.max_zh_chars_vertical
            else:
                max_en_chars = self.config.max_en_chars_horizontal
                max_zh_chars = self.config.max_zh_chars_horizontal
            logging.info(f"Using max_en_chars: {max_en_chars}, max_zh_chars: {max_zh_chars}")
            
            # F4.1: Translate to English first
            logging.info(f"Translating to English...")
            en_translations = self._translate_segments(asr_subset, target_lang="en")

            # F4.2: Split EN segments first to get precise timestamps
            en_translations = _split_long_segments(
                en_translations, max_en_chars=max_en_chars, max_zh_chars=max_zh_chars, lang='en', is_vertical_video=is_vertical_video
            )
            asr_split = _split_long_segments(
                asr_subset, max_en_chars=max_en_chars, max_zh_chars=max_zh_chars, lang='en', is_vertical_video=is_vertical_video
            )

            # F4.3: Translate Chinese BASED ON already-split EN segments
            # This ensures ZH timestamps are inherited from EN split segments (perfectly in sync)
            logging.info(f"Translating to Chinese (based on split EN segments)...")
            zh_translations = self._translate_segments(en_translations, target_lang="zh")
            zh_aligned_translations = [seg.copy() for seg in zh_translations]
            zh_aligned_max_chars = max_zh_chars if is_vertical_video else max_zh_chars
            for seg in zh_aligned_translations:
                seg['text'] = _wrap_cjk_text(seg.get('text', ''), zh_aligned_max_chars)

            # F4.4: If ZH text is still too long, split within its parent EN segment's time range
            zh_translations = _split_long_segments(
                zh_translations, max_en_chars=max_en_chars, max_zh_chars=max_zh_chars, lang='zh', is_vertical_video=is_vertical_video
            )

            en_count = len(en_translations)
            zh_count = len(zh_translations)
            logging.info(
                f"Subtitle split: original {len(asr_subset)} → "
                f"en {en_count} / zh {zh_count} segments"
            )

            # F4.3-F4.6: Generate subtitle files
            subtitle_files = {}

            # Original language subtitle
            original_srt = os.path.join(output_dir, f"{clip_name}_original.srt")
            self._generate_srt(asr_split, original_srt)
            subtitle_files['original'] = original_srt
            logging.info(f"✅ Generated original subtitle: {original_srt}")

            # Chinese subtitle
            zh_srt = os.path.join(output_dir, f"{clip_name}_zh.srt")
            self._generate_srt(zh_translations, zh_srt)
            subtitle_files['zh'] = zh_srt
            logging.info(f"✅ Generated Chinese subtitle: {zh_srt}")

            # Chinese subtitle aligned to EN timestamps
            zh_aligned_srt = os.path.join(output_dir, f"{clip_name}_zh_aligned.srt")
            self._generate_srt(zh_aligned_translations, zh_aligned_srt)
            subtitle_files['zh_aligned'] = zh_aligned_srt
            logging.info(f"✅ Generated aligned Chinese subtitle: {zh_aligned_srt}")

            # English subtitle
            en_srt = os.path.join(output_dir, f"{clip_name}_en.srt")
            self._generate_srt(en_translations, en_srt)
            subtitle_files['en'] = en_srt
            logging.info(f"✅ Generated English subtitle: {en_srt}")
            
            results.append({
                "clip_path": clip['clip_path'],
                "translations": {
                    "zh": zh_translations,
                    "en": en_translations
                },
                "subtitle_files": subtitle_files
            })
        
        # Save translation metadata
        final_result = {
            "original_metadata": clips_metadata_path,
            "clips": results
        }
        
        metadata_path = os.path.join(output_dir, "translations_metadata.json")
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)
        
        logging.info(f"\n✅ Translation complete: {len(results)} clips processed")
        logging.info(f"Metadata saved to: {metadata_path}")
        
        return final_result
    
    def _translate_segments(self, segments: List[Dict], target_lang: str) -> List[Dict]:
        """
        Translate ASR segments to target language.
        
        Args:
            segments: List of ASR segments with 'start', 'end', 'text'
            target_lang: Target language code ('zh' or 'en')
            
        Returns:
            List of translated segments with same structure
        """
        if not segments:
            return []

        # Bug fix: 若 target_lang 为 en 且原文已是英文，直接短路返回原文，不走翻译调用
        if target_lang == "en":
            texts_sample = [seg['text'] for seg in segments[:5] if seg.get('text', '').strip()]
            if texts_sample:
                english_hits = sum(1 for t in texts_sample if _looks_like_english_sentence(t))
                required_hits = len(texts_sample) if len(texts_sample) < 4 else 4
                if english_hits >= required_hits:
                    logging.info(
                        "[Translator] EN short-circuit: 原文大概率已是英文（采样 %d 句，%d 句通过），跳过翻译调用",
                        len(texts_sample),
                        english_hits,
                    )
                    return [seg.copy() for seg in segments]

        # Prepare batch translation
        texts = [seg['text'] for seg in segments]
        
        # Translate in batch
        translated_texts = self._batch_translate(texts, target_lang)
        
        # Combine with timestamps and metadata (preserve 'words' etc.)
        translated_segments = []
        for i, seg in enumerate(segments):
            translated_seg = seg.copy()
            translated_seg['text'] = translated_texts[i]
            translated_segments.append(translated_seg)
        
        return translated_segments
    
    def _batch_translate(self, texts: List[str], target_lang: str) -> List[str]:
        """
        Batch translate texts using configured backend.
        
        Args:
            texts: List of texts to translate
            target_lang: Target language code ('zh' or 'en')
            
        Returns:
            List of translated texts
        """
        if not texts:
            return []
        
        if self.backend == "siliconflow":
            return self._batch_translate_siliconflow(texts, target_lang)
        elif self.backend == "openai":
            return self._batch_translate_openai(texts, target_lang)
        elif self.backend == "googletrans":
            return self._batch_translate_google(texts, target_lang)
        else:
            logging.error(f"Unknown backend: {self.backend}")
            return texts
    
    def _batch_translate_openai(self, texts: List[str], target_lang: str) -> List[str]:
        """
        Batch translate texts using OpenAI API.
        """
        lang_name = "Chinese" if target_lang == "zh" else "English"
        
        # Prepare prompt
        numbered_texts = "\n".join([f"{i+1}. {text}" for i, text in enumerate(texts)])
        
        prompt = f"""Translate the following texts to {lang_name}. 
Keep the same numbering format. Only output the translated texts, no explanations.

{numbered_texts}"""
        
        try:
            response = self.openai.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": f"You are a professional translator. Translate texts to {lang_name} accurately and naturally."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            
            result = response.choices[0].message.content.strip()
            
            # Parse numbered results
            lines = result.split('\n')
            translated = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Remove numbering (e.g., "1. ", "2. ")
                if '. ' in line:
                    parts = line.split('. ', 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        translated.append(parts[1])
                    else:
                        translated.append(line)
                else:
                    translated.append(line)
            
            # Ensure we have the same number of translations
            if len(translated) != len(texts):
                logging.warning(f"Translation count mismatch: expected {len(texts)}, got {len(translated)}")
                return texts

            return translated

        except Exception as e:
            logging.error(f"OpenAI translation failed: {e}")
            return texts

    def _batch_translate_siliconflow(self, texts: List[str], target_lang: str) -> List[str]:
        """
        Batch translate using Siliconflow / DeepSeek-V3 (OpenAI-compatible API).

        Auto-packs chunks by character budget to stay within token limits.
        Uses concurrent processing for significant speedup (4-6x faster).
        """
        chunks = self._build_siliconflow_translation_chunks(texts, target_lang)
        if not chunks:
            return []

        if len(chunks) == 1:
            return self._translate_siliconflow_chunk(chunks[0][1], target_lang)

        char_budget, max_items = self._get_siliconflow_translation_chunk_limits(target_lang)
        logging.info(
            f"[Siliconflow] Large batch ({len(texts)} segments), "
            f"adaptive packing into {len(chunks)} chunks "
            f"(char_budget={char_budget}, max_items={max_items})"
        )

        # Determine concurrency based on CPU count and API limits.
        # Default to 6 workers to improve throughput on larger subtitle batches.
        max_workers = min(6, os.cpu_count() or 4)

        logging.info(
            f"[Siliconflow] Processing {len(chunks)} chunks with {max_workers} concurrent workers"
        )

        results = [None] * len(texts)

        def process_chunk(start_idx, chunk, chunk_num):
            try:
                chunk_result = self._translate_siliconflow_chunk(chunk, target_lang)
                return start_idx, chunk_result, None, chunk_num
            except Exception as e:
                logging.error(f"[Siliconflow] Chunk {chunk_num}/{len(chunks)} failed: {e}")
                return start_idx, None, e, chunk_num

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_chunk, start_idx, chunk, chunk_num): (start_idx, chunk_num)
                for chunk_num, (start_idx, chunk) in enumerate(chunks, 1)
            }

            completed = 0
            for future in as_completed(futures):
                idx, chunk_result, error, chunk_num = future.result()

                if chunk_result:
                    for i, translation in enumerate(chunk_result):
                        results[idx + i] = translation
                    completed += 1
                    logging.info(
                        f"[Siliconflow] Chunk {chunk_num}/{len(chunks)} done "
                        f"({completed}/{len(chunks)} completed, {len(chunk_result)} segments)"
                    )
                else:
                    logging.error(f"[Siliconflow] Chunk {chunk_num}/{len(chunks)} failed")

        return [r for r in results if r is not None]

    def _get_siliconflow_translation_chunk_limits(self, target_lang: str) -> tuple[int, int]:
        """Return (char_budget, max_items) for adaptive Siliconflow translation packing."""
        if target_lang == "en":
            return 1100, 24
        return 800, 14

    def _build_siliconflow_translation_chunks(
        self, texts: List[str], target_lang: str
    ) -> List[tuple[int, List[str]]]:
        """Pack texts into adaptive chunks using char budget and item caps."""
        if not texts:
            return []

        char_budget, max_items = self._get_siliconflow_translation_chunk_limits(target_lang)
        normalized_lengths = [len((text or "").strip()) for text in texts]
        if normalized_lengths:
            avg_chars = sum(normalized_lengths) / len(normalized_lengths)
            max_chars = max(normalized_lengths)
            if max_chars >= 180 or avg_chars >= 70:
                char_budget = max(600, int(char_budget * 0.8))
                max_items = max(8, int(max_items * 0.75))
            elif max_chars >= 120 or avg_chars >= 45:
                char_budget = max(800, int(char_budget * 0.9))
                max_items = max(10, int(max_items * 0.85))
        chunks: List[tuple[int, List[str]]] = []
        current: List[str] = []
        current_chars = 0
        chunk_start_idx = 0

        for idx, text in enumerate(texts):
            normalized = (text or "").strip()
            item_chars = len(normalized)

            should_flush = bool(current) and (
                current_chars + item_chars > char_budget or len(current) >= max_items
            )
            if should_flush:
                chunks.append((chunk_start_idx, current))
                current = []
                current_chars = 0
                chunk_start_idx = idx

            current.append(text)
            current_chars += item_chars

        if current:
            chunks.append((chunk_start_idx, current))

        return chunks

    def _translate_siliconflow_chunk(self, texts: List[str], target_lang: str) -> List[str]:
        """Translate one chunk, then degrade to smaller chunks / lines on failure."""
        translated = self._request_siliconflow_chunk(texts, target_lang)
        if translated is not None:
            return translated

        if len(texts) <= 1:
            return self._batch_translate_google(texts, target_lang)

        mid = max(1, len(texts) // 2)
        logging.info(
            "[Siliconflow] Degrading failed chunk of %d segments into %d + %d",
            len(texts),
            len(texts[:mid]),
            len(texts[mid:]),
        )
        return (
            self._translate_siliconflow_chunk(texts[:mid], target_lang)
            + self._translate_siliconflow_chunk(texts[mid:], target_lang)
        )

    def _request_siliconflow_chunk(self, texts: List[str], target_lang: str) -> Optional[List[str]]:
        """Request one exact-size chunk from Siliconflow. Return None on any quality failure."""
        if not texts:
            return []

        lang_name = "Chinese (Simplified)" if target_lang == "zh" else "English"
        n = len(texts)
        numbered_input = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

        # attempt 0: numbered list — proven more robust against LLM segment merging
        base_system_prompt = (
            f"你是专业字幕翻译器。输入是带序号的 {n} 行字幕原文。"
            f"你必须输出恰好 {n} 行译文，每行格式为 '序号. 译文'，序号从 1 到 {n} 连续，不能跳号、合并或漏行。"
            f"不要输出任何序号范围以外的内容、解释或 Markdown。"
            f"翻译目标语言是 {lang_name}，译文要自然、简洁、适合字幕阅读。"
        )
        base_user_prompt = (
            f"请将下面每行字幕翻译为 {lang_name}，严格保持序号和行数（共 {n} 行）。"
            f"只返回带序号的译文行，不要返回其他内容：\n\n{numbered_input}"
        )

        # attempt 1: same numbered format with stronger anti-merge emphasis
        retry_system_prompt = (
            f"你是专业字幕翻译器。输入是带序号的 {n} 行字幕原文，必须逐行翻译，严禁合并。"
            f"即使相邻两行原文高度相似或很短，也必须各自独立翻译，分别输出为 2 行，绝对不允许合并成 1 行。"
            f"输出必须恰好 {n} 行，格式严格为 '序号. 译文'（序号 1 到 {n} 连续），不得跳号或漏行。"
            f"不要输出其他内容。翻译目标语言是 {lang_name}，译文简洁适合字幕阅读。"
        )
        if target_lang == "zh":
            retry_system_prompt += " 除专有名词、缩写、数字外，禁止输出英文整句；所有内容翻译成中文。"
        elif target_lang == "en":
            retry_system_prompt += " 禁止输出中文；所有内容翻译成自然英文。"
        retry_user_prompt = (
            f"输入共 {n} 行，每行都必须有对应的译文行，不能少。"
            f"请翻译为 {lang_name}，只返回带序号的译文：\n\n{numbered_input}"
        )

        # Token budget: DeepSeek-V3.2 supports up to 8192 output tokens via Siliconflow
        avg_chars = sum(len(t) for t in texts) / max(n, 1)
        estimated_output_tokens = int(n * max(avg_chars * 2.5, 30)) + 300
        # 40% safety margin; cap raised to 8192 for DeepSeek-V3.2
        max_tokens = max(1024, min(int(estimated_output_tokens * 1.4), 8192))
        logging.debug(f"[Siliconflow] Chunk size={n}, avg_chars={avg_chars:.1f}, estimated_tokens={estimated_output_tokens}, max_tokens={max_tokens}")

        def _parse_numbered(raw: str, n: int):
            """Extract only numbered lines '1. ...' from LLM response."""
            result = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                if '. ' in line:
                    parts = line.split('. ', 1)
                    if parts[0].isdigit() and 1 <= int(parts[0]) <= n:
                        result.append(parts[1].strip())
            return result

        # attempt 1 uses higher max_tokens in case the first try was on the edge
        retry_max_tokens = min(int(max_tokens * 1.5), 8192)

        for attempt in range(2):  # retry once on mismatch
            try:
                if attempt == 0:
                    system_prompt = base_system_prompt
                    user_prompt = base_user_prompt
                    current_max_tokens = max_tokens
                else:
                    # Stronger anti-merge emphasis on retry
                    system_prompt = retry_system_prompt
                    user_prompt = retry_user_prompt
                    current_max_tokens = retry_max_tokens

                response = self._openai_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=0.2,
                    max_tokens=current_max_tokens,
                )

                raw = response.choices[0].message.content.strip()
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                logging.debug(
                    f"[Siliconflow] attempt {attempt+1} finish_reason={finish_reason} raw response:\n{raw}"
                )

                translated = _parse_numbered(raw, n)

                if (
                    len(translated) == len(texts)
                    and not _translation_has_language_drift(translated, target_lang)
                    and not _translation_has_meta_output(translated)
                ):
                    logging.info(f"[Siliconflow] Translated {len(translated)} segments via {self.model}")
                    return translated

                drift_detected = len(translated) == len(texts) and _translation_has_language_drift(translated, target_lang)
                meta_output_detected = len(translated) == len(texts) and _translation_has_meta_output(translated)
                if drift_detected:
                    logging.warning(
                        f"[Siliconflow] language drift detected (attempt {attempt+1}, target={target_lang}) "
                        + ("Retrying with stronger prompt..." if attempt == 0 else "Falling back to Google Translate.")
                    )
                    continue

                if meta_output_detected:
                    logging.warning(
                        f"[Siliconflow] meta-output detected (attempt {attempt+1}, target={target_lang}) "
                        + ("Retrying with stronger prompt..." if attempt == 0 else "Falling back to finer-grained translation.")
                    )
                    continue

                truncated = finish_reason == "length"
                logging.warning(
                    f"[Siliconflow] count mismatch (attempt {attempt+1}): "
                    f"expected {len(texts)}, got {len(translated)} "
                    f"(max_tokens={current_max_tokens}, finish_reason={finish_reason}"
                    + (", likely truncated" if truncated else ", likely segment merge")
                    + ")."
                    + (" Retrying with stronger prompt..." if attempt == 0 else " Falling back to finer-grained translation.")
                )

            except Exception as e:
                logging.error(f"[Siliconflow] Translation failed (attempt {attempt+1}): {e}")
                break

        return None

    def _batch_translate_google(self, texts: List[str], target_lang: str) -> List[str]:
        """
        Batch translate texts using Google Translate (deep-translator).
        """
        # Map language codes
        lang_code = "zh-CN" if target_lang == "zh" else "en"
        
        translated = []
        
        for text in texts:
            try:
                translator = self.google_translator(source='auto', target=lang_code)
                result = translator.translate(text)
                translated.append(result)
            except Exception as e:
                logging.warning(f"Failed to translate '{text[:30]}...': {e}")
                translated.append(text)  # Fallback to original
        
        return translated
    
    def _generate_srt(self, segments: List[Dict], output_path: str):
        """
        Generate SRT subtitle file.
        
        Args:
            segments: List of segments with 'start', 'end', 'text'
            output_path: Path to save SRT file
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            for i, seg in enumerate(segments, 1):
                start_time = self._format_srt_time(seg['start'])
                end_time = self._format_srt_time(seg['end'])
                text = seg['text']
                
                f.write(f"{i}\n")
                f.write(f"{start_time} --> {end_time}\n")
                f.write(f"{text}\n")
                f.write("\n")
    
    def _format_srt_time(self, seconds: float) -> str:
        """
        Format seconds to SRT time format (HH:MM:SS,mmm).
        
        Args:
            seconds: Time in seconds
            
        Returns:
            Formatted time string
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


if __name__ == "__main__":
    # Test with clips metadata
    translator = Translator()
    
    clips_metadata_path = "clips/clips_metadata.json"
    
    if not os.path.exists(clips_metadata_path):
        logging.error(f"Clips metadata not found: {clips_metadata_path}")
        exit(1)
    
    logging.info(f"\n{'='*70}")
    logging.info("Testing Translator with clips metadata")
    logging.info(f"{'='*70}\n")
    
    result = translator.translate_clips(clips_metadata_path)
    
    if result:
        logging.info(f"\n{'='*70}")
        logging.info("Translation Complete!")
        logging.info(f"{'='*70}")
        logging.info(f"\nTotal clips processed: {len(result['clips'])}")
        
        for i, clip in enumerate(result['clips'], 1):
            logging.info(f"\nClip {i}:")
            logging.info(f"  Path: {clip['clip_path']}")
            logging.info(f"  Subtitle files:")
            for lang, path in clip['subtitle_files'].items():
                logging.info(f"    {lang}: {path}")
    else:
        logging.error("Translation failed!")
