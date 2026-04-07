task_id: subtitle-detection-hardening-2026-04-03
objective: 收紧英文硬字幕误判链路，避免将底部 UI/广告文案误识别为 EN 字幕并错误切换烧录策略
selected_approach: 先做第一阶段规则增强，在 subtitle_detect 中加入 UI/广告负向特征并收紧 EN fallback，再在 embed_subtitles 中增加低置信安全回退；暂不引入 ASR 对齐校验
rejected_approaches:
  - 直接重构为 OCR+ASR 联合判定：实现成本更高，超出本轮先止血的范围
  - 先扩展 OCR 扫描区域：当前主问题是假阳性，不是漏检，先扩区域会放大噪声
key_assumptions:
  - 当前误判主入口是 EN fallback 过宽和英文字幕流评分缺少 UI/广告负向特征
  - 低置信时回退到双语烧录比误判为 EN 硬字幕更安全
scope: subtitle_detect.py, embed_subtitles.py, tests/test_subtitle_detect.py, tests/test_v1_1_features.py
known_risks:
  - 收紧阈值后，部分真实英文硬字幕视频可能从 EN 降为 NONE，导致双语烧录更冗余
  - 现有 OCR 区域和纯规则方案仍无法覆盖所有屏幕录制边缘场景
