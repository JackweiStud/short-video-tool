# Analyzer 模块优化方案

> 历史说明：这是早期优化方案文档，其中提到的 `verify_analyzer.py` 属于当时的仓库结构，当前仓库已改为以 `main.py` 和 `tests/` 目录为主的验证方式。

> **目标**：让分析结果能真正支撑"自动拆解长视频 → 生成可发布的短视频"这一核心需求。
>
> **当前版本问题**：ASR 中文不可用、高潮点检测覆盖不足、场景切分噪声大、缺少语义化的智能切分能力。

---

## 一、现状评估

### 已有能力

| 模块 | 技术栈 | 现状 |
|------|--------|------|
| F2.1 ASR | Whisper CLI (small) | ✅ 可运行，但硬编码 `language="en"`，中文完全不可用 |
| F2.2 音频高潮 | librosa + scipy | ✅ 可运行，但高潮点集中在一小段，覆盖率低 |
| F2.3 场景检测 | PySceneDetect | ✅ 可运行，但检测点过碎、有重复，噪声大 |
| 综合切分 | 无 | ❌ 不存在。三个维度各自独立，无综合判断 |

### 核心差距

```
当前状态：视频 → [ASR文本] + [高潮点] + [场景切换点]  → 三个独立列表（人工看不出怎么切）
期望状态：视频 → 智能分析 → 推荐切分方案 [{start, end, title, score, reason}]  → 直接可用
```

---

## 二、优化任务清单

### P0 — 必须修复（不修就不能用）

#### 任务 1：ASR 支持中文 + 语言自动检测

**文件**：`analyzer.py` → `_run_asr()`

**改动要点**：
- 移除 `language="en"` 硬编码
- 新增 `language` 参数，默认值为 `"auto"`
- `auto` 模式下不传 `--language`，让 Whisper 自动检测
- 用户可手动指定如 `"zh"`, `"en"`, `"ja"` 等

**代码改动示意**：
```python
def _run_asr(self, audio_path: str, model: str = "small", language: str = "auto") -> list:
    cmd = ["whisper", audio_path, "--model", model, "--output_format", "srt",
           "--output_dir", os.path.dirname(audio_path)]
    if language != "auto":
        cmd.extend(["--language", language])
    # ... 其余不变
```

**验收标准**：
- 中文教学视频（OpenClaw 教程）能正确输出中文字幕
- 英文视频仍能正确识别
- `analyze_video()` 新增可选参数 `language`

---

#### 任务 2：`analyze_video()` 暴露语言参数

**文件**：`analyzer.py` → `analyze_video()`

**改动要点**：
```python
def analyze_video(self, video_path: str, output_dir: str = "analysis_results",
                  language: str = "auto") -> dict:
    # ...
    asr_result = self._run_asr(audio_path, language=language)
```

**验收标准**：
- 调用时可传 `language="zh"` 或不传（自动检测）
- `verify_analyzer.py` 更新适配新参数

---

### P1 — 重要优化（直接影响切分质量）

#### 任务 3：音频高潮检测改进

**文件**：`analyzer.py` → `_analyze_audio()`

**当前问题**：
- `find_peaks(height=1.0)` 阈值太高 → 大量有意义的节点被过滤
- `top_n=5` 太少 → 长视频覆盖不足
- peaks 全集中在一个区间

**改动要点**：
1. **动态 top_n**：根据视频时长动态计算，建议每 60 秒至少 1-2 个检测点
   ```python
   duration_sec = len(y) / sr
   top_n = max(5, int(duration_sec / 30))  # 每 30 秒至少 1 个
   ```
2. **降低阈值**：从 `height=1.0` 降到 `height=0.5`，或使用百分位数动态阈值
   ```python
   threshold = np.percentile(climax_score, 85)  # 取 Top 15%
   peaks, props = find_peaks(climax_score, height=threshold, distance=sr//hop_length*3)
   ```
3. **增大最小间距**：`distance` 从 2 秒提高到 3-5 秒，避免密集聚集

**验收标准**：
- 590 秒视频 → 至少检测 10+ 个分散的高潮点
- 高潮点在时间轴上分布合理，不集中在某一段

---

