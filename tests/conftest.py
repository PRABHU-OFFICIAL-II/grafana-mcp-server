import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Provide a fake active session so tests don't need real auth
from grafana_mcp.auth import manager as _manager
from grafana_mcp.auth.session import Session

FAR_FUTURE = 9_999_999_999_999  # ms — never expires


@pytest.fixture(autouse=True)
def fake_session():
    _manager._current = Session(grafana_session="test-cookie", expires_at=FAR_FUTURE)
    yield
    _manager._current = None
