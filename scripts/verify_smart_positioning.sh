#!/bin/bash

echo "=========================================="
echo "智能字幕定位验证"
echo "=========================================="

# 1. 边界检测日志
echo -e "\n1. 边界检测结果:"
source venv/bin/activate
python3 << 'PYEOF'
from subtitle_detect import detect_subtitle_boundary
import subprocess

result = subprocess.run(["find", "downloads", "-name", "*GTC*.mp4"], capture_output=True, text=True)
video_path = result.stdout.strip().split('\n')[0]

boundary = detect_subtitle_boundary(video_path, sample_count=5)
print(f"  检测到的英文字幕上边界: {boundary:.3f} ({boundary*100:.1f}% from top)")

# Calculate Chinese position
zh_font_size = 720 * 0.04  # Assuming 720p height
text_height = zh_font_size * 1.2  # Approximate
offset = text_height * 1.3
zh_position = boundary - (offset / 720)
zh_position = max(0.5, zh_position)

print(f"  计算的中文字幕位置: {zh_position:.3f} ({zh_position*100:.1f}% from top)")
print(f"  字幕间距: {(boundary - zh_position)*100:.1f}%")
PYEOF

# 2. 抽帧对比
echo -e "\n2. 抽取新版本帧 (t=50s)..."
ffmpeg -y -ss 50 -i output/full_video_bilingual_auto.mp4 -vframes 1 -update 1 -q:v 2 debug_frames/output_smart_frame_50s.jpg 2>&1 | tail -1

# 3. 区域分析
echo -e "\n3. 字幕区域分析:"
python3 << 'PYEOF'
from PIL import Image

img = Image.open('debug_frames/output_smart_frame_50s.jpg')
w, h = img.size

regions = {
    "上部 (50%-60%)": (0.5, 0.6),
    "中上 (60%-70%)": (0.6, 0.7),
    "中下 (70%-80%)": (0.7, 0.8),
    "底部 (80%-90%)": (0.8, 0.9),
}

print(f"分辨率: {w}x{h}")
print("\n像素密度分布:")
for name, (start, end) in regions.items():
    region = img.crop((0, int(h * start), w, int(h * end)))
    pixels = list(region.getdata())
    non_black = sum(1 for p in pixels if sum(p) > 100)
    ratio = non_black / len(pixels)
    marker = " ← 中文字幕" if 0.6 <= start < 0.7 else (" ← 英文字幕" if 0.7 <= start < 0.9 else "")
    print(f"  {name}: {ratio*100:.1f}%{marker}")

# Save regions
zh_region = img.crop((0, int(h * 0.6), w, int(h * 0.7)))
en_region = img.crop((0, int(h * 0.7), w, int(h * 0.9)))
zh_region.save('debug_frames/output_smart_chinese_region.jpg')
en_region.save('debug_frames/output_smart_english_region.jpg')

print("\n✅ 已保存区域截图")
PYEOF

# 4. 对比图
echo -e "\n4. 生成三版本对比图..."
python3 << 'PYEOF'
from PIL import Image, ImageDraw, ImageFont

# Load three versions
old_img = Image.open('debug_frames/output_auto_frame_50s.jpg')  # 60% version
fixed_img = Image.open('debug_frames/output_fixed_frame_50s.jpg')  # 75% version
smart_img = Image.open('debug_frames/output_smart_frame_50s.jpg')  # Smart version

w, h = smart_img.size

# Create side-by-side comparison
comparison = Image.new('RGB', (w * 3, h))
comparison.paste(old_img, (0, 0))
comparison.paste(fixed_img, (w, 0))
comparison.paste(smart_img, (w * 2, 0))

# Add labels
draw = ImageDraw.Draw(comparison)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
except:
    font = ImageFont.load_default()

draw.text((20, 20), "V1: 60%", fill=(255, 255, 0), font=font)
draw.text((w + 20, 20), "V2: 75%", fill=(255, 255, 0), font=font)
draw.text((w * 2 + 20, 20), "V3: Smart", fill=(255, 255, 0), font=font)

comparison.save('debug_frames/subtitle_position_evolution.jpg')

print("✅ 已保存三版本对比图: debug_frames/subtitle_position_evolution.jpg")
PYEOF

echo -e "\n=========================================="
echo "验证完成"
echo "=========================================="
