# 更新日志 (Changelog)

本项目的所有重大变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)，

## [Unreleased] - 2026-04-07

### Added
- `config.py`: 新增 `subtitle_auto_hard_ocr_sample_count`，用于控制 `auto` 模式下硬字幕 OCR 的采样密度（默认 11）

### Changed
- `embed_subtitles.py` / `integrator.py`: 统一在 metadata 和 summary 中输出最终硬字幕策略，明确语义为 `EN=replace / ZH=skip / BILINGUAL=replace`
- `embed_subtitles.py`: 硬字幕替换式重烧策略参数化，默认 EN 走 replace、ZH 走 skip、BILINGUAL 走 replace，保留当前产品语义并便于后续按需切换
- `embed_subtitles.py`: 硬字幕烧录主路径从“保留原字幕 + 视觉对齐补另一种语言”切换为“估计原字幕区域 + 纯色遮罩 + 统一双语重烧”，替换式双语成片成为默认行为
- `embed_subtitles.py`: `EN / ZH / BILINGUAL` 硬字幕态统一映射到遮罩重烧，`ZH` 视频默认不再额外烧录字幕；`EN` 视频使用遮罩后的统一双语时间轴
- `embed_subtitles.py`: 遮罩尺寸按字幕内容自适应估算，不再使用固定全宽底条
- `subtitle_detect.py`: `auto` 模式下的 OCR 采样改为更密的全视频均匀采样，避免固定“前/中/后”稀疏采样漏掉中段硬字幕
- `subtitle_detect.py`: 当采样数大于 5 时，优先按全时长均匀铺点；保留短视频和低采样数时的旧策略
- `embed_subtitles.py`: `auto` 路径显式传入硬字幕 OCR 采样数，确保只影响自动硬字幕检测，不改手动字幕状态

### Fixed
- 原始视频存在 EN / ZH 硬字幕时，最终成片不再依赖“保留原字幕 + 再叠加另一种语言”的时间轴同步方式，避免中英字幕不同步、重合或错位
- GTC 2026 主题演讲样本中，原先因 OCR 采样点过少而漏掉的英文硬字幕被成功检出，`auto` 状态从 `NONE` 修正为 `EN`
- `auto` 硬字幕检测仅增强采样密度，不影响 `none/en/zh` 的手动判定与烧录逻辑

### Tested
- 原始 EN 硬字幕样本（Sam Altman / GTC）→ 遮罩替换式双语重烧，字幕卡片与双语内容对齐 ✓
- 原始 ZH 硬字幕样本（鳌拜 / 橘猫道长）→ 默认不额外烧录字幕，保留原视频 ✓
- `AI Will - GTC 2026主题演讲压轴彩蛋...mp4`：`auto` 检出 `EN`，触发硬字幕遮罩 + 双语重烧
- `Google AI - Today, we’re launching Gemma 4...mp4`：`auto` 保持 `NONE`，继续普通双语烧录
- `python3 -m py_compile config.py subtitle_detect.py embed_subtitles.py tests/test_v1_1_features.py tests/test_whisper_zh_fallback.py`
- `source venv/bin/activate && pytest -q tests/test_subtitle_detect.py tests/test_v1_1_features.py tests/test_whisper_zh_fallback.py`
- 61 tests passed

## [Unreleased] - 2026-03-19 ~ 2026-03-20

### Added
- `subtitle_detect.py`: OCR bounding box 返回 `subtitle_top_ratio`，支持动态检测 EN 硬字幕的像素级顶部位置（`_detect_language_from_ocr_regions` 新增 `region_subtitle_tops` 追踪）
- `embed_subtitles.py`: 动态 boundary 计算——根据 `subtitle_top_ratio` 实时计算 ZH 软字幕的 `MarginV`，替代固定像素偏移
- `embed_subtitles.py`: ASS 文件生成路径，使用 `\an2\pos` 绝对坐标注入覆盖 FFmpeg libass 忽略 MarginV 的问题
- `config.py`: 新增 `zh_font_size`（56pt）、`en_font_size`（52pt）、顶部/底部 margin ratio 配置项

