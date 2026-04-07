# 本地运行测试指南

**适用项目**: short-video-tool  
**更新日期**: 2026-03-16

---

## 前置准备

```bash
# 进入项目目录
cd /Users/jackwl/.openclaw/agents/it-team/code-agent/workspace/projects/short-video-tool

# 激活虚拟环境
source venv/bin/activate

# 确认 ffmpeg 已安装（音画同步测试需要）
ffmpeg -version
# 如未安装：brew install ffmpeg
```

---

## 完整模拟 CI（三个 Job）

### Job 1：代码质量检查

```bash
pip install ruff black
black --check --diff .
ruff check .
```

预期输出：
- black：列出格式不规范的文件（有 continue-on-error，不阻断）
- ruff：列出代码规范问题（有 continue-on-error，不阻断）

---

### Job 2：测试套件（最重要）

```bash
pip install pytest pytest-cov
pytest tests/ --cov=. --cov-report=term-missing -v
```

预期输出示例：
```
tests/test_analyzer.py::TestAnalyzerExceptions::test_nonexistent_file PASSED
tests/test_clipper.py::TestClipperExceptions::test_nonexistent_video PASSED
tests/test_subtitle_quality.py::TestSRTFormat::test_srt_parser_reads_entries PASSED
tests/test_av_sync.py::TestAudioVideoSync::test_ffprobe_available PASSED
...
XX passed, XX skipped in XX.Xs

---------- coverage ----------
downloader.py    77%
analyzer.py      77%
clipper.py       77%
...
TOTAL            77%
```

---

### Job 3：安全漏洞扫描

```bash
pip install safety
safety check --file requirements.txt
```

---

## 常用快捷命令

### 只跑不需要真实视频的用例（等价于 CI 环境）

```bash
pytest tests/ -v -m "not slow"
```

这是最常用的本地验证命令，速度快（约 10-30 秒），覆盖所有纯逻辑测试。

---

### 只跑新增的两个缺口测试

```bash
# 翻译质量测试
pytest tests/test_subtitle_quality.py -v

# 音画同步测试
pytest tests/test_av_sync.py -v

# 两个一起跑
pytest tests/test_subtitle_quality.py tests/test_av_sync.py -v
```

---

### 只跑某一个测试文件

```bash
pytest tests/test_clipper.py -v
pytest tests/test_analyzer.py -v
pytest tests/test_downloader.py -v
pytest tests/test_translator.py -v
pytest tests/test_integrator.py -v
pytest tests/test_e2e.py -v
```

---

### 只跑某一个具体用例

```bash
pytest tests/test_av_sync.py::TestAudioVideoSync::test_ffprobe_available -v
```

---

### 跑所有测试（包含需要真实视频的 slow 用例）

```bash
pytest tests/ -v
```

注意：标了 `@pytest.mark.slow` 的用例需要真实视频文件，没有视频会自动 skip。

---

### 生成 HTML 覆盖率报告（可在浏览器查看）

```bash
pytest tests/ --cov=. --cov-report=html -v
open htmlcov/index.html
```

---

## 测试文件说明

| 文件 | 测试内容 | CI 能跑？ |
|------|---------|---------|
| test_analyzer.py | Analyzer 异常处理 + 功能验证 | 部分（slow 需视频）|
| test_clipper.py | Clipper 异常处理 + 剪辑功能 | 部分（slow 需视频）|
| test_downloader.py | Downloader 初始化 + URL 验证 + mock 下载 | ✅ 全部 |
| test_translator.py | Translator 初始化 + 字幕生成 | 部分（slow 需数据）|
| test_integrator.py | Integrator 初始化 + 整合功能 | 部分（slow 需数据）|
| test_e2e.py | 端到端完整流程 | 部分（slow 需视频）|
| test_subtitle_quality.py | 翻译质量：SRT 格式 + 时间戳 + 翻译内容 | ✅ 全部（slow 需字幕）|
| test_av_sync.py | 音画同步：ffprobe 检测音视频流 | ✅ 全部（slow 需片段）|

---

## 失败时怎么看

pytest 失败会输出具体原因，例如：

```
FAILED tests/test_av_sync.py::TestAudioVideoSync::test_audio_video_duration_within_tolerance
AssertionError: clips/video_clip_1.mp4: audio-video duration mismatch 0.823s
(video=45.120s, audio=44.297s, tolerance=0.5s)
```

```
FAILED tests/test_subtitle_quality.py::TestSRTFormat::test_translation_differs_from_original
AssertionError: Translation must differ from original text
```

看 `FAILED` 行 + `AssertionError` 行，就能定位是哪个文件、哪个检查点出了问题。

---

## 标记说明

| 标记 | 含义 |
|------|------|
| `@pytest.mark.slow` | 需要真实视频/数据，CI 中 skip，本地有数据时才跑 |
| `@pytest.mark.e2e` | 端到端测试，需要完整流程数据 |
| 无标记 | 纯逻辑测试，CI 和本地都会跑 |
