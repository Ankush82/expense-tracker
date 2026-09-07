import os
import sys
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Get the directory of the test file
test_dir = os.path.dirname(os.path.abspath(__file__))
# Get the api directory (parent of the test directory)
api_dir = os.path.join(test_dir, '..', 'api')
# Add the api directory to the sys.path
sys.path.insert(0, api_dir)

# Set environment variables
os.environ['API_V1_STR'] = '/api/v1'
os.environ['VERSION'] = '0.1.0'
os.environ['POSTGRES_SERVER'] = 'localhost'
os.environ['POSTGRES_USER'] = 'test'
os.environ['POSTGRES_PASSWORD'] = 'test'
os.environ['POSTGRES_DB'] = 'test'
os.environ['REDIS_HOST'] = 'localhost'
os.environ['REDIS_PORT'] = '6379'
os.environ['REDIS_PASSWORD'] = ''
os.environ['REDIS_DB'] = '0'
os.environ['SECRET_KEY'] = 'test-secret-key'

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
    with open("../README.md", "r") as f:
        readme_content = f.read()
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
    with open("../.github/workflows/ci.yml", "r") as f:
        ci_config = yaml.safe_load(f)
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