#!/usr/bin/env python3
# print("Script starting...")
"""
Short Video Tool - 短视频一键处理工具

一键完成视频下载、语音识别分析、智能切片、翻译字幕、整合输出的全自动 Pipeline。
支持 YouTube / TikTok / Twitter 等平台 URL，也支持本地视频文件。

用法:
    python main.py --url <视频URL>                      # 基础：下载并处理
    python main.py --local-file <本地视频路径>           # 跳过下载，直接处理本地文件
    python main.py --summary-only-fast --url <视频URL>  # 仅抽音频 + 调用外部 Cohere 运行器生成总结
    python main.py --summary --url <视频URL>            # 正常流程结束后额外生成视频总结

视频源参数（二选一，必须提供其中之一）:
    --url <URL>              视频链接（支持 YouTube / TikTok / Twitter）
    --local-file <路径>       本地视频文件路径（跳过下载步骤）

输出控制:
    --output <目录>           输出目录（默认: output/）

切片控制:
    --min-duration <秒>       最短切片时长，单位秒（默认: 15）
    --max-duration <秒>       最长切片时长，单位秒（默认: 60）
    --max-clips <数量>        最大切片数量（默认: 5）
    --no-clip                 跳过切片，将整段视频作为单个片段处理
    --clip-strategy <策略>    切片选取策略（默认: opinion）
                                opinion — 观点驱动，提取有独立观点的片段
                                topic   — 主题/章节驱动，按话题结构切分
                                hybrid  — 混合模式，综合观点与主题

字幕相关:
    --embed-subtitles         软嵌入字幕轨道（可在播放器中开关）
    --burn-subtitles          硬烧双语字幕到画面（英文在上、中文在下）
    --subtitle-status <模式>  字幕策略（默认: auto）
                                auto — 自动检测源语言
                                en   — 源语言为英文，仅叠加中文翻译
                                zh   — 源语言为中文，保留原始字幕
                                none — 烧录双语字幕

语音识别与画质:
    --language <语言代码>     ASR 语音识别语言（默认: en）
    --quality <画质>          视频下载画质（如 1080p / 720p / best 等）

快速烧录模式（跳过 ASR/分析/翻译，直接烧字幕）:
    --burn-only               启用快速烧录模式
    --video <路径>            视频文件路径（burn-only 模式必填）
    --en-subtitle <路径>      英文字幕文件路径（可选，自动查找 <视频名>_en.srt）
    --zh-subtitle <路径>      中文字幕文件路径（可选，自动查找 <视频名>_zh.srt）

示例:
    # 基础下载 + 全流程处理
    python main.py --url "https://youtube.com/watch?v=VIDEO_ID"

    # 指定切片时长范围和最大切片数
    python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --min-duration 20 --max-duration 45 --max-clips 3

    # 硬烧双语字幕 + 使用主题切片策略
    python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --burn-subtitles --clip-strategy topic

    # 不切片，整段视频软嵌入字幕
    python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --no-clip --embed-subtitles

    # 处理本地视频文件，自定义输出目录
    python main.py --local-file ./my_video.mp4 --output ./result --burn-subtitles

    # 指定画质和 ASR 语言
    python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --quality 720p --language zh

    # 快速烧录：自动匹配同名字幕文件
    python main.py --burn-only --video video.mp4 --output Out-0403

    # 快速烧录：显式指定字幕文件
    python main.py --burn-only --video video.mp4 --en-subtitle video_en.srt --zh-subtitle video_zh.srt --output Out-0403

    # 仅生成快速视频总结（Cohere 外部运行器）
    python main.py --summary-only-fast --url "https://youtube.com/watch?v=VIDEO_ID" --output ./result
    python main.py --summary-only-fast --local-file ./my_video.mp4 --output ./result

    # 运行完整流程并额外生成视频总结
    python main.py --summary --local-file ./my_video.mp4 --output ./result
"""

import argparse
import atexit
import logging
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from datetime import datetime

