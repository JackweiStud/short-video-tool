#!/bin/bash

echo "=========================================="
echo "紧凑间距验证 (1.05x)"
echo "=========================================="

# 1. 边界检测日志
echo -e "\n1. 新的间距计算:"
source venv/bin/activate
python3 << 'PYEOF'
from subtitle_detect import detect_subtitle_boundary
import subprocess

result = subprocess.run(["find", "downloads", "-name", "*GTC*.mp4"], capture_output=True, text=True)
video_path = result.stdout.strip().split('\n')[0]

boundary = detect_subtitle_boundary(video_path, sample_count=5)
print(f"  英文字幕上边界: {boundary:.3f} ({boundary*100:.1f}% from top)")

# Calculate Chinese position with 1.05x offset
zh_font_size = 720 * 0.04  # Assuming 720p height
text_height = zh_font_size * 1.2  # Approximate
offset = text_height * 1.05  # Changed from 1.3 to 1.05
zh_position = boundary - (offset / 720)
zh_position = max(0.5, zh_position)

print(f"  中文字幕位置: {zh_position:.3f} ({zh_position*100:.1f}% from top)")
print(f"  字幕间距: {(boundary - zh_position)*100:.1f}% (1.05x 偏移)")
PYEOF

# 2. 抽帧
echo -e "\n2. 抽取新版本帧 (t=50s)..."
ffmpeg -y -ss 50 -i output/full_video_bilingual_auto.mp4 -vframes 1 -update 1 -q:v 2 debug_frames/output_tight_frame_50s.jpg 2>&1 | tail -1

# 3. 区域分析
echo -e "\n3. 字幕区域分析:"
python3 << 'PYEOF'
from PIL import Image

img = Image.open('debug_frames/output_tight_frame_50s.jpg')
w, h = img.size

regions = {
    "上部 (60%-65%)": (0.6, 0.65),
    "中文区 (65%-70%)": (0.65, 0.7),
    "英文区 (70%-75%)": (0.7, 0.75),
    "底部 (75%-80%)": (0.75, 0.8),
}

print(f"分辨率: {w}x{h}")
print("\n像素密度分布:")
for name, (start, end) in regions.items():
    region = img.crop((0, int(h * start), w, int(h * end)))
    pixels = list(region.getdata())
    non_black = sum(1 for p in pixels if sum(p) > 100)
    ratio = non_black / len(pixels)
    print(f"  {name}: {ratio*100:.1f}%")

# Save comparison
tight_region = img.crop((0, int(h * 0.65), w, int(h * 0.75)))
tight_region.save('debug_frames/output_tight_subtitle_region.jpg')

print("\n✅ 已保存紧凑间距区域截图")
PYEOF

# 4. 对比图 (1.3x vs 1.05x)
echo -e "\n4. 生成间距对比图..."
python3 << 'PYEOF'
from PIL import Image, ImageDraw, ImageFont

# Load two versions
smart_img = Image.open('debug_frames/output_smart_frame_50s.jpg')  # 1.3x version
tight_img = Image.open('debug_frames/output_tight_frame_50s.jpg')  # 1.05x version

w, h = tight_img.size

# Create side-by-side comparison
comparison = Image.new('RGB', (w * 2, h))
comparison.paste(smart_img, (0, 0))
comparison.paste(tight_img, (w, 0))

# Add labels
draw = ImageDraw.Draw(comparison)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
except:
    font = ImageFont.load_default()

draw.text((20, 20), "1.3x offset (6.2%)", fill=(255, 255, 0), font=font)
draw.text((w + 20, 20), "1.05x offset (tight)", fill=(255, 255, 0), font=font)

comparison.save('debug_frames/spacing_comparison.jpg')

print("✅ 已保存间距对比图: debug_frames/spacing_comparison.jpg")
PYEOF

echo -e "\n=========================================="
echo "验证完成"
echo "=========================================="
