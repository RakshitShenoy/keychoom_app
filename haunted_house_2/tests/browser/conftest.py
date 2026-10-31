"""Fixtures for the real-browser tests (Playwright + Chromium + a live uvicorn server)."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.browser.e2e_support import E2ELobby, LiveServer  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@pytest.fixture(scope="session")
def pw_browser():
    if sync_playwright is None:
        pytest.skip("playwright is not installed (pip install playwright && playwright install chromium)")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # no browser binary available
            pytest.skip(f"Chromium could not be launched: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def server(tmp_path):
    srv = LiveServer(tmp_path)
    srv.migrate()
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def new_context(pw_browser):
    made = []

    def _make(**kwargs):
        ctx = pw_browser.new_context(**kwargs)
        ctx.set_default_timeout(15000)
        made.append(ctx)
        return ctx

    yield _make
    for c in made:
        c.close()


# ---------------------------------------------------------------------------------------------
def ui_join(page, base, code, name):
    page.goto(base + "/player")
    page.fill("#game_code", code)
    page.fill("#display_name", name)
    page.click("#join-form button[type=submit]")
    page.wait_for_selector("#lobby:not(.hidden)")


def ui_join_team(page, team_code):
    page.fill("#team_code", team_code)
    page.click("#btn-join-team")
    page.wait_for_selector("#my-team:not(.hidden)")


def wait_live(page, timeout=20000):
    page.wait_for_function(
        "document.querySelector('#hh-conn') && document.querySelector('#hh-conn').textContent === 'LIVE'", timeout=timeout)


def wait_game(page, timeout=20000):
    page.wait_for_selector("#hh-game .hh-stage", timeout=timeout)
    wait_live(page, timeout)
    page.wait_for_function("document.querySelector('#hh-clock') && document.querySelector('#hh-clock').textContent !== '--:--'", timeout=timeout)


SNAP_JS = """async () => {
  const me = await (await fetch('/api/me')).json();
  const q = s => document.querySelector(s);
  const all = s => [...document.querySelectorAll(s)];
  return {
    load: document.documentElement.getAttribute('data-page-load'),
    nav: performance.getEntriesByType('navigation')[0].type,
    player: me.player && me.player.player_id, team: me.player && me.player.team_id,
    name: me.player && me.player.display_name,
    floor: q('#hh-floorlabel') && q('#hh-floorlabel').textContent,
    hints: q('#hh-hintpill') && q('#hh-hintpill').textContent,
    conn: q('#hh-conn') && q('#hh-conn').textContent,
    solved: all('.hh-obj[data-state="solved"]').map(e => e.dataset.object).sort(),
    frags: all('.hh-frag').map(e => e.dataset.collected).join(''),
    slots: all('.hh-slot').map(e => e.textContent).join(''),
    clock: q('#hh-clock') && q('#hh-clock').textContent,
    members: all('#hh-teambox [data-member]').map(e => e.dataset.member).sort(),
    room: q('#hh-roomtitle') && q('#hh-roomtitle').textContent,
  };
}"""


def snap(page):
    return page.evaluate(SNAP_JS)


def clock_seconds(text):
    parts = [int(x) for x in text.split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def build_started_game(server, browser_page, per_team=3, extra_browser_pages=()):
    """3 teams x 3 players. The player under test lives in the browser (team 1); everyone else is an API player.
    Returns (lobby, api_players_by_slot)."""
    lobby = E2ELobby(server)
    api = [lobby.join(f"Bot {i}") for i in range(1, 9 - len(extra_browser_pages) + 0)]
    return lobby, api
