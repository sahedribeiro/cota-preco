import pytest
from app.core.config import settings

@pytest.fixture(autouse=True)
def isolated_auth_settings(monkeypatch):
    # Existing domain tests bypass auth explicitly; auth tests turn it back on.
    monkeypatch.setattr(settings,'auth_enabled',False)
