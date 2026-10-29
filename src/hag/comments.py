"""Comment stripping.

The statute does not mention comments, but the registration centre's practice
is that identifying material contains no comments. This is recorded as [审]
(industry practice) rather than [法] in docs/STANDARDS.md, and the report says
so explicitly rather than claiming a legal basis.

Design notes:

* A character-level state machine, not regular expressions. Regex cannot tell
  a ``//`` inside a string literal from a line comment, and getting that wrong
  corrupts the material.
* Python is handled with the stdlib tokenizer plus ``ast`` so docstrings are
  removed and empty bodies are repaired with ``pass``.
* Every result records the method used, so the extraction manifest can be
  honest about which files had line-only stripping and may still contain
  trailing comments.
"""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass, field
from enum import Enum


class StripMethod(str, Enum):
    """How completely a file's comments could be removed."""

    FULL = "full"
    """Line, trailing, block comments and docstrings all removed."""

    LINE_ONLY = "line_only"
    """Only whole-line comments removed; trailing comments may remain."""

    NONE = "none"
    """No comment syntax known (e.g. JSON); nothing removed."""


@dataclass(frozen=True, slots=True)
class SpecialString:
    """A string literal whose syntax the generic scanner cannot infer.

    The state machine understands `"`, `'` and backtick, which covers most
    languages. A handful use additional forms that matter for comment
    stripping, because the body may contain `//` or `#`:

      * PowerShell here-strings `@" ... "@` span lines and are opened only at
        the end of a line, closed only at the start of one;
      * Groovy slashy strings `/.../` look exactly like division, and are only
        recognisable in an expression position;
      * HCL heredocs `<<EOF ... EOF`.

    `opener` and `closer` are literal text. `at_line_end` / `at_line_start`
    record the PowerShell rule that the delimiters must own their line.
    """

    opener: str
    closer: str
    escape: str = ""
    multiline: bool = True
    at_line_end: bool = False
    at_line_start: bool = False


@dataclass(frozen=True, slots=True)
class CommentSyntax:
    line: tuple[str, ...] = ()
    block: tuple[tuple[str, str], ...] = ()
    special: tuple[SpecialString, ...] = ()
    slashy_regex: bool = False
    """True for Groovy, where `/.../` is a string in expression position."""


_C_LIKE = CommentSyntax(line=("//",), block=(("/*", "*/"),))
_HASH = CommentSyntax(line=("#",))
_DASH = CommentSyntax(line=("--",))
_SEMI = CommentSyntax(line=(";",))
_HTML = CommentSyntax(block=(("<!--", "-->"),))

# Single-file components mix dialects: a Vue or Svelte file holds an HTML
# template, a script block and a style block, so all three comment styles can
# appear. Handling only the C-like pair left `<!-- -->` in place, which is a
# real omission rather than a safe conservative choice.
_SFC = CommentSyntax(line=("//",), block=(("/*", "*/"), ("<!--", "-->")))

# PowerShell here-strings. The opener must end its line and the closer must
# begin one; inside, `#` is literal text, not a comment.
_POWERSHELL = CommentSyntax(
    line=("#",),
    block=(("<#", "#>"),),
    special=(
        SpecialString("@'", "'@", at_line_end=True, at_line_start=True),
        SpecialString('@"', '"@', at_line_end=True, at_line_start=True),
    ),
)

# Groovy slashy strings. `/` is division in most positions, so this is only
# treated as a string when the previous non-space character cannot end an
# expression. Getting it wrong in the other direction would delete code.
_GROOVY = CommentSyntax(
    line=("//",),
    block=(("/*", "*/"),),
    special=(
        SpecialString('"""', '"""', escape="\\"),
        SpecialString("'''", "'''"),
        SpecialString("$/", "/$", escape="\\"),
    ),
    slashy_regex=True,
)

# HCL/Terraform: `//` and `#` line comments, `/* */` blocks, and heredocs.
_HCL = CommentSyntax(
    line=("//", "#"),
    block=(("/*", "*/"),),
    special=(SpecialString('"', '"', escape="\\"),),
)

# R: `#` comments. Raw strings `r"( ... )"` may contain `#`.
_R_LANG = CommentSyntax(
    line=("#",),
    special=(SpecialString('r"(', ')"'), SpecialString('r"[', ']"')),
)

