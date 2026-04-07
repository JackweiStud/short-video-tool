"""
短视频工具配置管理模块

本模块提供集中式的配置管理,旨在消除硬编码参数并提高代码的可维护性。

使用方法:
    from config import Config
    
    config = Config()
    print(config.downloads_dir)
    print(config.min_clip_duration)
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

# 工程根目录（config.py 位于根目录，取其所在目录）
_PROJECT_ROOT = Path(__file__).resolve().parent

VALID_VIDEO_QUALITIES = ("720p", "1080p", "best", "worst")
_ENV_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


def _parse_env_line(line: str) -> Optional[tuple[str, str]]:
    """Parse a single `.env` line into a key/value pair."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()

    if "=" not in stripped:
        return None

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None

    value = raw_value.lstrip()
    if value.startswith(("'", '"')):
        quote = value[0]
        parsed: list[str] = []
        escaped = False

        for char in value[1:]:
            if quote == '"' and escaped:
                parsed.append(char)
                escaped = False
                continue

            if quote == '"' and char == "\\":
                escaped = True
                continue

            if char == quote:
                break

            parsed.append(char)

        value = "".join(parsed)
    else:
        value = _ENV_INLINE_COMMENT_RE.sub("", value).strip()

    return key, value


def _load_project_env(force: bool = False) -> None:
    """Load variables from the project root `.env` file if it exists."""
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            continue

        key, value = parsed
        if force or key not in os.environ:
            os.environ[key] = value


_load_project_env()


