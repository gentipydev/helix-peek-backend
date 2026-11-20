"""Tests for GET /health."""


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_the_api_maps_are_not_served(client):
    """No /docs, /redoc or /openapi.json: bytes sent for no reader (2026-11-20)."""
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404, path


def test_the_gene_route_is_retired(client):
    """`/gene/{id}/{gene}` read NCBI for anyone and sent a gene's whole genomic
    sequence; the app reads each record as a stored track (retired 2026-11-20)."""
    for path in ("/gene/NG_012232/DMD", "/gene/NG_007114/INS"):
        assert client.get(path).status_code == 404, path