# Languages we can strip fully with the state machine.
_FULL_STRIP: dict[str, CommentSyntax] = {
    ".c": _C_LIKE,
    ".h": _C_LIKE,
    ".cc": _C_LIKE,
    ".cpp": _C_LIKE,
    ".cxx": _C_LIKE,
    ".hpp": _C_LIKE,
    ".cs": _C_LIKE,
    ".java": _C_LIKE,
    ".js": _C_LIKE,
    ".jsx": _C_LIKE,
    ".mjs": _C_LIKE,
    ".cjs": _C_LIKE,
    ".ts": _C_LIKE,
    ".tsx": _C_LIKE,
    ".kt": _C_LIKE,
    ".kts": _C_LIKE,
    ".swift": _C_LIKE,
    ".go": _C_LIKE,
    ".rs": _C_LIKE,
    ".dart": _C_LIKE,
    ".scala": _C_LIKE,
    ".php": _C_LIKE,
    ".groovy": _GROOVY,
    ".gvy": _GROOVY,
    ".gradle": _GROOVY,
    ".tf": _HCL,
    ".tfvars": _HCL,
    ".hcl": _HCL,
    ".r": _R_LANG,
    ".css": CommentSyntax(block=(("/*", "*/"),)),
    ".scss": CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    ".less": CommentSyntax(line=("//",), block=(("/*", "*/"),)),
    ".vue": _SFC,
    ".svelte": _SFC,
    ".html": _HTML,
    ".htm": _HTML,
    ".xml": _HTML,
    ".ps1": _POWERSHELL,
    ".psm1": _POWERSHELL,
}

# Languages where only whole-line comments can be removed safely here.
_LINE_ONLY_STRIP: dict[str, CommentSyntax] = {
    ".sh": _HASH,
    ".bash": _HASH,
    ".zsh": _HASH,
    ".rb": _HASH,
    ".py": _HASH,  # overridden by the tokenizer path
    ".yml": _HASH,
    ".yaml": _HASH,
    ".toml": _HASH,
    ".pl": _HASH,
    ".sql": _DASH,
    ".lua": _DASH,
    ".pas": CommentSyntax(line=("//",), block=(("{", "}"), ("(*", "*)"),)),
    ".bat": CommentSyntax(line=("REM ", "rem ", "::")),
    ".cmd": CommentSyntax(line=("REM ", "rem ", "::")),
}

NO_COMMENT_SUFFIXES = frozenset({".json", ".jsonc"})

# Files whose comments are preserved because they carry meaning to interpreters.
_SHEBANG = "#!"
_PY_ENCODING_MARKERS = ("coding:", "coding=")


@dataclass(slots=True)
class StripResult:
    """Text with comments removed, plus provenance for the manifest."""

    text: str
    method: StripMethod
    suffix: str
    removed_comment_lines: int = 0
    removed_blank_lines: int = 0
    note: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.method is StripMethod.FULL


def syntax_for(suffix: str) -> CommentSyntax | None:
    suffix = suffix.lower()
    return _FULL_STRIP.get(suffix) or _LINE_ONLY_STRIP.get(suffix)