@dataclass
class Config:
    """
    短视频工具的核心配置类。
    
    所有配置项均可通过环境变量进行覆盖。
    优先级:环境变量 > 默认值
    """
    
    # ==================== 路径设置 ====================
    
    downloads_dir: str = field(
        default_factory=lambda: os.getenv("DOWNLOADS_DIR", "downloads")
    )
    """视频下载目录"""
    
    output_dir: str = field(
        default_factory=lambda: os.getenv("OUTPUT_DIR", "output")
    )
    """根输出目录"""
    
    analysis_dir: str = field(
        default_factory=lambda: os.getenv("ANALYSIS_DIR", "analysis_results")
    )
    """分析结果存储目录"""
    
    clips_dir: str = field(
        default_factory=lambda: os.getenv("CLIPS_DIR", "clips")
    )
    """裁剪后的视频剪辑目录"""
    
    subtitles_dir: str = field(
        default_factory=lambda: os.getenv("SUBTITLES_DIR", "subtitles")
    )
    """字幕文件目录"""
    
    # ==================== 裁剪器 (Clipper) 设置 ====================
    
    min_clip_duration: int = field(
        default_factory=lambda: int(os.getenv("MIN_CLIP_DURATION", "60"))
    )
    """剪辑最小长度(秒)"""
    
    max_clip_duration: int = field(
        default_factory=lambda: int(os.getenv("MAX_CLIP_DURATION", "180"))
    )
    """剪辑最大长度(秒)"""
    
    max_clips: int = field(
        default_factory=lambda: int(os.getenv("MAX_CLIPS", "6"))
    )
    """生成的最大剪辑数量"""
    
    # ==================== 分析器 (Analyzer) 设置 ====================
    
    whisper_model: str = field(
        default_factory=lambda: os.getenv("WHISPER_MODEL", "medium")
    )
    """Whisper 模型大小:tiny, base, small, medium, large, large-v2, large-v3"""

    whisper_word_timestamps: bool = field(
        default_factory=lambda: os.getenv("WHISPER_WORD_TIMESTAMPS", "true").lower() == "true"
    )
    """是否开启词级时间戳(用于 D+B 精准字幕对齐, 推荐开启)"""

    # ==================== ASR 分段配置 ====================

    asr_chunk_duration: int = field(
        default_factory=lambda: int(os.environ.get("ASR_CHUNK_DURATION", "300"))
    )
    """每段秒数，默认5分钟"""

    asr_overlap_seconds: int = field(
        default_factory=lambda: int(os.environ.get("ASR_OVERLAP_SECONDS", "5"))
    )
    """段间 overlap 秒数"""

    asr_segment_timeout: int = field(
        default_factory=lambda: int(os.environ.get("ASR_SEGMENT_TIMEOUT", "600"))
    )
    """单段超时秒数"""

    asr_cache_dir: str = field(
        default_factory=lambda: os.environ.get("ASR_CACHE_DIR", "cache/asr")
    )
    """缓存目录（相对项目根）"""

    asr_vad_filter: bool = field(
        default_factory=lambda: os.environ.get("ASR_VAD_FILTER", "true").lower() == "true"
    )
    """VAD 静音过滤"""

    asr_vad_min_duration_threshold: float = field(
        default_factory=lambda: 60.0 * 60.0
    )
    """>60分钟视频启用 VAD（3600秒）"""

    asr_language: str = field(
        default_factory=lambda: os.getenv("ASR_LANGUAGE", "en")
    )
    """ASR 语言代码(例如:en, zh, es, fr)"""

    faster_whisper_local_model_dir: str = field(
        default_factory=lambda: os.getenv("FASTER_WHISPER_LOCAL_MODEL_DIR", "~/models")
    )
    """faster-whisper 本地模型目录，模型子目录格式为 faster-whisper-{model}，设为空字符串则跳过本地查找直接下载"""

    mlx_whisper_local_model_dir: str = field(
        default_factory=lambda: os.getenv("MLX_WHISPER_LOCAL_MODEL_DIR", "~/models")
    )
    """mlx-whisper 本地模型目录（Apple Silicon），模型子目录格式为 whisper-{model}-mlx，设为空字符串则跳过本地查找直接下载"""

    audio_climax_top_n: int = field(
        default_factory=lambda: int(os.getenv("AUDIO_CLIMAX_TOP_N", "5"))
    )
    """要检测的音频高潮点数量"""
    
    scene_detection_threshold: float = field(
        default_factory=lambda: float(os.getenv("SCENE_DETECTION_THRESHOLD", "27.0"))
    )
    """场景检测灵敏度阈值(值越低越灵敏)"""
    
    # ==================== 语义分段 (Topic Segmentation) 设置 ====================
    
    enable_topic_segmentation: bool = field(
        default_factory=lambda: os.getenv("ENABLE_TOPIC_SEGMENTATION", "true").lower() == "true"
    )
    """是否启用 LLM 语义分段（默认开启，优先产出更高质量的切片边界）"""
    
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "siliconflow")
    )
    """LLM provider: siliconflow, openai, anthropic"""
    
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-ai/DeepSeek-V3")
    )
    """LLM 模型名称"""
    
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
    )
    """LLM API base URL"""
    
    llm_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("LLM_API_KEY")
    )
    """LLM API key (optional; required to enable topic segmentation / Siliconflow backend)"""
    
    topic_segment_min_duration: int = field(
        default_factory=lambda: int(os.getenv("TOPIC_SEGMENT_MIN_DURATION", "20"))
    )
    """语义分段最小时长(秒),用于二次拆分过长章节"""

    topic_segment_chunk_duration: int = field(
        default_factory=lambda: int(os.getenv("TOPIC_SEGMENT_CHUNK_DURATION", "1500"))
    )
    """LLM 语义分段的单窗口时长(秒)，默认 25 分钟"""

    topic_segment_chunk_overlap_seconds: int = field(
        default_factory=lambda: int(os.getenv("TOPIC_SEGMENT_CHUNK_OVERLAP_SECONDS", "180"))
    )
    """LLM 语义分段窗口重叠秒数，默认 3 分钟"""

    topic_segment_max_workers: int = field(
        default_factory=lambda: int(os.getenv("TOPIC_SEGMENT_MAX_WORKERS", "4"))
    )
    """LLM 语义分段并发窗口数"""
    
    llm_timeout: int = field(
        default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "60"))
    )
    """LLM API 调用超时(秒)"""
    
    # ==================== 翻译器 (Translator) 设置 ====================
    
    openai_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY")
    )
    """用于 GPT 翻译的 OpenAI API 密钥(可选)"""
    
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    """翻译使用的 OpenAI 模型"""
    
    translation_backend: str = field(
        default_factory=lambda: os.getenv("TRANSLATION_BACKEND", "auto")
    )
    """翻译后端:auto (自动), openai, googletrans"""

    # ==================== 字幕样式设置 (Subtitles Styling) ====================
    font_name_en: str = field(
        default_factory=lambda: os.getenv("FONT_NAME_EN", "Arial")
    )
    """英文字幕字体名称 (FFmpeg)"""

    font_name_zh: str = field(
        default_factory=lambda: os.getenv("FONT_NAME_ZH", "Heiti SC")
    )
    """中文字幕字体名称 (FFmpeg)"""

    font_size_en_vertical: int = field(
        default_factory=lambda: int(os.getenv("FONT_SIZE_EN_VERTICAL", "36"))
    )
    """垂直视频 (9:16) 英文字幕字体大小"""

    font_size_zh_vertical: int = field(
        default_factory=lambda: int(os.getenv("FONT_SIZE_ZH_VERTICAL", "40"))
    )
    """垂直视频 (9:16) 中文字幕字体大小"""

    font_size_en_horizontal: int = field(
        default_factory=lambda: int(os.getenv("FONT_SIZE_EN_HORIZONTAL", "52"))
    )
    """水平视频 (16:9) 英文字幕字体大小"""

    font_size_zh_horizontal: int = field(
        default_factory=lambda: int(os.getenv("FONT_SIZE_ZH_HORIZONTAL", "56"))
    )
    """水平视频 (16:9) 中文字幕字体大小"""

    margin_v_en_vertical: int = field(
        default_factory=lambda: int(os.getenv("MARGIN_V_EN_VERTICAL", "30"))
    )
    """垂直视频 (9:16) 英文字幕垂直边距"""

    margin_v_zh_vertical: int = field(
        default_factory=lambda: int(os.getenv("MARGIN_V_ZH_VERTICAL", "40"))
    )
    """垂直视频 (9:16) 中文字幕垂直边距"""

    margin_v_en_horizontal: int = field(
        default_factory=lambda: int(os.getenv("MARGIN_V_EN_HORIZONTAL", "40"))
    )
    """水平视频 (16:9) 英文字幕垂直边距"""

    margin_v_zh_horizontal: int = field(
        default_factory=lambda: int(os.getenv("MARGIN_V_ZH_HORIZONTAL", "15"))
    )
    """水平视频 (16:9) 中文字幕垂直边距"""
    
    # ==================== 字幕长度限制 (Subtitle Length Limits) ====================
    max_en_chars_vertical: int = field(
        default_factory=lambda: int(os.getenv("MAX_EN_CHARS_VERTICAL", "30"))
    )
    """垂直视频 (9:16) 英文字幕最大字符数"""

    max_zh_chars_vertical: int = field(
        default_factory=lambda: int(os.getenv("MAX_ZH_CHARS_VERTICAL", "15"))
    )
    """垂直视频 (9:16) 中文字幕最大字符数"""

    max_en_chars_horizontal: int = field(
        default_factory=lambda: int(os.getenv("MAX_EN_CHARS_HORIZONTAL", "42"))
    )
    """水平视频 (16:9) 英文字幕最大字符数"""

    max_zh_chars_horizontal: int = field(
        default_factory=lambda: int(os.getenv("MAX_ZH_CHARS_HORIZONTAL", "22"))
    )
    """水平视频 (16:9) 中文字幕最大字符数"""

    # ==================== 字幕重叠处理 (Subtitle Overlap Handling) ====================
    # These margins are used when hard subtitles are detected.
    # The soft subtitles will be positioned dynamically to avoid overlapping with hard subtitles.
    # Values are fractional (0.0 to 1.0) of the video height.

    soft_subtitle_top_margin_vertical_en: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_TOP_MARGIN_VERTICAL_EN", "0.05"))
    )
    """垂直视频中,当检测到硬字幕时,英文字幕烧录到顶部区域的垂直起始位置"""
    soft_subtitle_top_margin_vertical_zh: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_TOP_MARGIN_VERTICAL_ZH", "0.15"))
    )
    """垂直视频中,当检测到硬字幕时,中文字幕烧录到顶部区域的垂直起始位置"""

    soft_subtitle_bottom_margin_vertical_en: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_BOTTOM_MARGIN_VERTICAL_EN", "0.85"))
    )
    """垂直视频中,当检测到硬字幕时,英文字幕烧录到底部区域的垂直起始位置"""
    soft_subtitle_bottom_margin_vertical_zh: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_BOTTOM_MARGIN_VERTICAL_ZH", "0.95"))
    )
    """垂直视频中,当检测到硬字幕时,中文字幕烧录到底部区域的垂直起始位置"""

    soft_subtitle_top_margin_horizontal_en: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_TOP_MARGIN_HORIZONTAL_EN", "0.05"))
    )
    """水平视频中,当检测到硬字幕时,英文字幕烧录到顶部区域的垂直起始位置"""
    soft_subtitle_top_margin_horizontal_zh: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_TOP_MARGIN_HORIZONTAL_ZH", "0.15"))
    )
    """水平视频中,当检测到硬字幕时,中文字幕烧录到顶部区域的垂直起始位置"""

    soft_subtitle_bottom_margin_horizontal_en: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_BOTTOM_MARGIN_HORIZONTAL_EN", "0.85"))
    )
    """水平视频中,当检测到硬字幕时,英文字幕烧录到底部区域的垂直起始位置"""
    soft_subtitle_bottom_margin_horizontal_zh: float = field(
        default_factory=lambda: float(os.getenv("SOFT_SUBTITLE_BOTTOM_MARGIN_HORIZONTAL_ZH", "0.95"))
    )
    """水平视频中,当检测到硬字幕时,中文字幕烧录到底部区域的垂直起始位置"""

    subtitle_auto_en_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("SUBTITLE_AUTO_EN_CONFIDENCE_THRESHOLD", "0.85"))
    )
    """auto 模式下 EN 硬字幕判定的最小置信度"""

    subtitle_auto_hard_ocr_sample_count: int = field(
        default_factory=lambda: int(os.getenv("SUBTITLE_AUTO_HARD_OCR_SAMPLE_COUNT", "11"))
    )
    """auto 模式下硬字幕 OCR 采样点数量"""

    subtitle_hard_boundary_fallback: float = field(
        default_factory=lambda: float(os.getenv("SUBTITLE_HARD_BOUNDARY_FALLBACK", "0.803"))
    )
    """硬字幕边界缺失时的回退边界（从顶部归一化 0-1）"""

    subtitle_hard_mask_top_padding_horizontal: int = field(
        default_factory=lambda: int(os.getenv("SUBTITLE_HARD_MASK_TOP_PADDING_HORIZONTAL", "18"))
    )
    """水平视频中，遮挡原硬字幕区域时在检测边界之上额外保留的纯色遮罩高度（像素，基于 1080p 基线）"""

    subtitle_hard_mask_top_padding_vertical: int = field(
        default_factory=lambda: int(os.getenv("SUBTITLE_HARD_MASK_TOP_PADDING_VERTICAL", "28"))
    )
    """竖屏视频中，遮挡原硬字幕区域时在检测边界之上额外保留的纯色遮罩高度（像素，基于 1920p 基线）"""

    subtitle_hard_mask_color: str = field(
        default_factory=lambda: os.getenv("SUBTITLE_HARD_MASK_COLOR", "black@0.95")
    )
    """硬字幕替换模式下的纯色遮罩颜色，供 FFmpeg drawbox 使用"""

    subtitle_hard_burn_mode_en: str = field(
        default_factory=lambda: os.getenv("SUBTITLE_HARD_BURN_MODE_EN", "replace")
    )
    """EN 硬字幕场景下的默认烧录策略：replace / skip"""

    subtitle_hard_burn_mode_zh: str = field(
        default_factory=lambda: os.getenv("SUBTITLE_HARD_BURN_MODE_ZH", "skip")
    )
    """ZH 硬字幕场景下的默认烧录策略：replace / skip"""

    subtitle_hard_burn_mode_bilingual: str = field(
        default_factory=lambda: os.getenv("SUBTITLE_HARD_BURN_MODE_BILINGUAL", "replace")
    )
    """BILINGUAL 硬字幕场景下的默认烧录策略：replace / skip"""
    
    # ==================== 下载器 (Downloader) 设置 ====================
    
    ytdlp_cookies_browser: str = field(
        default_factory=lambda: os.getenv("YTDLP_COOKIES_BROWSER", "chrome")
    )
    """yt-dlp 从哪个浏览器读取登录态 cookies；设为空字符串则禁用"""

    ytdlp_youtube_player_client: str = field(
        default_factory=lambda: os.getenv("YTDLP_YOUTUBE_PLAYER_CLIENT", "tv")
    )
    """yt-dlp 的 YouTube player_client；设为空字符串则不传该参数"""

    video_quality: str = field(
        default_factory=lambda: os.getenv("VIDEO_QUALITY", "best")
    )
    """默认视频质量:720p, 1080p, best, worst"""
    
    download_retries: int = field(
        default_factory=lambda: int(os.getenv("DOWNLOAD_RETRIES", "5"))
    )
    """下载重试次数"""
    
    # ==================== 处理设置 ====================
    
    ffmpeg_timeout: int = field(
        default_factory=lambda: int(os.getenv("FFMPEG_TIMEOUT", "300"))
    )
    """FFmpeg 操作超时时间(秒)"""
    
    enable_gpu: bool = field(
        default_factory=lambda: os.getenv("ENABLE_GPU", "false").lower() == "true"
    )
    """是否启用 GPU 加速 Whisper(需要 CUDA 环境)"""
    
    # ==================== 日志设置 ====================
    
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    """日志级别:DEBUG, INFO, WARNING, ERROR, CRITICAL"""
    
    logs_dir: Path = field(
        default_factory=lambda: _PROJECT_ROOT / 'logs'
    )
    """日志目录"""

    log_file: str = field(
        default_factory=lambda: os.getenv("LOG_FILE", str(_PROJECT_ROOT / 'logs' / 'main.log'))
    )
    """日志文件路径"""
    
    # ==================== 辅助方法 ====================
    
    def __post_init__(self):
        """初始化后的配置校验"""
        # 校验剪辑时长
        if self.min_clip_duration >= self.max_clip_duration:
            raise ValueError(
                f"最小剪辑时长 ({self.min_clip_duration}) 必须小于 "
                f"最大剪辑时长 ({self.max_clip_duration})"
            )
        
        if self.min_clip_duration < 5:
            raise ValueError("最小剪辑时长必须至少为 5 秒")
        
        if self.max_clip_duration > 300:
            raise ValueError("最大剪辑时长不能超过 300 秒(5 分钟)")
        
        # 校验 Whisper 模型
        valid_models = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]
        if self.whisper_model not in valid_models:
            raise ValueError(
                f"无效的 whisper_model: {self.whisper_model}。 "
                f"必须是以下之一: {', '.join(valid_models)}"
            )
        
        # 校验翻译后端
        valid_backends = ["auto", "openai", "siliconflow", "googletrans"]
        if self.translation_backend not in valid_backends:
            raise ValueError(
                f"无效的 translation_backend: {self.translation_backend}。 "
                f"必须是以下之一: {', '.join(valid_backends)}"
            )
        
        # 校验视频质量
        if self.video_quality not in VALID_VIDEO_QUALITIES:
            raise ValueError(
                f"无效的 video_quality: {self.video_quality}。 "
                f"必须是以下之一: {', '.join(VALID_VIDEO_QUALITIES)}"
            )
        
        # 校验 LLM provider
        valid_providers = ["siliconflow", "openai", "anthropic"]
        if self.llm_provider not in valid_providers:
            raise ValueError(
                f"无效的 llm_provider: {self.llm_provider}。 "
                f"必须是以下之一: {', '.join(valid_providers)}"
            )

        if self.topic_segment_chunk_duration <= self.topic_segment_min_duration:
            raise ValueError(
                "topic_segment_chunk_duration 必须大于 topic_segment_min_duration"
            )

        if self.topic_segment_chunk_overlap_seconds < 0:
            raise ValueError("topic_segment_chunk_overlap_seconds 不能小于 0")

        if self.topic_segment_chunk_overlap_seconds >= self.topic_segment_chunk_duration:
            raise ValueError(
                "topic_segment_chunk_overlap_seconds 必须小于 topic_segment_chunk_duration"
            )

        if self.topic_segment_max_workers < 1:
            raise ValueError("topic_segment_max_workers 必须至少为 1")
    
    def create_directories(self):
        """如果所需目录不存在,则创建它们"""
        directories = [
            self.downloads_dir,
            self.output_dir,
            self.analysis_dir,
            self.clips_dir,
            self.subtitles_dir,
        ]
        
        for directory in directories:
            Path(directory).mkdir(parents=True, exist_ok=True)
    
    def to_dict(self) -> dict:
        """将配置转换为字典"""
        return {
            "paths": {
                "downloads_dir": self.downloads_dir,
                "output_dir": self.output_dir,
                "analysis_dir": self.analysis_dir,
                "clips_dir": self.clips_dir,
                "subtitles_dir": self.subtitles_dir,
            },
            "clipper": {
                "min_clip_duration": self.min_clip_duration,
                "max_clip_duration": self.max_clip_duration,
                "max_clips": self.max_clips,
            },
            "analyzer": {
                "whisper_model": self.whisper_model,
                "whisper_word_timestamps": self.whisper_word_timestamps,
                "asr_chunk_duration": self.asr_chunk_duration,
                "asr_overlap_seconds": self.asr_overlap_seconds,
                "asr_segment_timeout": self.asr_segment_timeout,
                "asr_cache_dir": self.asr_cache_dir,
                "asr_vad_filter": self.asr_vad_filter,
                "asr_vad_min_duration_threshold": self.asr_vad_min_duration_threshold,
                "asr_language": self.asr_language,
                "audio_climax_top_n": self.audio_climax_top_n,
                "scene_detection_threshold": self.scene_detection_threshold,
            },
            "topic_segmentation": {
                "enable_topic_segmentation": self.enable_topic_segmentation,
                "llm_provider": self.llm_provider,
                "llm_model": self.llm_model,
                "llm_base_url": self.llm_base_url,
                "llm_api_key": "***" if self.llm_api_key else None,
                "topic_segment_min_duration": self.topic_segment_min_duration,
                "topic_segment_chunk_duration": self.topic_segment_chunk_duration,
                "topic_segment_chunk_overlap_seconds": self.topic_segment_chunk_overlap_seconds,
                "topic_segment_max_workers": self.topic_segment_max_workers,
                "llm_timeout": self.llm_timeout,
            },
            "translator": {
                "openai_api_key": "***" if self.openai_api_key else None,
                "openai_model": self.openai_model,
                "translation_backend": self.translation_backend,
                "font_name_en": self.font_name_en,
                "font_name_zh": self.font_name_zh,
                "font_size_en_vertical": self.font_size_en_vertical,
                "font_size_zh_vertical": self.font_size_zh_vertical,
                "font_size_en_horizontal": self.font_size_en_horizontal,
                "font_size_zh_horizontal": self.font_size_zh_horizontal,
                "margin_v_en_vertical": self.margin_v_en_vertical,
                "margin_v_zh_vertical": self.margin_v_zh_vertical,
                "margin_v_en_horizontal": self.margin_v_en_horizontal,
                "margin_v_zh_horizontal": self.margin_v_zh_horizontal,
                "max_en_chars_vertical": self.max_en_chars_vertical,
                "max_zh_chars_vertical": self.max_zh_chars_vertical,
                "max_en_chars_horizontal": self.max_en_chars_horizontal,
                "max_zh_chars_horizontal": self.max_zh_chars_horizontal,
                "subtitle_auto_en_confidence_threshold": self.subtitle_auto_en_confidence_threshold,
                "subtitle_auto_hard_ocr_sample_count": self.subtitle_auto_hard_ocr_sample_count,
                "subtitle_hard_boundary_fallback": self.subtitle_hard_boundary_fallback,
                "subtitle_hard_mask_top_padding_horizontal": self.subtitle_hard_mask_top_padding_horizontal,
                "subtitle_hard_mask_top_padding_vertical": self.subtitle_hard_mask_top_padding_vertical,
                "subtitle_hard_mask_color": self.subtitle_hard_mask_color,
                "subtitle_hard_burn_mode_en": self.subtitle_hard_burn_mode_en,
                "subtitle_hard_burn_mode_zh": self.subtitle_hard_burn_mode_zh,
                "subtitle_hard_burn_mode_bilingual": self.subtitle_hard_burn_mode_bilingual,
            },
            "downloader": {
                "ytdlp_cookies_browser": self.ytdlp_cookies_browser,
                "ytdlp_youtube_player_client": self.ytdlp_youtube_player_client,
                "video_quality": self.video_quality,
                "download_retries": self.download_retries,
            },
            "processing": {
                "ffmpeg_timeout": self.ffmpeg_timeout,
                "enable_gpu": self.enable_gpu,
            },
            "logging": {
                "log_level": self.log_level,
                "log_file": self.log_file,
            },
        }
    
    def __repr__(self) -> str:
        """配置类的字符串表示形式"""
        config_dict = self.to_dict()
        lines = ["Configuration:"]
        
        for section, values in config_dict.items():
            lines.append(f"\n[{section.upper()}]")
            for key, value in values.items():
                lines.append(f"  {key}: {value}")
        
        return "\n".join(lines)


