"""What every request is held to before a route sees it (`app/guard.py`).

The suite runs with the per-process limits off (the root `conftest.py`); each
test here sets the limits it is about, and starts from no counts.
"""

import pytest

from app import db, guard
from app.config import settings


@pytest.fixture(autouse=True)
def fresh_counts():
    guard.reset()
    yield
    guard.reset()


@pytest.fixture
def no_database(monkeypatch):
    monkeypatch.setattr(db, "pool", None)


def test_every_answer_says_not_to_sniff_or_frame_it(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_a_clients_requests_are_limited_a_minute(client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "requests_per_minute", 3)
    said = [client.get("/catalog").status_code for _ in range(4)]
    # The first three reach the route (503: no database); the fourth does not.
    assert said == [503, 503, 503, 429]
    refused = client.get("/catalog")
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "60"
    assert refused.json() == {"detail": "Too many requests from one place. Ask again in a minute."}


def test_another_address_has_a_minute_of_its_own(client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "requests_per_minute", 1)
    one = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
    two = {"x-forwarded-for": "198.51.100.2"}
    assert client.get("/catalog", headers=one).status_code == 503
    assert client.get("/catalog", headers=one).status_code == 429
    assert client.get("/catalog", headers=two).status_code == 503


def test_the_service_as_a_whole_is_limited_whatever_address_is_claimed(
        client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "service_requests_per_minute", 2)
    said = [client.get("/catalog", headers={"x-forwarded-for": f"203.0.113.{n}"}).status_code
            for n in range(3)]
    assert said == [503, 503, 429]


def test_posts_have_a_lower_limit_than_reads(client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "posts_per_minute", 2)
    said = [client.post("/proteins/resolve", json={"gene": "BRCA1"}).status_code
            for _ in range(3)]
    assert said == [503, 503, 429]
    # Reads from the same place are still answered.
    assert client.get("/catalog").status_code == 503


def test_the_health_checks_are_never_held_to_the_services_minute(client, monkeypatch,
                                                                  no_database):
    monkeypatch.setattr(settings, "service_requests_per_minute", 1)
    assert client.get("/catalog").status_code == 503
    assert client.get("/catalog").status_code == 429
    # The service's minute is spent, and the keep-alive is still answered.
    assert [client.get("/health").status_code for _ in range(5)] == [200] * 5


def test_the_health_checks_count_against_their_clients_minute(client, monkeypatch):
    monkeypatch.setattr(settings, "requests_per_minute", 3)
    said = [client.get(path).status_code
            for path in ("/health", "/health/db", "/health", "/health/db")]
    assert said == [200, 200, 200, 429]


SPENT = {"detail": "The service has sent all it may today. Ask again after midnight UTC."}


def test_past_the_days_bytes_every_route_but_the_health_checks_is_refused(
        client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "response_bytes_per_day", 1_000)
    # No database: the route answers its own 503, a few hundred bytes with its
    # headers, until the day's thousand are sent; then the guard answers.
    answered = 0
    while client.get("/catalog").json() != SPENT:
        answered += 1
        assert answered < 20
    assert answered >= 1
    assert guard.day.sent() >= 1_000
    refused = client.get("/catalog")
    assert refused.status_code == 503
    assert refused.json() == SPENT
    assert 1 <= int(refused.headers["retry-after"]) <= guard.DAY_SECONDS
    assert client.post("/proteins/resolve", json={"gene": "BRCA1"}).json() == SPENT
    # The keep-alive is still answered.
    assert client.get("/health").json() == {"status": "ok"}


def test_the_days_bytes_count_bodies_and_headers(client, monkeypatch):
    monkeypatch.setattr(settings, "response_bytes_per_day", 10_000_000)
    response = client.get("/health")
    sent = guard.day.sent()
    assert sent > len(response.content)
    assert sent < len(response.content) + 1_000


def test_the_day_refuses_once_its_bytes_are_spent_and_says_so_once(client, monkeypatch,
                                                                  no_database, caplog):
    monkeypatch.setattr(settings, "response_bytes_per_day", 1)
    assert client.get("/health").status_code == 200
    with caplog.at_level("WARNING", logger="app.guard"):
        assert [client.get("/catalog").json() for _ in range(3)] == [SPENT] * 3
    assert sum("RESPONSE_BYTES_PER_DAY" in record.getMessage()
               for record in caplog.records) == 1


def test_no_days_budget_is_held_at_zero(client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "response_bytes_per_day", 0)
    guard.day.add(10 ** 12)
    response = client.get("/catalog")
    assert response.status_code == 503
    assert response.json() != SPENT


def test_a_day_starts_again_at_midnight_utc():
    now = [2 * guard.DAY_SECONDS - 10.0]
    day = guard.Day(clock=lambda: now[0])
    day.add(500)
    assert day.sent() == 500
    assert day.seconds_left() == 10
    assert day.first_refusal() and not day.first_refusal()
    now[0] = 2 * guard.DAY_SECONDS + 1.0
    assert day.sent() == 0
    assert day.first_refusal()
    assert day.seconds_left() == guard.DAY_SECONDS - 1


@pytest.mark.parametrize("headers, peer, said", [
    ({"cf-connecting-ip": "198.51.100.9", "x-forwarded-for": "203.0.113.7"}, None,
     "198.51.100.9"),
    ({"x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2"}, ("10.0.0.3", 1), "203.0.113.7"),
    ({}, ("192.0.2.4", 5000), "192.0.2.4"),
    ({}, None, "unknown"),
])
def test_a_client_is_the_address_it_asks_from(headers, peer, said):
    scope = {"headers": [(k.encode(), v.encode()) for k, v in headers.items()], "client": peer}
    assert guard.client_of(scope) == said


def test_a_body_larger_than_the_service_reads_is_refused_unread(client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "max_body_bytes", 64)
    response = client.post("/proteins/resolve", json={"gene": "B" * 100})
    assert response.status_code == 413
    assert response.json() == {"detail": "The request body is larger than this service reads."}


def test_a_body_with_no_length_is_still_held_to_the_limit(client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "max_body_bytes", 64)

    def chunks():
        yield b'{"gene": "'
        yield b"B" * 100
        yield b'"}'

    response = client.post("/proteins/resolve", content=chunks(),
                           headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_a_body_within_the_limit_reaches_the_route_whole(client, monkeypatch, no_database):
    monkeypatch.setattr(settings, "max_body_bytes", 64)
    # No database: the route read the body, validated it and answered 503.
    assert client.post("/proteins/resolve", json={"gene": "BRCA1"}).status_code == 503
    # And one the route reads as invalid is the route's 422, not the guard's.
    assert client.post("/proteins/resolve", json={"gene": ""}).status_code == 422


def test_a_window_forgets_its_counts_each_minute(monkeypatch):
    window = guard.Minute()
    now = [120.0]
    monkeypatch.setattr(guard.time, "monotonic", lambda: now[0])
    assert window.take("a", 1) and not window.take("a", 1)
    now[0] = 180.0
    assert window.take("a", 1)


def test_a_window_with_too_many_keys_lets_new_ones_through_uncounted():
    window = guard.Minute(keys_at_most=2)
    assert window.take("a", 1) and window.take("b", 1)
    # A third key is not kept: the service's own limit is what holds then.
    assert window.take("c", 1) and window.take("c", 1)
    assert not window.take("a", 1)
