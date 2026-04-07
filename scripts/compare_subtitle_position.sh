#!/bin/bash

echo "=========================================="
echo "中文字幕位置调整对比"
echo "=========================================="

# 抽取新版本帧 (t=50s)
echo -e "\n1. 抽取新版本帧 (t=50s)..."
ffmpeg -y -ss 50 -i output/full_video_bilingual_auto.mp4 -vframes 1 -update 1 -q:v 2 debug_frames/output_fixed_frame_50s.jpg 2>&1 | tail -1

# 对比分析
echo -e "\n2. 位置对比分析..."
source venv/bin/activate
python3 << 'PYEOF'
from PIL import Image, ImageDraw, ImageFont

# Load old and new frames
old_img = Image.open('debug_frames/output_auto_frame_50s.jpg')
new_img = Image.open('debug_frames/output_fixed_frame_50s.jpg')

w, h = new_img.size

print(f"分辨率: {w}x{h}")

# Analyze different regions
regions = {
    "上部 (50%-60%)": (0.5, 0.6),
    "中部 (60%-70%)": (0.6, 0.7),
    "中下 (70%-80%)": (0.7, 0.8),
    "底部 (80%-90%)": (0.8, 0.9),
}

print("\n旧版本（60%位置）像素密度:")
for name, (start, end) in regions.items():
    region = old_img.crop((0, int(h * start), w, int(h * end)))
    pixels = list(region.getdata())
    non_black = sum(1 for p in pixels if sum(p) > 100)
    ratio = non_black / len(pixels)
    print(f"  {name}: {ratio*100:.1f}%")

print("\n新版本（75%位置）像素密度:")
for name, (start, end) in regions.items():
    region = new_img.crop((0, int(h * start), w, int(h * end)))
    pixels = list(region.getdata())
    non_black = sum(1 for p in pixels if sum(p) > 100)
    ratio = non_black / len(pixels)
    print(f"  {name}: {ratio*100:.1f}%")

# Save comparison regions
new_top = new_img.crop((0, int(h * 0.7), w, int(h * 0.8)))
new_bottom = new_img.crop((0, int(h * 0.8), w, int(h * 0.9)))
new_top.save('debug_frames/output_fixed_chinese_region.jpg')
new_bottom.save('debug_frames/output_fixed_english_region.jpg')

# Create side-by-side comparison
comparison = Image.new('RGB', (w * 2, h))
comparison.paste(old_img, (0, 0))
comparison.paste(new_img, (w, 0))

# Add labels
draw = ImageDraw.Draw(comparison)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
except:
    font = ImageFont.load_default()

draw.text((20, 20), "Before (60%)", fill=(255, 255, 0), font=font)
draw.text((w + 20, 20), "After (75%)", fill=(255, 255, 0), font=font)

comparison.save('debug_frames/subtitle_position_comparison.jpg')

print("\n✅ 已保存对比图: debug_frames/subtitle_position_comparison.jpg")
PYEOF

echo -e "\n=========================================="
echo "对比完成"
echo "=========================================="
