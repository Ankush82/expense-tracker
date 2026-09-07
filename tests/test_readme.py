def test_readme_exists():
    """Test that README exists and contains required sections."""
    with open("../README.md", "r") as f:
        content = f.read()
    
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
    with open("../README.md", "r") as f:
        readme_content = f.read()
    
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
    with open("../Makefile", "r") as f:
        makefile_content = f.read()
    
    # Check for required commands from the story
    required_commands = ["dev", "test", "migrate", "lint", "type-check", "ci", "clean", "help"]
    for cmd in required_commands:
        assert f"{cmd}:" in makefile_content, f"Missing command {cmd} in Makefile"

def test_ci_pipeline_exists():
    """Test that CI pipeline configuration exists."""
    import yaml
    with open("../.github/workflows/ci.yml", "r") as f:
        ci_config = yaml.safe_load(f)
    
    # Check that the CI has jobs for lint, type-check, test, docker-build
    job_names = list(ci_config['jobs'].keys())
    assert "lint" in job_names, "Missing lint job in CI"
    assert "type-check" in job_names, "Missing type-check job in CI"
    assert "test" in job_names, "Missing test job in CI"
    assert "docker-build" in job_names, "Missing docker-build job in CI"

def test_docker_compose_exists():
    """Test that docker-compose file exists with required services."""
    import yaml
    with open("../infra/docker-compose.yml", "r") as f:
        compose_config = yaml.safe_load(f)
    
    # Check for required services from the story
    services = compose_config['services']
    required_services = ["api", "web", "postgres", "redis", "worker", "mailhog"]
    for service in required_services:
        assert service in services, f"Missing service {service} in docker-compose"