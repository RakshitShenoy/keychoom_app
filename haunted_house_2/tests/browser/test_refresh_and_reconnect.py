"""PRE-PHASE FIX: browser refresh / session restoration, verified in a REAL browser against a REAL server.

A reload must (1) really reload the page, (2) keep the same player session, and (3) restore every piece of
server-owned state. The server stays authoritative: nothing here relies on browser storage."""
import re

import pytest

from play_support import SOLUTIONS
from tests.browser.conftest import (
    clock_seconds, snap, ui_join, ui_join_team, wait_game, wait_live,
)
from tests.browser.e2e_support import E2ELobby


def start_game_with_browser_player(server, page, name="Alice Browser"):
    """Player under test joins by UI and joins team 1 by UI; 8 API players fill the rest of the 3 teams."""
    lobby = E2ELobby(server)
    bots = [lobby.join(f"Bot {i}") for i in range(1, 9)]
    ui_join(page, server.base, lobby.code, name)
    lobby.start_formation()
    leaders = bots[:3]
    for slot, leader in enumerate(leaders, start=1):
        assert lobby.assign(slot, leader).status_code == 200
    code1 = lobby.team_code(1)
    ui_join_team(page, code1)                               # the real browser joins team 1 with the team code
    for member, slot in ((bots[3], 1), (bots[4], 2), (bots[5], 2), (bots[6], 3), (bots[7], 3)):
        assert lobby.join_team(member, lobby.team_code(slot)).status_code == 200
    assert lobby.lock().status_code == 200
    lobby.start()
    wait_game(page)
    return lobby, bots


def solve_in_ui(page, object_id, answer):
    page.click(f'[data-object="{object_id}"]')
    page.wait_for_selector(f'[data-object="{object_id}"][data-state="mine"]')
    page.fill("#gm-answer", answer)
    page.click("#gm-submit")
    page.wait_for_selector(f'[data-object="{object_id}"][data-state="solved"]')


def test_reload_restores_the_same_player_and_all_server_state(server, new_context):
    ctx = new_context()
    page = ctx.new_page()
    lobby, bots = start_game_with_browser_player(server, page)
    teammate = lobby.play(bots[0])                           # the team-1 leader, a bot

    # ---- progress through part of the floor (UI for the player under test, API for a teammate)
    solve_in_ui(page, "candle", SOLUTIONS["candle"])
    solve_in_ui(page, "painting", SOLUTIONS["painting"])
    page.once("dialog", lambda d: d.accept())
    page.click("#hh-usehint")                               # hint 1 costs 30 seconds
    page.wait_for_function("document.querySelector('#hh-hintpill').textContent === 'Hints 3/4'")
    assert teammate.investigate("vent").status_code == 200  # a teammate is mid-investigation (holds the vent)
    page.wait_for_selector('[data-object="vent"][data-state="occupied"]')

    before = snap(page)
    assert before["solved"] == ["candle", "painting"] and before["hints"] == "Hints 3/4"
    assert before["floor"] == "Floor 1 of 8" and before["name"] == "Alice Browser"
    assert before["nav"] != "reload"
    before_clock = clock_seconds(before["clock"])
    lobby_players_before = len(lobby.lobby()["players"])

    # ---- reload for real
    page.reload()
    wait_game(page)
    after = snap(page)

    # 1. it genuinely reloaded (new page-load id, navigation type 'reload'), it is not a frozen page
    assert after["nav"] == "reload"
    assert after["load"] and after["load"] != before["load"]
    # 2. same session: same player id, same team, same name; nobody new appeared
    assert after["player"] == before["player"] and after["team"] == before["team"] and after["name"] == before["name"]
    assert len(lobby.lobby()["players"]) == lobby_players_before == 9
    # 3. server-owned state came back: floor, room, solved objects, vault pieces, fragments, hints, members
    for key in ("floor", "room", "solved", "slots", "frags", "hints", "members"):
        assert after[key] == before[key], key
    assert after["solved"] == ["candle", "painting"] and after["hints"] == "Hints 3/4"
    # the teammate still holds the vent
    page.wait_for_selector('[data-object="vent"][data-state="occupied"]')
    # 4. the timer was restored from the server deadline (not restarted, penalty kept)
    after_clock = clock_seconds(after["clock"])
    assert 0 <= before_clock - after_clock <= 30, (before["clock"], after["clock"])
    assert before_clock < 3600 - 20  # the hint penalty + elapsed time are included
    # 5. the WebSocket reconnected by itself: a teammate's action now arrives WITHOUT another reload
    assert after["conn"] == "LIVE"
    assert teammate.answer("vent", SOLUTIONS["vent"]).json()["result"]["result"] == "CORRECT"
    page.wait_for_selector('[data-object="vent"][data-state="solved"]')
    page.wait_for_selector(".hh-slot.on >> nth=3")           # all pieces appear (vault now openable)
    page.wait_for_selector("#gm-vault")


