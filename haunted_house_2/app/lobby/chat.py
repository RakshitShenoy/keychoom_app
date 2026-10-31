"""Public lobby chat. Open only while the game is still in the waiting lobby."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import GameStatus
from app.db.models import ChatMessage, Game, Player
from app.errors import AppError
from app.lobby.events import LobbyEvent, LobbyEventBus, LobbyEventType
from app.lobby.locks import game_lock
from app.timers.clock import utcnow

if TYPE_CHECKING:
    from app.config import Settings

CHAT_CLOSED_MESSAGE = "Public chat is now closed. Team communication will be private."


def chat_is_open(game: Game) -> bool:
    # Once formation has ever started the chat stays closed, even if formation is reopened.
    return game.status == GameStatus.LOBBY.value and game.team_formation_started_at is None


def clean_chat_text(raw: str, max_length: int) -> str:
    text = "".join(ch for ch in raw if ch.isprintable() or ch in "\n\t")
    text = " ".join(text.split())
    if not text:
        raise AppError(422, "EMPTY_MESSAGE", "Message cannot be empty.")
    if len(text) > max_length:
        raise AppError(422, "MESSAGE_TOO_LONG", f"Message must be at most {max_length} characters.")
    return text


def send_chat_message(
    db: Session, settings: "Settings", bus: LobbyEventBus, game_id: str, player_id: str, raw: str
) -> ChatMessage:
    body = clean_chat_text(raw, settings.chat_max_length)
    with game_lock:
        game = db.scalar(
            select(Game).where(Game.id == game_id).execution_options(populate_existing=True)
        )
        player = db.scalar(
            select(Player)
            .where(Player.id == player_id, Player.game_id == game_id)
            .execution_options(populate_existing=True)
        )
        if game is None or player is None:
            raise AppError(404, "PLAYER_NOT_FOUND", "No such player in this game.")
        if not chat_is_open(game):
            raise AppError(409, "CHAT_CLOSED", CHAT_CLOSED_MESSAGE)

        message = ChatMessage(
            game_id=game.id, player_id=player.id, display_name=player.display_name,
            body=body, created_at=utcnow(),
        )
        db.add(message)
        db.commit()
    bus.publish_change(
        LobbyEvent(LobbyEventType.CHAT_MESSAGE, game.id, {"display_name": player.display_name})
    )
    return message


def recent_messages(db: Session, game: Game, limit: int) -> list[ChatMessage]:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.game_id == game.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    ).all()
    return list(reversed(rows))
