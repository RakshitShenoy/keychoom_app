"""ConnectionManager is pure Python: scoping rules can be tested without a server."""
from app.ws.manager import Connection, ConnectionManager


class ImmediateLoop:
    """Stands in for an event loop: runs the callback right away."""

    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


class DeadLoop:
    def call_soon_threadsafe(self, fn, *args):
        raise RuntimeError("Event loop is closed")


def conn(game, role="player", player=None, team=None, loop=None, size=50):
    c = Connection(game, role, player, loop or ImmediateLoop(), size)
    c.team_id = team
    return c


def drain(c):
    items = []
    while not c.queue.empty():
        items.append(c.queue.get_nowait())
    return items


def test_broadcast_reaches_only_the_target_game():
    manager = ConnectionManager()
    a1, a2 = conn("A", player="p1"), conn("A", "host")
    b1 = conn("B", player="p3")
    for c in (a1, a2, b1):
        manager.add(c)
    assert manager.send_game("A", {"type": "X"}) == 2
    assert len(drain(a1)) == 1 and len(drain(a2)) == 1
    assert drain(b1) == []  # game B received absolutely nothing


def test_role_scoping():
    manager = ConnectionManager()
    host, player = conn("A", "host"), conn("A", player="p1")
    manager.add(host)
    manager.add(player)
    manager.send_game("A", {"type": "H"}, role="host")
    manager.send_game("A", {"type": "P"}, role="player")
    assert [i[1]["type"] for i in drain(host)] == ["H"]
    assert [i[1]["type"] for i in drain(player)] == ["P"]


def test_team_scoped_broadcast():
    manager = ConnectionManager()
    t1a, t1b = conn("A", player="p1", team="T1"), conn("A", player="p2", team="T1")
    t2, loner, host = conn("A", player="p3", team="T2"), conn("A", player="p4"), conn("A", "host")
    other_game = conn("B", player="p5", team="T1")  # same team id string, different game
    for c in (t1a, t1b, t2, loner, host, other_game):
        manager.add(c)
    assert manager.send_team("A", "T1", {"type": "TEAM_ONLY"}) == 2
    assert len(drain(t1a)) == 1 and len(drain(t1b)) == 1
    for c in (t2, loner, host, other_game):
        assert drain(c) == []


def test_player_presence_counts_tabs():
    manager = ConnectionManager()
    tab1, tab2 = conn("A", player="p1"), conn("A", player="p1")
    assert manager.add(tab1) is True      # first connection of the player
    assert manager.add(tab2) is False     # second tab
    assert manager.remove(tab1) is False  # still connected through tab2
    assert manager.remove(tab2) is True   # last one gone
    assert manager.remove(tab2) is False  # unknown now
    assert manager.count() == 0


def test_host_connections_never_count_as_player_presence():
    manager = ConnectionManager()
    host = conn("A", "host")
    assert manager.add(host) is False
    assert manager.remove(host) is False


def test_close_player_targets_only_that_player():
    manager = ConnectionManager()
    kicked, other = conn("A", player="p1"), conn("A", player="p2")
    manager.add(kicked)
    manager.add(other)
    assert manager.close_player("A", "p1", 4403, {"type": "ERROR"}) == 1
    assert drain(kicked)[0][0] == "close" and drain(other) == []


def test_dead_event_loop_marks_connection_closed():
    c = conn("A", player="p1", loop=DeadLoop())
    assert c.send({"type": "X"}) is False
    assert c.closed is True and c.send({"type": "Y"}) is False


def test_slow_consumer_is_dropped_with_a_close_message():
    c = conn("A", player="p1", size=3)
    for i in range(4):
        c.send({"type": "M", "n": i})
    items = drain(c)
    assert len(items) == 1 and items[0][0] == "close"
