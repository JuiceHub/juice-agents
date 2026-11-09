"""Exercise the standalone history animation through its real browser controls.

The page opens with ``file://`` and must work without a server, model, or network
connection. Browser profiles remain inside the repository's temporary test root;
the tests do not produce screenshots, traces, downloads, or fixture files.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest


HISTORY_PAGE = Path(__file__).resolve().parents[2] / "tmp" / "china-history.html"
CHAPTER_COUNT = 18


@pytest.fixture(scope="module")
def browser():
    """Use the installed Node executable and report missing browsers explicitly."""

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.fail(
            "History browser tests require Playwright: python -m pip install playwright",
            pytrace=False,
        )

    # This host supplies a working system Node. Reusing it also avoids the
    # Playwright driver's bundled Node requiring a newer host C library.
    with pytest.MonkeyPatch.context() as environment:
        if node := shutil.which("node"):
            environment.setenv("PLAYWRIGHT_NODEJS_PATH", node)
        with sync_playwright() as playwright:
            try:
                instance = playwright.chromium.launch(headless=True)
            except Error as error:
                pytest.fail(
                    "Chromium could not start. Run python -m playwright install chromium. "
                    f"Original error: {error}",
                    pytrace=False,
                )
            yield instance
            instance.close()


@pytest.fixture
def page(browser):
    """Give every scenario a fresh page and catch offline or JavaScript regressions."""

    assert HISTORY_PAGE.is_file(), f"Missing standalone page: {HISTORY_PAGE}"
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    current = context.new_page()
    errors: list[str] = []
    network_requests: list[str] = []
    external_files: list[str] = []
    current.on("pageerror", lambda error: errors.append(error.stack or str(error)))

    def record_file_dependencies(request):
        if request.url.startswith("file:") and request.url != HISTORY_PAGE.as_uri():
            external_files.append(request.url)

    current.on("request", record_file_dependencies)

    def block_network(route):
        # A single-file teaching page must not silently depend on hosted fonts,
        # images, libraries, or APIs to render its timeline and animation.
        network_requests.append(route.request.url)
        route.abort()

    context.route(re.compile(r"^https?://"), block_network)
    current.goto(HISTORY_PAGE.as_uri(), wait_until="load")
    yield current
    context.close()
    assert not errors, f"Page JavaScript errors: {errors}"
    assert not network_requests, f"Unexpected network dependencies: {network_requests}"
    assert not external_files, f"Unexpected separate asset files: {external_files}"


def _expect_chapter(page, number: int) -> None:
    """Observe the public chapter counter rather than internal JavaScript state."""

    from playwright.sync_api import expect

    expect(page.locator("#chapter-count")).to_have_text(
        re.compile(rf"\s*{number:02d}\s*/\s*{CHAPTER_COUNT}\s*")
    )


def _jump_to(page, index: int) -> None:
    """Emulate a user's range input, including the final change notification."""

    slider = page.locator("#chapter-range")
    # Playwright's fill() intentionally excludes range inputs. Set the native
    # control's value, then deliver the same events emitted by pointer dragging.
    slider.evaluate("(element, value) => { element.value = String(value); }", index)
    slider.dispatch_event("input")
    slider.dispatch_event("change")
    _expect_chapter(page, index + 1)


def test_page_opens_offline_and_navigates_chapters(page):
    """The local file presents the first chapter and supports both directions."""

    from playwright.sync_api import expect

    expect(page.locator("html")).to_have_attribute("lang", re.compile(r"zh(?:-.*)?"))
    expect(page.locator("#chapter-title")).to_have_text("文明初曙")
    _expect_chapter(page, 1)
    expect(page.locator("#chapter-range")).to_have_attribute("type", "range")
    expect(page.locator("#chapter-range")).to_have_attribute("min", "0")
    expect(page.locator("#chapter-range")).to_have_attribute("max", "17")
    expect(page.locator("#previous-button")).to_be_disabled()
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")
    expect(page.locator("#playback-speed")).to_have_value("1")

    page.locator("#next-button").click()
    _expect_chapter(page, 2)
    page.locator("#previous-button").click()
    _expect_chapter(page, 1)