def _strip_state_machine(text: str, syntax: CommentSyntax) -> tuple[str, int]:
    """Remove comments using a character-level state machine.

    Tracks string literals so that ``"http://example.com"`` keeps its slashes.
    Beyond the common `"`, `'` and backtick forms, a language may declare
    special strings (PowerShell here-strings, Groovy slashy strings, HCL and R
    raw strings) whose bodies would otherwise be mistaken for comments.

    A character state machine rather than regular expressions, because the
    question "is this `//` inside a string?" cannot be answered by matching
    patterns; it depends on everything before it on the line.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    removed_lines = 0
    line_in_comment = False

    in_string: str | None = None
    escaped = False

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        # Inside a string literal: copy verbatim until the terminator.
        if in_string is not None:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif text.startswith(in_string, i):
                out.append(text[i + 1 : i + len(in_string)])
                i += len(in_string) - 1
                in_string = None
            i += 1
            continue

        # A language-specific string whose body must be left untouched.
        handled_special = False
        for special in syntax.special:
            if not text.startswith(special.opener, i):
                continue
            if special.at_line_end:
                rest = text[i + len(special.opener) :]
                if not (rest.startswith("\n") or rest.startswith("\r\n")):
                    continue
            close_at = text.find(special.closer, i + len(special.opener))
            if close_at == -1:
                out.append(text[i:])
                i = n
            else:
                stop = close_at + len(special.closer)
                out.append(text[i:stop])
                i = stop
            handled_special = True
            break
        if handled_special:
            continue

        # Groovy slashy string: `/.../` only in expression position, so that
        # ordinary division like `total / count` is left alone.
        if (
            syntax.slashy_regex
            and ch == "/"
            and not text.startswith("//", i)
            and not text.startswith("/*", i)
        ):
            prev = _last_significant(out)
            if prev and prev in "(=,:[!&|?{;+-*%<>~^":
                close_at = _find_slashy_close(text, i + 1)
                if close_at is not None:
                    out.append(text[i : close_at + 1])
                    i = close_at + 1
                    continue

        # String starts.
        if ch in "\"'`":
            if ch == '"' and text.startswith('"""', i):
                in_string = '"""'
            elif ch == "'" and text.startswith("'''", i):
                in_string = "'''"
            else:
                in_string = ch
            out.append(ch)
            i += 1
            continue

        # Block comment start.
        started_block = False
        for open_tok, close_tok in syntax.block:
            if open_tok and text.startswith(open_tok, i):
                end = text.find(close_tok, i + len(open_tok))
                segment_end = n if end == -1 else end + len(close_tok)
                removed_lines += _newlines_in(text[i:segment_end])
                # Preserve the newlines so line numbers stay comparable.
                out.append("\n" * _newlines_in(text[i:segment_end]))
                i = segment_end
                started_block = True
                break
        if started_block:
            continue

        # Line comment start.
        matched_line = False
        for token in syntax.line:
            if token and text.startswith(token, i):
                end = text.find("\n", i)
                segment_end = n if end == -1 else end
                removed_lines += 1
                out.append("\n" * _newlines_in(text[i:segment_end]))
                i = segment_end
                matched_line = True
                break
        if matched_line:
            continue

        if ch == "\n":
            line_in_comment = False
        out.append(ch)
        i += 1

    _ = line_in_comment  # kept for readability of the state machine
    return "".join(out), removed_lines


def _last_significant(out: list[str]) -> str:
    """The most recent non-space character emitted so far.

    Used to decide whether a `/` starts a Groovy slashy string or is division:
    only an expression boundary before it makes it a string.
    """
    for chunk in reversed(out):
        for ch in reversed(chunk):
            if not ch.isspace():
                return ch
    return ""


def _find_slashy_close(text: str, start: int) -> int | None:
    """Index of the closing `/` of a Groovy slashy string, or None.

    A slashy string cannot span lines, so reaching the newline means this was
    division after all.
    """
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "/":
            return i
        if ch == "\n":
            return None
        i += 1
    return None


def _newlines_in(segment: str) -> int:
    return segment.count("\n")


def _strip_python(text: str) -> tuple[str, int, list[str]]:
    """Remove Python comments and docstrings, repairing emptied bodies.

    Uses the tokenizer so string contents are never touched, then ``ast`` to
    find docstrings. Returns (text, removed_comment_lines, warnings).
    """
    warnings: list[str] = []
    lines = text.splitlines(keepends=True)
    protected: set[int] = set()

    # Preserve shebang (line 1) and PEP 263 encoding declarations (lines 1-2).
    for idx in (0, 1):
        if idx < len(lines):
            stripped = lines[idx].lstrip()
            if stripped.startswith(_SHEBANG):
                protected.add(idx)
            elif any(marker in stripped for marker in _PY_ENCODING_MARKERS) and stripped.startswith(
                "#"
            ):
                protected.add(idx)

    comment_lines: set[int] = set()
    trailing_cut: dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                row = tok.start[0] - 1
                if row in protected:
                    continue
                # A comment occupying the whole line counts as a comment line.
                source_line = lines[row] if row < len(lines) else ""
                if source_line.strip().startswith("#"):
                    comment_lines.add(row)
                else:
                    # Trailing comment: cut from the comment's column onwards.
                    trailing_cut[row] = tok.start[1]
    except (tokenize.TokenError, IndentationError) as exc:
        warnings.append(f"python tokenizer failed: {exc}")
        return text, 0, warnings

    docstring_lines: set[int] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        warnings.append(f"python ast.parse failed: {exc}")
        tree = None

    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                continue
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    start = first.lineno - 1
                    end = getattr(first, "end_lineno", first.lineno) - 1
                    docstring_lines.update(range(start, end + 1))

    remove = comment_lines | docstring_lines
    kept: list[str] = []
    removed = 0

    for idx, line in enumerate(lines):
        if idx in remove:
            removed += 1
            if line.endswith("\n"):
                kept.append("\n")
            continue
        if idx in trailing_cut:
            cut = trailing_cut[idx]
            body = line[:cut].rstrip()
            kept.append(body + ("\n" if line.endswith("\n") else ""))
            continue
        kept.append(line)

    # Repair bodies that consisted only of a docstring: those need `pass`.
    repaired = _repair_empty_bodies("".join(kept), docstring_lines, warnings)
    return repaired, removed, warnings


