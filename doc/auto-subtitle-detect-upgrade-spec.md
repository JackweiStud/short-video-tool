# 规格包：auto 字幕判定升级 — 「连续变化字幕流」判定

> 版本：v1.1 | 日期：2026-03-24 | 负责人：codeAgent | 审核：techLeadAgent | 状态：**已交付验证通过**

---
## 项目路径：<项目根目录>

运行环境：在当前项目根目录创建并激活的 `venv/`

## 1. 目标

把 `--subtitle-status auto` 的判定逻辑从「检测到英文文字即判为硬字幕」升级为「检测到连续变化的英文字幕流才判为硬字幕」，消除 UI 固定文字（如 ALL-IN、LIVE、Breaking News 等短词）导致的静默误判，使 auto 模式在 95%+ 的实际视频场景下可靠。

---

## 2. 范围 / 非目标

**范围**：
- 修改 `subtitle_detect.py` 中 `_detect_language_from_ocr_regions()` 的判定层逻辑
- 在现有多帧采样结果基础上，新增多维度 score 联合判定
- 保证短视频（≤30s）最少采 3 帧，score 逻辑统一适用
- 新增 `--subtitle-detect-debug` flag，输出每帧/每区域的 score 明细（可选，便于验证）
- 补充回归测试用例：UI 固定文字不触发误判

**非目标**：
- 不修改 OCR 采样逻辑（`_build_ocr_sample_timestamps`、`_ocr_region_with_vision_bbox`）
- 不修改下游烧录流程（`embed_subtitles.py`）
- 不做语义分析、字体大小识别、颜色分析
- 不修改 `--subtitle-status none / bilingual / zh-only` 路径（零影响）
- 不改变函数签名和返回值类型

---

## 3. 验收标准（必须是业务结果）
测试文件：`downloads/Chief Nerd - JASON： “Elon seems to think we're gonna have one robot for every huma....mp4`
- [x] 用含固定 UI 英文短词的视频跑 `--subtitle-status auto`，不再被误判为 EN 硬字幕 ✓
  - Chief Nerd JASON 视频（含 ALL-IN UI 文字）→ `Burn subtitles: False (status: auto)`，误判已消除
- [x] 现有回归测试全部通过 ✓
  - `python3 -m pytest tests/test_subtitle_detect.py -v` → **38 passed in 1.17s**
- [x] 新补充「UI 固定文字误判」+ 「真实字幕流正判」回归用例通过 ✓
  - `TestSubtitleStreamScoreFilter::test_fixed_short_en_watermark_is_filtered_to_none` PASSED
  - `TestSubtitleStreamScoreFilter::test_changing_english_subtitle_stream_is_detected_as_en` PASSED
- [x] 端到端 pipeline 验证通过 ✓
  - Chief Nerd 视频完整跑完，pipeline 正常输出 2 个 clip + 双语字幕，退出码 0
- [ ] 用有真实英文硬字幕的视频跑 `--subtitle-status auto` 正判验证（待有合适样本时补充）

**不接受**：
- ❌ "代码没报错"
- ❌ "函数返回 True"
- ❌ "脚本退出码 0"

---

## 4. 关键约束

- **只改判定层**：`_detect_language_from_ocr_regions()` 内部逻辑，不动其他函数
- **短视频保障**：`sample_count` 传入时，确保最少采 3 帧；现有代码已有此逻辑则复用，否则补上
- **不引入新依赖**：只用标准库 + 项目已有依赖
- **项目路径**：从当前仓库根目录执行
- **不破坏现有测试**：改动后所有已有测试必须继续通过

---

## 5. 技术方案

### 核心思路：多维度 score 联合判定

现有逻辑在 `_detect_language_from_ocr_regions()` 末尾，通过 `_detect_language_from_text(merged)` 判断语言，只要 merged 文本含英文就可能返回 EN。

**升级方案**：在合并文本做语言判定之前，先对每个通过持久性+水印过滤的 region 计算一个「字幕流可信度 score」，score 不达标的 region 从 `final_eligible_regions_data` 中降权或排除。

### Score 规则（每个 eligible region 独立计算）

| 维度 | 条件 | 分值 |
|------|------|------|
| 跨帧文本变化 | 相邻帧文本内容不完全相同（similarity < 0.85）的帧对占比 >= 40% | +3 |
| 文本长度达标 | 有效帧中文本平均长度 >= 15 字符 | +2 |
| 字幕区位置 | region 属于底部区域（y_frac_start >= 0.5） | +2 |
| 非单词 UI label | 有效帧中词数 >= 3 的帧占比 >= 50% | +1 |
| 连续帧数达标 | 有效文本帧数 >= 3 | +1 |

