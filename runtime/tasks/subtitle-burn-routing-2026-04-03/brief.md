task_id: subtitle-burn-routing-2026-04-03
objective: 收敛下游字幕烧录链路，降低硬编码策略风险，并让 ZH 硬字幕视频产出双语成片
selected_approach: 将 auto EN 置信度阈值和硬字幕边界回退值移入配置；在烧录层为 EN/ZH 硬字幕分别提供单语叠加路径；把检测与实际烧录策略写回 clip metadata
rejected_approaches:
  - 继续保持 ZH 状态直接复制源视频：无法满足双语成片需求
  - 只在日志里打印策略不落 metadata：后续排查仍需翻日志
key_assumptions:
  - ZH 硬字幕视频默认也是底部硬字幕，因此 EN 软字幕应上移到硬字幕上方
  - 将策略参数移入 config 不会影响现有 CLI 使用方式
scope: config.py, embed_subtitles.py, tests/test_v1_1_features.py, tests/test_whisper_zh_fallback.py
known_risks:
  - 对 ZH 硬字幕视频增加 EN 顶部字幕后，部分视频可能出现顶部空间偏紧
  - 批处理回写 metadata 会改变 clips_metadata.json 的结构，需要保持向后兼容
  - EN 硬字幕 fallback 路径需要保持为高级备用能力，不能误变成主路径
