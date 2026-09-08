"""Story 0.1 acceptance tests. This file predates, and largely
duplicates, api/tests/test_health.py's own two mocked health-check
tests -- kept as-is (not deduplicated here; that's a separate,
deliberate cleanup someone should choose to do, not a side effect of
Story 0.4) plus three tests unique to this file: README/CI-config
checks and one real, unmocked call to /healthz.

Real bug, found live on Story 0.4: this file used permanent
`os.environ[...] = ...` assignments at module import time -- exactly
the leak-into-sibling-test-files bug api/tests/test_health.py's own
docstring already documents fixing (via monkeypatch) for the
identical scenario. Once both directories became collectible together
in one pytest session (see pytest.ini's own comment), this file's
permanent POSTGRES_USER='test' overwrite made every OTHER test file
that runs afterward in the same session fail with a real
`role "test" does not exist`, since 'test' is not a real local
Postgres role this project's own tests connect with (see
tests/test_story_0_2_acceptance.py, tests/test_story_0_4_acceptance.py).
monkeypatch restores every value automatically after each test,
matching api/tests/test_health.py's own established fix.
"""
import sys
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_DIR = _REPO_ROOT / "api"
sys.path.insert(0, str(_API_DIR))


@pytest.fixture(autouse=True)
def _health_test_env(monkeypatch):
    monkeypatch.setenv('API_V1_STR', '/api/v1')
    monkeypatch.setenv('VERSION', '0.1.0')
    monkeypatch.setenv('POSTGRES_SERVER', 'localhost')
    monkeypatch.setenv('POSTGRES_USER', 'test')
    monkeypatch.setenv('POSTGRES_PASSWORD', 'test')
    monkeypatch.setenv('POSTGRES_DB', 'test')
    monkeypatch.setenv('REDIS_HOST', 'localhost')
    monkeypatch.setenv('REDIS_PORT', '6379')
    monkeypatch.setenv('REDIS_PASSWORD', '')
    monkeypatch.setenv('REDIS_DB', '0')
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key')

def test_health_check():
    # Import the app (which will use the environment variables)
    from app.main import app

    # Mock the database and redis connections
    with patch('psycopg2.connect') as mock_pg, \
         patch('redis.Redis') as mock_redis:

        # Setup postgres mock
        mock_pg_conn = MagicMock()
        mock_pg.return_value = mock_pg_conn

        # Setup redis mock
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis.return_value = mock_redis_instance

        client = TestClient(app)

        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert data["db"] == "connected"
        assert data["redis"] == "connected"


def test_health_check_db_disconnected():
    # Import the app (which will use the environment variables)
    from app.main import app

    # Mock the database connection to fail and redis to succeed
    with patch('psycopg2.connect') as mock_pg, \
         patch('redis.Redis') as mock_redis:

        # Setup postgres mock to raise an exception
        mock_pg.side_effect = Exception("Connection failed")

        # Setup redis mock
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis.return_value = mock_redis_instance

        client = TestClient(app)

        response = client.get("/healthz")
        assert response.status_code == 503
        data = response.json()
        # Error responses are wrapped in 'detail' by FastAPI HTTPException
        assert "detail" in data
        detail = data["detail"]
        assert detail["status"] == "error"
        assert detail["version"] == "0.1.0"
        assert detail["db"] == "disconnected"
        assert detail["redis"] == "connected"


def test_readme_env_vars_documentation():
    """Test that README documents each env var, its purpose and whether it is required."""
    readme_content = (_REPO_ROOT / "README.md").read_text()
    # Check that the environment variables table exists
    assert "## Environment Variables" in readme_content
    # Check for each required variable from the story
    required_vars = ["SECRET_KEY", "POSTGRES_SERVER", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "REDIS_HOST"]
    for var in required_vars:
        assert var in readme_content
    # Additionally, check that the table has columns for Variable, Required, Default, Description
    assert "| Variable | Required | Default | Description |" in readme_content

def test_ci_pipeline_jobs():
    """Test that CI pipeline runs on every PR and fails on lint, type or test errors."""
    import yaml
    ci_config = yaml.safe_load((_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    # Check that the CI has jobs for lint, type-check, test, docker-build
    job_names = list(ci_config['jobs'].keys())
    assert "lint" in job_names
    assert "type-check" in job_names
    assert "test" in job_names
    assert "docker-build" in job_names


def test_health_check_without_mocks():
    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/healthz")
    # The health endpoint should return either 200 (if services are up) or 503 (if postgres or redis is down)
    assert response.status_code in [200, 503]
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "db" in data
    assert "redis" in data

    if response.status_code == 200:
        assert data["status"] == "ok"
        assert data["db"] == "connected"
        assert data["redis"] == "connected"
    else:
        assert response.status_code == 503
        assert data["status"] == "error"
        # At least one of db or redis is disconnected
        assert data["db"] == "disconnected" or data["redis"] == "disconnected"