from analyzer import Analyzer
from clipper import Clipper
from config import VALID_VIDEO_QUALITIES, get_config
from downloader import Downloader
from integrator import Integrator
from translator import Translator


# ─────────────────────────────────────────────────────────────
# Single-Instance Protection (PID Lock File)
# ─────────────────────────────────────────────────────────────

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOCK_DIR = os.path.join(_PROJECT_ROOT, "tmp")
LOCK_FILE = os.getenv(
    "SHORT_VIDEO_TOOL_LOCK_FILE", os.path.join(_LOCK_DIR, "short-video-tool.lock")
)


def _acquire_lock() -> None:
    """
    Acquire a PID lock file to enforce single-instance execution.

    If another instance is already running (lock file exists and PID is live),
    prints a clear error and exits immediately.
    On success, writes current PID to the lock file and registers cleanup
    via atexit + signal handlers so the lock is always released on exit.
    """
    my_pid = os.getpid()
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)

    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                existing_pid = int(f.read().strip())
            # Check if that process is still alive
            os.kill(existing_pid, 0)  # signal 0 = probe only, no kill
            # Process is alive → refuse to start
            print(
                f"\n❌ 单实例保护：另一个 pipeline 实例正在运行中！"
                f"\n   当前 PID: {my_pid}"
                f"\n   持锁 PID: {existing_pid}"
                f"\n   Lock 文件: {LOCK_FILE}"
                f"\n   项目目录: {_PROJECT_ROOT}"
                f"\n   行为: 不等待，立即退出，避免忙等待"
                f"\n\n   若确认该进程已死，请手动删除 lock 文件后重试："
                f"\n   rm {LOCK_FILE}\n",
                file=sys.stderr,
            )
            sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            # PID invalid or process dead → stale lock, overwrite it
            pass

    # Write our PID to the lock file
    with open(LOCK_FILE, "w") as f:
        f.write(str(my_pid))

    # Register cleanup: always remove lock on exit
    def _release_lock():
        try:
            if os.path.exists(LOCK_FILE):
                with open(LOCK_FILE, "r") as f:
                    if f.read().strip() == str(my_pid):
                        os.remove(LOCK_FILE)
        except Exception:
            pass

    atexit.register(_release_lock)

    # Also handle SIGTERM / SIGINT so kill/Ctrl-C also cleans up
    for sig in (signal.SIGTERM, signal.SIGINT):
        original = signal.getsignal(sig)

        def _handler(signum, frame, _orig=original):
            _release_lock()
            if callable(_orig):
                _orig(signum, frame)
            else:
                sys.exit(128 + signum)

        signal.signal(sig, _handler)


def _configure_logging(log_level: str, log_file: str) -> None:
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )


def _configure_output_dirs(config, output_root: str) -> None:
    config.output_dir = output_root
    config.analysis_dir = os.path.join(output_root, "analysis")
    config.clips_dir = os.path.join(output_root, "clips")
    config.subtitles_dir = os.path.join(output_root, "subtitles")


def _resolve_input_video(args, config) -> str | None:
    if args.local_file:
        local_path = os.path.abspath(args.local_file)
        if not os.path.exists(local_path):
            logging.error(f"Local file not found: {local_path}")
            return None
        logging.info(f"✅ Using local file: {local_path}")
        return local_path

    downloader = Downloader(output_dir=config.downloads_dir, config=config)
    download_result = downloader.download_video(url=args.url, quality=args.quality)
    if not download_result:
        logging.error("Failed to download video")
        return None

    video_path = download_result["filepath"]
    logging.info(f"✅ Downloaded: {video_path}")
    return video_path