### Changed
- `subtitle_detect.py`: `_detect_language_from_ocr_regions` 返回值从 2-tuple 升级为 3-tuple `(status, lang, top_ratio)`
- `subtitle_detect.py`: `subtitle_top_ratio` 计算逻辑修复——仅从 `y_frac_start >= 0.5` 的底部区域取值，顶部 UI 区域（浏览器标签栏、App 菜单）只参与语言检测，不参与位置计算（修复屏幕录制视频误判为 0.033 的 bug）
- `embed_subtitles.py`: 检测到 EN 硬字幕（`ocr_lang=en`）时，跳过 EN 软字幕烧录，仅烧录 ZH 软字幕到顶部，避免双重英文字幕
- `embed_subtitles.py`: PlayResX/Y 改用视频原生分辨率（原固定 PlayResY=288），解决坐标空间不匹配导致定位错误的问题
- 字体大小：ZH 56pt、EN 52pt（原 16px，在 1080p 视频上不可读）

### Fixed
- 屏幕录制类视频（Clay/Attio 等）顶部 UI 文字被持续性过滤误判为字幕区域，导致 `subtitle_top_ratio=0.033` 而非正确的 `0.811`（commit `893d89d`）
- FFmpeg libass 在 PlayResY 与视频高度不匹配时忽略 MarginV，改为 ASS `\an2\pos` 绝对定位解决（commit `2752166`）
- float-to-int 截断导致 margin 比例 `0.15 → 0px`，修复为 `int(0.15 * height) = 162px`（commit `b90c85c`）
- ZH 软字幕与 EN 软字幕重叠（commit `b90c85c`）

### Tested (真实视频验证)
- Michelle Lim（屏幕录制，1920x1080，EN 硬字幕）→ clip_1~4 boundary 正确，ZH 顶部，EN 软字幕跳过 ✓
- clip_2/3（含顶部 UI 文字）→ top_ratio 修复后 0.033→0.811，定位正确 ✓
- Luke The Dev（352x640 竖屏，EN 硬字幕）→ boundary=0.8332，MarginV=185，ZH 顶部 ✓
- 33 unit tests passed，2 slow network tests skipped ✓

---

## [Unreleased] - 2026-03-19

### Added
- `subtitle_detect.py`: 新增 `_ocr_region_with_vision()` 函数，使用 Apple Vision Framework 对字幕区域图像进行 OCR（支持中文简体/繁体 + 英文）
- `subtitle_detect.py`: 新增 `_detect_language_from_ocr_regions()` 函数，对视频多帧多区域采样 OCR，合并文本后按中文字符占比判断语言
- `subtitle_detect.py`: 新增 `_detect_language_from_audio()` 函数，作为 OCR 失败时的兜底语言检测（使用 faster-whisper）
- `requirements.txt`: 新增 `pyobjc-framework-Vision>=12.0` 依赖

### Changed
- `subtitle_detect.py` 路径 B（硬字幕检测）语言识别从音频语言检测改为 Apple Vision OCR，修复翻译类视频（英文原声+中文硬字幕）被误判为 `en` 的问题
- OCR 扫描区域扩展为顶部25%/底部25%/中部30%，覆盖竖屏视频多样字幕位置
- OCR 语言检测不再依赖像素密度预筛，直接对所有区域跑 Vision OCR

### Fixed
- 翻译类视频（如黄仁勋英文演讲+中文字幕）路径 B 语言识别误判问题：原 `en` → 修复后 `zh`

### Tested (真实视频验证)
- 英文音频+中文硬字幕（谢嘉琪黄仁勋视频）→ `zh / 0.36` ✓
- 中文音频+中文硬字幕（Paidax）→ `zh / 0.40` ✓
- 英文音频+英文硬字幕（Michelle Lim）→ `en / 0.99` ✓
- 英文音频+无字幕（Miko AI）→ `en / 0.91`（画面含英文UI文字，已知边界情况）

## [P0-2] - 2026-03-19

### Added
- `subtitle_detect.py`: 新建字幕检测模块，支持软字幕（路径A，ffprobe）和硬字幕（路径B，像素密度+OCR）检测
- `subtitle_detect.py`: `SubtitleStatus` 枚举（NONE/EN/ZH/BILINGUAL/UNCERTAIN）
- `subtitle_detect.py`: `detect_subtitle_status(video_path, sample_count)` 主接口，返回 `(SubtitleStatus, confidence)`
- `tests/test_subtitle_detect.py`: 33 个单元测试，覆盖所有路径和边界条件

