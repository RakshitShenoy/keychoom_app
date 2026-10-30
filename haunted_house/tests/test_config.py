import pytest

from app.config import Settings


def make(**overrides):
    return Settings(_env_file=None, **overrides)


def test_defaults_match_the_design():
    s = make()
    assert (s.min_teams_limit, s.max_teams_limit) == (3, 25)
    assert (s.min_players_limit, s.max_players_limit) == (3, 10)
    assert s.max_hints == 4
    assert s.hint_penalty_schedule == (30, 60, 90, 120)
    assert s.object_lock_lease_seconds > 0
    assert s.ws_heartbeat_interval_seconds > 0


def test_environment_override(monkeypatch):
    monkeypatch.setenv("MAX_TEAMS_LIMIT", "12")
    assert Settings(_env_file=None).max_teams_limit == 12


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_teams_limit": 10, "max_teams_limit": 5},
        {"min_players_limit": 9, "max_players_limit": 4},
        {"hint_penalties_seconds": "30,60"},
        {"hint_penalties_seconds": "a,b,c,d"},
        {"hint_penalties_seconds": "30,-60,90,120"},
        {"ws_heartbeat_interval_seconds": 30, "ws_heartbeat_timeout_seconds": 30},
        {"default_base_duration_seconds": 10},
    ],
)
def test_inconsistent_settings_rejected(overrides):
    with pytest.raises(ValueError):
        make(**overrides)


def test_production_requires_private_secret():
    with pytest.raises(ValueError):
        make(app_env="production")
    assert make(app_env="production", secret_key="x" * 40).app_env == "production"
