# 字幕检测过滤增强规格文件

> 版本：v1.0  
> 日期：2026-03-23  
> 状态：待确认（未执行）

---

## 一、背景与诉求

### 问题描述

当前 `subtitle_detect.py` 的字幕检测逻辑在以下两类场景中会产生误判，将非字幕文字区域错误识别为字幕：

**场景 A：固定 UI 水印**
- 视频底部或顶部的版权文字，如 `© 2024 Brand`
- 品牌 Logo 文字，如角落的频道名称
- 特征：位置固定，内容跨视频全程不变

**场景 B：持续标题栏**
- 直播间顶部/底部主播名称，如 `@某某直播中`
- 新闻频道底部标题栏，如节目名称
- 特征：持续显示，内容在较长时间段内不变

### 目标

- 覆盖场景 A 和场景 B，解决约 80% 的误判问题
- 不要求解决 100%（全屏弹幕、半透明叠加文字等复杂场景不在范围内）
- **不破坏现有字幕检测功能**，正常字幕视频结果不退化

---

## 二、改动范围

### 改动文件

- **`subtitle_detect.py`**（唯一改动文件）
  - `_build_ocr_sample_timestamps`：改为两端对称采样
  - `_ocr_region_with_vision_bbox`：扩展返回值，增加 x 坐标
  - `_detect_language_from_ocr_regions`（或其调用链）：增加两个过滤条件

### 不改动

- `embed_subtitles.py`
- `subtitle_sync.py`
- `main.py`
- 任何对外接口和返回值结构
- 其他所有模块

### 对外接口不变

`detect_subtitle_status` 返回值结构保持不变：
```python
(status: SubtitleStatus, confidence: float, ocr_lang: str, subtitle_top_ratio: float)
```

---

## 三、方案详细设计

### 改动 1：采样逻辑改为两端对称采样

**改动函数**：`_build_ocr_sample_timestamps`

**当前逻辑（问题）**：
```
优先取前段固定时间点：5s、10s、15s、20s、30s
以 60s 视频为例，5 个采样点全在前 30s
```

**前段密集采样的问题**：
- 字幕内容在前 30s 内变化可能较少，相似度高，有被误判为水印的风险
- 视频后段才出现的字幕会被完全漏检
- 固定水印虽然也能被识别（内容不变），但整体覆盖不均匀

**新逻辑（两端对称采样）**：

```python
def _build_ocr_sample_timestamps(duration: float, sample_count: int) -> List[float]:
    """
    两端对称采样策略：从视频开头和结尾同时向中间取点。
    - 覆盖全程，确保字幕内容帧间变化可被检测
    - 固定水印在两端仍然内容相同，可被跨帧过滤
    - 短视频（< 20s）降级为均匀采样
    """
    if duration <= 0 or sample_count <= 0:
        return []

    # 极短视频：均匀采样，避免两端点重叠
    if duration < 20:
        return [
            round(duration * (i + 1) / (sample_count + 1), 2)
            for i in range(sample_count)
        ]

    half = sample_count // 2
    # 前段：从 5s 开始，步长 5s
    front = [5.0 * (i + 1) for i in range(half)]
    # 后段：从 duration-5s 开始，步长 5s
    back = [duration - 5.0 * (i + 1) for i in range(half)]
    # 奇数个时补中点
    mid = [duration / 2] if sample_count % 2 == 1 else []

    timestamps = sorted(set(front + back + mid))
    # 过滤超出范围的点
    return [t for t in timestamps if 0.1 <= t <= duration - 0.1]
```

**以 60s 视频、sample_count=5 为例**：
```
front = [5s, 10s]
back  = [55s, 50s]
mid   = [30s]
结果  = [5s, 10s, 30s, 50s, 55s]  ← 全程均匀覆盖
```

**以 300s 视频、sample_count=5 为例**：
```
front = [5s, 10s]
back  = [295s, 290s]
mid   = [150s]
结果  = [5s, 10s, 150s, 290s, 295s]
```

**以 10s 短视频、sample_count=5 为例（降级均匀采样）**：
```
结果 = [1.67s, 3.33s, 5.0s, 6.67s, 8.33s]
```

---

### 改动 2：`_ocr_region_with_vision_bbox` 扩展返回值

**当前返回**：
```python
List[Tuple[str, float, float]]  # (text, y_top_norm, y_bot_norm)
```

**新返回（向后兼容扩展）**：
```python
List[Tuple[str, float, float, float, float]]  # (text, y_top_norm, y_bot_norm, x_left_norm, x_right_norm)
```

