"""Story 0.1 acceptance tests for the repo's top-level docs/config.

Real bug, found live on Story 0.4: every `open("../X")` call here
resolved relative to whatever directory `pytest` happened to be
invoked FROM, not this file's own location -- so this file only ever
had a chance of passing when pytest's CWD happened to be exactly
`api/` (CI's own `cd api && pytest`), and even then was never actually
collected there (api/pytest.ini's testpaths never included this
top-level tests/ directory at all -- see pytest.ini's own comment).
Resolving every path from `_REPO_ROOT` (derived from `__file__`) makes
these correct regardless of the invoking CWD.
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_readme_exists():
    """Test that README exists and contains required sections."""
    content = (_REPO_ROOT / "README.md").read_text()

    # Check for required sections from the story
    assert "# Expense Tracker" in content
    assert "## Quick Start" in content
    assert "## Environment Variables" in content
    assert "## Available Commands" in content
    assert "## Local Development" in content
    assert "## Architecture" in content
    assert "## CI/CD" in content

def test_readme_env_vars_table():
    """Test that README documents each env var, its purpose and whether it is required."""
    readme_content = (_REPO_ROOT / "README.md").read_text()

    # Check that the environment variables table exists
    assert "## Environment Variables" in readme_content

    # Check for each required variable from the story
    required_vars = ["SECRET_KEY", "POSTGRES_SERVER", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "REDIS_HOST"]
    for var in required_vars:
        assert var in readme_content, f"Missing required variable {var} in README"

    # Additionally, check that the table has columns for Variable, Required, Default, Description
    assert "| Variable | Required | Default | Description |" in readme_content

def test_makefile_commands():
    """Test that Makefile contains required commands."""
    makefile_content = (_REPO_ROOT / "Makefile").read_text()

    # Check for required commands from the story
    required_commands = ["dev", "test", "migrate", "lint", "type-check", "ci", "clean", "help"]
    for cmd in required_commands:
        assert f"{cmd}:" in makefile_content, f"Missing command {cmd} in Makefile"

def test_ci_pipeline_exists():
    """Test that CI pipeline configuration exists."""
    import yaml
    ci_config = yaml.safe_load((_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())

    # Check that the CI has jobs for lint, type-check, test, docker-build
    job_names = list(ci_config['jobs'].keys())
    assert "lint" in job_names, "Missing lint job in CI"
    assert "type-check" in job_names, "Missing type-check job in CI"
    assert "test" in job_names, "Missing test job in CI"
    assert "docker-build" in job_names, "Missing docker-build job in CI"

def test_docker_compose_exists():
    """Test that docker-compose file exists with required services."""
    import yaml
    compose_config = yaml.safe_load((_REPO_ROOT / "infra" / "docker-compose.yml").read_text())

    # Check for required services from the story
    services = compose_config['services']
    required_services = ["api", "web", "postgres", "redis", "worker", "mailhog"]
    for service in required_services:
        assert service in services, f"Missing service {service} in docker-compose"
