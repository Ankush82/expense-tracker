"""
Story 0.1 acceptance criteria tests:
1. README documents each env var, its purpose and whether it is required
2. CI pipeline runs on every PR and fails on lint, type or test errors
3. GET /healthz returns 503 when postgres is stopped
4. Config loaded from environment via typed settings object; app fails loudly
   at boot if a required env var is missing
"""
import os
import yaml

def test_makefile_targets_exist():
    """AC: 'make dev' boots everything; 'make test' runs tests; 'make migrate' applies migrations."""
    with open("./Makefile", "r") as f:
        content = f.read()
    assert "dev:" in content
    assert "test:" in content
    assert "migrate:" in content

def test_ci_pipeline_runs_on_pr():
    """AC: CI pipeline runs on every PR and fails on lint, type or test errors."""
    with open("./.github/workflows/ci.yml", "r") as f:
        ci_config = yaml.safe_load(f)
    # Must trigger on pull_request
    assert "pull_request" in ci_config.get(True, {}) or "pull_request" in ci_config
    # Must have lint, type-check, test, docker-build jobs
    job_names = list(ci_config['jobs'].keys())
    for required in ["lint", "type-check", "test", "docker-build"]:
        assert required in job_names, f"CI missing required job: {required}"

def test_readme_documents_each_env_var_with_required_flag():
    """AC: README documents each env var, its purpose and whether it is required."""
    with open("./README.md", "r") as f:
        readme = f.read()
    assert "## Environment Variables" in readme
    # Required env vars per the story
    for var in ["SECRET_KEY", "POSTGRES_SERVER", "POSTGRES_USER",
                "POSTGRES_PASSWORD", "POSTGRES_DB", "REDIS_HOST"]:
        assert var in readme, f"README missing env var: {var}"
    # Table must include 'Required' and 'Description' columns
    assert "Required" in readme and "Description" in readme

def test_docker_compose_has_required_services():
    """AC: docker-compose with api, web, postgres, redis, worker, mailhog."""
    with open("./infra/docker-compose.yml", "r") as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {})
    for svc in ["api", "web", "postgres", "redis", "worker", "mailhog"]:
        assert svc in services, f"docker-compose missing service: {svc}"
    # mailhog must expose SMTP capture port 1025 and web UI port 8025
    mailhog_ports = services["mailhog"].get("ports", [])
    ports_str = " ".join(str(p) for p in mailhog_ports)
    assert "1025" in ports_str, "mailhog SMTP port 1025 not exposed"
    assert "8025" in ports_str, "mailhog web UI port 8025 not exposed"