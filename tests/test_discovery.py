"""Discovery must never lose files silently and never ingest generated output."""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.discovery import (
    SkipReason,
    decode_bytes,
    discover,
    summarize_by_suffix,
)


def write(root: Path, rel: str, content: str | bytes, encoding: str = "utf-8") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding=encoding)
    return path


class TestBasicDiscovery:
    def test_finds_source_files(self, tmp_path: Path) -> None:
        write(tmp_path, "src/main.py", "print(1)\n")
        write(tmp_path, "src/app.ts", "const a = 1;\n")
        result = discover(tmp_path)
        assert {s.rel_path for s in result.sources} == {"src/main.py", "src/app.ts"}

    def test_relative_paths_use_forward_slashes(self, tmp_path: Path) -> None:
        write(tmp_path, "a/b/c.py", "x = 1\n")
        result = discover(tmp_path)
        assert result.sources[0].rel_path == "a/b/c.py"

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(NotADirectoryError):
            discover(tmp_path / "nope")


class TestDirectoryPruning:
    @pytest.mark.parametrize(
        "skip_dir", ["node_modules", "dist", "build", ".next", "coverage", "target", "vendor"]
    )
    def test_skips_dependency_dirs(self, tmp_path: Path, skip_dir: str) -> None:
        write(tmp_path, "src/keep.py", "x = 1\n")
        write(tmp_path, f"{skip_dir}/dropped.py", "y = 2\n")
        result = discover(tmp_path)
        assert [s.rel_path for s in result.sources] == ["src/keep.py"]

    def test_skips_nested_dependency_dirs(self, tmp_path: Path) -> None:
        write(tmp_path, "packages/a/node_modules/x/index.js", "1\n")
        write(tmp_path, "packages/a/src/index.js", "2\n")
        result = discover(tmp_path)
        assert [s.rel_path for s in result.sources] == ["packages/a/src/index.js"]

    def test_skips_own_output_dir(self, tmp_path: Path) -> None:
        write(tmp_path, "软件著作权申请资料/代码.py", "x = 1\n")
        write(tmp_path, "src/real.py", "y = 2\n")
        result = discover(tmp_path)
        assert [s.rel_path for s in result.sources] == ["src/real.py"]


class TestFileExclusion:
    def test_lock_file_skipped_with_reason(self, tmp_path: Path) -> None:
        write(tmp_path, "package-lock.json", "{}")
        result = discover(tmp_path)
        assert result.sources == []
        assert result.skipped[0].reason is SkipReason.LOCK_FILE

    def test_binary_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
        result = discover(tmp_path)
        assert result.sources == []
        assert any(s.reason is SkipReason.GENERATED for s in result.skipped)

    def test_nul_bytes_detected_as_binary(self, tmp_path: Path) -> None:
        write(tmp_path, "weird.dat.txt", b"abc\x00def")
        result = discover(tmp_path)
        assert result.sources == []

    def test_empty_file_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "empty.py", "")
        result = discover(tmp_path)
        assert result.skipped[0].reason is SkipReason.EMPTY

    def test_oversize_file_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "big.py", "x = 1\n" * 1000)
        result = discover(tmp_path, max_bytes=100)
        assert result.skipped[0].reason is SkipReason.TOO_LARGE

    def test_minified_bundle_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "app.min.js", "var a=1;\n")
        result = discover(tmp_path)
        assert result.sources == []

    def test_generated_protobuf_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "service_pb2.py", "x = 1\n")
        result = discover(tmp_path)
        assert result.sources == []

    def test_docs_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "README.md", "# hi")
        result = discover(tmp_path)
        assert result.sources == []


class TestEncodings:
    def test_utf8(self) -> None:
        text, enc = decode_bytes("中文".encode())
        assert text == "中文"
        assert enc == "utf-8"

    def test_utf8_bom(self) -> None:
        text, enc = decode_bytes("\ufeffx = 1\n".encode("utf-8"))
        assert text == "x = 1\n"
        assert enc == "utf-8-sig"

    def test_gb18030(self) -> None:
        text, enc = decode_bytes("中文注释".encode("gb18030"))
        assert text == "中文注释"
        assert enc == "gb18030"

    def test_undecodable_raises(self) -> None:
        # 0x81 0x30 is an invalid GB18030 two-byte sequence, and 0x81 alone is
        # not valid UTF-8 either, so no candidate encoding should accept it.
        with pytest.raises(UnicodeDecodeError):
            decode_bytes(b"\x81\x30\xff")


class TestReporting:
    def test_every_skip_has_a_reason(self, tmp_path: Path) -> None:
        write(tmp_path, "a.png", b"\x00")
        write(tmp_path, "b.py", "")
        write(tmp_path, "c.py", "x = 1\n")
        result = discover(tmp_path)
        assert len(result.skipped) == 2
        assert all(s.reason for s in result.skipped)

    def test_summary_by_suffix(self, tmp_path: Path) -> None:
        write(tmp_path, "a.py", "x = 1\n" * 10)
        write(tmp_path, "b.py", "y = 2\n")
        write(tmp_path, "c.ts", "let a = 1;\n")
        result = discover(tmp_path)
        summary = summarize_by_suffix(result.sources)
        assert summary[0][0] == ".py"
        assert summary[0][1] == 2

    def test_symbolic_extension_agnostic(self, tmp_path: Path) -> None:
        """Content is what matters; an unusual suffix must still be discovered."""
        write(tmp_path, "script.gd", "extends Node\n")
        result = discover(tmp_path)
        assert [s.rel_path for s in result.sources] == ["script.gd"]
