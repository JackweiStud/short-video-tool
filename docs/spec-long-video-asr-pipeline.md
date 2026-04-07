# 规格文档：长视频稳定 ASR 流水线改造

> 状态：待确认 | 版本：v1.0 | 日期：2026-03-25

---

## 1. 目标

让 short-video-tool 能稳定处理 30 分钟以上（含 2.5 小时）的长视频，ASR 阶段不再卡死、不再需要人工重启，支持断点续跑，最终输出完整字幕段列表供后续 clip/translate/embed 使用。

---

## 2. 范围 / 非目标

### 范围
- 替换 `analyzer.py` 中 `_run_asr` 的 ASR 引擎为 faster-whisper（Python API 调用）
- 实现音频分段逻辑（每段 ≤ 10 分钟，段间 5 秒 overlap 防边界截断）
- 实现段级缓存（`.cache/asr/chunk_N.json`），支持断点续跑
- 实现段级 timeout（600 秒/段），超时标记失败段，不阻塞整体
- 实现失败段重试机制（`--retry-failed` 模式）
- VAD 静音过滤（视频 > 60 分钟时自动开启）
- 分段结果合并为统一时间轴，输出格式与现有 `asr_result` 完全兼容
- 保留 openai-whisper CLI 作为 fallback（faster-whisper import 失败时自动降级）
- 在 venv 中安装 faster-whisper 依赖
- 更新 `requirements.txt`

### 非目标
- 不修改 clipper / translator / embed_subtitles / integrator
- 不修改 main.py 调用接口（`analysis_result` 结构保持不变）
- 不修改 config.py 已有配置项（只新增）
- 不做云端 ASR 接入
- 不做 GPU/MPS 加速（当前 CPU-only，后续可扩展）
- 不处理非音频分析部分（scene detection、audio features 不动）

---

## 3. 验收标准（必须是业务结果）

- [ ] `faster-whisper` 成功安装进 venv，`import faster_whisper` 无报错，提供安装日志
- [ ] 用 `downloads/Lex Fridman CEO of NVIDIA.mp4`（2.5 小时）跑 ASR，**全程不卡死**，有分段进度输出，提供完整运行日志
- [ ] ASR 在 **90 分钟内**完成（faster-whisper small，CPU，含 VAD），提供开始/结束时间戳
- [ ] 输出 `asr_result` 段列表非空，包含正确 `start/end/text` 字段，提供前 10 段内容截图或日志
- [ ] 断点续跑验证：中途手动 kill，重新运行，已完成分段跳过，从断点继续，提供日志证明
- [ ] 对现有短视频（< 10 分钟）跑通无回归，提供一条短视频的运行日志
- [ ] fallback 机制验证：注释掉 faster_whisper import，确认自动降级到 whisper CLI，提供日志

**不接受**：
- ❌ "代码没报错"
- ❌ "函数逻辑正确"
- ❌ 只跑短视频不跑长视频
- ❌ 无断点续跑实证

---

## 4. 关键约束

### 路径
- 项目根目录：`/Users/jackwl/.openclaw/agents/it-team/code-agent/workspace/projects/short-video-tool/`
- 虚拟环境：`./venv/`（使用 `./venv/bin/pip` 安装依赖）
- 段级缓存目录：`./cache/asr/`（自动创建，不纳入 git）
- 长视频测试文件：`./downloads/Lex Fridman CEO of NVIDIA.mp4`

### 依赖安装
```bash
# 必须在项目 venv 中安装，不影响全局环境
/Users/jackwl/.openclaw/agents/it-team/code-agent/workspace/projects/short-video-tool/venv/bin/pip install faster-whisper
```
- 安装后更新 `requirements.txt` 追加 `faster-whisper`
- 现有 Homebrew 全局 whisper CLI（`/opt/homebrew/bin/whisper`）保留不动
- 模型缓存：faster-whisper 会自动下载 CT2 格式 small 模型（~250MB），缓存在 `~/.cache/huggingface/`，**不影响** `~/.cache/whisper/*.pt`

### 接口兼容性（强制）
- `_run_asr` 返回值格式不变：
```python
[
  {"start": 0.0, "end": 2.5, "text": "Hello world",
   "words": [{"word": "Hello", "start": 0.0, "end": 0.4}, ...]},
  ...
]
```
- `analyze()` 方法签名不变
- `main.py` 无需任何修改

### 资源限制
- 内存：分段处理，峰值控制在 ~600MB（模型 400MB + 单段音频 ~150MB）
- 磁盘：段缓存约 50MB/小时视频，处理完成后可手动清理
- 并发：单进程顺序处理，不并行（避免内存叠加）

---

## 5. 技术方案

### 5.1 整体流程

```
视频文件
  → [已有] ffmpeg 提取音频 WAV（analyzer.py _extract_audio）
  → [新增] 按 10 分钟分段（overlap 5s）
  → [新增] 遍历每段：
      ├── 检查段缓存 .cache/asr/chunk_N.json → 命中则跳过
      ├── faster-whisper 推理（timeout 600s）
      ├── 写段缓存
      └── 输出进度：[3/15] chunk 3 done, 2m34s
  → [新增] 合并所有段，修正时间轴偏移，去除 overlap 重叠
  → 返回统一 asr_result（格式不变）
```