**改动方式**：在现有 bbox 解析中同时提取 x 坐标：
```python
x_left_norm = bbox.origin.x
x_right_norm = bbox.origin.x + bbox.size.width
results.append((text, y_top_norm, y_bot_norm, x_left_norm, x_right_norm))
```

所有旧的调用方在解包时加 `_` 兼容：
```python
text, y_top, y_bot, *_ = item  # 兼容新旧格式
```

---

### 改动 3：新增水平居中过滤

**新增函数**：`_is_subtitle_geometry`

**过滤逻辑**：
- 字幕通常居中显示，x_center（文字块水平中心）应接近 0.5
- 角落水印的 x_center 偏左（< 0.25）或偏右（> 0.75）
- 字幕宽度通常覆盖画面 25% 以上

```python
def _is_subtitle_geometry(
    x_left: float,
    x_right: float,
    x_center_tolerance: float = 0.25,
    min_width: float = 0.25
) -> bool:
    """
    判断文字块是否符合字幕的几何特征。
    x_left, x_right: 归一化 [0,1] 坐标
    x_center_tolerance: x_center 偏离 0.5 的最大允许值（默认 0.25）
    min_width: 文字块最小宽度（默认 0.25，即画面宽度 25%）
    返回 True 表示符合字幕几何特征，False 表示应排除
    """
    x_center = (x_left + x_right) / 2
    width = x_right - x_left
    if abs(x_center - 0.5) > x_center_tolerance:
        logging.debug(
            f"[SubtitleFilter] 排除：x_center={x_center:.2f} 偏离中心超过 {x_center_tolerance}"
        )
        return False
    if width < min_width:
        logging.debug(
            f"[SubtitleFilter] 排除：width={width:.2f} 小于最小宽度 {min_width}"
        )
        return False
    return True
```

**配置参数**（可通过 `config.py` 或直接函数参数覆盖）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `x_center_tolerance` | 0.25 | x_center 偏离 0.5 的最大允许值 |
| `min_width` | 0.25 | 文字块最小宽度（画面宽度比例） |

---

### 改动 4：新增跨帧内容不变过滤（固定水印过滤）

**新增函数**：`_is_fixed_watermark`

**过滤逻辑**：
- 对同一区域在多个采样帧识别到的文字内容，计算帧间相似度
- 如果所有帧的文字内容高度相似（> 90%），判定为固定水印，排除
- 使用标准库 `difflib.SequenceMatcher`，不引入新依赖

```python
import difflib

def _is_fixed_watermark(
    frame_texts: List[str],
    similarity_threshold: float = 0.9,
    min_frames: int = 3
) -> bool:
    """
    判断跨帧内容是否固定不变（固定水印特征）。
    frame_texts: 每帧该区域识别到的文字列表
    similarity_threshold: 帧间相似度阈值（默认 0.9）
    min_frames: 最少需要的帧数，不足时不做过滤（默认 3）
    返回 True 表示是固定水印（应排除），False 表示内容有变化（保留）
    """
    # 帧数不足，不判断（避免短视频误杀）
    texts = [t for t in frame_texts if t.strip()]
    if len(texts) < min_frames:
        logging.debug(
            f"[SubtitleFilter] 跨帧过滤跳过：有效帧数 {len(texts)} < {min_frames}"
        )
        return False

    # 计算所有帧对之间的相似度
    scores = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            score = difflib.SequenceMatcher(None, texts[i], texts[j]).ratio()
            scores.append(score)

    avg_score = sum(scores) / len(scores)
    if avg_score >= similarity_threshold:
        logging.debug(
            f"[SubtitleFilter] 排除固定水印：跨帧相似度 {avg_score:.2f} >= {similarity_threshold}"
        )
        return True
    return False
```

**触发条件（同时满足才启用跨帧过滤）**：

| 条件 | 说明 |
|------|------|
| 视频时长 > 30s | 短视频不启用，避免字幕内容少被误杀 |
| 有效采样帧数 >= 3 | 样本不足不判断 |
| 帧间相似度 > 90% | 严格阈值，避免误杀相似但有变化的字幕 |

**配置参数**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `similarity_threshold` | 0.9 | 判定为固定水印的相似度阈值 |
| `min_frames` | 3 | 最少有效帧数 |
| `min_duration_for_watermark_filter` | 30s | 视频时长低于此值不启用跨帧过滤
---

## 四、过滤逻辑集成位置

在 `_detect_language_from_ocr_regions` 函数内，现有持久性过滤之后，增加两层过滤：

