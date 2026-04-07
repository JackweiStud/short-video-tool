# 翻译模块 (Translator) 文档

## 功能概述

翻译模块负责把切片后的 ASR 文本转换成多语言字幕，并生成标准 SRT 文件。

## 核心功能

### F4.1 - 生成英文字幕

- 如果原文已经是英文，会尽量短路复用原文
- 否则翻译成英文并保留时间戳

### F4.2 - 生成中文字幕

- 根据英文结果或原文结果生成简体中文
- 保持和片段起点对齐的时间戳

### F4.3 - 输出标准字幕文件

- 原始语言：`*_original.srt`
- 英文：`*_en.srt`
- 中文：`*_zh.srt`

## 后端优先级

当 `TRANSLATION_BACKEND=auto` 时，当前实现按下面顺序选择：

1. `siliconflow`
2. `openai`
3. `googletrans`

## 常用配置

- `TRANSLATION_BACKEND`
- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_API_KEY`
- `OPENAI_API_KEY`

## 使用方式

```python
from translator import Translator

translator = Translator()
result = translator.translate_clips(
    clips_metadata_path="clips/clips_metadata.json",
    output_dir="subtitles",
)

print(result["clips"][0]["subtitle_files"])
```

## 输出

翻译完成后通常会生成：

- `subtitles/<clip_name>_original.srt`
- `subtitles/<clip_name>_en.srt`
- `subtitles/<clip_name>_zh.srt`
- `subtitles/translations_metadata.json`

## 当前实现特点

- 支持批量翻译
- 会尽量避免语言漂移
- 当高级后端不可用时自动降级到免费后端
- 结果可以直接交给字幕嵌入或硬烧录流程

## 推荐验证方式

旧的 `verify_translator.py` 已不在当前仓库中。
建议通过主流程验证：

```bash
python main.py --local-file "./sample.mp4"
```

跑完后检查：

- `subtitles/`
- `subtitles/translations_metadata.json`
- 字幕语言和时间戳是否正常

## 依赖

- `deep-translator`
- `openai`
- `requests`
