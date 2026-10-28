"""Font resolution and glyph-coverage checking.

Two problems this module exists to prevent:

1. A ``.ttc`` collection is accepted by fpdf2 but always uses face 0, which is
   not necessarily the face you wanted. The font is validated by checking that
   it actually contains the glyphs we are about to draw.

2. A font that renders Latin fine may have no CJK glyphs, producing blank
   boxes in the submitted material. Coverage is verified per character before
   rendering rather than discovered afterwards.

Nothing here renders. It answers "can this font draw this text?" so the
caller can fail loudly instead of shipping tofu.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Monospace fonts, preferred in this order. Code must align visually.
MONO_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
    "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
)

# CJK fonts. SimSun first because it is the conventional document typeface.
CJK_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/simsun.ttc",
    "/Library/Fonts/SimSun.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)

# Characters with no glyph in either font. These are structural, not content.
_INVISIBLE_OK = {"\n", "\r", "\t"}


class FontError(Exception):
    """Raised when a usable font cannot be located or lacks required glyphs."""


@dataclass(frozen=True, slots=True)
class ResolvedFont:
    path: Path
    label: str
    charset: frozenset[int]

    def has(self, char: str) -> bool:
        return ord(char) in self.charset


@dataclass(frozen=True, slots=True)
class FontPair:
    mono: ResolvedFont
    cjk: ResolvedFont

    def missing_characters(self, texts: Iterable[str]) -> list[str]:
        """Characters that neither font can draw, preserving first-seen order."""
        missing: list[str] = []
        seen: set[str] = set()
        for text in texts:
            for char in text:
                if char in _INVISIBLE_OK or char in seen:
                    continue
                if self.mono.has(char) or self.cjk.has(char):
                    continue
                seen.add(char)
                missing.append(char)
        return missing


def font_charset(path: Path) -> frozenset[int]:
    """Unicode code points the font can render.

    Falls back to an empty set when the font cannot be parsed, so the caller
    treats it as "cannot render anything" rather than assuming success.
    """
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise FontError("fontTools is required to inspect fonts") from exc

    try:
        font = TTFont(str(path), lazy=True, fontNumber=0)
    except Exception as exc:
        raise FontError(f"cannot open font {path}: {exc}") from exc

    try:
        cmap: dict[int, object] = {}
        for table in font["cmap"].tables:
            cmap.update(table.cmap)
        return frozenset(cmap)
    except Exception:
        return frozenset()
    finally:
        try:
            font.close()
        except Exception:
            pass


def resolve_font(candidates: Sequence[str], label: str) -> ResolvedFont | None:
    """First candidate that exists and exposes at least one glyph."""
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            charset = font_charset(path)
        except FontError:
            continue
        if not charset:
            continue
        return ResolvedFont(path=path, label=f"{label}:{path.name}", charset=charset)
    return None


def resolve_font_pair(workdir: Path | None = None) -> FontPair:
    """Locate one monospace font and one CJK font.

    `workdir` is accepted for future use (bundled fallback fonts) and is not
    currently read.
    """
    _ = workdir
    mono = resolve_font(MONO_CANDIDATES, "mono")
    if mono is None:
        raise FontError(
            "no monospace font found; tried: " + ", ".join(MONO_CANDIDATES)
        )
    cjk = resolve_font(CJK_CANDIDATES, "cjk")
    if cjk is None:
        raise FontError("no CJK font found; tried: " + ", ".join(CJK_CANDIDATES))
    return FontPair(mono=mono, cjk=cjk)


def assert_coverage(fonts: FontPair, texts: Iterable[str]) -> None:
    """Raise if any character cannot be drawn by either font.

    Kept for callers that genuinely require every glyph, such as the header,
    where a missing character would be visible on all sixty pages.
    """
    missing = fonts.missing_characters(texts)
    if missing:
        listed = ", ".join(f"{ch!r} (U+{ord(ch):04X})" for ch in missing[:20])
        more = "" if len(missing) <= 20 else f" and {len(missing) - 20} more"
        raise FontError(
            f"neither {fonts.mono.label} nor {fonts.cjk.label} can render: "
            f"{listed}{more}"
        )


# Substitution for characters the material fonts cannot draw.
UNRENDERABLE_SUBSTITUTE = "?"

# Range of code points that are almost certainly decorative when they appear in
# source: emoji, pictographs, dingbats used as bullets, variation selectors and
# zero-width joiners. A project with an emoji in a UI string is ordinary; it
# must not stop the whole run.
_DECORATIVE_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1FAFF),  # emoji and pictographs
    (0x2600, 0x27BF),  # misc symbols and dingbats
    (0x2B00, 0x2BFF),  # arrows and misc symbols
    (0xFE00, 0xFE0F),  # variation selectors
    (0x200D, 0x200D),  # zero-width joiner
    (0x1F1E6, 0x1F1FF),  # regional indicators (flags)
    (0xE0020, 0xE007F),  # tag characters
)


def is_decorative(char: str) -> bool:
    """True when a character is decorative rather than information."""
    code = ord(char)
    return any(low <= code <= high for low, high in _DECORATIVE_RANGES)


def sanitize_unrenderable(text: str, fonts: FontPair) -> tuple[str, list[str]]:
    """Replace characters neither font can draw, reporting what was lost.

    Replacing rather than aborting is the deliberate choice. An emoji in a UI
    string is a normal thing to find in a real project, and refusing to produce
    any material because of one is a worse outcome than printing a placeholder
    and saying so. The substitution is visible, never silent.
    """
    out: list[str] = []
    replaced: list[str] = []
    for char in text:
        if char in "\n\r\t":
            out.append(char)
            continue
        if fonts.mono.has(char) or fonts.cjk.has(char):
            out.append(char)
            continue
        out.append(UNRENDERABLE_SUBSTITUTE)
        if char not in replaced:
            replaced.append(char)
    return "".join(out), replaced


def environment_report() -> dict[str, str]:
    """Diagnostic snapshot for the environment check tool."""
    report: dict[str, str] = {}
    for label, candidates in (("mono", MONO_CANDIDATES), ("cjk", CJK_CANDIDATES)):
        found = next((c for c in candidates if os.path.isfile(c)), "")
        report[f"{label}_font"] = found or "<not found>"
    return report
