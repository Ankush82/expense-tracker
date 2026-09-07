"""Root-level conftest so `pytest` run from the repo root (not `cd api &&
...`) still finds api/'s package and its installed dependencies. STORY 0.1's
own tests live under api/tests/, and api/ manages its dependencies via
requirements.txt (not a root pyproject.toml), so nothing at the repo root
otherwise tells a bare `pytest` where to look."""

import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent / "api"
sys.path.insert(0, str(_API_DIR))

_VENV_SITE_PACKAGES = next(_API_DIR.glob(".venv/lib/python3.*/site-packages"), None)
if _VENV_SITE_PACKAGES is not None and _VENV_SITE_PACKAGES.is_dir():
    sys.path.insert(0, str(_VENV_SITE_PACKAGES))
