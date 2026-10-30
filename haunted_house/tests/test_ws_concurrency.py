"""Team capacity is enforced by the server even when many players act at the same instant."""
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db.models import Player


def race(lobby, racers, code):
    barrier = threading.Barrier(len(racers))

    def attempt(player):
        barrier.wait()  # everybody fires together
        return lobby.join_team(player, code).status_code

    with ThreadPoolExecutor(max_workers=len(racers)) as pool:
        return list(pool.map(attempt, racers))


def test_three_players_race_for_the_last_slot(make_lobby, db):
    lobby = make_lobby(min_players=3, max_players=3)
    players = lobby.join_many(5)
    lobby.start_formation()
    lobby.assign(1, players[0])
    code = lobby.team_code(1)
    assert lobby.join_team(players[1], code).status_code == 200  # 2 of 3

    results = race(lobby, players[2:5], code)

    assert sorted(results) == [200, 409, 409]  # exactly one winner
    team_id = lobby.team(1)["team_id"]
    assert db.scalar(select(func.count()).select_from(Player).where(Player.team_id == team_id)) == 3
    assert lobby.team(1)["player_count"] == 3 and lobby.team(1)["status"] == "FULL"


def test_eleven_players_race_for_four_places(make_lobby, db):
    lobby = make_lobby(min_players=3, max_players=5)
    players = lobby.join_many(12)
    lobby.start_formation()
    lobby.assign(1, players[0])  # the leader already occupies 1 of 5 places
    code = lobby.team_code(1)

    results = race(lobby, players[1:], code)

    assert results.count(200) == 4 and results.count(409) == 7
    team_id = lobby.team(1)["team_id"]
    assert db.scalar(select(func.count()).select_from(Player).where(Player.team_id == team_id)) == 5
    assert lobby.team(1)["player_count"] == 5  # never 6/5 or more


def test_capacity_race_is_consistent_for_connected_clients(make_lobby, live):
    lobby = make_lobby(min_players=3, max_players=3)
    players = lobby.join_many(5)
    lobby.start_formation()
    lobby.assign(1, players[0])
    code = lobby.team_code(1)
    host = lobby.host_socket(live)
    host.wait_for_type("LOBBY_STATE")
    host.collect(0.5)

    race(lobby, players[1:], code)  # 4 players, 2 places

    final = host.wait_for_state(lambda s: s["teams"][0]["player_count"] == 3)
    assert final["teams"][0]["status"] == "FULL"
    assert sum(1 for t in final["teams"] if t["player_count"] > 3) == 0
    assert final["counts"]["unassigned_count"] == 2


def test_two_host_requests_do_not_double_assign_a_leader(make_lobby, copy_cookies):
    from fastapi.testclient import TestClient

    lobby = make_lobby()
    alice, bob = lobby.join("Alice"), lobby.join("Bob")
    lobby.start_formation()
    team_id = lobby.team(1)["team_id"]
    second_tab = TestClient(lobby.app)  # the same host in a second browser tab
    copy_cookies(lobby.host, second_tab)
    barrier = threading.Barrier(2)

    def assign(args):
        client, player = args
        barrier.wait()
        return client.post(
            lobby.url(f"/teams/{team_id}/leader"), json={"player_id": player.id}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(assign, [(lobby.host, alice), (second_tab, bob)]))
    assert sorted(results) == [200, 409]
    assert lobby.team(1)["leader_name"] in ("Alice", "Bob")