### 5.2 分段参数

| 参数 | 值 | 说明 |
|------|-----|------|
| chunk_duration | 600s（10 分钟） | 单段最大时长 |
| overlap | 5s | 段间重叠，防边界截断 |
| timeout_per_chunk | 600s | 单段 ASR 超时 |
| vad_threshold | 自动（视频 > 60 分钟时开启） | 静音过滤 |

### 5.3 新增 config 项（config.py）

```python
use_faster_whisper: bool  # 默认 True，False 强制走 whisper CLI
asr_chunk_duration: int   # 默认 600（秒）
asr_chunk_overlap: int    # 默认 5（秒）
asr_chunk_timeout: int    # 默认 600（秒）
asr_cache_dir: str        # 默认 "cache/asr"
asr_vad_filter: str       # "auto"（默认）| "on" | "off"
```

### 5.4 fallback 降级链

```
try: import faster_whisper
    → 使用 faster-whisper Python API（主路径）
except ImportError:
    → 使用现有 whisper CLI subprocess（fallback，原逻辑保留）
```

### 5.5 缓存 key 设计

```
.cache/asr/{video_md5}_{model}_{language}_chunk{N:03d}.json
```
- `video_md5`：取视频文件前 1MB 的 MD5（快速，不读全文件）
- 确保不同视频、不同模型、不同语言的缓存互不干扰

---

## 6. 测试关注点（给 testAgent）

- 验证 2.5 小时长视频 ASR 全程不卡死，有进度输出
- 验证段缓存命中逻辑：已完成段不重跑
- 验证断点续跑：kill 后重启，从断点继续
- 验证 timeout：人工注入慢速段，确认 600s 后超时标记，不阻塞后续段
- 验证 overlap 去重：合并后时间轴无重复段
- 验证短视频（< 10 分钟）无回归
- 验证 fallback：faster_whisper 不可用时降级到 whisper CLI
- 验证 `asr_result` 输出格式与原格式 100% 兼容

---

## 7. 交付要求

1. **必须提供可直接运行的验证命令**
```bash
# 完整长视频测试
cd /Users/jackwl/.openclaw/agents/it-team/code-agent/workspace/projects/short-video-tool
source venv/bin/activate
python main.py --file "downloads/Lex Fridman CEO of NVIDIA.mp4" --language en

# 断点续跑测试（先 kill 再重跑）
python main.py --file "downloads/Lex Fridman CEO of NVIDIA.mp4" --language en --resume

# 短视频回归测试
python main.py --file "downloads/Blurry： Cursor Now Shows You Demos, Not Diffs.mp4" --language en
```

2. **必须提供业务结果验证**
   - 长视频完整运行日志（含进度输出和总耗时）
   - `asr_result` 前 10 段内容（证明转录有效）
   - 断点续跑前后日志对比（证明缓存命中）

3. **风险主动暴露**
   - 开工前：预计风险点
   - 进行中：阻塞超过 15 分钟立即报告

---

## 8. 预期时间

- 编码实现：2-3 小时
- 长视频验证（2.5 小时视频跑一遍）：1-2 小时
- 合计：**3-5 小时**

---

## 9. 风险说明

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| faster-whisper CT2 模型下载失败 | 低 |
---

## 9. 风险说明

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| faster-whisper CT2 模型首次下载慢（需网络） | 低 | 首次运行延迟 | 安装后预热一次：`python -c "from faster_whisper import WhisperModel; WhisperModel('small')"`  |
| overlap 合并时产生重复文本 | 中 | 字幕重叠 | 合并时以时间轴去重，后段优先（overlap 区域取后段结果） |
| 某段 ASR 超时（600s） | 低 | 该段缺失 | 标记为失败段，跳过继续，最终报告缺失段范围；支持 `--retry-failed` |
| 磁盘空间不足（缓存+WAV） | 低 | 写入失败 | 运行前检查可用空间 > 3GB；WAV 提取后可配置自动清理 |
| venv pip 安装 faster-whisper 依赖冲突 | 低 | 安装失败 | 安装前检查 Python 版本 >= 3.8；失败时输出明确错误 |

---

## 10. 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `analyzer.py` | 修改 | 新增分段逻辑、faster-whisper 主路径、缓存、timeout、合并 |
| `config.py` | 修改 | 新增 6 个 ASR 配置项 |
| `requirements.txt` | 修改 | 追加 `faster-whisper` |
| `venv/` | 安装 | `venv/bin/pip install faster-whisper` |
| `cache/asr/` | 新建目录 | 段级缓存目录（加入 .gitignore） |
| `.gitignore` | 修改 | 追加 `cache/` |

**不改动**：`main.py` / `clipper.py` / `translator.py` / `embed_subtitles.py` / `integrator.py`

---

> 文档路径：`docs/spec-long-video-asr-pipeline.md`
> 待 Jack 确认后由 techLeadAgent 派发给 codeAgent 执行。
