"""Default team names/icons for the team slots created with the lobby."""
from __future__ import annotations

_NAMES = (
    "Phantom", "Specter", "Wraith", "Banshee", "Ghoul", "Poltergeist", "Revenant", "Spirit",
    "Apparition", "Haunt", "Shade", "Wisp", "Lich", "Ghast", "Zombie", "Vampire", "Werewolf",
    "Mummy", "Gargoyle", "Goblin", "Witch", "Warlock", "Reaper", "Cryptkeeper", "Nightmare",
)
_ICONS = ("skull", "ghost", "bat", "pumpkin", "candle", "cauldron", "spider", "raven", "moon", "web")


def default_team_name(slot_no: int) -> str:
    if 1 <= slot_no <= len(_NAMES):
        return f"Team {_NAMES[slot_no - 1]}"
    return f"Team {slot_no}"


def default_team_icon(slot_no: int) -> str:
    return _ICONS[(slot_no - 1) % len(_ICONS)]
