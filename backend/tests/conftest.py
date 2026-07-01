"""Pytest fixtures for the mietspiegel-ai test suite.

Resets the module-level city database cache before every test so each
test gets a fresh database instance — preventing order-dependent failures
where an earlier test mutates the cache and a later test sees stale data.
"""

from __future__ import annotations

import pytest

from app.services.data_loader import reset_database_cache


@pytest.fixture(autouse=True)
def _clear_city_cache():
    reset_database_cache()
    yield
    reset_database_cache()
