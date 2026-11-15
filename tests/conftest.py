import pytest

from market import models


@pytest.fixture(autouse=True)
def no_dotenv(tmp_path, monkeypatch):
    """No test reads the repository's .env: the file is looked for in the test's own folder."""
    monkeypatch.setattr(models, "ENV_FILE", str(tmp_path / ".env"))
