task_id: translation-stability-topic-refine-2026-04-03
objective: 收紧 topic 候选纯度并提升 Siliconflow 字幕翻译稳定性，避免双主题块和逐行翻译失真
selected_approach: 在 analyzer 层继续收紧 topic 相邻主题合并条件；在 translator 中改为 JSON array 协议、增加内容级校验、缩小 chunk，并把失败降级粒度从整块降到子块/单行
rejected_approaches:
  - 仅调整 prompt：无法解决翻译返回格式脆弱和整体块级回退问题
  - 只在 clipper 层继续修 topic：会把策略语义继续混入 clip 后处理
key_assumptions:
  - 现有 Siliconflow 模型对 JSON array 指令遵循度高于逐行编号文本
  - topic 主题纯度问题主要来自 analyzer 层相邻主题合并过宽，而不是 clipper 时长修正
scope: analyzer.py, translator.py, tests/test_translator.py, tests/test_opinion_first_v1.py, tests/test_clipper.py
known_risks:
  - 更严格的翻译校验会提高 fallback 触发频率，带来额外请求和耗时
  - topic 合并阈值收紧后，部分视频可能返回更少的 clips
