"""Phase 8 + 9 in a real browser: room UI states, responsive/touch layouts, jumpscare overlays,
reduced effects, and the winner broadcast to every browser (players and host)."""
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from play_support import SOLUTIONS, final_code, play_whole_building, preset_by_title, solve_floor
from app.timers.clock import utcnow
from tests.browser.conftest import snap, ui_join, ui_join_team, wait_game, wait_live
from tests.browser.e2e_support import LIBRARY, E2ELobby

SHOTS = Path("/tmp/hh_shots")


def start_game(server, pages, bots_per_team=None):
    """pages: [(page, slot)] real browsers placed on the given team slots (UI join + UI team join).
    Every other seat is an API bot. Returns (lobby, bots_by_slot)."""
    lobby = E2ELobby(server)
    seats = {1: 3, 2: 3, 3: 3}
    browsers_per_slot = {s: [p for p, sl in pages if sl == s] for s in seats}
    bots = {s: [lobby.join(f"Bot {s}-{i}") for i in range(1, seats[s] - len(browsers_per_slot[s]) + 1)] for s in seats}
    for n, (page, slot) in enumerate(pages, start=1):
        ui_join(page, server.base, lobby.code, f"Human {n}")
    lobby.start_formation()
    for slot in seats:
        assert lobby.assign(slot, bots[slot][0]).status_code == 200
    for slot in seats:
        code = lobby.team_code(slot)
        for member in bots[slot][1:]:
            assert lobby.join_team(member, code).status_code == 200
        for page in browsers_per_slot[slot]:
            ui_join_team(page, code)
    assert lobby.lock().status_code == 200
    lobby.start()
    for page, _ in pages:
        wait_game(page)
    return lobby, bots


def db_exec(server, sql, params=()):
    con = sqlite3.connect(server.tmp_path / "e2e.db")
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def sqlite_now(offset_seconds=0):
    return (utcnow() + timedelta(seconds=offset_seconds)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def to_floor_two(lobby, bots, slot=1):
    players = [lobby.play(b) for b in bots[slot]]
    # the browser player is the 3rd member of the team but teammates can finish the whole floor alone
    assert len(players) >= 2
    solve_floor(players, LIBRARY, climb=True)
    return players


# ================================================================ room states and the HUD
def test_the_room_shows_object_states_hud_pawns_and_cooldown(server, new_context):
    ctx = new_context(viewport={"width": 1366, "height": 800})
    page = ctx.new_page()
    lobby, bots = start_game(server, [(page, 1)])
    mate = lobby.play(bots[1][0])

    # HUD: floor, fragments (8 slots), hints, timer, connection, members
    assert page.inner_text("#hh-floorlabel") == "Floor 1 of 8"
    assert page.locator(".hh-frag").count() == 8 and page.inner_text("#hh-hintpill") == "Hints 4/4"
    assert page.inner_text("#hh-conn") == "LIVE" and page.inner_text("#hh-clock") != "--:--"
    assert page.locator("#hh-tray .hh-mate").count() == 3
    states = {e.get_attribute("data-object"): e.get_attribute("data-state") for e in page.locator(".hh-obj").all()}
    assert states["candle"] == "available" and "locked" in states.values() and states["vault"] == "locked"

    # a teammate investigates: occupied + a pawn marker appears on that object
    assert mate.investigate("candle").status_code == 200
    page.wait_for_selector('[data-object="candle"][data-state="occupied"]')
    assert page.locator('[data-object="candle"] .hh-pawn').count() == 1
    # clicking an occupied/locked object explains why instead of doing anything
    page.click('[data-object="candle"]')
    assert "already investigating" in page.inner_text("#hh-msg")
    page.click('[data-object="painting"]')
    assert "locked" in page.inner_text("#hh-msg")
    assert mate.release("candle").status_code == 200
    page.wait_for_selector('[data-object="candle"][data-state="available"]')

    # my own investigation, a wrong answer (visible cooldown), then the right one
    page.click('[data-object="candle"]')
    page.wait_for_selector('[data-object="candle"][data-state="mine"]')
    page.fill("#gm-answer", "999999")
    page.click("#gm-submit")
    page.wait_for_selector(".hh-msg.bad")
    assert page.inner_text("#gm-submit") == "Wait..." or page.locator(".hh-answer.bad").count() == 1
    page.wait_for_function("document.querySelector('#gm-submit') && document.querySelector('#gm-submit').textContent === 'Submit'")
    page.fill("#gm-answer", SOLUTIONS["candle"])
    page.click("#gm-submit")
    page.wait_for_selector('[data-object="candle"][data-state="solved"]')
    assert page.locator("#hh-cluebox").count() == 1                 # the clue notes now hold the reveal

    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / "desktop_floor1.png"))


