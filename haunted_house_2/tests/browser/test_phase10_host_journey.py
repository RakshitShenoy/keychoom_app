"""The whole product in real Chromium, driven through the host's and a player's actual pages:
landing -> rules -> host creates the lobby -> players join -> formation -> leaders -> lock -> start ->
Floor 1 -> host dashboard (live) -> hint -> pause / resume -> refresh -> kick -> winner."""
import httpx
import pytest

from tests.browser.conftest import ui_join, ui_join_team, wait_game, snap, clock_seconds
from tests.browser.e2e_support import LIBRARY, Handle
from play_support import Play, final_code, play_whole_building, solve_floor

TEAMS, PER_TEAM = 3, 3


class Shim:
    def __init__(self, code): self.code = code
    def url(self, suffix): return f"/api/games/{self.code}{suffix}"


def api_player(server, code, name):
    client = httpx.Client(base_url=server.base, timeout=20)
    r = client.post("/api/games/join", json={"game_code": code, "display_name": name})
    assert r.status_code == 201, r.text
    return Play(Shim(code), Handle(client, r.json()["player_id"], name))


def test_full_product_journey_in_real_browsers(server, new_context):
    host_ctx, human_ctx = new_context(), new_context()
    host, human = host_ctx.new_page(), human_ctx.new_page()
    host.on("dialog", lambda d: d.accept())

    # ---- landing -> rules -> host creates the lobby with the real form
    host.goto(server.base + "/")
    assert "Escape the Haunted House" in host.inner_text("h1")
    host.click("a.btn[href='/host']")
    assert "Host rules" in host.inner_text("main")
    host.fill("#team_count", str(TEAMS)); host.fill("#min_players_per_team", "3"); host.fill("#max_players_per_team", "3")
    host.click("#create-form button[type=submit]")
    host.wait_for_selector("#lobby:not(.hidden)")
    code = host.inner_text("#game-code").strip()
    assert len(code) == 6

    # ---- players join: one real browser, the rest API clients
    ui_join(human, server.base, code, "T1P3")
    bots = {(t, p): api_player(server, code, f"T{t}P{p}") for t in (1, 2, 3) for p in (1, 2, 3) if (t, p) != (1, 3)}
    host.wait_for_function("document.querySelector('#player-count').textContent.startsWith('9')")
    host.wait_for_selector("#conn-status.live")

    # ---- team formation, leaders, codes, lock, start - all through the host's buttons
    host.click("#btn-start")
    host.wait_for_function("document.querySelector('#lobby-state').textContent === 'TEAM_FORMATION'")
    for i in range(TEAMS):
        card = host.locator("#teams .team").nth(i)
        card.locator("select").select_option(label=f"T{i + 1}P1")
        card.get_by_text("Assign leader").click()
        host.wait_for_function(f"document.querySelectorAll('#teams .team')[{i}].querySelector('.teamcode')")
    codes = [host.locator("#teams .team").nth(i).locator(".teamcode").inner_text().strip() for i in range(TEAMS)]
    assert len(set(codes)) == TEAMS
    for (t, p), bot in bots.items():
        if p != 1:
            assert bot.client.post(f"/api/games/{code}/teams/join", json={"team_code": codes[t - 1]}).status_code == 200
    ui_join_team(human, codes[0])
    host.wait_for_function("!document.querySelector('#btn-lock').disabled")
    host.click("#btn-lock")
    host.wait_for_function("document.querySelector('#lobby-state').textContent === 'READY_TO_START'")
    host.click("#btn-startgame")
    host.wait_for_selector("#dash-card:not(.hidden)")
    wait_game(human)
    assert host.inner_text("#dash-status") == "RUNNING"

    # ---- the host sees all 3 teams without any reload, and the dashboard updates live
    assert host.locator("#dash-teams tr[data-team]").count() == TEAMS
    t1_name = host.locator("#dash-teams tr[data-team]").nth(0).get_attribute("data-team")
    t2_name = host.locator("#dash-teams tr[data-team]").nth(1).get_attribute("data-team")
    team2 = [bots[(2, p)] for p in (1, 2, 3)]
    team2[0].investigate("candle"); assert team2[0].answer("candle", "3").status_code == 200
    host.wait_for_function(
        "[...document.querySelectorAll('.dash-card-team')].some(c => c.dataset.team === %r && c.innerText.includes('Solved on this floor: 1/3'))" % t2_name)
    team2[1].hint(1)
    host.wait_for_function("[...document.querySelectorAll('.dash-card-team')].some(c => c.dataset.team === %r && c.innerText.includes('Hints left 3'))" % t2_name)

    # ---- players cannot reach the host's data
    status = human.evaluate("async (c) => (await fetch('/api/games/' + c + '/play/host')).status", code)
    assert status == 403
    assert human.evaluate("async (c) => (await fetch('/api/games/' + c + '/pause', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})).status", code) == 403
    assert "Game dashboard" not in human.inner_text("body")

    # ---- pause: the player's page stops, the server rejects actions, the clock is frozen
    before = clock_seconds(human.inner_text("#hh-clock"))
    host.click("#btn-pause")
    human.wait_for_selector("#hh-paused", timeout=15000)
    assert host.inner_text("#dash-status") == "PAUSED"
    frozen_a = human.evaluate("async (c) => (await (await fetch('/api/games/' + c + '/play/state')).json()).state.timer.seconds_remaining", code)
    human.wait_for_timeout(2500)
    frozen_b = human.evaluate("async (c) => (await (await fetch('/api/games/' + c + '/play/state')).json()).state.timer.seconds_remaining", code)
    assert abs(frozen_a - frozen_b) < 1.5
    assert human.evaluate("async (c) => (await fetch('/api/games/' + c + '/play/objects/candle/investigate', {method:'POST'})).status", code) == 409
    # a refresh while paused still shows the pause (state restored from the server)
    human.reload()
    human.wait_for_selector("#hh-paused", timeout=15000)

    # ---- resume: the cover goes, play continues, the player kept their place
    host.click("#btn-resume")
    human.wait_for_selector("#hh-paused", state="detached", timeout=15000)
    wait_game(human)
    after = clock_seconds(human.inner_text("#hh-clock"))
    assert before - after < 10  # the pause did not eat the team's time
    assert snap(human)["name"] == "T1P3" and snap(human)["nav"] == "reload"

    # ---- host removes a player: that session loses access, the team continues
    victim = bots[(3, 3)]
    row = host.locator("#players tr", has_text="T3P3")
    row.get_by_text("Kick").click()
    host.wait_for_function("![...document.querySelectorAll('#players tr')].some(r => r.innerText.includes('T3P3'))")
    assert victim.client.get(f"/api/games/{code}/play/state").status_code == 401

    # ---- a team (two players left) climbs the whole house and wins; everyone is told, only one winner
    t3_name = host.locator("#dash-teams tr[data-team]").nth(2).get_attribute("data-team")
    team3 = [bots[(3, 1)], bots[(3, 2)]]
    played = play_whole_building(team3, LIBRARY)
    won = team3[0].call("POST", "/final-door", {"code": final_code(played)})
    assert won.status_code == 200 and won.json()["result"]["result"] == "CORRECT"
    human.wait_for_selector("#hh-gameover", timeout=15000)
    assert t3_name in human.inner_text("#hh-winner")
    host.wait_for_selector("#dash-winner-name", timeout=15000)
    assert t3_name in host.inner_text("#dash-winner-name")
    assert host.locator("#dash-teams tr.win").count() == 1
    assert host.inner_text("#dash-status").startswith("FINISHED")
    assert host.locator("#btn-pause").is_disabled() and host.locator("#btn-resume").is_disabled()
    assert "Completion time" in host.inner_text("#dash-winner-time")
    again = team3[1].call("POST", "/final-door", {"code": final_code(played)})
    assert again.status_code == 409  # the win is final