# 全局配置实例
_config: Optional[Config] = None


def get_config() -> Config:
    """
    获取全局配置实例(单例模式)。
    
    返回:
        Config: 全局配置实例
    """
    global _config
    _load_project_env()
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """
    从环境变量重新加载配置。
    
    返回:
        Config: 新的配置实例
    """
    global _config
    _load_project_env(force=True)
    _config = Config()
    return _config


if __name__ == "__main__":
    # 配置模块测试
    print("="*70)
    print("配置模块测试 (Configuration Test)")
    print("="*70)
    
    # 创建配置实例
    config = Config()
    
    # 打印配置
    print(config)
    
    # 验证测试
    print("\n" + "="*70)
    print("校验逻辑测试 (Validation Tests)")
    print("="*70)
    
    try:
        # 测试无效的剪辑时长
        invalid_config = Config()
        invalid_config.min_clip_duration = 100
        invalid_config.max_clip_duration = 50
        invalid_config.__post_init__()
    except ValueError as e:
        print(f"✓ 捕获到预期的错误: {e}")
    
    try:
        # 测试无效的模型
        invalid_config = Config()
        invalid_config.whisper_model = "invalid"
        invalid_config.__post_init__()
    except ValueError as e:
        print(f"✓ 捕获到预期的错误: {e}")
    
    print("\n" + "="*70)
    print("✓ 配置模块运行正常 (Configuration module working correctly)")
    print("="*70)
