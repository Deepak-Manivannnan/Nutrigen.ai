"""Marker registration for the live suite only.

Kept inside tests/live/ so registering the marker cannot change collection,
rootdir or .env resolution for the fast suite in tests/.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: hits the real LLM and real external APIs; slow and billed. "
        "Lives outside tests/ so the default run never collects it.",
    )
