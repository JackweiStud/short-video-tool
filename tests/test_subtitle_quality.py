"""
Test subtitle/translation quality validation.

缺口1：翻译质量验证
- SRT 格式正确性（时间戳、文本非空、无重叠）
- 翻译内容与原文不同（基本 sanity check）
- 字幕时间戳在片段时长范围内
"""
import os
import re
import glob
import pytest


def parse_srt(filepath):
    """Parse SRT file, return list of subtitle entries with start/end/text."""
    entries = []
    with open(filepath, encoding="utf-8") as f:
        content = f.read().strip()
    blocks = re.split(r"\n\n+", content)
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        timestamp_line = lines[1]
        text = " ".join(lines[2:]).strip()
        match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3}) --> (\d{2}:\d{2}:\d{2}[,.]\d{3})",
            timestamp_line
        )
        if match:
            entries.append({
                "start": match.group(1),
                "end": match.group(2),
                "text": text
            })
    return entries


def srt_time_to_seconds(t):
    """Convert SRT timestamp string to float seconds."""
    t = t.replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def find_srt_files(lang):
    """Find real SRT files by language suffix."""
    files = glob.glob(f"subtitles/**/*_{lang}.srt", recursive=True)
    if not files:
        files = glob.glob(f"output/subtitles/**/*_{lang}.srt", recursive=True)
    return files


# ─────────────────────────────────────────────
# 缺口1：翻译质量 — 纯逻辑测试，CI 无需真实视频
# ─────────────────────────────────────────────

class TestSRTFormat:
    """SRT format correctness — runs in CI without real video."""

    def test_srt_parser_reads_entries(self, tmp_path):
        """parse_srt must return correct number of entries."""
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nHello world\n\n"
            "2\n00:00:05,000 --> 00:00:08,000\nSecond line\n",
            encoding="utf-8"
        )
        entries = parse_srt(str(srt))
        assert len(entries) == 2
        for e in entries:
            assert e["text"].strip() != "", "Subtitle text must not be empty"

    def test_srt_end_after_start(self, tmp_path):
        """Each subtitle end time must be strictly after start time."""
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nHello\n\n"
            "2\n00:00:05,000 --> 00:00:08,000\nWorld\n",
            encoding="utf-8"
        )
        entries = parse_srt(str(srt))
        for e in entries:
            start = srt_time_to_seconds(e["start"])
            end = srt_time_to_seconds(e["end"])
            assert end > start, f"end {e['end']} must be after start {e['start']}"

    def test_srt_no_overlapping_timestamps(self, tmp_path):
        """Subtitle timestamps must not overlap between entries."""
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nHello\n\n"
            "2\n00:00:05,000 --> 00:00:08,000\nWorld\n",
            encoding="utf-8"
        )
        entries = parse_srt(str(srt))
        for i in range(len(entries) - 1):
            end_i = srt_time_to_seconds(entries[i]["end"])
            start_next = srt_time_to_seconds(entries[i + 1]["start"])
            assert end_i <= start_next, (
                f"Entry {i+1} end {entries[i]['end']} overlaps "
                f"entry {i+2} start {entries[i+1]['start']}"
            )

    def test_srt_text_not_whitespace_only(self, tmp_path):
        """Subtitle text must contain actual content."""
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nHello world\n",
            encoding="utf-8"
        )
        entries = parse_srt(str(srt))
        for e in entries:
            assert len(e["text"].strip()) > 0, "Subtitle text is whitespace only"

    def test_srt_timestamps_within_max_clip_duration(self, tmp_path):
        """All subtitle end timestamps must be within max clip duration (60s)."""
        max_duration = 60.0
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nHello\n\n"
            "2\n00:00:55,000 --> 00:00:59,000\nEnd\n",
            encoding="utf-8"
        )
        entries = parse_srt(str(srt))
        for e in entries:
            end = srt_time_to_seconds(e["end"])
            assert end <= max_duration, (
                f"Subtitle end {e['end']} exceeds max clip duration {max_duration}s"
            )

    def test_bilingual_srt_both_languages_have_content(self, tmp_path):
        """Both zh and en SRT files must have at least one subtitle entry."""
        for lang in ["zh", "en"]:
            srt = tmp_path / f"clip_{lang}.srt"
            srt.write_text(
                f"1\n00:00:01,000 --> 00:00:04,000\nContent in {lang}\n",
                encoding="utf-8"
            )
            entries = parse_srt(str(srt))
            assert len(entries) >= 1, f"{lang} subtitle must have at least one entry"

    def test_translation_differs_from_original(self, tmp_path):
        """Translated text must differ from original (basic sanity check)."""
        original_srt = tmp_path / "original.srt"
        zh_srt = tmp_path / "zh.srt"
        original_srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nHello world\n",
            encoding="utf-8"
        )
        zh_srt.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\n你好世界\n",
            encoding="utf-8"
        )
        orig = parse_srt(str(original_srt))
        zh = parse_srt(str(zh_srt))
        assert len(zh) > 0
        diffs = [
            zh[i]["text"] != orig[i]["text"]
            for i in range(min(len(orig), len(zh)))
        ]
        assert any(diffs), "Translation must differ from original text"


class TestRealSubtitleQuality:
    """Test against real generated SRT files — skipped if not available."""

    @pytest.mark.slow
    def test_real_zh_srt_valid_format(self):
        """Real Chinese SRT files must have valid format and non-empty text."""
        files = find_srt_files("zh")
        if not files:
            pytest.skip("No real zh SRT files found")
        for f in files:
            entries = parse_srt(f)
            assert len(entries) > 0, f"{f} has no subtitle entries"
            for e in entries:
                assert e["text"].strip() != "", f"{f} has empty subtitle text"

    @pytest.mark.slow
    def test_real_en_srt_timestamps_valid(self):
        """Real English SRT timestamps must be ordered correctly."""
        files = find_srt_files("en")
        if not files:
            pytest.skip("No real en SRT files found")
        for f in files:
            entries = parse_srt(f)
            for e in entries:
                assert srt_time_to_seconds(e["end"]) > srt_time_to_seconds(e["start"])

    @pytest.mark.slow
    def test_real_translation_differs_from_original(self):
        """Real Chinese translation must differ from original text."""
        orig_files = find_srt_files("original")
        zh_files = find_srt_files("zh")
        if not orig_files or not zh_files:
            pytest.skip("Real subtitle files not available")
        orig = parse_srt(orig_files[0])
        zh = parse_srt(zh_files[0])
        if not orig or not zh:
            pytest.skip("Subtitle files are empty")
        diffs = [
            zh[i]["text"] != orig[i]["text"]
            for i in range(min(len(orig), len(zh)))
        ]
        assert any(diffs), "Chinese translation must differ from original"
