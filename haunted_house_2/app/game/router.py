"""Gameplay HTTP endpoints. Thin layer: authenticate, validate the shape of the request, call the
service. Browsers send only intents; the team, the floor and every outcome are resolved server-side
from the session cookie. Unknown fields are rejected (``extra="forbid"``)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import require_host_of_game, require_player_of_game
from app.db.models import Game, Player
from app.db.session import get_db
from app.game import service
from app.game.views import build_host_dashboard
from app.lobby.views import Viewer, build_lobby_view
from app.schemas.lobby import LobbyResponse

router = APIRouter(prefix="/api/games/{game_code}", tags=["game"])

ObjectIdParam = Annotated[str, Path(pattern=r"^[A-Za-z0-9_\-]{1,64}$")]


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(max_length=200)


class VaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(max_length=32)


class HintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = Field(ge=1, le=99)


class QuizAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(max_length=64)
    choice: str = Field(max_length=16)


class EmptyRequest(BaseModel):
    """No fields: a browser cannot choose a floor or a fragment, so extra fields are rejected."""
    model_config = ConfigDict(extra="forbid")


class FinalDoorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(max_length=32)


def _runtime(request: Request):
    return request.app.state.game


@router.post("/start", response_model=LobbyResponse)
def start_game(
    request: Request, game: Game = Depends(require_host_of_game), db: Session = Depends(get_db)
) -> LobbyResponse:
    game = service.start_game(db, _runtime(request), game.id)
    return build_lobby_view(db, request.app.state.settings, Viewer("host", game))


@router.get("/play/state")
def play_state(
    request: Request, player: Player = Depends(require_player_of_game), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return service.get_state(db, _runtime(request), player)


@router.post("/play/objects/{object_id}/investigate")
def investigate(
    request: Request, object_id: ObjectIdParam,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.investigate(db, _runtime(request), player, object_id)


@router.post("/play/objects/{object_id}/heartbeat")
def heartbeat(
    request: Request, object_id: ObjectIdParam, player: Player = Depends(require_player_of_game)
) -> dict[str, Any]:
    return service.heartbeat(_runtime(request), player, object_id)


@router.post("/play/objects/{object_id}/release")
def release(
    request: Request, object_id: ObjectIdParam,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.release(db, _runtime(request), player, object_id)


@router.post("/play/objects/{object_id}/answer")
def answer(
    payload: AnswerRequest, request: Request, object_id: ObjectIdParam,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.submit_answer(db, _runtime(request), player, object_id, payload.answer)


@router.post("/play/objects/{object_id}/quiz/start")
def quiz_start(
    request: Request, object_id: ObjectIdParam,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.quiz_start(db, _runtime(request), player, object_id)


@router.post("/play/objects/{object_id}/quiz/answer")
def quiz_answer(
    payload: QuizAnswerRequest, request: Request, object_id: ObjectIdParam,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.quiz_answer(db, _runtime(request), player, object_id, payload.question_id, payload.choice)


@router.post("/play/objects/{object_id}/dare/confirm")
def dare_confirm(
    request: Request, object_id: ObjectIdParam,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.dare_confirm(db, _runtime(request), player, object_id)


@router.post("/play/vault")
def vault(
    payload: VaultRequest, request: Request,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.submit_vault(db, _runtime(request), player, payload.code)


@router.post("/play/hints")
def hint(
    payload: HintRequest, request: Request,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.use_hint(db, _runtime(request), player, payload.level)


@router.post("/play/floor/next")
def next_floor(
    request: Request, payload: EmptyRequest | None = None,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.advance_floor(db, _runtime(request), player)


@router.post("/play/final-door")
def final_door(
    payload: FinalDoorRequest, request: Request,
    player: Player = Depends(require_player_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.open_final_door(db, _runtime(request), player, payload.code)


@router.post("/pause")
def pause(
    request: Request, payload: EmptyRequest | None = None,
    game: Game = Depends(require_host_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    game = service.pause_game(db, _runtime(request), game.id)
    return build_host_dashboard(db, _runtime(request), game)


@router.post("/resume")
def resume(
    request: Request, payload: EmptyRequest | None = None,
    game: Game = Depends(require_host_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    game = service.resume_game(db, _runtime(request), game.id)
    return build_host_dashboard(db, _runtime(request), game)


@router.get("/play/host")
def host_dashboard(
    request: Request, game: Game = Depends(require_host_of_game), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return build_host_dashboard(db, _runtime(request), game)
