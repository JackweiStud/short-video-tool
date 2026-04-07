import os
from pathlib import Path
from unittest.mock import patch

import pytest

import main


def test_lock_file_defaults_to_project_tmp_dir():
    expected = os.path.join(main._PROJECT_ROOT, "tmp", "short-video-tool.lock")
    assert main.LOCK_FILE == expected


def test_acquire_lock_creates_project_local_lock_file(tmp_path, monkeypatch):
    lock_file = tmp_path / "tmp" / "short-video-tool.lock"
    monkeypatch.setattr(main, "LOCK_FILE", str(lock_file))

    main._acquire_lock()

    assert lock_file.exists()
    assert lock_file.read_text().strip() == str(os.getpid())


def test_acquire_lock_exits_immediately_on_live_lock(tmp_path, monkeypatch, capsys):
    lock_file = tmp_path / "tmp" / "short-video-tool.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("43210")
    monkeypatch.setattr(main, "LOCK_FILE", str(lock_file))

    with patch("main.os.kill") as mock_kill:
        with pytest.raises(SystemExit) as exc:
            main._acquire_lock()

    assert exc.value.code == 1
    mock_kill.assert_called_once_with(43210, 0)

    captured = capsys.readouterr()
    assert "不等待，立即退出，避免忙等待" in captured.err
    assert str(lock_file) in captured.err
    assert "持锁 PID: 43210" in captured.err