def _run_burn_only(args, config) -> int:
    """
    快速烧录模式：跳过 ASR / 分析 / 翻译，直接将已有字幕硬烧到视频上。

    字幕文件查找逻辑（优先级从高到低）:
      1. 用户通过 --en-subtitle / --zh-subtitle 显式指定
      2. 与视频同目录下的 <视频名>_en.srt / <视频名>_zh.srt
      3. 与视频同目录下的 <视频名>.en.srt / <视频名>.zh.srt
    """
    from embed_subtitles import embed_subtitles_batch

    start_time = datetime.now()

    # ── 校验 --video ──
    if not args.video:
        print("❌ --burn-only 模式需要提供 --video 参数", file=sys.stderr)
        return 1

    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        print(f"❌ 视频文件不存在: {video_path}", file=sys.stderr)
        return 1

    video_dir = os.path.dirname(video_path)
    video_basename = os.path.splitext(os.path.basename(video_path))[0]

    # ── 查找 / 校验字幕文件 ──
    def _find_subtitle(explicit_path: str | None, lang: str) -> str | None:
        """按优先级查找字幕文件，返回绝对路径或 None。"""
        if explicit_path:
            p = os.path.abspath(explicit_path)
            if os.path.exists(p):
                return p
            print(f"⚠️  指定的 {lang} 字幕文件不存在: {p}", file=sys.stderr)
            return None
        # Auto-discover: <stem>_<lang>.srt  or  <stem>.<lang>.srt
        candidates = [
            os.path.join(video_dir, f"{video_basename}_{lang}.srt"),
            os.path.join(video_dir, f"{video_basename}.{lang}.srt"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    en_srt = _find_subtitle(args.en_subtitle, "en")
    zh_srt = _find_subtitle(args.zh_subtitle, "zh")

    if not en_srt and not zh_srt:
        print(
            f"❌ 未找到任何字幕文件。请使用 --en-subtitle / --zh-subtitle 指定，\n"
            f"   或确保视频同目录下存在 {video_basename}_en.srt / {video_basename}_zh.srt",
            file=sys.stderr,
        )
        return 1

    # ── 准备输出目录 ──
    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    # 为 embed_subtitles_batch 准备临时 clips / subtitles 目录结构
    clips_dir = os.path.join(output_dir, "_burn_clips")
    subtitles_dir = os.path.join(output_dir, "_burn_subtitles")
    final_dir = output_dir
    os.makedirs(clips_dir, exist_ok=True)
    os.makedirs(subtitles_dir, exist_ok=True)

    # ── 软链接 / 拷贝文件到工作目录 ──
    video_filename = os.path.basename(video_path)
    clip_dest = os.path.join(clips_dir, video_filename)
    if not os.path.exists(clip_dest):
        shutil.copy2(video_path, clip_dest)

    subtitle_files = {}
    if en_srt:
        en_dest = os.path.join(subtitles_dir, f"{video_basename}_en.srt")
        if not os.path.exists(en_dest):
            shutil.copy2(en_srt, en_dest)
        subtitle_files["en"] = en_dest
    if zh_srt:
        zh_dest = os.path.join(subtitles_dir, f"{video_basename}_zh.srt")
        if not os.path.exists(zh_dest):
            shutil.copy2(zh_srt, zh_dest)
        subtitle_files["zh"] = zh_dest

    # ── 构建 clips_data ──
    clips_data = [
        {
            "clip_id": "clip_1",
            "clip_path": clip_dest,
            "subtitle_files": subtitle_files,
        }
    ]

    logging.info("=" * 70)
    logging.info("Short Video Tool - 快速烧录模式 (burn-only)")
    logging.info("=" * 70)
    logging.info(f"视频: {video_path}")
    logging.info(f"EN 字幕: {en_srt or '(无)'}")
    logging.info(f"ZH 字幕: {zh_srt or '(无)'}")
    logging.info(f"字幕策略: {args.subtitle_status}")
    logging.info(f"输出目录: {output_dir}")

    # ── 调用烧录 ──
    embed_result = embed_subtitles_batch(
        clips_dir=clips_dir,
        subtitles_dir=subtitles_dir,
        output_dir=final_dir,
        burn=True,
        subtitle_status=args.subtitle_status,
        clips_data=clips_data,
    )

    # ── 清理临时工作目录 ──
    for tmp_dir in (clips_dir, subtitles_dir):
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

    # ── 结果汇报 ──
    end_time = datetime.now()
    total_time = (end_time - start_time).total_seconds()

    if embed_result and embed_result.get("successful", 0) > 0:
        logging.info("=" * 70)
        logging.info("✅ 快速烧录完成!")
        logging.info("=" * 70)
        logging.info(f"耗时: {total_time:.2f} 秒")
        logging.info(f"输出目录: {output_dir}/")
        for v in embed_result.get("videos", []):
            logging.info(f"  → {v['output']} ({v['size_mb']:.2f} MB)")
        return 0
    else:
        logging.error("❌ 快速烧录失败")
        return 1


def _run_summary_only_fast(args, config, video_path: str) -> int:
    """
    Minimal fast-summary bridge:
    1. Extract WAV audio via the main project's Analyzer
    2. Invoke /Users/jackwl/Code/Cohere-ASR/scripts/autoFull.sh
    3. Copy the generated transcript and Markdown summary back into this project
    """
    cohere_root = "/Users/jackwl/Code/Cohere-ASR"
    auto_full_script = os.path.join(cohere_root, "scripts", "autoFull.sh")

    if not os.path.exists(auto_full_script):
        logging.error("Cohere runner not found: %s", auto_full_script)
        return 1

    os.makedirs(config.analysis_dir, exist_ok=True)
    os.makedirs(os.path.join(args.output, "summary"), exist_ok=True)

    analyzer = Analyzer(config=config)
    audio_path = analyzer._extract_audio(video_path, config.analysis_dir)
    if not audio_path or not os.path.exists(audio_path):
        logging.error("Failed to extract audio for summary-only-fast")
        return 1

    runner_output_dir = os.path.join(args.output, "_cohere_runner")
    os.makedirs(runner_output_dir, exist_ok=True)

    command = [
        "/bin/zsh",
        auto_full_script,
        "--input",
        audio_path,
        "--output",
        runner_output_dir,
        "--language",
        args.language,
    ]

    logging.info("Invoking external Cohere runner: %s", auto_full_script)
    logging.info(
        "Cohere runner command: %s",
        " ".join(shlex.quote(part) for part in command),
    )
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=cohere_root,
    )

    if result.stdout:
        logging.info("Cohere runner stdout:\n%s", result.stdout.strip())
    if result.stderr:
        logging.warning("Cohere runner stderr:\n%s", result.stderr.strip())

    if result.returncode != 0:
        logging.error("Cohere runner failed with exit code %s", result.returncode)
        return 1

    generated_summary_path = os.path.join(runner_output_dir, "transcript_summary.md")
    if not os.path.exists(generated_summary_path):
        logging.error("Cohere runner did not produce summary: %s", generated_summary_path)
        return 1

    generated_transcript_path = os.path.join(runner_output_dir, "transcript.txt")
    if os.path.exists(generated_transcript_path):
        transcript_output_path = os.path.join(config.analysis_dir, "cohere_transcript.txt")
        shutil.copy2(generated_transcript_path, transcript_output_path)
        logging.info("Summary-only-fast transcript saved to: %s", transcript_output_path)
    else:
        logging.warning(
            "Cohere runner transcript not found: %s",
            generated_transcript_path,
        )

    summary_output_dir = os.path.join(args.output, "summary")
    os.makedirs(summary_output_dir, exist_ok=True)
    summary_path = os.path.join(
        summary_output_dir,
        analyzer._build_video_summary_filename(video_path),
    )
    shutil.copy2(generated_summary_path, summary_path)

    logging.info("Summary-only-fast saved to: %s", summary_path)
    return 0


def main():
    # ── Single-instance guard (must run before anything else) ──
    _acquire_lock()

    try:
        config = get_config()
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    _configure_logging(config.log_level, config.log_file)

    quality_choices = list(VALID_VIDEO_QUALITIES)
    quality_default = config.video_quality

    parser = argparse.ArgumentParser(
        description="Short Video Tool — 短视频一键处理工具\n"
                    "一键完成视频下载、语音识别分析、智能切片、翻译字幕、整合输出。\n"
                    "支持 YouTube / TikTok / Twitter 等平台 URL，也支持本地视频文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础下载 + 全流程处理
  python main.py --url "https://youtube.com/watch?v=VIDEO_ID"

  # 指定切片时长范围和最大切片数
  python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --min-duration 20 --max-duration 45 --max-clips 3

  # 硬烧双语字幕 + 使用主题切片策略
  python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --burn-subtitles --clip-strategy topic

  # 不切片，整段视频软嵌入字幕
  python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --no-clip --embed-subtitles

  # 处理本地视频文件，自定义输出目录
  python main.py --local-file ./my_video.mp4 --output ./result --burn-subtitles

  # 指定画质和 ASR 语言
  python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --quality 720p --language zh

  # 快速烧录：自动匹配同名字幕文件
  python main.py --burn-only --video video.mp4 --output Out-0403

  # 快速烧录：显式指定字幕文件
  python main.py --burn-only --video video.mp4 --en-subtitle video_en.srt --zh-subtitle video_zh.srt --output Out-0403

  # 仅生成视频总结
  python main.py --summary-only --url "https://youtube.com/watch?v=VIDEO_ID" --output ./result

  # 仅生成快速视频总结（Cohere 外部运行器）
  python main.py --summary-only-fast --url "https://youtube.com/watch?v=VIDEO_ID" --output ./result

  # 运行完整流程并额外生成视频总结
  python main.py --summary --local-file ./my_video.mp4 --output ./result
        """,
    )

    # ── 视频源参数（二选一） ──
    parser.add_argument(
        "--url",
        required=False,
        default=None,
        help="视频链接（支持 YouTube / TikTok / Twitter）",
    )
    parser.add_argument(
        "--local-file",
        default=None,
        help="本地视频文件路径（跳过下载步骤）",
    )

    # ── 输出控制 ──
    parser.add_argument(
        "--output",
        default=config.output_dir,
        help="输出目录（默认: %(default)s）",
    )

    summary_group = parser.add_mutually_exclusive_group()
    summary_group.add_argument(
        "--summary",
        action="store_true",
        help="在完整流程结束后额外生成视频总结 Markdown",
    )
    summary_group.add_argument(
        "--summary-only",
        action="store_true",
        help="仅执行 ASR + LLM 视频总结，不运行切片、翻译、整合、烧录",
    )
    summary_group.add_argument(
        "--summary-only-fast",
        action="store_true",
        help="仅抽音频并调用外部 Cohere 运行器生成快速总结，不运行完整主流程",
    )

    # ── 切片控制 ──
    parser.add_argument(
        "--min-duration",
        type=int,
        default=config.min_clip_duration,
        help="最短切片时长，单位秒（默认: %(default)s）",
    )
    parser.add_argument(
        "--max-duration",
        type=int,
        default=config.max_clip_duration,
        help="最长切片时长，单位秒（默认: %(default)s）",
    )
    parser.add_argument(
        "--max-clips",
        type=int,
        default=config.max_clips,
        help="最大切片数量（默认: %(default)s）",
    )
    parser.add_argument(
        "--no-clip",
        action="store_true",
        help="跳过切片，将整段视频作为单个片段处理",
    )
    parser.add_argument(
        "--clip-strategy",
        default="opinion",
        choices=["opinion", "topic", "hybrid"],
        help="切片选取策略: opinion=观点驱动, topic=主题/章节驱动, hybrid=混合模式（默认: %(default)s）",
    )

    # ── 字幕相关 ──
    parser.add_argument(
        "--embed-subtitles",
        action="store_true",
        help="软嵌入字幕轨道（可在播放器中开关）",
    )
    parser.add_argument(
        "--burn-subtitles",
        action="store_true",
        help="硬烧双语字幕到画面（英文在上、中文在下）",
    )
    parser.add_argument(
        "--subtitle-status",
        choices=["auto", "en", "zh", "none"],
        default="auto",
        help="字幕策略: auto=自动检测源语言, en=仅叠加中文翻译, zh=保留原始中文字幕, none=烧录双语（默认: %(default)s）",
    )

    # ── 语音识别与画质 ──
    parser.add_argument(
        "--language",
        default=config.asr_language,
        help="ASR 语音识别语言（默认: %(default)s）",
    )
    parser.add_argument(
        "--quality",
        default=quality_default,
        choices=quality_choices,
        help="视频下载画质（默认: %(default)s）",
    )

    # ── 快速烧录模式 ──
    parser.add_argument(
        "--burn-only",
        action="store_true",
        help="仅烧录字幕，跳过 ASR/分析/翻译流程（需配合 --video 使用）",
    )
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="视频文件路径（burn-only 模式必填）",
    )
    parser.add_argument(
        "--en-subtitle",
        type=str,
        default=None,
        help="英文字幕文件路径（可选，默认自动查找 <视频名>_en.srt）",
    )
    parser.add_argument(
        "--zh-subtitle",
        type=str,
        default=None,
        help="中文字幕文件路径（可选，默认自动查找 <视频名>_zh.srt）",
    )

    args = parser.parse_args()

    # ── Burn-only fast path ──────────────────────────────────────────
    if args.burn_only:
        return _run_burn_only(args, config)

    # Validate: must provide either --url or --local-file
    if not args.url and not args.local_file:
        parser.error("必须提供 --url 或 --local-file 其中之一")

    # Derive all sub-dirs from --output so every run is fully isolated
    output_root = args.output
    _configure_output_dirs(config, output_root)
    config.min_clip_duration = args.min_duration
    config.max_clip_duration = args.max_duration
    config.max_clips = args.max_clips
    config.asr_language = args.language

    logging.info("=" * 70)
    logging.info("Short Video Tool - Starting Pipeline")
    logging.info("=" * 70)
    logging.info(f"URL: {args.url or '(local file mode)'}")
    logging.info(f"Output: {args.output}")
    logging.info(f"Clip duration: {args.min_duration}-{args.max_duration} seconds")
    logging.info(f"Embed subtitles: {args.embed_subtitles}")
    logging.info(
        f"Burn subtitles: {args.burn_subtitles} (status: {args.subtitle_status})"
    )
    logging.info(f"No-clip mode: {args.no_clip}")
    logging.info(f"Language: {args.language}")
    logging.info(f"Quality: {args.quality}")

    start_time = datetime.now()

    try:
        # Step 1: Download video (or use local file)
        logging.info("\n" + "=" * 70)
        if args.summary_only or args.summary_only_fast:
            logging.info("Step 1/2: Loading input video...")
        else:
            logging.info("Step 1/5: Downloading video...")
        logging.info("=" * 70)

        video_path = _resolve_input_video(args, config)
        if not video_path:
            return 1

        if args.summary_only_fast:
            logging.info("\n" + "=" * 70)
            logging.info("Step 2/2: Generating fast video summary...")
            logging.info("=" * 70)

            status = _run_summary_only_fast(args, config, video_path)
            if status != 0:
                return status

            end_time = datetime.now()
            total_time = (end_time - start_time).total_seconds()
            logging.info("=" * 70)
            logging.info("Summary-only-fast Complete!")
            logging.info("=" * 70)
            logging.info(f"Total time: {total_time:.2f} seconds")
            logging.info(f"Output directory: {args.output}/")
            logging.info(f"Summary directory: {os.path.join(args.output, 'summary')}")
            logging.info(f"Log file: {config.log_file}")
            return 0

        if args.summary_only:
            analyzer = Analyzer(config=config)
            logging.info("\n" + "=" * 70)
            logging.info("Step 2/2: Generating video summary...")
            logging.info("=" * 70)

            summary_analysis_result = analyzer.analyze_video_for_summary(
                video_path=video_path,
                output_dir=config.analysis_dir,
            )

            if not summary_analysis_result:
                logging.error("Failed to generate summary-only analysis")
                return 1

            summary_output_dir = os.path.join(args.output, "summary")
            summary_path = analyzer.generate_video_summary(
                analysis_result=summary_analysis_result,
                output_dir=summary_output_dir,
                video_path=video_path,
            )
            if not summary_path:
                return 1

            end_time = datetime.now()
            total_time = (end_time - start_time).total_seconds()
            logging.info("=" * 70)
            logging.info("Summary-only Complete!")
            logging.info("=" * 70)
            logging.info(f"Total time: {total_time:.2f} seconds")
            logging.info(f"Output directory: {args.output}/")
            logging.info(f"Video summary: {summary_path}")
            logging.info(f"Log file: {config.log_file}")
            return 0

        # Step 2: Analyze video
        logging.info("\n" + "=" * 70)
        logging.info("Step 2/5: Analyzing video...")
        logging.info("=" * 70)

        # Skip topic segmentation if --no-clip is specified
        if args.no_clip:
            logging.info("--no-clip mode: skipping topic segmentation (not needed for full video)")
            # Temporarily disable topic segmentation for this analysis
            original_enable_topic = config.enable_topic_segmentation
            config.enable_topic_segmentation = False

        analyzer = Analyzer(config=config)

        analysis_result = analyzer.analyze_video(
            video_path=video_path,
            output_dir=config.analysis_dir,
            clip_strategy=args.clip_strategy,
        )

        # Restore original setting
        if args.no_clip:
            config.enable_topic_segmentation = original_enable_topic

        if not analysis_result:
            logging.error("Failed to analyze video")
            return 1

        analysis_path = os.path.join(config.analysis_dir, "analysis_result.json")
        logging.info(f"✅ Analysis complete: {analysis_path}")

        # Step 3: Clip video
        logging.info("\n" + "=" * 70)
        logging.info("Step 3/5: Clipping video...")
        logging.info("=" * 70)

        clips_metadata_path = os.path.join(config.clips_dir, "clips_metadata.json")

        if args.no_clip:
            # --no-clip: skip clipper, treat entire video as single clip
            logging.info(
                "--no-clip: skipping clip step, using full video as single clip"
            )
            os.makedirs(config.clips_dir, exist_ok=True)
            video_filename = os.path.basename(video_path)
            clip_dest = os.path.join(config.clips_dir, video_filename)
            if not os.path.exists(clip_dest):
                shutil.copy2(video_path, clip_dest)
            # get duration via ffprobe (analysis_result does not store duration)
            try:
                import subprocess as _sp, json as _json

                _probe = _sp.run(
                    [
                        "ffprobe",
                        "-v",
                        "quiet",
                        "-print_format",
                        "json",
                        "-show_format",
                        video_path,
                    ],
                    capture_output=True,
                    text=True,
                )
                duration = float(
                    _json.loads(_probe.stdout).get("format", {}).get("duration", 0)
                )
            except Exception:
                duration = 0.0
            asr_segments = analysis_result.get("asr_result", [])
            no_clip_metadata = {
                "clips": [
                    {
                        "clip_id": "clip_1",
                        "filename": video_filename,
                        "filepath": clip_dest,
                        "clip_path": clip_dest,
                        "start_time": 0,
                        "end_time": duration,
                        "duration": duration,
                        "score": 1.0,
                        "asr_segments": asr_segments,
                        "asr_subset": asr_segments,
                    }
                ]
            }
            with open(clips_metadata_path, "w", encoding="utf-8") as f:
                json.dump(no_clip_metadata, f, ensure_ascii=False, indent=2)
            logging.info(f"✅ No-clip mode: 1 clip (full video, {duration:.1f}s)")
        else:
            clipper = Clipper(
                min_duration=args.min_duration,
                max_duration=args.max_duration,
                max_clips=args.max_clips,
                config=config,
            )
            clip_result = clipper.clip_video(
                video_path=video_path,
                analysis_result=analysis_result,
                output_dir=config.clips_dir,
            )

            if not clip_result or not clip_result["clips"]:
                logging.error("Failed to clip video or no clips generated")
                return 1

            logging.info(
                f"✅ Clipping complete: {len(clip_result['clips'])} clips created"
            )

        # Step 4: Translate and generate subtitles
        logging.info("\n" + "=" * 70)
        logging.info("Step 4/5: Translating and generating subtitles...")
        logging.info("=" * 70)

        config.subtitles_dir = os.path.join(args.output, "subtitles")
        translator = Translator(config=config)
        translation_result = translator.translate_clips(
            clips_metadata_path=clips_metadata_path, output_dir=config.subtitles_dir
        )

        if not translation_result:
            logging.error("Failed to translate clips")
            return 1

        translations_metadata_path = os.path.join(
            config.subtitles_dir, "translations_metadata.json"
        )
        logging.info(
            f"✅ Translation complete: {len(translation_result['clips'])} clips translated"
        )

        # Step 5: Integrate outputs
        logging.info("\n" + "=" * 70)
        logging.info("Step 5/5: Integrating outputs...")
        logging.info("=" * 70)

        integrator = Integrator(output_dir=args.output, config=config)
        integration_result = integrator.integrate(
            video_path=video_path,
            analysis_result_path=analysis_path,
            clips_metadata_path=clips_metadata_path,
            translations_metadata_path=translations_metadata_path,
        )

        if not integration_result:
            logging.error("Failed to integrate outputs")
            return 1

        logging.info(f"✅ Integration complete: {args.output}/")

        # Optional: Embed subtitles
        if args.embed_subtitles or args.burn_subtitles:
            burn = args.burn_subtitles
            mode_name = "hard-burn bilingual" if burn else "soft-embed"
            logging.info("\n" + "=" * 70)
            logging.info(f"Optional: Embedding subtitles [{mode_name}]...")
            logging.info("=" * 70)

            from embed_subtitles import embed_subtitles_batch

            embed_result = embed_subtitles_batch(
                clips_dir=os.path.join(args.output, "clips"),
                subtitles_dir=os.path.join(args.output, "subtitles"),
                output_dir=os.path.join(args.output, "clips_with_subtitles"),
                burn=burn,
                subtitle_status=args.subtitle_status,
                asr_segments=analysis_result.get("asr_result"),
                clips_data=translation_result.get("clips", []),
            )

            if embed_result:
                logging.info(
                    f"✅ Subtitles embedded: {embed_result['successful']}/{embed_result['total_processed']} videos"
                )
            else:
                logging.warning("⚠️ Failed to embed subtitles")

        summary_path = None
        if args.summary:
            logging.info("\n" + "=" * 70)
            logging.info("Optional: Generating video summary...")
            logging.info("=" * 70)
            summary_output_dir = os.path.join(args.output, "summary")
            summary_path = analyzer.generate_video_summary(
                analysis_result=analysis_result,
                output_dir=summary_output_dir,
                video_path=video_path,
            )
            if summary_path:
                logging.info(f"✅ Video summary generated: {summary_path}")
            else:
                logging.warning("⚠️ Failed to generate video summary")

        # Summary
        end_time = datetime.now()
        total_time = (end_time - start_time).total_seconds()

        logging.info("\n" + "=" * 70)
        logging.info("Pipeline Complete!")
        logging.info("=" * 70)
        logging.info(f"Total time: {total_time:.2f} seconds")
        logging.info(f"Output directory: {args.output}/")
        logging.info(f"Summary report: {args.output}/summary.md")
        if summary_path:
            logging.info(f"Video summary: {summary_path}")
        logging.info(f"Log file: {config.log_file}")

        return 0

    except KeyboardInterrupt:
        logging.warning("\n\nPipeline interrupted by user")
        return 1
    except Exception as e:
        logging.error(f"\n\nPipeline failed with error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    main()
