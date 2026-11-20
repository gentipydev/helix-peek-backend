"""Shared test fixtures.

NCBI_EMAIL is set before importing the app: app.config instantiates Settings at
import time, so without it the import itself fails. That is deliberate
production behaviour (see test_missing_email_blocks_startup), which the rest of
the suite has to work around.
"""

import io
import os
import pathlib

os.environ.setdefault("NCBI_EMAIL", "tests@example.com")

import pytest
from Bio import SeqIO
from fastapi.testclient import TestClient

from app import main
from app.main import app

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def fresh_health():
    """`/health/db` lets one answer stand for a while; no test sees another's."""
    main.forget_health()
    yield
    main.forget_health()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def genbank_text():
    """The real NG_007114 GenBank record, saved from NCBI."""
    return (FIXTURES / "ng_007114.gb").read_text()


@pytest.fixture
def genbank_record(genbank_text):
    """NG_007114 already parsed, for tests of the pure parser."""
    return SeqIO.read(io.StringIO(genbank_text), "genbank")
