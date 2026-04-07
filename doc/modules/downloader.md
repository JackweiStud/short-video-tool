# 下载模块 (Downloader) 文档

## 功能概述

下载模块负责从公开视频平台获取源视频，并抽取后续流程需要的元数据。
它本身不处理账号登录，而是把下载逻辑交给 `yt-dlp`。

## 支持的平台

- YouTube
- TikTok
- X / Twitter

## 核心功能

### F1.1 - 视频下载

- 使用 `yt-dlp`
- 支持 `best`、`worst`、`720p`、`1080p` 等质量参数
- 自动重试下载和分片下载失败

### F1.2 - 元数据提取

下载完成后会返回常用元数据，例如：

- `filepath`
- `title`
- `uploader`
- `duration`
- `description`
- `upload_date`
- `view_count`
- `like_count`
- `original_url`
- `webpage_url`

## 当前配置方式

下载器里原先写死的浏览器 cookies 和 YouTube `player_client` 已经迁到 `config.py`。

### 相关配置项

- `YTDLP_COOKIES_BROWSER`
  - 默认值：`chrome`
  - 作用：告诉 `yt-dlp` 从哪个浏览器读取 cookies
  - 设为空字符串时：禁用 cookies 读取

- `YTDLP_YOUTUBE_PLAYER_CLIENT`
  - 默认值：`tv`
  - 作用：给 YouTube extractor 传递更稳的 `player_client`
  - 设为空字符串时：不传该参数

## X / Twitter 下载策略

项目当前策略是“复用本机浏览器登录态”，不是“项目内登录”。

也就是说：

1. 先在本机 Chrome 登录 X/Twitter
2. `yt-dlp` 通过 `cookiesfrombrowser` 读取 cookies
3. 再尝试下载目标视频

因此，当前下载器：

- 不保存账号密码
- 不弹登录窗口
- 不自己维护 cookie 文件

## 使用方式

### 基本示例

```python
from downloader import Downloader

downloader = Downloader()
result = downloader.download_video(
    url="https://www.youtube.com/watch?v=VIDEO_ID",
    quality="best",
)

print(result["filepath"])
print(result["title"])
```

### 输入

- `url`：视频链接
- `quality`：下载质量

### 输出

返回值包含文件路径和常用元数据，适合直接交给后续分析模块。

## 已知限制

### TikTok

- 可能受地区、IP 或 cookies 限制
- 某些视频需要代理或额外登录态

### X / Twitter

- 某些推文需要登录后才能访问
- 某些视频可能受地区或权限限制
- 当前仓库没有额外实现“手工 cookie 文件上传”流程

## 推荐验证方式

下载模块不再依赖旧的 `verify_downloader.py`。
推荐的验证方式：

```bash
python -m pytest -q tests/test_downloader.py tests/test_config_integration.py
```

做业务验证时，建议直接跑一个公开视频链接：

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

## 依赖

- `yt-dlp`
- `ffmpeg`
- Chrome 浏览器（当你需要复用登录态下载 X/Twitter 视频时）
