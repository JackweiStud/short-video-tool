# 剪辑模块 (Clipper) 文档

## 功能概述

剪辑模块负责根据分析结果挑出“适合发布”的片段，并调用 `ffmpeg` 生成短视频文件。

## 核心功能

### F3.1 - 智能识别关键片段

- 优先使用主题分段结果
- 结合分段分数、时长和 ASR 覆盖度排序
- 长主题段会尽量按 ASR 边界拆分，减少切到句子中间
- 没有主题段时回退到音频高潮点和场景切换点
- 自动去重和去重叠

### F3.2 - 自动剪辑生成片段

- 使用 `ffmpeg`
- 输出可直接用于翻译和烧录的 `clips_metadata.json`
- 为每个片段生成对应的 `asr_subset`

### F3.3 - 控制片段时长

- `Clipper` 类单独使用时，默认是 `15-60` 秒
- 从 `main.py` 进入时，默认使用配置层的 `60-180` 秒
- 最终以传入参数或配置为准

## 使用方式

```python
from clipper import Clipper

clipper = Clipper(min_duration=20, max_duration=45)
result = clipper.clip_video(
    video_path="downloads/video.mp4",
    analysis_result=analysis_result,
    output_dir="clips",
)

print(len(result["clips"]))
```

## 输出

返回结果通常包含：

- `original_video`
- `clips`
  - `clip_path`
  - `start_time`
  - `end_time`
  - `duration`
  - `score`
  - `asr_subset`

同时会写出：

- `clips/clips_metadata.json`

## 实现特点

- 使用重编码而不是简单 `copy`，优先保证切点精度和音画同步
- 会把 ASR 文本裁剪到片段范围内，供翻译模块直接消费
- 会处理主题段过长、片段过短和尾段合并等边界

## 推荐验证方式

旧的 `verify_clipper.py` 已不在当前仓库中。
建议使用：

```bash
python main.py --local-file "./sample.mp4"
```

跑完后检查：

- `clips/`
- `clips/clips_metadata.json`
- 输出片段时长是否符合预期

## 依赖

- `ffmpeg`
- Python 标准库
