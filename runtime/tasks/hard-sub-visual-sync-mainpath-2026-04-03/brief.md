task_id: hard-sub-visual-sync-mainpath-2026-04-03
objective: 将 EN/ZH 硬字幕主路径的时间源统一升级为 SubtitleSync 视觉对齐结果，并保留回退
selected_approach: 在 embed_subtitles 主路径优先生成视觉对齐版目标语言 SRT，失败再回退现有 zh_aligned 或 en.srt
rejected_approaches:
  - 仅调烧录位置：不能解决硬字幕与新字幕时间轴不一致的问题
  - 继续只在 fallback 使用 SubtitleSync：主路径仍会持续出现时间不同步
key_assumptions:
  - SubtitleSync 对多数 EN/ZH 硬字幕样本能产出稳定的对齐窗口
  - 当前传入的 asr_segments 足以驱动视觉对齐后的翻译
scope: embed_subtitles.py, tests/test_v1_1_features.py, tests/test_whisper_zh_fallback.py
known_risks:
  - 视觉对齐在高噪声画面下可能失败，需要稳定回退到现有时间轴
