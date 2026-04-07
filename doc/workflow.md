# 跨平台短视频智能剪辑与翻译工具流程图

`main.py` 是这个项目的核心入口。
你给它一个公开视频链接，或者一个本地视频文件，它会按固定流水线完成下载、分析、切片、翻译、字幕生成和结果整合。

## 完整流程

```mermaid
graph TB
    Start([开始：URL 或本地视频]) --> Input[输入源<br/>YouTube / TikTok / X(Twitter) / Local File]

    Input --> Download[模块 1：下载<br/>yt-dlp + 元数据提取]
    Download --> Analyze[模块 2：分析<br/>ASR / 音频高潮 / 场景检测 / 字幕边界]
    Analyze --> Clip[模块 3：切片<br/>主题段优先，回退高潮点和场景点]
    Clip --> Translate[模块 4：翻译与字幕<br/>英文 / 中文 / 原文字幕]
    Translate --> Integrate[模块 5：整合输出<br/>统一目录 + 摘要报告]
    Integrate --> SubtitleOpt{可选字幕处理}

    SubtitleOpt -->|软嵌| Embed[嵌入字幕轨道]
    SubtitleOpt -->|硬烧| Burn[烧录双语字幕]
    SubtitleOpt -->|跳过| Output[输出结果]

    Embed --> Output
    Burn --> Output

    Output --> Result1[剪辑视频]
    Output --> Result2[字幕文件]
    Output --> Result3[可分享成片]
    Output --> Result4[summary.md 和 integration_metadata.json]
```

## 当前核心链路

### 1. 下载

- 使用 `yt-dlp`
- 支持 YouTube、TikTok、X/Twitter
- X/Twitter 默认通过 Chrome cookies 复用本机登录态

### 2. 分析

- 优先使用 `faster-whisper`
- 不可用时回退到 `whisper` CLI
- 结合音频高潮点、场景检测和字幕边界
- 支持 D+B 字幕同步：词级时间戳 + 视觉边界

### 3. 切片

- 优先使用主题分段结果
- 回退到音频高潮点 + 场景切换点
- 自动裁剪到可发布的片段时长

### 4. 翻译

- 默认 `auto` 后端
- 优先 `siliconflow`，其次 `openai`，最后 `googletrans`
- 生成原文、英文、中文字幕

### 5. 输出

- 整理原视频、片段、字幕、分析结果
- 生成 `summary.md`
- 生成 `integration_metadata.json`

## 价值

### 自动化

- 一条命令跑完整链路
- 不用人工先切片再逐段翻译
- 对已有本地视频也能直接处理

### 可迁移

- 主流程不再依赖旧工作区绝对路径
- 项目整体移动到新目录后，重新创建 `venv/` 即可继续使用

### 适合内容生产

- 从长视频提取可传播片段
- 生成双语字幕素材
- 可选软字幕和硬烧录

## 输出结构

```text
output/
├── original/
│   └── video.mp4
├── clips/
│   ├── video_clip_1.mp4
│   ├── video_clip_2.mp4
│   └── video_clip_3.mp4
├── subtitles/
│   ├── video_clip_1_original.srt
│   ├── video_clip_1_zh.srt
│   ├── video_clip_1_en.srt
│   └── ...
├── analysis/
│   └── analysis_result.json
├── clips_with_subtitles/
│   └── ...
├── integration_metadata.json
└── summary.md
```

## 快速开始

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
python main.py --local-file "./my_video.mp4"
python main.py --url "https://example.com/video" --burn-subtitles
```

更多安装和环境说明，见项目根目录 `README.md` 与 `doc/DEPLOYMENT.md`。