def _repair_empty_bodies(text: str, removed: set[int], warnings: list[str]) -> str:
    """Insert `pass` where removing a docstring left a suite empty.

    This is the only place where material text is not literally the source.
    The extraction manifest records it.
    """
    try:
        ast.parse(text)
        return text
    except SyntaxError:
        pass

    lines = text.splitlines()
    repaired: list[str] = []
    for idx, line in enumerate(lines, start=1):
        repaired.append(line)
        if not line.rstrip().endswith(":"):
            continue
        # Look ahead: if the next non-blank line is not indented, the suite is empty.
        look = idx
        while look < len(lines) and not lines[look].strip():
            look += 1
        indent = len(line) - len(line.lstrip())
        if look >= len(lines):
            repaired.append(" " * (indent + 4) + "pass")
            continue
        next_line = lines[look]
        next_indent = len(next_line) - len(next_line.lstrip())
        if next_line.strip() and next_indent <= indent:
            repaired.append(" " * (indent + 4) + "pass")

    candidate = "\n".join(repaired)
    try:
        ast.parse(candidate)
    except SyntaxError as exc:
        warnings.append(f"python body repair did not yield parseable code: {exc}")
        return text
    warnings.append("inserted `pass` into a body left empty by docstring removal")
    _ = removed
    return candidate


def strip_comments(text: str, suffix: str) -> StripResult:
    """Remove comments from `text` according to `suffix`.

    Never raises on unknown suffixes: it reports ``StripMethod.NONE`` and lets
    the caller decide whether that is acceptable.
    """
    suffix = suffix.lower()

    if suffix == ".py":
        stripped, removed, warnings = _strip_python(text)
        return StripResult(
            text=stripped,
            method=StripMethod.FULL,
            suffix=suffix,
            removed_comment_lines=removed,
            warnings=warnings,
        )

    if suffix in NO_COMMENT_SUFFIXES:
        return StripResult(
            text=text,
            method=StripMethod.NONE,
            suffix=suffix,
            note="format has no comment syntax",
        )

    syntax = _FULL_STRIP.get(suffix)
    method = StripMethod.FULL
    if syntax is None:
        syntax = _LINE_ONLY_STRIP.get(suffix)
        method = StripMethod.LINE_ONLY if syntax else StripMethod.NONE

    if syntax is None:
        return StripResult(
            text=text,
            method=StripMethod.NONE,
            suffix=suffix,
            note="no comment syntax registered for this suffix",
        )

    if method is StripMethod.LINE_ONLY:
        stripped, removed = _strip_whole_lines_only(text, syntax)
        return StripResult(
            text=stripped,
            method=StripMethod.LINE_ONLY,
            suffix=suffix,
            removed_comment_lines=removed,
            note="only whole-line comments removed; trailing comments may remain",
            warnings=["trailing comments may remain; review this file manually"],
        )

    stripped, removed = _strip_state_machine(text, syntax)
    return StripResult(
        text=stripped,
        method=StripMethod.FULL,
        suffix=suffix,
        removed_comment_lines=removed,
    )


def _strip_whole_lines_only(text: str, syntax: CommentSyntax) -> tuple[str, int]:
    """Remove lines whose first non-space characters start a comment."""
    out: list[str] = []
    removed = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if any(stripped.startswith(tok) for tok in syntax.line if tok):
            removed += 1
            out.append("\n" if line.endswith("\n") else "")
            continue
        out.append(line)
    return "".join(out), removed
