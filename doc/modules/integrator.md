# 整合与输出模块 (Integrator) 文档

## 功能概述

整合模块负责把下载、分析、切片和翻译结果整理到统一目录下，并生成最终摘要文件。

## 核心功能

### F5.1 - 统一目录结构

输出目录通常包含：

```text
output/
├── original/
├── clips/
├── subtitles/
├── analysis/
├── clips_with_subtitles/
├── integration_metadata.json
└── summary.md
```

其中 `clips_with_subtitles/` 是可选目录，只在字幕嵌入或烧录时生成。

### F5.2 - 文件整理

- 复制原始视频
- 复制分析结果
- 复制切片和字幕文件
- 保持文件之间的关联关系

### F5.3 - 生成摘要

- 输出 `summary.md`
- 输出 `integration_metadata.json`
- 记录处理时间、片段数量、字幕数量等信息

## 使用方式

```python
from integrator import Integrator

integrator = Integrator(output_dir="output")
result = integrator.integrate(
    video_path="downloads/video.mp4",
    analysis_result_path="analysis_results/analysis_result.json",
    clips_metadata_path="clips/clips_metadata.json",
    translations_metadata_path="subtitles/translations_metadata.json",
)

print(result["output_dir"])
```

## 输出

整合结果通常包含：

- `video_title`
- `original_video`
- `analysis_result`
- `clips`
- `statistics`
- `summary_report`

## 当前实现特点

- 使用 `shutil.copy2` 保留文件元数据
- 自动创建目录结构
- 在主流程结束时作为统一收口步骤使用

## 推荐验证方式

旧的 `verify_integrator.py` 已不在当前仓库中。
建议直接跑主流程后检查：

- `output/summary.md`
- `output/integration_metadata.json`
- 目录结构是否完整

## 依赖

- Python 标准库
