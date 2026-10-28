"""One executable, two audiences.

The shipped file must open a window when double-clicked and speak MCP when a
client starts it with ``--mcp``. Getting the dispatch wrong is invisible until
someone's agent silently receives nothing, so the decision is tested directly.
"""

from __future__ import annotations

import pytest

from hag import app


class TestModeDispatch:
    def test_double_click_opens_window(self, monkeypatch) -> None:
        called: list[str] = []
        monkeypatch.setattr(app, "_run_mcp", lambda: called.append("mcp") or 0)
        monkeypatch.setattr(app, "_run_self_test", lambda: called.append("self") or 0)
        monkeypatch.setattr("hag.gui.main", lambda: called.append("gui") or 0)

        monkeypatch.setattr(app.sys, "argv", ["hag.exe"])
        app.main()

        assert called == ["gui"]

    def test_mcp_flag_serves_protocol(self, monkeypatch) -> None:
        called: list[str] = []
        monkeypatch.setattr(app, "_run_mcp", lambda: called.append("mcp") or 0)
        monkeypatch.setattr("hag.gui.main", lambda: called.append("gui") or 0)

        monkeypatch.setattr(app.sys, "argv", ["hag.exe", "--mcp"])
        app.main()

        assert called == ["mcp"]

    def test_self_test_flag_runs_verification(self, monkeypatch) -> None:
        called: list[str] = []
        monkeypatch.setattr(app, "_run_self_test", lambda: called.append("self") or 0)
        monkeypatch.setattr("hag.gui.main", lambda: called.append("gui") or 0)

        monkeypatch.setattr(app.sys, "argv", ["hag.exe", "--self-test"])
        app.main()

        assert called == ["self"]

    def test_self_test_wins_over_mcp(self, monkeypatch) -> None:
        """Both flags present is ambiguous; verification takes precedence so a
        broken build cannot hang waiting on a protocol client."""
        called: list[str] = []
        monkeypatch.setattr(app, "_run_mcp", lambda: called.append("mcp") or 0)
        monkeypatch.setattr(app, "_run_self_test", lambda: called.append("self") or 0)

        monkeypatch.setattr(app.sys, "argv", ["hag.exe", "--mcp", "--self-test"])
        app.main()

        assert called == ["self"]

    def test_unknown_flag_still_opens_window(self, monkeypatch) -> None:
        called: list[str] = []
        monkeypatch.setattr("hag.gui.main", lambda: called.append("gui") or 0)
        monkeypatch.setattr(app.sys, "argv", ["hag.exe", "--nonsense"])
        app.main()
        assert called == ["gui"]


class TestMcpSafety:
    def test_mcp_never_writes_to_stdout(self, monkeypatch, capsys) -> None:
        """stdout carries the protocol; a stray print corrupts the stream."""

        class FakeServer:
            def run(self) -> None:
                pass

        monkeypatch.setattr("hag.server.create_server", lambda: FakeServer())
        app._run_mcp()
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_mcp_failure_goes_to_stderr(self, monkeypatch, capsys) -> None:
        def boom() -> None:
            raise RuntimeError("server exploded")

        monkeypatch.setattr("hag.server.create_server", boom)
        code = app._run_mcp()
        captured = capsys.readouterr()
        assert code == 1
        assert captured.out == ""
        assert "exploded" in captured.err
