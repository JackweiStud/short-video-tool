# 分析模块 (Analyzer) 文档

## 功能概述

分析模块负责把视频拆成“可理解的结构化信号”，供后续切片、翻译和字幕处理使用。
它会输出语音转写结果、音频高潮点、场景切换点，以及主题分段等信息。

## 核心功能

### F2.1 - 语音转文字 (ASR)

- 优先使用 `faster-whisper`
- 如果 `faster-whisper` 不可用，会回退到 `openai-whisper` 提供的 `whisper` CLI
- 支持长音频分段处理、缓存、超时控制和重跑续传
- 支持词级时间戳，为字幕精对齐提供基础数据

### F2.2 - 音频分析

- 使用 `librosa` 提取音频特征
- 结合 RMS、频谱质心、过零率识别音频高潮点
- 输出时间点和评分，供 `clipper.py` 参考

### F2.3 - 场景检测

- 使用 `PySceneDetect` 检测画面切换
- 输出场景边界时间点，帮助切片避开突兀硬切

### F2.4 - 主题分段

- 默认启用主题分段
- 使用 LLM 根据 ASR 文本生成章节边界
- 输出 `start`、`end`、`topic`、`summary`、`score`、`reason`
- `clipper.py` 会优先使用这些主题段做切片

### F2.5 - D+B 精准字幕同步

- `Direct`：来自 Whisper 词级时间戳
- `Boundary`：来自字幕检测和视觉边界
- 结合两者减少硬字幕偏移和覆盖不准的问题

## 使用方式

### 基本示例

```python
from analyzer import Analyzer

analyzer = Analyzer()
result = analyzer.analyze(
    video_path="downloads/video.mp4",
    output_dir="analysis_results",
)

print(len(result["asr_result"]))
print(len(result["audio_climax_points"]))
print(len(result["scene_changes"]))
```

### 输入

- `video_path`：待分析的视频文件
- `output_dir`：分析结果目录，常见为 `analysis_results/`

### 输出

返回字典通常包含：

- `video_path`
- `asr_result`
- `audio_climax_points`
- `scene_changes`
- `topic_segments`
- `topic_summaries`

其中 `asr_result` 的每个 segment 可能包含：

- `start`
- `end`
- `text`
- `words`：当开启词级时间戳时可用

## 当前实现要点

### ASR 优先级

1. `faster-whisper`
2. `whisper` CLI fallback

### Whisper CLI 定位方式

`Analyzer` 会按下面顺序查找：

1. `WHISPER_CLI_PATH`
2. `PATH` 里的 `whisper`
3. 常见安装位置，例如 `/opt/homebrew/bin/whisper`

### 长视频处理

- 默认按段处理，避免超长音频一次性转写
- 每段带 overlap，减少边界漏词
- 已完成分段会进入 `cache/asr/`，重复运行时可复用

## 常用环境变量

- `WHISPER_MODEL`
- `WHISPER_WORD_TIMESTAMPS`
- `WHISPER_CLI_PATH`
- `ASR_LANGUAGE`
- `ASR_CHUNK_DURATION`
- `ASR_OVERLAP_SECONDS`
- `ASR_SEGMENT_TIMEOUT`
- `ASR_CACHE_DIR`
- `FASTER_WHISPER_LOCAL_MODEL_DIR`
- `SCENE_DETECTION_THRESHOLD`

## 已知限制

1. 默认语言仍是英文，处理中文视频时建议显式传 `--language zh`
2. 主题分段依赖可用的 LLM 配置，未配置时会退回到非语义切片
3. `whisper` CLI fallback 仍依赖本机存在可执行的 `whisper`
4. CPU 模式下长视频分析耗时会明显增加

## 推荐验证方式

Analyzer 不再依赖旧的 `verify_analyzer.py`。
当前更稳妥的验证方式是：

```bash
python main.py --help
python -m pytest -q tests/test_config_integration.py tests/test_main_lock.py
```

如果你要做业务验证，建议直接跑一个短视频样例：

```bash
python main.py --local-file "./sample.mp4"
```

## 依赖

- `faster-whisper`
- `openai-whisper`
- `librosa`
- `scenedetect`
- `opencv-python`
- `numpy`
- `scipy`
- `ffmpeg`