本项目遵循 [语义化版本 (Semantic Versioning)](https://semver.org/spec/v2.0.0.html)。

## [1.0.0] - 2026-03-16

### 新增 (Added)

#### 核心模块
- **视频下载模块** (`downloader.py`)
  - 支持 YouTube、TikTok 和 Twitter(X) 视频下载
  - 自动提取元数据（标题、作者、时长、描述）
  - 多种画质选项（720p, best, worst）
  - 鲁棒的错误处理和重试机制

- **智能分析模块** (`analyzer.py`)
- 基于 mlx-whisper / faster-whisper 的高精度 ASR（自动语音识别）
  - 基于 librosa 的音频高潮检测（RMS 能量 + 频谱质心 + 过零率）
  - 基于 PySceneDetect 的场景切换检测
  - 全面的校验和错误处理

- **智能剪辑模块** (`clipper.py`)
  - 多维度评分机制，用于识别精彩片段
  - 与场景切换自动对齐的智能边界处理
  - 针对各类视频的备选兜底策略
  - ASR 文本子集提取及时间戳调整
  - 极速、无损的 FFmpeg 拷贝模式剪辑（15-60 秒片段）

- **翻译模块** (`translator.py`)
  - 双后端支持：OpenAI GPT（高质量）+ Google Translate（免费）
  - 自动故障转移机制
  - 批量翻译以提高效率
  - SRT 字幕生成（原文、中文、英文）
  - 精确的时间戳同步

- **整合模块** (`integrator.py`)
  - 统一的输出目录结构
  - 自动生成 Markdown 摘要报告
  - 完整的元数据记录
  - 文件复制与组织归档

- **字幕嵌入模块** (`embed_subtitles.py`)
  - 软嵌入模式：将 SRT 作为字幕轨道混流（速度快，无需转码）
  - 硬压模式：双语字幕（上英下中）直接烧录进视频帧
  - 自动检测 CJK 字体用于中文渲染
  - 支持 moviepy + Pillow 渲染（无需 libfreetype 依赖）

#### 入口点
- **一键式流水线** (`main.py`)
  - 从 URL 到最终输出的全自动化流程
  - 可配置的片段时长（--min-duration, --max-duration）
  - 可选的字幕嵌入（--embed-subtitles, --burn-subtitles）
  - 详尽的日志记录和进度追踪

#### 验证与测试
- 所有核心模块的验证脚本：
  - `verify_analyzer.py` - 分析模块功能验证
  - `verify_clipper.py` - 剪辑模块功能验证
  - `verify_translator.py` - 翻译模块功能验证
  - `verify_integrator.py` - 整合模块功能验证
- 异常处理测试：
  - `test_exceptions.py` - 分析模块异常场景
  - `test_clipper_exceptions.py` - 剪辑模块异常场景
  - `test_exceptions_quick.py` - 快速异常测试套件

#### 文档
- 全面的 README.md，包含：
  - 功能概览
  - 快速入门指南
  - 使用示例
  - 项目结构
  - 性能性能指标
  - 技术亮点
- `doc/modules/` 目录下的详细模块文档：
  - `analyzer.md` - 分析模块文档
  - `clipper.md` - 剪辑模块文档
  - `translator.md` - 翻译模块文档
  - `integrator.md` - 整合模块文档
  - `downloader.md` - 下载模块文档
- 工作流图表 (`doc/workflow.md`)
- 分析器优化计划 (`doc/analyzer-optimization-plan.md`)

#### 基础设施
- 适用于 Python 项目的 `.gitignore`
- 包含所有依赖项的 `requirements.txt`
- 虚拟环境配置说明

### 技术亮点

- **智能剪辑算法**：结合音频高潮与场景切换的多维度评分，并实现智能边界对齐。
- **兜底策略**：在高潮点或场景切换无法识别时，能够优雅降级。
- **FFmpeg 拷贝模式**：超快速剪辑（每个片段约 0.06 秒），且保持原始画质。
- **双翻译后端**：OpenAI GPT 保证质量，Google Translate 提供免费额度。
- **双语字幕渲染**：硬压模式采用英文（白色，顶部）+ 中文（黄色，底部）布局。
- **ASR 文本子集提取**：为每个片段自动提取对应文本并调整时间戳。
- **鲁棒的错误处理**：所有模块均具备全面的校验和异常处理机制。

### 性能指标

- **下载**：取决于网络环境
- **分析**：5 分钟视频约需 6 分钟（ASR 4分钟 + 音频分析 35秒 + 场景检测 22秒）
- **剪辑**：每个片段约 0.06 秒（FFmpeg 拷贝模式）
- **翻译**：每个片段约 17 秒（Google Translate）
- **整合**：约 0.04 秒

### 已知问题

- **TikTok 下载**：可能受 IP 限制或需要 Cookie/代理配置。
- **Twitter(X) 下载**：复杂的视频嵌入模式可能需要额外配置。
- **ASR 语言**：默认为英语；中文视频需要手动指定语言参数。
- **翻译质量**：Google Translate 免费版质量有限；生产环境建议使用 OpenAI GPT。

### 依赖项

#### 核心依赖
- yt-dlp==2026.3.13 (视频下载)
- faster-whisper / mlx-whisper (ASR 语音识别，需要 ffmpeg)
- librosa==0.11.0 (音频分析)
- scenedetect==0.6.7.1 (场景检测)
- opencv-python==4.13.0.92 (视频处理)
- deep-translator==1.11.4 (翻译)
- openai==2.28.0 (可选，用于 GPT 翻译)

#### 系统依赖
- ffmpeg (音视频处理必备)
- Python 3.10+ (推荐)

### 项目统计

- **代码总行数**：3,275 行（不含虚拟环境）
- **核心模块数**：6 个
- **验证脚本数**：7 个
- **文档文件数**：9 个

---

## [1.1.0] - 2026-03-18

### 新增 (Added)
- **翻译后端升级** (`translator.py`)
  - 集成 Siliconflow (DeepSeek-V3) 后端，支持 ASR 语境纠错与地道翻译。
  - 增强 System Prompt，专门针对同音误听（如 "inference" vs "in France"）进行 AI 纠错。
  - Google Translate 强制作为全局 Fallback 兜底。
- **D+B 字幕同步系统** (`subtitle_sync.py`)
  - 实现 Direct (ASR 时间戳) + Boundary (视觉边界检测) 双重校准技术。
  - 解决硬字幕重叠与同步偏移问题。
- **短视频竖屏自适应** (`embed_subtitles.py`)
  - 针对 9:16 画幅动态调整底边距（下调至 10%）避开 UI。
  - 字体大小基于 `min(W, H)` 计算，防止竖屏过大。
  - 智能自动换行逻辑，收紧至 85% 屏幕宽度触发换行。
- **高清全长视频生成** (`generate_full_video.py`)
  - 新增专用脚本用于生成带有精准双语字幕的全长 1080p 视频。
- **流程精细化控制**
  - `main.py` 增加 `--subtitle-status` 参数，支持手动指定字幕检测策略（auto/none/en）。
  - 支持从命令行直接指定 `--quality 1080p`。

### 优化 (Changed)
- **画质优先策略**：`config.py` 默认 `video_quality` 提升为 `best`。
- **下载引擎升级**：`downloader.py` 支持 `bestvideo+bestaudio` 模式，确保 YouTube/X 平台获取最高 1080p+ 画质。
- **渲染精度**：`embed_subtitles.py` 在 `clips_data` 模式下仅处理新生成切片，不再扫描旧资产。
- **安全性调整**：移除 `main.py` 启动时的暴力清空逻辑，保留历史任务结果。

### 修复 (Fixed)
- 修复了竖屏视频因高度判定导致 `yt-dlp` 降级到 360p 的下载逻辑。
- 修复了 Translator 类在 Google 翻译初始化失败时的 AttributeError。
- 修复了 FFmpeg 转码导致的某些场景下音频轨道丢失的问题。

---

## [待发布] (Unreleased)

### 计划中
- 自动化端到端画质对比用例
- 针对 1:1 (Square) 视频的专用样式模板
- AI 视频超分整合 (Upscaling)
- Web UI 远程管理界面

---

## 发布说明 (Release Notes)

### 版本 1.0.0 - 首次发布

这是短视频工具的第一个稳定版本，提供了一套完整的自动化流水线，用于：
1. 从 YouTube、TikTok 和 Twitter 下载视频
2. 智能分析（ASR + 音频高潮 + 场景检测）
3. 智能剪辑（15-60 秒精彩片段）
4. 多语言翻译（中英双语字幕）
5. 统一的输出整合
6. 可选的字幕嵌入（软混流或硬压制）

该工具已具备基础使用场景的生产就绪能力，但在企业级部署前仍需完善基础设施（如 CI/CD、自动化测试）。

---

[1.0.0]: https://github.com/yourusername/short-video-tool/releases/tag/v1.0.0
