"""Filling the material from sources that are mostly not Chinese.

A Django or Vue project is full of `verbose_name="羊只状态"` and
`<h3>项目定位</h3>`. Those are genuine code: deleting them would falsify the
source, which is worse than having Chinese on the page. The lever available is
which files the sixty pages are drawn from.
"""

from __future__ import annotations

from hag.comments import StripMethod
from hag.pipeline import CleanedFile, prefer_low_cjk_order


def make(name: str, lines: list[str]) -> CleanedFile:
    return CleanedFile(
        rel_path=name, suffix=".py", lines=tuple(lines), method=StripMethod.FULL
    )


CLEAN = make("models.py", [f"    field_{i} = {i}" for i in range(100)])
LIGHT = make("views.py", ['    label = "中文"' if i % 10 == 0 else "    x = 1" for i in range(100)])
HEAVY_DJANGO = make(
    "models_cn.py",
    [f'    f{i} = models.CharField("羊只编号", max_length=50)' for i in range(100)],
)
HEAVY_VUE = make("page.vue", [f"    <li>羊只档案 {i}</li>" for i in range(100)])


class TestCjkMeasurement:
    def test_counts_only_lines_with_chinese(self) -> None:
        assert CLEAN.cjk_line_count == 0
        assert LIGHT.cjk_line_count == 10
        assert HEAVY_DJANGO.cjk_line_count == 100

    def test_ratio(self) -> None:
        assert CLEAN.cjk_ratio == 0.0
        assert abs(LIGHT.cjk_ratio - 0.10) < 1e-9
        assert HEAVY_VUE.cjk_ratio == 1.0

    def test_empty_file_has_zero_ratio(self) -> None:
        assert make("empty.py", []).cjk_ratio == 0.0


class TestOrdering:
    def test_cleanest_files_come_first(self) -> None:
        ordered = prefer_low_cjk_order([HEAVY_DJANGO, HEAVY_VUE, LIGHT, CLEAN])
        assert ordered[0].rel_path == "models.py"
        assert ordered[-1].rel_path in {"models_cn.py", "page.vue"}

    def test_light_files_before_heavy(self) -> None:
        ordered = prefer_low_cjk_order([HEAVY_DJANGO, LIGHT])
        assert [f.rel_path for f in ordered] == ["views.py", "models_cn.py"]

    def test_order_is_stable_within_a_band(self) -> None:
        a = make("a.py", ["x = 1"] * 100)
        b = make("b.py", ["y = 2"] * 100)
        ordered = prefer_low_cjk_order([a, b])
        assert [f.rel_path for f in ordered] == ["a.py", "b.py"]

    def test_no_file_is_dropped(self) -> None:
        files = [CLEAN, LIGHT, HEAVY_DJANGO, HEAVY_VUE]
        assert {f.rel_path for f in prefer_low_cjk_order(files)} == {
            f.rel_path for f in files
        }

    def test_reordering_does_not_change_line_total(self) -> None:
        files = [CLEAN, LIGHT, HEAVY_DJANGO, HEAVY_VUE]
        before = sum(f.line_count for f in files)
        after = sum(f.line_count for f in prefer_low_cjk_order(files))
        assert before == after


class TestContentIsNotAltered:
    def test_chinese_lines_remain_in_the_file(self) -> None:
        """Reordering decides which files are used, never what they contain."""
        ordered = prefer_low_cjk_order([HEAVY_DJANGO])
        assert ordered[0].lines == HEAVY_DJANGO.lines
        assert ordered[0].cjk_line_count == 100
