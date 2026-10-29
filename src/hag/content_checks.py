"""Checks for content that reviewers treat as a red flag.

These come from practice rather than the statute, and they all point at one
question: does this look like the applicant's own work, written by them, at a
plausible time?

  * Someone else's company name in the source suggests the code was taken
    from elsewhere.
  * Hard-coded dates can suggest the code predates the company or was written
    for another project.
  * Code more than a few years old can invite questions about originality.

None of these is disqualifying on its own. They are reported as warnings with
the location, because the fix is a judgement call: a copyright header from a
third-party library should be removed, while a date inside a test fixture is
usually harmless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Company markers. A match means "some organisation is named here", which is
# then compared against the applicant.
_COMPANY_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,20}?(?:有限公司|股份有限公司|有限责任公司|集团|科技公司|研究院|研究所))"
    r"|([A-Z][A-Za-z0-9&.\- ]{1,40}?(?:Inc\.?|Ltd\.?|LLC|Corp\.?|Corporation|Company))"
)

# Copyright-style declarations, which is where a borrowed name usually sits.
_COPYRIGHT_LINE = re.compile(
    r"(?:copyright|©|\(c\)|版权|版权所有)", re.IGNORECASE
)

_ISO_DATE = re.compile(r"\b(19|20)\d{2}[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b")
_YEAR_ONLY = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    path: str
    line_no: int
    text: str


@dataclass(slots=True)
class ContentSuspicion:
    """Everything questionable found across the selected material."""

    foreign_companies: list[Finding] = field(default_factory=list)
    dates: list[Finding] = field(default_factory=list)
    old_years: list[Finding] = field(default_factory=list)

    def add_company(self, path: str, line_no: int, text: str) -> None:
        if len(self.foreign_companies) < 50:
            self.foreign_companies.append(Finding("company", path, line_no, text))

    def add_date(self, path: str, line_no: int, text: str) -> None:
        if len(self.dates) < 50:
            self.dates.append(Finding("date", path, line_no, text))

    def add_old_year(self, path: str, line_no: int, text: str) -> None:
        if len(self.old_years) < 50:
            self.old_years.append(Finding("year", path, line_no, text))


def _normalise(text: str) -> str:
    return re.sub(r"[\s·•\-.&]+", "", text).lower()


def _is_applicant(name: str, applicant: str) -> bool:
    """True when the named company is plausibly the applicant itself."""
    a = _normalise(name)
    b = _normalise(applicant)
    if not a or not b:
        return False
    return a in b or b in a


def scan_line(
    path: str,
    line_no: int,
    line: str,
    applicant: str,
    *,
    earliest_year: int = 2015,
) -> ContentSuspicion:
    """Inspect one line for a foreign company, dates, and implausible years."""
    result = ContentSuspicion()

    for match in _COMPANY_PATTERN.finditer(line):
        name = match.group(1) or match.group(2) or ""
        name = name.strip()
        if not name or _is_applicant(name, applicant):
            continue
        # An organisation named in a copyright line is the case reviewers care
        # about most; elsewhere it may be sample data.
        if _COPYRIGHT_LINE.search(line):
            result.add_company(path, line_no, line.strip())
        else:
            result.add_company(path, line_no, line.strip())

    if _ISO_DATE.search(line):
        result.add_date(path, line_no, line.strip())
    else:
        for year_match in _YEAR_ONLY.finditer(line):
            year = int(year_match.group(1))
            if year < earliest_year:
                result.add_old_year(path, line_no, line.strip())
                break

    return result


def merge(into: ContentSuspicion, other: ContentSuspicion) -> None:
    into.foreign_companies.extend(other.foreign_companies)
    into.dates.extend(other.dates)
    into.old_years.extend(other.old_years)
