"""Visual smoke test: every page renders in light and dark with no errors.

It drives a real browser (the Edge or Chrome already installed, through Playwright) against the
demo, and fails on any console error, script error, failed request, HTTP error or
Content-Security-Policy violation. It also checks the dark theme really changes the colours.
Skipped when Playwright or a browser isn't available.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tools import screenshots


@pytest.fixture(scope="module")
def shots() -> list[screenshots.Shot]:
    try:
        return screenshots.capture(out=None)
    except RuntimeError as exc:  # no browser on this machine
        pytest.skip(str(exc))


def test_every_page_renders_in_both_themes(shots):
    taken = {(s.name, s.theme) for s in shots}
    wanted = {(name, theme) for name, _path in screenshots.pages() for theme in ("light", "dark")}
    assert taken == wanted


def test_no_page_has_errors(shots):
    problems = {f"{s.name}-{s.theme}": s.problems for s in shots if s.problems}
    assert problems == {}


def test_dark_mode_changes_the_colours(shots):
    backgrounds = {(s.name, s.theme): s.background for s in shots}
    for name, _path in screenshots.pages():
        assert backgrounds[(name, "light")] != backgrounds[(name, "dark")], name
