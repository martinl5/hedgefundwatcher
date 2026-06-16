import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip real sleeps (rate limiting and retry backoff) so tests stay fast."""
    monkeypatch.setattr("time.sleep", lambda *args, **kwargs: None)