```
采样帧 OCR
    ↓
[现有] 持久性过滤（consistent_regions：在多帧中出现的区域）
    ↓
[新增] 水平居中过滤（_is_subtitle_geometry）
    → 排除角落水印、偏置标题
    ↓
[新增] 跨帧内容不变过滤（_is_fixed_watermark，仅 duration > 30s 时启用）
    → 排除固定水印、固定标题栏
    ↓
字幕区域候选（进入语言判断）
```

兜底逻辑（底部区域任意文字算字幕）同样需要经过这两层过滤，不能绕过。

---

## 五、验收标准

### 必须通过

| 测试场景 | 期望结果 |
|----------|----------|
| 视频底部有固定版权水印（单行，内容全程不变） | `SubtitleStatus.NONE` |
| 视频顶部有持续直播标题栏（内容全程不变） | 不影响语言判断，`subtitle_top_ratio` 不被污染 |
| 视频角落有品牌 Logo 文字（x_center < 0.25） | 被水平居中过滤排除 |
| 正常字幕视频（居中，内容帧间变化） | 结果与改动前完全一致，不退化 |
| 短视频（< 20s）有正常字幕 | 均匀采样，不触发跨帧过滤，结果正确 |
| 视频时长 < 30s 有固定水印 | 跨帧过滤不启用，仅靠几何过滤；水平居中的固定水印可能保留（已知限制） |

### 不要求覆盖（已知限制）

- 全屏弹幕
- 半透明叠加文字
- 短视频（< 30s）中水平居中的固定水印
- 片头反复出现的固定 slogan（极低概率误杀场景）

---

## 六、风险说明

| 风险 | 等级 | 触发条件 | 影响 | 建议 |
|------|------|----------|------|------|
| 短视频字幕内容极少变化被误判为水印 | 低 | 视频 < 30s + 字幕只有 1-2 句 | 字幕漏检 | 30s 时长保护已覆盖 |
| 片头重复 slogan 被误判为水印 | 极低 | 5 个采样点全落在重复 slogan 段 | 字幕漏检 | 两端采样已大幅降低概率 |
| 居中的固定水印（宽度 > 25%）无法被几何过滤排除 | 低 | 水印刚好居中且较宽 | 误判为字幕 | 跨帧过滤兜底覆盖此场景 |

---

## 七、日志要求

所有过滤操作必须有 `logging.debug` 级别日志，格式统一：

```
[SubtitleFilter] 排除：原因 + 具体数值
[SubtitleFilter] 跨帧过滤跳过：原因
[SubtitleFilter] 排除固定水印：跨帧相似度 x.xx
```

不产生 `WARNING` 或 `ERROR` 级别日志（过滤是正常路径，不是异常）。

---

## 八、不引入新依赖

- `difflib`：Python 标准库，已可用
- `Vision`：已在现有代码中使用（macOS Only，策略2本来如此）
- 无需新增 pip 依赖

---

## 九、执行前确认清单

- [ ] Jack 确认规格文件内容无误
- [ ] 确认改动只涉及 `subtitle_detect.py`
- [ ] 确认验收标准可接受（特别是已知限制部分）
- [ ] 派工给 codeAgent 执行
- [ ] codeAgent 交付后由 testAgent 验证

---

## 十、指定测试视频

**路径**：
```text
downloads/Chief Nerd - JASON： "Elon seems to think we're gonna have one robot for every huma....mp4
```

**视频信息**：
- 时长：102s（约 1 分 42 秒）
- 大小：18MB
- 两端对称采样点（sample_count=5）：5s, 10s, 51s, 95s, 97s
- 时长 > 30s，跨帧过滤启用

**验收步骤**：
```bash
cd <项目根目录>
python3 -c "
from subtitle_detect import detect_subtitle_status
video = 'downloads/Chief Nerd - JASON： \"Elon seems to think we\'re gonna have one robot for every huma....mp4'
result = detect_subtitle_status(video)
print(f'status={result[0]}, confidence={result[1]:.2f}, lang={result[2]}, top_ratio={result[3]:.2f}')
"
```

**期望结果**：
- 如该视频有硬字幕：`status` 应为 `ZH` 或 `EN` 或 `BILINGUAL`，`confidence > 0.5`
- 如该视频无硬字幕：`status` 应为 `NONE`
- 固定 UI 水印（如有）不应影响 `status` 和 `ocr_lang` 判断
- 改动前后结果对比：记录改动前结果，改动后结果应与改动前一致或更准确
