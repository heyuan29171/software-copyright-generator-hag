"""Masking secrets before they reach a submitted document.

Identifying material is sent to a registration body and may be shown to
reviewers. A hard-coded API key or a colleague's phone number does not belong
in it, and the risk is not hypothetical: source trees routinely contain test
credentials that nobody cleaned up.

The masks keep the shape of the value, so the code still reads as code and a
reviewer can see a secret was there. Removing the line entirely would create a
gap in the program; replacing the whole value with stars would misrepresent the
literal's type.

What is masked:
  * values assigned to keys that name a credential
  * well-known token prefixes (sk-, ghp_, AKIA, ...)
  * private IP addresses
  * mainland mobile numbers
  * email addresses

Detecting is always on. Masking changes the text, so it is reported for every
match: the summary states how many values were replaced, and the caller decides
whether that is acceptable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MaskRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str

    def apply(self, line: str) -> tuple[str, int]:
        """Return the masked line and how many replacements were made."""
        return self.pattern.subn(self.replacement, line)


# Each rule keeps enough of the value to show that something was removed while
# making it unusable.
_MASK_RULES: tuple[MaskRule, ...] = (
    MaskRule(
        "凭据赋值",
        re.compile(
            r"""((?:api[_-]?key|secret|token|passwd|password|access[_-]?key|"""
            r"""app[_-]?secret|private[_-]?key|auth[_-]?token)\s*[:=]\s*["'])([^"']{4,})(["'])""",
            re.IGNORECASE,
        ),
        r"\1****\3",
    ),
    MaskRule(
        "平台密钥",
        # The common prefixes fall into two shapes: `sk-...` and `ghp_...` use
        # a separator, while AWS access key IDs are `AKIA` followed directly by
        # sixteen characters. Both are covered.
        re.compile(
            r"\b(?:(?:sk|pk|ghp|gho|ghs|glpat|xox[baprs])[-_][A-Za-z0-9_-]{8,}"
            r"|(?:AKIA|ASIA)[A-Z0-9]{12,})\b"
        ),
        "****",
    ),
    MaskRule(
        "内网IP",
        re.compile(r"\b(?:10|192\.168|127)(?:\.\d{1,3}){2,3}\b"),
        "***.***.***.***",
    ),
    MaskRule(
        "内网IP",
        re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}\b"),
        "***.***.***.***",
    ),
    MaskRule(
        "手机号",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "***********",
    ),
    MaskRule(
        "邮箱",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "***@***",
    ),
)


def mask_line(line: str) -> tuple[str, list[str]]:
    """Mask one line, returning the result and the names of rules that fired."""
    fired: list[str] = []
    text = line
    for rule in _MASK_RULES:
        text, count = rule.apply(text)
        if count:
            fired.append(rule.name)
    return text, fired


@dataclass(slots=True)
class MaskReport:
    total_replacements: int = 0
    rules: dict[str, int] = None  # type: ignore[assignment]
    samples: list[tuple[str, int]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rules is None:
            self.rules = {}
        if self.samples is None:
            self.samples = []

    def record(self, path: str, line_no: int, names: list[str]) -> None:
        self.total_replacements += 1
        for name in names:
            self.rules[name] = self.rules.get(name, 0) + 1
        if len(self.samples) < 10:
            self.samples.append((path, line_no))

    @property
    def fired(self) -> bool:
        return self.total_replacements > 0

    def summary(self) -> str:
        if not self.fired:
            return "未发现需要脱敏的内容"
        parts = "、".join(f"{name} {count} 处" for name, count in sorted(self.rules.items()))
        return f"已脱敏 {self.total_replacements} 行：{parts}"


def looks_like_secret_assignment(line: str) -> bool:
    """True when the line assigns something to a credential-looking key.

    Kept for callers that want to flag a line without masking it.
    """
    return any(rule.name == "凭据赋值" and rule.apply(line)[1] for rule in _MASK_RULES)