def test_a_frozen_page_is_distinguishable_from_a_real_reload(server, new_context):
    ctx = new_context()
    page = ctx.new_page()
    start_game_with_browser_player(server, page)
    first = snap(page)
    page.wait_for_timeout(1200)
    still = snap(page)
    assert still["load"] == first["load"] and still["nav"] != "reload"     # no reload happened: same page-load id
    page.reload()
    wait_game(page)
    assert snap(page)["load"] != first["load"]                              # a real reload always changes it


def test_reload_after_the_vault_and_a_floor_change_keeps_fragments_and_floor(server, new_context):
    ctx = new_context()
    page = ctx.new_page()
    lobby, bots = start_game_with_browser_player(server, page)
    for oid in ("candle", "painting", "vent"):
        solve_in_ui(page, oid, SOLUTIONS[oid])
    page.fill("#gm-vault", "K7R4")
    page.click("#gm-vault-btn")
    page.wait_for_selector("#hh-climb")
    page.reload()
    wait_game(page)
    page.click("#hh-climb")
    page.wait_for_function("document.querySelector('#hh-floorlabel').textContent === 'Floor 2 of 8'")
    mid = snap(page)
    assert mid["frags"].startswith("1") and mid["frags"].count("1") == 1
    page.reload()
    wait_game(page)
    end = snap(page)
    assert end["floor"] == "Floor 2 of 8" and end["frags"] == mid["frags"] and end["room"] == mid["room"] and end["player"] == mid["player"]


def test_the_page_reconnects_after_the_server_restarts_and_state_survives(server, new_context):
    ctx = new_context()
    page = ctx.new_page()
    lobby, bots = start_game_with_browser_player(server, page)
    solve_in_ui(page, "candle", SOLUTIONS["candle"])
    before = snap(page)

    server.stop()                                           # the server goes away...
    page.wait_for_function("document.querySelector('#hh-conn').textContent !== 'LIVE'", timeout=30000)
    assert page.is_visible("#hh-reconn")                    # ...and the player sees a clear reconnecting notice
    server.start()                                          # ...and comes back on the same database
    wait_live(page, timeout=45000)

    after = snap(page)
    assert after["load"] == before["load"]                  # same page (not reloaded); the socket reconnected on its own
    for key in ("player", "team", "floor", "solved", "hints", "members", "room"):
        assert after[key] == before[key], key
    assert not page.is_visible("#hh-reconn")
    # and a hard reload after the restart still restores the same session
    page.reload()
    wait_game(page)
    again = snap(page)
    assert again["player"] == before["player"] and again["solved"] == ["candle"]


def test_presence_follows_the_real_connection_and_the_player_keeps_their_place(server, new_context):
    import time

    ctx = new_context()
    page = ctx.new_page()
    lobby, bots = start_game_with_browser_player(server, page)

    def connected():
        row = next(p for p in lobby.lobby()["players"] if p["display_name"] == "Alice Browser")
        return row["is_connected"], row["role"]

    assert connected() == (True, "PLAYER")
    page.close()                                              # the tab is closed: the socket drops
    deadline = time.time() + 10
    while time.time() < deadline and connected()[0]:
        time.sleep(0.25)
    assert connected()[0] is False and len(lobby.lobby()["players"]) == 9   # offline, but still a member of the team
    again = ctx.new_page()                                    # reopening the site in the same browser...
    again.goto(server.base + "/player")
    wait_game(again)
    assert snap(again)["name"] == "Alice Browser" and snap(again)["floor"] == "Floor 1 of 8"
    assert connected() == (True, "PLAYER") and len(lobby.lobby()["players"]) == 9   # ...restores the SAME player, not a new one
