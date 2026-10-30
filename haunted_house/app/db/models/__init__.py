"""Importing this package registers every model on ``Base.metadata`` (needed by Alembic)."""
from app.db.models.chat import ChatMessage
from app.db.models.dare import DareConfirmation
from app.db.models.event import GameEvent
from app.db.models.game import Game
from app.db.models.hint import HintUsage
from app.db.models.jumpscare import JumpscareEvent
from app.db.models.player import Player
from app.db.models.quiz import QuizAttempt
from app.db.models.team import Team
from app.db.models.team_state import ObjectState, TeamFloorState

__all__ = [
    "ChatMessage",
    "DareConfirmation",
    "Game",
    "GameEvent",
    "HintUsage",
    "JumpscareEvent",
    "ObjectState",
    "Player",
    "QuizAttempt",
    "Team",
    "TeamFloorState",
]