**判定阈值**：score >= 5 才认定为「可信英文字幕流」，纳入最终 merged 文本做语言判定。

score < 5 的 region：从 final_eligible_regions_data 中排除（等同于 UI 文字过滤）。

### 关键细节

- score 计算复用已有的 `region_frame_texts[region_key]`（已是 sample_count 长度的列表），无需额外采样
- 跨帧变化检测复用 `difflib.SequenceMatcher`（已在 `_is_fixed_watermark` 中使用）
- ZH 文本不受 score 限制（中文 UI 误判场景极少，且 ZH 判定走不同路径）
- score 明细通过 `logging.debug` 输出，开启 `--subtitle-detect-debug` 时升级为 `logging.info`

### 短视频（< 30s）处理

现有代码对 `duration >= 30.0` 才调用 `_is_fixed_watermark`。score 判定**不受时长限制**，统一执行。
确认 `_build_ocr_sample_timestamps` 对短视频的最小采样数，如果 < 3 则补一个 `max(sample_count, 3)` 的保障。

---

## 6. 测试关注点（给 testAgent）

- **误判场景**：含 "ALL-IN"、"LIVE"、"Breaking"、"100%" 等短英文 UI 标签的视频，auto 不应判为 EN 硬字幕
- **正判场景**：有真实连续变化英文硬字幕的视频，auto 仍应正确判为 EN 硬字幕
- **回归**：`--subtitle-status none / bilingual / zh-only` 行为不变
- **边界**：视频时长 < 30s，score 逻辑仍正常运行
- **边界**：纯中文视频，ZH 判定不受影响
- **边界**：极短英文字幕（"OK." "Yes."）：允许漏判（score 不达标），这是已知可接受的边界

---

## 7. 交付要求（强制）

1. **必须提供可直接运行的验证命令**
   - 例如：`python3 -m pytest tests/test_subtitle_detect.py -v`
   - 以及端到端验证：`python3 main.py --local-file <视频路径> --subtitle-status auto`

2. **必须提供业务结果验证**
   - 误判修复：运行日志显示 UI 文字 region 被 score 过滤，未触发 EN 判定
   - 正判保留：运行日志显示真实字幕 region score 达标，正确触发 EN 判定

3. **风险主动暴露**
   - 开工前：现有 `_is_fixed_watermark` 与新 score 逻辑是否有重叠/冲突？
   - 进行中：score 阈值是否需要根据实际视频调整？
   - 阻塞超过 15 分钟立即报告

---

## 8. 预期时间

2-3 小时

---

## 9. 验证结论（2026-03-24）

**验证时间**：2026-03-24 17:08-17:14 GMT+8

**单元测试**：`python3 -m pytest tests/test_subtitle_detect.py -v` → 38 passed in 1.17s ✓

**端到端验证命令**：
```bash
cd <项目根目录>
source venv/bin/activate
VIDEO=$(ls downloads/ | grep 'Chief Nerd - JASON')
python3 main.py --local-file "downloads/$VIDEO" --subtitle-status auto
```

**端到端结果**：
- 视频：Chief Nerd - JASON（含 ALL-IN UI 文字）
- auto 判定结果：NONE（未检测到英文硬字幕）
- 日志关键行：`Burn subtitles: False (status: auto)` ✓
- Pipeline 完整运行：2 个 clip 输出，双语字幕生成正常，退出码 0 ✓
- 总耗时：327.54 秒

**已知剩余边界**：重复度高的短句英文字幕（如 "OK." "Yes."）score 可能偏低触发漏判；目前无真实样本复现，记录为观察项，有样本再调 S1/S2 阈值。

**正判样本验证**：待有真实英文硬字幕视频样本时补充。

---

## 附：当前代码关键路径速查

- 判定入口：`subtitle_detect.py` → `detect_subtitle_status()` → `_detect_language_from_ocr_regions()`
- 水印过滤：`_is_fixed_watermark()`（已有，score 可复用其 difflib 逻辑）
- 采样时间戳：`_build_ocr_sample_timestamps()`
- OCR 执行：`_ocr_region_with_vision_bbox()`
- 改动位置：`_detect_language_from_ocr_regions()` 末尾的 `final_eligible_regions_data` 汇总段，约第 530-610 行区间