def test_expiry_is_shown_to_the_player_when_the_team_runs_out_of_time(server, new_context):
    ctx = new_context()
    page = ctx.new_page()
    lobby, bots = start_game(server, [(page, 1)])
    db_exec(server, "UPDATE teams SET deadline_at = ? WHERE slot_no = 1", (sqlite_now(-5),))
    page.wait_for_selector("#hh-timeup", timeout=15000)               # the sweeper eliminates the team and pushes the state
    assert "no longer escape" in page.inner_text("#hh-timeup")
    page.click("#hh-timeup button")
    assert page.locator('.hh-obj[data-state="available"]').count() == 0


# ================================================================ responsive layouts, touch
@pytest.mark.parametrize("label,opts", [
    ("mobile", dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)),
    ("tablet", dict(viewport={"width": 820, "height": 1180}, has_touch=True)),
    ("laptop", dict(viewport={"width": 1280, "height": 720})),
])
def test_layout_fits_the_screen_and_objects_work_with_touch_or_mouse(server, new_context, label, opts):
    ctx = new_context(**opts)
    page = ctx.new_page()
    lobby, bots = start_game(server, [(page, 1)])
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 1, f"{label}: page scrolls sideways by {overflow}px"
    box = page.locator("#hh-stage").bounding_box()
    assert box["width"] <= opts["viewport"]["width"] and box["height"] >= 300
    obj = page.locator('[data-object="candle"] .sigil').bounding_box()
    assert obj["width"] >= 44 and obj["height"] >= 44                      # a comfortable tap target
    if opts.get("has_touch"):
        page.tap('[data-object="candle"]')
    else:
        page.click('[data-object="candle"]')
    page.wait_for_selector('[data-object="candle"][data-state="mine"]')
    assert page.locator("#gm-answer").is_visible()
    # the answer box and its button stay inside the viewport on every size
    inp = page.locator("#gm-answer").bounding_box()
    assert inp["x"] >= 0 and inp["x"] + inp["width"] <= opts["viewport"]["width"] + 1
    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{label}_task.png"))


# ================================================================ jumpscares in the browser
def trap_id_for(page):
    title = page.inner_text("#hh-roomtitle")
    preset = preset_by_title(LIBRARY, title)
    return next(o for o in preset["objects"] if o["type"] == "trap")["id"]


def test_a_trap_object_flashes_a_scare_for_about_five_seconds_and_changes_nothing(server, new_context):
    ctx = new_context()
    page = ctx.new_page()
    lobby, bots = start_game(server, [(page, 1)])
    to_floor_two(lobby, bots)
    page.reload()
    wait_game(page)
    page.wait_for_function("document.querySelector('#hh-floorlabel').textContent === 'Floor 2 of 8'")
    trap = trap_id_for(page)
    before = snap(page)

    page.click(f'[data-object="{trap}"]')
    page.wait_for_selector("#hh-scare.on", timeout=5000)
    assert page.locator("#hh-scare img").get_attribute("src").startswith("/static/img/jumpscares/")
    assert page.evaluate("document.querySelector('#hh-scare img').naturalWidth") > 0     # the artwork really loaded
    page.wait_for_timeout(3500)
    assert page.locator("#hh-scare.on").count() == 1                                      # still showing after 3.5 s
    page.wait_for_selector("#hh-scare:not(.on)", state="attached", timeout=6000)                            # gone after ~5 s
    after = snap(page)
    for key in ("solved", "slots", "frags", "hints", "floor", "room"):
        assert after[key] == before[key], key                                              # puzzle state untouched
    # touching it again right away only gives flavour text (team cooldown)
    page.click(f'[data-object="{trap}"]')
    page.wait_for_selector(".hh-msg.info")
    page.wait_for_timeout(600)
    assert page.locator("#hh-scare.on").count() == 0


