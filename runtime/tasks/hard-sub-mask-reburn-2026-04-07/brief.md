task_id: hard-sub-mask-reburn-2026-04-07
objective: 将存在硬字幕的视频默认改为遮挡原字幕区域并统一烧录 EN+ZH 双语成片
selected_approach: 复用现有 NONE 路线的双语时间轴与 FFmpeg 烧录，新增基于检测边界的纯色遮罩层；EN/ZH/BILINGUAL 在 auto 下统一走 mask+dual burn，保留 NONE 不变
rejected_approaches:
  - 保留原硬字幕并继续做视觉时间对齐：时间轴混用导致双语不同步，复杂度高且用户感知差
  - 全量依赖 moviepy 逐帧遮挡：成本高、速度慢、无必要
key_assumptions:
  - subtitle_boundary 足以估算需要遮挡的底部字幕区域
  - 用户当前更看重双语同步和稳定性，而非保留原视频字幕设计
scope: embed_subtitles.py, 可能涉及 main/metadata/tests，保持 subtitle_detect.py 主判定接口不变
known_risks:
  - 遮罩高度过大可能伤画面
  - 顶部或中部硬字幕样本仍可能不适配当前底部遮罩策略
