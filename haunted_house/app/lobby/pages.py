"""Functional development pages (the polished haunted UI comes later)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return request.app.state.templates.TemplateResponse(request, "landing.html", {})


@router.get("/host", response_class=HTMLResponse)
def host_page(request: Request):
    s = request.app.state.settings
    return request.app.state.templates.TemplateResponse(
        request,
        "host.html",
        {
            "min_teams": s.min_teams_limit,
            "max_teams": s.max_teams_limit,
            "min_players": s.min_players_limit,
            "max_players": s.max_players_limit,
            "min_minutes": s.min_base_duration_seconds // 60,
            "max_minutes": s.max_base_duration_seconds // 60,
            "default_minutes": s.default_base_duration_seconds // 60,
        },
    )


@router.get("/player", response_class=HTMLResponse)
def player_page(request: Request):
    s = request.app.state.settings
    return request.app.state.templates.TemplateResponse(
        request,
        "player.html",
        {"code_length": s.game_code_length, "max_name": s.max_display_name_length},
    )