@pytest.mark.parametrize("mode,expect_img,expect_flash", [("full", True, True), ("reduced", True, False), ("off", False, False)])
def test_reduced_effects_option_softens_or_removes_the_scare(server, new_context, mode, expect_img, expect_flash):
    ctx = new_context()
    ctx.add_init_script(f"window.localStorage.setItem('hh_fx', '{mode}')")
    page = ctx.new_page()
    lobby, bots = start_game(server, [(page, 1)])
    to_floor_two(lobby, bots)
    page.reload()
    wait_game(page)
    page.wait_for_function("document.querySelector('#hh-floorlabel').textContent === 'Floor 2 of 8'")
    assert page.evaluate("document.documentElement.getAttribute('data-fx')") == mode
    page.click(f'[data-object="{trap_id_for(page)}"]')
    page.wait_for_selector("#hh-scare.on")
    assert (page.locator("#hh-scare img").count() == 1) is expect_img
    assert (page.locator("#hh-scare .flash").count() == 1) is expect_flash
    if mode == "off":
        assert "shadow" in page.inner_text("#hh-scare").lower()
        assert page.evaluate("getComputedStyle(document.querySelector('.hh-light')).animationName") == "none"
    page.click("#hh-scare .skip")                                                          # always dismissable
    page.wait_for_selector("#hh-scare:not(.on)", state="attached")


def test_the_fx_button_cycles_levels_and_the_choice_survives_a_reload(server, new_context):
    ctx = new_context()
    page = ctx.new_page()
    start_game(server, [(page, 1)])
    levels = []
    for _ in range(3):
        page.click("#hh-fxbtn")
        levels.append(page.evaluate("document.documentElement.getAttribute('data-fx')"))
    assert sorted(levels) == ["full", "off", "reduced"]
    chosen = levels[-1]
    page.reload()
    wait_game(page)
    assert page.evaluate("document.documentElement.getAttribute('data-fx')") == chosen


def test_a_scheduled_scare_reaches_only_the_right_team_in_the_browser(server, new_context):
    ctx1, ctx2 = new_context(), new_context()
    p1, p2 = ctx1.new_page(), ctx2.new_page()
    lobby, bots = start_game(server, [(p1, 1), (p2, 2)])
    team1 = lobby.lobby()["teams"][0]["team_id"]
    db_exec(server, "UPDATE jumpscare_events SET scheduled_at = ? WHERE team_id = ? AND fired_at IS NULL", (sqlite_now(-10), team1))
    p1.wait_for_selector("#hh-scare.on", timeout=15000)                                     # the sweeper fires it, the socket delivers it
    assert p1.locator("#hh-scare img").count() == 1
    p2.wait_for_timeout(1500)
    assert p2.locator("#hh-scare.on").count() == 0                                          # team 2 saw nothing
    p1.wait_for_selector("#hh-scare:not(.on)", state="attached", timeout=8000)
    # cooldown: the other two team-1 scares that were rescheduled-due do not fire straight after
    db_exec(server, "UPDATE jumpscare_events SET scheduled_at = ? WHERE team_id = ? AND fired_at IS NULL", (sqlite_now(-10), team1))
    p1.wait_for_timeout(2500)
    assert p1.locator("#hh-scare.on").count() == 0


# ================================================================ the winner, everywhere at once
def test_the_winner_is_broadcast_to_every_team_browser_and_the_host(server, new_context):
    ctx1, ctx2, ctx_host = new_context(), new_context(), new_context()
    p1, p2, ph = ctx1.new_page(), ctx2.new_page(), ctx_host.new_page()
    lobby, bots = start_game(server, [(p1, 1), (p2, 2)])
    # the host's real browser
    host_cookie = next(c for c in lobby.host.cookies.jar if c.name == "hh_host")
    ctx_host.add_cookies([{"name": "hh_host", "value": host_cookie.value, "url": server.base}])
    ph.goto(server.base + "/host")
    ph.wait_for_selector("#dash-card:not(.hidden)", timeout=15000)

    # team 3 (all bots) climbs the whole house and opens the final door
    players = [lobby.play(b) for b in bots[3]]
    played = play_whole_building(players, LIBRARY)
    won = players[0].call("POST", "/final-door", {"code": final_code(played)})
    assert won.status_code == 200 and won.json()["result"]["result"] == "CORRECT"
    winner_name = lobby.team(3)["name"]

    for page in (p1, p2):
        page.wait_for_selector("#hh-gameover", timeout=15000)
        assert "Too late" in page.inner_text("#hh-gameover")
        assert winner_name in page.inner_text("#hh-winner")
    ph.wait_for_selector("#dash-winner-name", timeout=15000)
    assert winner_name in ph.inner_text("#dash-winner-name")
    assert ph.locator('tr.win[data-team="%s"]' % winner_name).count() == 1
    # the losers' rooms are frozen: no object can be started any more
    p1.click("#hh-gameover button")
    assert p1.locator('.hh-obj[data-state="available"]').count() == 0