#### 任务 4：场景检测去噪 + 去重

**文件**：`analyzer.py` → `_detect_scenes()`

**当前问题**：
- 0.0s 重复出现
- 屏幕录制类视频小变动太多，导致碎片化
- 缺少最小间隔过滤

**改动要点**：
1. **去重**：移除重复的 0.0s
2. **最小间隔合并**：相邻切换点 < 2 秒的合并为一个
3. **可配置阈值**：不同类型视频使用不同阈值
   ```python
   def _detect_scenes(self, video_path: str, threshold: float = 27.0,
                      min_scene_len: float = 2.0) -> list:
       # ... 检测后过滤
       filtered = [scene_changes[0]]
       for t in scene_changes[1:]:
           if t - filtered[-1] >= min_scene_len:
               filtered.append(t)
       return filtered
   ```

**验收标准**：
- 0.0s 不重复
- 相邻切换点间隔 ≥ 2 秒
- 590 秒教学视频 → 场景切换点从 66 个降到 20-30 个有意义的切点

---

### P2 — 核心增强（实现智能切分）

#### 任务 5：新增综合切分推荐引擎

**新文件建议**：`splitter.py`

**功能描述**：
综合 ASR、音频高潮、场景切换三个维度，生成**可直接使用的切分推荐方案**。

**输入**：`analyzer.analyze_video()` 的输出
**输出**：

```python
{
    "segments": [
        {
            "index": 1,
            "start": 0.0,
            "end": 60.56,
            "duration": 60.56,
            "title": "第一步：安装 Node.js",       # 从 ASR 提取
            "score": 0.85,                         # 综合推荐分
            "reason": "完整章节，含开头、安装步骤、结束语",
            "has_speech": true,
            "climax_count": 1,
            "scene_changes": 8
        },
        {
            "index": 2,
            "start": 61.84,
            "end": 107.72,
            "title": "第二步：安装 Git",
            ...
        }
    ],
    "metadata": {
        "total_segments": 6,
        "avg_duration": 98.3,
        "recommended_for": "教学类短视频"
    }
}
```

**核心算法**：

```
1. 从 ASR 文本中提取结构标记（"第一步"、"接下来"、"最后"等关键词）
2. 将结构标记对应的时间点作为"强切分点"
3. 在没有语义标记的区间，用场景切换 + 音频高潮的加权评分选择次优切分点
4. 确保每段时长在合理范围内（建议 30s-180s，可配置）
5. 为每段生成标题（从 ASR 文本中提取关键句）
```

**关键词库**（中文）：
```python
CHAPTER_KEYWORDS = [
    r"第[一二三四五六七八九十\d]+步",
    r"第[一二三四五六七八九十\d]+[个点部分]",
    r"首先|其次|然后|接下来|最后|最终",
    r"总结|回顾|下面|接着",
    r"步骤\s*\d+",
]
```

**验收标准**：
- 对 OpenClaw 教程视频，能自动识别出 5-7 个教学步骤
- 每段有标题、时长合理（30s-180s）
- 综合评分可用于排序（推荐发哪几段效果好）

---

#### 任务 6：新增视频切割导出功能

**新文件建议**：`exporter.py`

**功能**：根据切分方案，用 ffmpeg 导出独立的短视频文件。

```python
class Exporter:
    def export_segments(self, video_path: str, segments: list,
                        output_dir: str = "exports") -> list:
        """
        根据切分方案导出短视频
        
        Returns:
            list: [{"index": 1, "filepath": "exports/01_安装Node.mp4", ...}]
        """
        results = []
        for seg in segments:
            output_file = os.path.join(output_dir,
                f"{seg['index']:02d}_{seg['title'][:20]}.mp4")
            cmd = [
                "ffmpeg", "-i", video_path,
                "-ss", str(seg['start']),
                "-to", str(seg['end']),
                "-c", "copy",  # 无损切割，速度极快
                "-y", output_file
            ]
            subprocess.run(cmd, ...)
            results.append({"index": seg['index'], "filepath": output_file})
        return results
```

**验收标准**：
- 根据 splitter 方案自动切出多个 mp4 文件
- 无损切割，速度快（不重新编码）
- 输出文件名包含序号和标题

