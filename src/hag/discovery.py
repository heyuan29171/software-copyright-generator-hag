"""Project discovery.

Identify source files by **content**, not by a fixed extension whitelist. A
Godot project, a GameMaker script or a language invented next year should not
need a code change to be picked up. Only explicit exclusions (build output,
dependencies, binary assets, minified bundles) are name-based.

Every skipped file is reported with a reason. Silent skipping is how material
ends up inexplicably short, which then pushes a project below the 60-page
threshold and forces an arbitrary fallback.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Directories never walked. Matched on plain name at any depth.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".vs",
        "__pycache__",
        "node_modules",
        "bower_components",
        "vendor",
        "dist",
        "build",
        "out",
        "target",
        "coverage",
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".turbo",
        ".cache",
        ".venv",
        "venv",
        "env",
        ".eggs",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        # Generated materials must never be re-ingested.
        "软件著作权申请资料",
    }
)

# Filenames never included.
SKIP_FILES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        ".DS_Store",
        "Thumbs.db",
    }
)

SKIP_SUFFIXES = frozenset(
    {
        # binary / media
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svg",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".class",
        ".jar",
        ".pyc",
        ".o",
        ".a",
        ".wasm",
        # generated / minified
        ".map",
        ".min.js",
        ".min.css",
        # docs and formats that are not source
        ".md",
        ".txt",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }
)

# Treated as binary if a NUL byte appears in the first block.
_BINARY_SNIFF_BYTES = 8192
_MAX_FILE_BYTES = 2 * 1024 * 1024


class SkipReason(str, Enum):
    BINARY = "binary"
    TOO_LARGE = "too_large"
    EMPTY = "empty"
    LOCK_FILE = "lock_file"
    GENERATED = "generated"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class SkippedFile:
    path: Path
    reason: SkipReason
    detail: str = ""


@dataclass(slots=True)
class SourceFile:
    path: Path
    rel_path: str
    suffix: str
    size: int
    text: str
    encoding: str = "utf-8"


@dataclass(slots=True)
class DiscoveryResult:
    sources: list[SourceFile] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.sources)


def _looks_generated(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered.endswith(".min.js")
        or lowered.endswith(".min.css")
        or lowered.endswith(".bundle.js")
        or "_pb2.py" in lowered
        or lowered.endswith(".pb.go")
        or lowered.endswith(".designer.cs")
        or lowered.endswith(".g.dart")
    )


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode source bytes, trying the encodings that appear in real repos.

    Returns (text, encoding_name). Raises UnicodeDecodeError only if every
    candidate fails.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"

    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", data, 0, len(data), "no candidate encoding decoded the file")


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


def iter_files(root: Path) -> Iterator[Path]:
    """Walk `root`, pruning dependency and build directories."""
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            if entry.name in SKIP_DIRS:
                continue
            yield from iter_files(entry)
        elif entry.is_file():
            yield entry


def discover(root: Path, *, max_bytes: int = _MAX_FILE_BYTES) -> DiscoveryResult:
    """Scan `root` and return decodable source files plus skip reasons."""
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    result = DiscoveryResult()

    for path in iter_files(root):
        name = path.name
        rel = path.relative_to(root).as_posix()

        if name in SKIP_FILES:
            result.skipped.append(SkippedFile(path, SkipReason.LOCK_FILE))
            continue

        lowered = name.lower()
        if any(lowered.endswith(suffix) for suffix in SKIP_SUFFIXES):
            result.skipped.append(SkippedFile(path, SkipReason.GENERATED, "excluded by suffix"))
            continue

        if _looks_generated(name):
            result.skipped.append(SkippedFile(path, SkipReason.GENERATED, "generated/minified"))
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            result.skipped.append(SkippedFile(path, SkipReason.UNREADABLE, str(exc)))
            continue

        if size == 0:
            result.skipped.append(SkippedFile(path, SkipReason.EMPTY))
            continue
        if size > max_bytes:
            result.skipped.append(
                SkippedFile(path, SkipReason.TOO_LARGE, f"{size} bytes > {max_bytes}")
            )
            continue

        try:
            data = path.read_bytes()
        except OSError as exc:
            result.skipped.append(SkippedFile(path, SkipReason.UNREADABLE, str(exc)))
            continue

        if _is_binary(data):
            result.skipped.append(SkippedFile(path, SkipReason.BINARY))
            continue

        try:
            text, encoding = decode_bytes(data)
        except UnicodeDecodeError as exc:
            result.skipped.append(SkippedFile(path, SkipReason.UNREADABLE, str(exc)))
            continue

        result.sources.append(
            SourceFile(
                path=path,
                rel_path=rel,
                suffix=path.suffix.lower(),
                size=size,
                text=text,
                encoding=encoding,
            )
        )

    return result


def summarize_by_suffix(sources: Iterable[SourceFile]) -> list[tuple[str, int, int]]:
    """Aggregate (suffix, file count, total bytes) sorted by byte volume."""
    buckets: dict[str, list[int]] = {}
    for source in sources:
        bucket = buckets.setdefault(source.suffix or "<none>", [0, 0])
        bucket[0] += 1
        bucket[1] += source.size
    return [(suffix, count, size) for suffix, (count, size) in sorted(buckets.items(), key=lambda kv: -kv[1][1])]
