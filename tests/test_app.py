"""The packaged application must fail visibly, not silently.

A windowed executable has no console, so an exception during startup normally
produces a window that flashes and vanishes. These tests check the entry point
reports the failure instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hag import app


class TestFatalReporting:
    def test_writes_a_log_when_no_console(self, tmp_path: Path, monkeypatch) -> None:
        """With no display and no console, a log file is the last resort."""
        monkeypatch.setattr(app, "_show_fatal", app._show_fatal)

        # Simulate the fallback path: tkinter unavailable.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):  # noqa: ANN001, ANN202
            if name.startswith("tkinter"):
                raise ImportError("no tkinter")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setattr(app.sys, "executable", str(tmp_path / "app.exe"))

        app._show_fatal("boom")

        log = tmp_path / "启动失败.txt"
        assert log.is_file()
        assert "boom" in log.read_text(encoding="utf-8")


class TestMain:
    def test_returns_nonzero_on_startup_failure(self, monkeypatch) -> None:
        def explode() -> int:
            raise RuntimeError("gui broke")

        monkeypatch.setattr("hag.gui.main", explode)
        reported: list[str] = []
        monkeypatch.setattr(app, "_show_fatal", reported.append)

        code = app.main()

        assert code == 1
        assert reported
        assert "gui broke" in reported[0]

    def test_reports_traceback_not_just_message(self, monkeypatch) -> None:
        def explode() -> int:
            raise ValueError("specific detail")

        monkeypatch.setattr("hag.gui.main", explode)
        reported: list[str] = []
        monkeypatch.setattr(app, "_show_fatal", reported.append)

        app.main()

        assert "ValueError" in reported[0]
        assert "specific detail" in reported[0]
