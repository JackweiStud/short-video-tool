#!/usr/bin/env bash
# burn_full_video.sh — 快速烧录双语字幕到全长视频
# 使用 imageio-ffmpeg (内置 libass)，不依赖系统 ffmpeg
# 用法: bash burn_full_video.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Prefer an explicit override, otherwise use ffmpeg from PATH.
FFMPEG="${FFMPEG_BIN:-$(command -v ffmpeg || true)}"
VIDEO="downloads/Don't Sell AI Automation, Sell OpenClaw Swarms Instead (entire strategy).mp4"
EN_SRT="subtitles/full_video_en.srt"
ZH_SRT="subtitles/full_video_zh.srt"
TMP_STEP1="/tmp/full_en_burned.mp4"
OUT="output/Don't Sell AI Automation, Sell OpenClaw Swarms Instead (entire strategy)_full_bilingual.mp4"

if [ -z "$FFMPEG" ]; then
    echo "❌ 找不到 ffmpeg，请先安装 ffmpeg 或设置 FFMPEG_BIN"
    exit 1
fi

echo "========================================"
echo " 全长双语字幕视频 - FFmpeg 烧录"
echo "========================================"
echo "Source : $VIDEO"
echo "Output : $OUT"
echo ""

# 检查素材
if [ ! -f "$EN_SRT" ] || [ ! -f "$ZH_SRT" ]; then
    echo "❌ SRT 文件不存在，请先运行翻译步骤"
    exit 1
fi
EN_LINES=$(wc -l < "$EN_SRT")
ZH_LINES=$(wc -l < "$ZH_SRT")
echo "EN SRT : $EN_LINES 行"
echo "ZH SRT : $ZH_LINES 行"
echo ""

# Step 1: 烧录英文字幕（白色，MarginV=30 在下方偏上）
echo "Step 1/2: 烧录英文字幕 (白色)..."
START1=$(date +%s)
"$FFMPEG" -y -i "$VIDEO" \
  -vf "subtitles=${EN_SRT}:force_style='Fontname=Arial,Fontsize=12,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=0.8,Alignment=2,MarginV=30'" \
  -c:v libx264 -preset fast -crf 18 \
  -c:a copy "$TMP_STEP1"
END1=$(date +%s)
echo "Step 1 完成 ($(( END1-START1 ))s): $(du -h $TMP_STEP1 | cut -f1)"
echo ""

# Step 2: 叠加中文字幕（黄色，MarginV=10 在最底部）
echo "Step 2/2: 烧录中文字幕 (黄色)..."
START2=$(date +%s)
"$FFMPEG" -y -i "$TMP_STEP1" \
  -vf "subtitles=${ZH_SRT}:force_style='Fontname=Arial,Fontsize=14,PrimaryColour=&H0000FFFF&,OutlineColour=&H000000&,Outline=1,Alignment=2,MarginV=10'" \
  -c:v libx264 -preset fast -crf 18 \
  -c:a copy -movflags +faststart "$OUT"
END2=$(date +%s)
echo "Step 2 完成 ($(( END2-START2 ))s)"
echo ""

# 清理临时文件
rm -f "$TMP_STEP1"

# 结果
if [ -f "$OUT" ]; then
    SIZE=$(du -h "$OUT" | cut -f1)
    echo "========================================"
    echo "✅ 完成!"
    echo "文件 : $OUT"
    echo "大小 : $SIZE"
    echo "耗时 : $(( END2-START1 ))s"
    echo "========================================"
    # 用 macOS open 打开 Finder 定位文件
    open -R "$OUT" 2>/dev/null || true
else
    echo "❌ 输出文件不存在，请检查错误"
    exit 1
fi