---

### P3 — 锦上添花

#### 任务 7：Whisper 模型可选 + 性能优化

| 模型 | 大小 | 速度 | 精度 | 建议场景 |
|------|------|------|------|----------|
| tiny | 39M | 极快 | 低 | 快速预览/测试 |
| base | 74M | 快 | 中 | 简单内容 |
| small | 244M | 中 | 较好 | **默认推荐** |
| medium | 769M | 慢 | 好 | 中文内容推荐 |
| large-v3 | 1.5G | 很慢 | 最好 | 高精度需求 |

- 中文视频推荐用 `medium` 或 `large-v3`，`small` 对中文效果一般
- 新增参数 `model` 到 `analyze_video()`

#### 任务 8：verify_analyzer.py 升级

- 适配新增的 `language` 参数
- 增加对切分方案的验证（如果 splitter 完成）
- 增加中文/英文双测试用例

---

## 三、实施优先级 & 工期估算

```
P0 (必须修) ──────────────────────────────
  任务 1：ASR 中文支持          ~30 分钟
  任务 2：暴露 language 参数    ~15 分钟

P1 (重要优化) ────────────────────────────
  任务 3：高潮检测改进          ~1 小时
  任务 4：场景检测去噪          ~30 分钟

P2 (核心增强) ────────────────────────────
  任务 5：切分推荐引擎          ~2-3 小时（核心功能）
  任务 6：视频切割导出          ~1 小时

P3 (锦上添花) ────────────────────────────
  任务 7：模型可选 + 性能       ~30 分钟
  任务 8：验证脚本升级          ~30 分钟
```

**建议实施顺序**：`P0(1,2) → P1(3,4) → P2(5,6) → P3(7,8)`

全部完成后预计耗时 **6-8 小时**，可分批交付。

---

## 四、优化后的目标架构

```
                    ┌─────────────────────────────────────────┐
                    │            short-video-tool             │
                    └─────────────────────────────────────────┘
                                       │
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
 ┌─────────────┐ ┌──────────┐  ┌─────────────┐ ┌──────────┐  ┌──────────┐
 │ downloader  │ │ analyzer │  │  splitter   │ │ exporter │  │ verify   │
 │    .py      │ │   .py    │  │    .py      │ │   .py    │  │   .py    │
 │             │ │          │  │   (新增)     │ │  (新增)   │  │  (升级)  │
 │ · YouTube   │ │ · ASR    │  │ · 语义切分  │ │ · ffmpeg │  │ · 双语  │
 │ · TikTok    │ │   (多语言)│  │ · 智能推荐  │ │   切割   │  │   测试  │
 │ · Twitter   │ │ · 高潮点 │  │ · 评分排序  │ │ · 批量   │  │ · 切分  │
 │ · B站 ...   │ │   (优化) │  │ · 标题提取  │ │   导出   │  │   验证  │
 │             │ │ · 场景   │  │             │ │          │  │          │
 │             │ │   (去噪) │  │             │ │          │  │          │
 └─────────────┘ └──────────┘  └─────────────┘ └──────────┘  └──────────┘
                       │               │               │
                       ▼               ▼               ▼
              analysis_result   split_plan.json   exports/
                  .json         (切分推荐方案)     01_xxx.mp4
                                                  02_xxx.mp4
```

**Pipeline**：
```
下载视频 → 分析(ASR+音频+场景) → 智能切分推荐 → 导出短视频 → 发布
```

---

## 五、验收测试用例

| 测试 | 输入 | 期望输出 |
|------|------|----------|
| 中文教学视频 ASR | OpenClaw 安装教程 | 中文字幕，"第一步""第二步"可识别 |
| 英文视频 ASR | COSTA RICA 4K | 英文字幕或正确识别 "Music"/"Stay connected" |
| 高潮点覆盖 | 590 秒视频 | ≥10 个点，时间轴均匀分布 |
| 场景去噪 | 屏幕录制视频 | 无重复 0.0s，间隔 ≥2s |
| 智能切分 | OpenClaw 教程 | 5-7 段，每段 30-180s，有标题 |
| 视频导出 | 切分方案 | 对应数量的 mp4 文件，可播放 |
