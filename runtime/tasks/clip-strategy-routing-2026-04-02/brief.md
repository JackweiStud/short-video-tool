task_id: clip-strategy-routing-2026-04-02
objective: 让 clip-strategy 成为真实的业务路径选择器，opinion/topic 跳过无关分析，hybrid 保留全量分析
selected_approach: 在 analyzer 中按策略裁剪分析步骤，在 clipper 中按策略限制 fallback 行为，并补充三种策略的回归测试
rejected_approaches:
  - 仅修改日志和文档：不能改变实际耗时和行为语义
  - 只在 clipper 层忽略无关结果：analyzer 仍会浪费时间跑无关分析
key_assumptions:
  - 用户期望 opinion/topic 只走各自主路径，而不是隐式跑 hybrid
  - 现有 downstream 允许 audio_climax_points 和 scene_changes 为空列表
scope: analyzer.py, clipper.py, tests/test_analyzer.py, tests/test_clipper.py
known_risks:
  - opinion/topic 在 topic segmentation 失败时会更早返回空结果，而不是自动回退
  - 现有依赖全量 analysis_result 的外部脚本如果假设 scene_changes 一定非空，可能需要同步适配
