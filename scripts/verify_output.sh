#!/bin/bash

echo "=========================================="
echo "验证完整视频产物"
echo "=========================================="

# 1. 源视频信息
echo -e "\n1. 源视频信息:"
SRC=$(find downloads -name '*GTC*.mp4' | head -1)
ffprobe -v error -show_entries stream=width,height,codec_name -show_entries format=duration,size -of default=noprint_wrappers=1 "$SRC" 2>&1 | grep -E "(width|height|duration|size|codec_name)"

# 2. 输出视频信息
echo -e "\n2. 输出视频信息 (Auto Strategy):"
ffprobe -v error -show_entries stream=width,height,codec_name -show_entries format=duration,size -of default=noprint_wrappers=1 output/full_video_bilingual_auto.mp4 2>&1 | grep -E "(width|height|duration|size|codec_name)"

# 3. 几何保真验证
echo -e "\n3. 几何保真验证:"
SRC_RES=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$SRC" 2>&1)
OUT_RES=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 output/full_video_bilingual_auto.mp4 2>&1)
echo "  源视频分辨率: $SRC_RES"
echo "  输出视频分辨率: $OUT_RES"
if [ "$SRC_RES" = "$OUT_RES" ]; then
    echo "  ✅ 几何保真验证通过"
else
    echo "  ❌ 几何保真验证失败"
fi

# 4. 抽帧对比
echo -e "\n4. 抽帧对比 (t=100s):"
ffmpeg -y -ss 100 -i "$SRC" -vframes 1 -update 1 -q:v 2 debug_frames/source_frame_100s.jpg 2>&1 | tail -1
ffmpeg -y -ss 100 -i output/full_video_bilingual_auto.mp4 -vframes 1 -update 1 -q:v 2 debug_frames/output_auto_frame_100s.jpg 2>&1 | tail -1
ls -lh debug_frames/*_frame_100s.jpg | awk '{print "  " $9 ": " $5}'

echo -e "\n=========================================="
echo "验证完成"
echo "=========================================="
