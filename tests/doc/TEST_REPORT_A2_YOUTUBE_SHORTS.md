# 测试报告 - YouTube Shorts 9:16 无字幕

**测试日期**: 2026-03-20  
**测试人**: codeAgent  
**项目**: short-video-tool

---

## 1. 测试用例

| 项目 | 内容 |
|------|------|
| **用例ID** | A2 |
| **视频URL** | https://youtube.com/shorts/rtH7iXTsJe8?si=GkiZgdTHwYr8SwX3 |
| **视频格式** | 9:16 (1280x2276) |
| **视频时长** | 103秒 (1分43秒) |
| **语音语言** | 英文 |
| **原视频字幕** | 无 |

---

## 2. 测试脚本

```bash
cd /Users/jackwl/.openclaw/agents/it-team/code-agent/workspace/projects/short-video-tool
source venv/bin/activate

python main.py \
  --url "https://youtube.com/shorts/rtH7iXTsJe8?si=GkiZgdTHwYr8SwX3" \
  --output output \
  --burn-subtitles
```

---

## 3. 预期结果

### 3.1 流程预期

| 步骤 | 预期行为 |
|------|----------|
| 下载 | 成功下载视频 |
| ASR | 英文语音识别，生成时间戳 |
| 音频分析 | 检测高潮点 |
| 场景检测 | 检测场景切换点 |
| 语义分段 | LLM语义分析，按topic分段 |
| 切片 | 按语义分段切片 (15-60秒) |
| 翻译 | 英文→中文翻译 |
| 字幕嵌入 | 硬烧录双语字幕 |

### 3.2 字幕预期 (关键)

根据需求文档，英文语音+无字幕的视频应该：

| 项目 | 预期 |
|------|------|
| **检测结果** | OCR无检测 → Whisper检测为英文 |
| **SubtitleStatus** | `SubtitleStatus.NONE` (无原视频字幕) |
| **字幕策略** | 同时烧录英文+中文 |
| **字幕布局** | 英文白色在上，中文黄色在下 |
| **字幕位置** | 底部，避开UI区域 |
| **9:16边距** | 底部边距 15% (避开 TikTok/Shorts UI) |

### 3.3 输出文件预期

```
output/
├── original/
│   └── *.mp4                        # 原始视频
├── clips/
│   ├── *_clip_1.mp4                 # 切片1
│   ├── *_clip_2.mp4                 # 切片2
│   └── ...
├── subtitles/
│   ├── *_clip_1_en.srt              # 英文字幕
│   ├── *_clip_1_zh.srt              # 中文字幕
│   └── ...
├── clips_with_subtitles/
│   ├── *_clip_1_bilingual.mp4       # 硬烧录双语视频
│   └── ...
├── analysis/
│   └── *_analysis.json
├── summary.md
└── integration_metadata.json
```

---

## 4. 实际测试结果

### 4.1 流程执行

| 步骤 | 状态 | 耗时 | 详情 |
|------|------|------|------|
| 下载 | ✅ | 8s | 6.26 MB |
| ASR | ✅ | 3m 4s | Whisper medium, 22 segments |
| 音频分析 | ✅ | 1s | 5个高潮点 |
| 场景检测 | ✅ | 3s | 33个场景切换 |
| 语义分段 | ✅ | 16s | 4个topic段落 |
| 切片 | ✅ | 14s | 4个片段 (28s, 22s, 28s, 21s) |
| 翻译 | ✅ | 2m | Siliconflow DeepSeek-V3 |
| 字幕嵌入 | ⚠️ | 30s | 有异常 (见下文) |

**总耗时**: 6分16秒

### 4.2 输出文件

```
output/
├── clips_with_subtitles/
│   ├── *_clip_1_bilingual.mp4 (6.06 MB)
│   ├── *_clip_2_bilingual.mp4 (2.97 MB)
│   ├── *_clip_3_bilingual.mp4 (4.68 MB)
│   └── *_clip_4_bilingual.mp4 (4.69 MB)
```

### 4.3 字幕检测日志

```
OCR returned no text from any region.
Whisper detected language: 'en'
Audio language: English detected via Whisper.
Auto-detected subtitle status: SubtitleStatus.EN (confidence: 0.80), ocr_lang=en, boundary=None
Source has EN hard subtitle: burning ZH-only above EN hard sub (boundary=0.8030).
```

---

## 5. 问题与建议

### 5.1 Bug: SubtitleStatus 判断逻辑错误

| 项目 | 内容 |
|------|------|
| **问题描述** | OCR未检测到原视频字幕，但系统仍识别为 `SubtitleStatus.EN` |
| **期望行为** | OCR无检测 → Whisper检测语言 → 判断为 `SubtitleStatus.NONE` → 同时烧录英文+中文 |
| **实际行为** | OCR无检测 → Whisper检测为en → 判断为 `SubtitleStatus.EN` → 只烧录中文 |
| **影响范围** | 所有英文语音无字幕的视频 |

**关键代码位置**: `embed_subtitles.py` 中的 `_hard_burn_bilingual_auto()` 函数

**建议修复**: 
```python
# 当前逻辑
if ocr_lang == 'en':
    status = SubtitleStatus.EN  # 错误

# 修复后逻辑
if ocr_text_exists:  # 只有OCR检测到字幕才判断为有字幕
    status = SubtitleStatus.EN
else:
    status = SubtitleStatus.NONE
```

### 5.2 建议: 增加完整视频不切片选项

| 项目 | 内容 |
|------|------|
| **需求** | 用户希望支持"完整不切片的双语视频" |
| **当前状态** | 只有切片版本，无完整视频版本 |
| **建议** | 增加 `--no-clip` 参数，跳过切片步骤，直接对完整视频生成双语字幕 |

**使用方式**:
```bash
python main.py --url "URL" --burn-subtitles --no-clip
```

### 5.3 建议: 翻译 API 稳定性

| 项目 | 内容 |
|------|------|
| **问题** | Siliconflow 翻译偶尔返回不完整，触发 Google Translate fallback |
| **日志** | `count mismatch (attempt 1): expected 20, got 18` |
| **影响** | 翻译质量可能下降 |

### 5.4 建议: ASR 模型选择

| 项目 | 内容 |
|------|------|
| **当前** | 使用 Whisper medium 模型 |
| **耗时** | 3分4秒 (103秒视频) |
| **建议** | 对于短视频可考虑使用 small 或 base 模型，速度更快 |

---

## 6. 总结

| 项目 | 状态 |
|------|------|
| 流程完整性 | ✅ 全流程执行成功 |
| 字幕生成 | ✅ 中英字幕正确 |
| 字幕嵌入 | ⚠️ 只有中文，缺少英文 |
| 问题数 | 1 (SubtitleStatus 判断逻辑) |

---

## 7. 相关文件

| 文件 | 路径 |
|------|------|
| 测试执行记录 | `tests/TEST_EXECUTION_LOG.md` |
| 测试用例文档 | `tests/doc/REQUIREMENT_TEST_CASES.md` |
| 输出目录 | `output/` |
| 主日志 | `logs/main.log` |

---

*报告生成时间: 2026-03-20*
