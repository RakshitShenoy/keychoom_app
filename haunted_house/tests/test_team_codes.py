import re

import pytest

from app.teams.codes import generate_team_code, normalize_team_code

SHAPE = re.compile(r"^TEAM-[A-HJ-KM-NP-Z2-9]{4}$")


def test_generated_codes_have_the_documented_shape():
    for _ in range(200):
        assert SHAPE.match(generate_team_code(4))


def test_codes_are_random():
    assert len({generate_team_code(4) for _ in range(300)}) > 280


@pytest.mark.parametrize(
    "raw",
    ["TEAM-7K4P", "team-7k4p", "7K4P", " 7k4p ", "team 7k4p", "TEAM7K4P", "team_7k4p"],
)
def test_normalisation_accepts_friendly_input(raw):
    assert normalize_team_code(raw, 4) == "TEAM-7K4P"


def test_normalisation_handles_body_that_starts_with_team():
    assert normalize_team_code("TEAM-TEAM", 4) == "TEAM-TEAM"
    assert normalize_team_code("TEAM", 4) == "TEAM-TEAM"


@pytest.mark.parametrize("raw", ["", "   ", "---", None, 12])
def test_empty_or_non_text_normalises_to_empty(raw):
    assert normalize_team_code(raw, 4) == ""
