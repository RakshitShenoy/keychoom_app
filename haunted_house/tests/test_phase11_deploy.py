"""Deployment behaviour: cookies behind HTTPS, production configuration, the start script."""
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


def cookie_header(response):
    return response.headers["set-cookie"]


def test_session_cookies_are_secure_when_the_request_arrives_over_https(app):
    with TestClient(app, base_url="https://testserver") as https_client:
        assert "Secure" in cookie_header(https_client.post("/api/games", json={}))
    with TestClient(app, base_url="http://testserver") as http_client:
        assert "Secure" not in cookie_header(http_client.post("/api/games", json={}))


def test_secure_flag_can_still_be_forced_by_configuration(settings):
    from app.main import create_app

    forced = create_app(settings.model_copy(update={"cookie_secure": True}))
    from app.db.base import Base

    Base.metadata.create_all(forced.state.db.engine)
    with TestClient(forced, base_url="http://testserver") as client:
        assert "Secure" in cookie_header(client.post("/api/games", json={}))


def test_env_example_documents_every_environment_setting():
    from app.config import Settings

    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    missing = [name.upper() for name in Settings.model_fields if name.upper() not in documented]
    # fields that are deliberately not exposed as documented knobs (derived or internal)
    assert missing == [], f"add these to .env.example: {missing}"


def test_env_example_contains_no_real_secret():
    text = (ROOT / ".env.example").read_text()
    assert "SECRET_KEY=change-me" in text
    assert not re.search(r"SECRET_KEY=[A-Za-z0-9_\-]{32,}", text)


def test_replit_files_use_the_real_entry_point_and_bind_all_interfaces():
    start = (ROOT / "scripts" / "start.sh").read_text()
    assert "app.main:app" in start and "--host 0.0.0.0" in start and "${PORT:-8000}" in start
    assert "--workers 1" in start and "alembic upgrade head" in start and "--proxy-headers" in start
    replit = (ROOT / ".replit").read_text()
    assert "scripts/start.sh" in replit
    assert not re.search(r"^\s*SECRET_KEY\s*=", replit, re.M)  # no secret value is stored in the file
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "start.sh")]).returncode == 0


def test_start_script_refuses_to_run_production_without_a_secret():
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "start.sh")], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "APP_ENV": "production"},
    )
    assert result.returncode == 1 and "SECRET_KEY" in result.stderr


def test_no_hard_coded_hosts_in_application_code():
    offenders = []
    for folder in ("app", "static", "templates"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in {".py", ".js", ".html"} and "migrations" not in str(path):
                text = path.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"(127\.0\.0\.1|localhost|ws://[a-z0-9])", text):
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_static_assets_and_pages_are_found_from_any_working_directory(settings, tmp_path, monkeypatch):
    from app.db.base import Base
    from app.main import create_app

    monkeypatch.chdir(tmp_path)  # paths are anchored to the package, not to the current directory
    application = create_app(settings)
    Base.metadata.create_all(application.state.db.engine)
    with TestClient(application) as client:
        assert client.get("/static/js/realtime.js").status_code == 200
        assert client.get("/static/css/dev.css").status_code == 200
        for page in ("/", "/host", "/player"):
            assert client.get(page).status_code == 200