def test_range_and_chapter_menu_reach_every_period(page):
    """Every range position and menu entry must point to a complete chapter."""

    from playwright.sync_api import expect

    titles = set()
    for index in range(CHAPTER_COUNT):
        _jump_to(page, index)
        title = page.locator("#chapter-title").inner_text().strip()
        assert title, f"Chapter {index + 1} has no title"
        titles.add(title)
    assert len(titles) == CHAPTER_COUNT
    expect(page.locator("#next-button")).to_be_disabled()

    page.locator("#chapter-menu-button").click()
    dialog = page.locator("#chapter-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("button[data-chapter]")).to_have_count(CHAPTER_COUNT)
    tang = dialog.locator("button[data-chapter='10']")
    expect(tang).to_contain_text("唐")
    tang.click()
    expect(dialog).not_to_be_visible()
    _expect_chapter(page, 11)


def test_reading_dialogs_preserve_the_timeline_and_explain_sources(page):
    """Reading overlays trap navigation, expose useful prose, and can be closed."""

    from playwright.sync_api import expect

    page.locator("#play-button").click()
    page.locator("#details-button").click()
    story = page.locator("#story-dialog")
    expect(story).to_be_visible()
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")
    expect(story.locator("#story-title")).to_have_text(re.compile(r"\S"))
    assert len(story.inner_text().strip()) > 60
    page.keyboard.press("ArrowRight")
    _expect_chapter(page, 1)
    story.locator("[data-close-dialog]").click()
    expect(story).not_to_be_visible()
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")

    page.locator("#about-button").click()
    about = page.locator("#about-dialog")
    expect(about).to_be_visible()
    expect(about).to_contain_text(re.compile("约|近似|推定|传统"))
    assert about.locator("a[href^='https://']").count() >= 1
    page.keyboard.press("Escape")
    expect(about).not_to_be_visible()


def test_global_keyboard_shortcuts_respect_focused_controls(page):
    """Shortcuts work on the scene while native form controls keep their keys."""

    from playwright.sync_api import expect

    page.keyboard.press("ArrowRight")
    _expect_chapter(page, 2)
    page.keyboard.press("ArrowLeft")
    _expect_chapter(page, 1)
    page.keyboard.press("Space")
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "暂停播放")
    page.keyboard.press("Space")
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")

    next_button = page.locator("#next-button")
    next_button.focus()
    page.keyboard.press("ArrowRight")
    _expect_chapter(page, 1)
    page.keyboard.press("Space")
    _expect_chapter(page, 2)
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")

    slider = page.locator("#chapter-range")
    slider.focus()
    page.keyboard.press("ArrowRight")
    expect(slider).to_have_value("2")
    _expect_chapter(page, 3)

    speed = page.locator("#playback-speed")
    speed.focus()
    page.keyboard.press("ArrowDown")
    _expect_chapter(page, 3)
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")


def test_playback_advances_pauses_and_stops_at_the_last_chapter(page):
    """Virtual time exercises real playback without a long or flaky wall-clock wait."""

    from playwright.sync_api import expect

    page.clock.install()
    # Reload after installing the clock so even animation callbacks registered
    # during startup use virtual time rather than a pre-existing native timer.
    page.reload(wait_until="load")
    speed = page.locator("#playback-speed")
    for value in ("0.75", "1.5", "1"):
        speed.select_option(value)
        expect(speed).to_have_value(value)

    page.locator("#play-button").click()
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "暂停播放")
    page.clock.run_for(18_500)
    assert page.locator("#chapter-range").input_value() != "0"
    page.locator("#play-button").click()
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")
    paused_chapter = page.locator("#chapter-count").inner_text()
    page.clock.run_for(15_000)
    expect(page.locator("#chapter-count")).to_have_text(paused_chapter)

    # At 0.75x, 19 seconds is still within an 18-second narrated chapter;
    # switching to 1.5x must then advance it within another 13 seconds.
    _jump_to(page, 0)
    speed.select_option("0.75")
    page.locator("#play-button").click()
    page.clock.run_for(19_000)
    _expect_chapter(page, 1)
    speed.select_option("1.5")
    page.clock.run_for(13_000)
    _expect_chapter(page, 2)
    page.locator("#play-button").click()

    _jump_to(page, CHAPTER_COUNT - 2)
    speed.select_option("1.5")
    page.locator("#play-button").click()
    page.clock.run_for(40_000)
    _expect_chapter(page, CHAPTER_COUNT)
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "开始播放")
    expect(page.locator("#next-button")).to_be_disabled()


@pytest.mark.parametrize("reduced_motion", ["no-preference", "reduce"])
def test_mobile_controls_work_without_horizontal_overflow(page, reduced_motion):
    """A phone viewport and reduced motion both retain the full teaching flow."""

    from playwright.sync_api import expect

    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion=reduced_motion)
    page.reload(wait_until="load")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    ), "The phone layout overflows horizontally"

    for selector in ("#play-button", "#next-button", "#chapter-menu-button", "#details-button"):
        expect(page.locator(selector)).to_be_visible()
        expect(page.locator(selector)).to_be_enabled()

    page.locator("#next-button").click()
    _expect_chapter(page, 2)
    page.locator("#chapter-menu-button").click()
    page.locator("#chapter-dialog button[data-chapter='10']").click()
    _expect_chapter(page, 11)
    page.locator("#details-button").click()
    expect(page.locator("#story-dialog")).to_be_visible()
    page.locator("#story-dialog [data-close-dialog]").click()
    page.locator("#play-button").click()
    expect(page.locator("#play-button")).to_have_attribute("aria-label", "暂停播放")
