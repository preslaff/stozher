"""Webhooks: which alerts a consumer gets, signed, retried, and never sent to a private address (docs/api.md section 6)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from stozher_api.models import Alert, AuditLog, Webhook, WebhookDelivery
from stozher_api.webhook_logic import (
    MAX_AGE,
    RETRY_DELAYS,
    UnsafeUrl,
    area_overlaps,
    check_address,
    check_url,
    matched_event,
    next_attempt,
    sign,
    wants,
)
from stozher_api.webhook_service import deliver_due, dispatch

from .test_api import hdr

VARNA_AREA = {
    "type": "Polygon",
    "coordinates": [[[27.9, 43.2], [28.0, 43.2], [28.0, 43.25], [27.9, 43.25], [27.9, 43.2]]],
}
URL = "https://consumer.example/hook"
PUBLIC = ["93.184.216.34"]


# ---- pure rules --------------------------------------------------------------------------------


def test_which_alerts_a_webhook_wants() -> None:
    kw = dict(
        events=["hotspot"],
        bbox=None,
        min_level="info",
        include_sim=False,
        kind="hotspot_new",
        level="warning",
        route_id=None,
        sim=False,
        area=VARNA_AREA,
    )
    assert wants(**kw) == "hotspot"  # type: ignore[arg-type]
    assert wants(**{**kw, "events": ["alert"]}) == "alert"  # type: ignore[arg-type]
    assert wants(**{**kw, "kind": "route_affected", "route_id": "r1"}) is None  # type: ignore[arg-type]
    assert (
        wants(**{**kw, "events": ["route"], "kind": "route_affected", "route_id": "r1"}) == "route"
    )  # type: ignore[arg-type]
    assert wants(**{**kw, "min_level": "critical"}) is None  # type: ignore[arg-type]
    assert wants(**{**kw, "sim": True}) is None  # type: ignore[arg-type]
    assert wants(**{**kw, "sim": True, "include_sim": True}) == "hotspot"  # type: ignore[arg-type]
    assert wants(**{**kw, "bbox": [27.0, 43.0, 28.5, 43.5]}) == "hotspot"  # type: ignore[arg-type]
    assert wants(**{**kw, "bbox": [23.0, 42.0, 24.0, 43.0]}) is None  # type: ignore[arg-type]
    assert wants(**{**kw, "bbox": [27.0, 43.0, 28.5, 43.5], "area": None}) is None  # type: ignore[arg-type]
    assert matched_event(["trust_change"], "hotspot_new", None) is None


def test_area_overlap_uses_every_coordinate_shape() -> None:
    multi = {"type": "MultiPolygon", "coordinates": [VARNA_AREA["coordinates"]]}
    assert area_overlaps(multi, [27.0, 43.0, 28.5, 43.5])
    assert area_overlaps({"type": "Point", "coordinates": [27.95, 43.22]}, [27.9, 43.2, 28.0, 43.3])
    assert not area_overlaps(
        {"type": "Point", "coordinates": [10.0, 10.0]}, [27.9, 43.2, 28.0, 43.3]
    )
    assert not area_overlaps({}, [0, 0, 1, 1])


def test_signature_is_hmac_sha256_hex_of_the_raw_body() -> None:
    import hashlib
    import hmac

    body = b'{"a":1}'
    assert sign("whsec_x", body) == hmac.new(b"whsec_x", body, hashlib.sha256).hexdigest()
    assert sign("whsec_y", body) != sign("whsec_x", body)


def test_retries_back_off_and_give_up_inside_24_hours() -> None:
    t0 = datetime(2026, 10, 15, tzinfo=UTC)
    now = t0
    waits = []
    for done in range(1, len(RETRY_DELAYS) + 1):
        nxt = next_attempt(done, t0, now)
        assert nxt is not None
        waits.append(nxt - now)
        now = nxt
    assert waits == list(RETRY_DELAYS) and now - t0 <= MAX_AGE
    assert next_attempt(len(RETRY_DELAYS) + 1, t0, now) is None  # out of retries
    assert next_attempt(1, t0, t0 + MAX_AGE) is None  # too old


@pytest.mark.parametrize(
    "url",
    [
        "http://consumer.example/hook",
        "https://localhost/hook",
        "https://127.0.0.1/hook",
        "https://10.1.2.3/hook",
        "https://192.168.1.9:8443/hook",
        "https://169.254.169.254/latest",
        "https://[::1]/hook",
        "https://user:pw@consumer.example/hook",
        "https://printer.local/hook",
        "https:///hook",
    ],
)
def test_unsafe_urls_are_refused(url: str) -> None:
    with pytest.raises(UnsafeUrl):
        check_url(url)


def test_public_urls_and_addresses_pass_and_private_resolutions_do_not() -> None:
    assert check_url("https://consumer.example:8443/x") == ("consumer.example", 8443)
    check_address("93.184.216.34")
    for bad in (
        "10.0.0.5",
        "127.0.0.1",
        "172.16.0.1",
        "169.254.1.1",
        "::1",
        "fe80::1",
        "224.0.0.1",
    ):
        with pytest.raises(UnsafeUrl):
            check_address(bad)


# ---- the API ----------------------------------------------------------------------------------


def use_resolver(client: httpx.AsyncClient, table: dict[str, list[str]]) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return table.get(host, [])

    client.app.state.webhook_resolver = resolve  # type: ignore[attr-defined]


async def create(c: httpx.AsyncClient, **over: object) -> dict:  # type: ignore[type-arg]
    body = {"url": URL, "events": ["alert"], **over}
    r = await c.post("/v1/webhooks", headers=hdr(c, "operator"), json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_register_list_delete_with_the_secret_shown_once(client):  # type: ignore[no-untyped-def]
    use_resolver(client, {"consumer.example": PUBLIC})
    made = await create(
        client, events=["hotspot", "alert"], bbox=[27, 43, 28.5, 43.5], min_level="warning"
    )
    assert made["secret"].startswith("whsec_") and made["events"] == ["alert", "hotspot"]
    assert made["bbox"] == [27, 43, 28.5, 43.5] and made["min_level"] == "warning"
    listed = (await client.get("/v1/webhooks", headers=hdr(client, "operator"))).json()
    assert listed["count"] == 1 and "secret" not in json.dumps(listed)
    assert listed["items"][0]["deliveries"] == {"pending": 0, "ok": 0, "failed": 0}
    for role in ("responder", "reporter", "public", "system"):
        assert (await client.get("/v1/webhooks", headers=hdr(client, role))).status_code == 403
        r = await client.post(
            "/v1/webhooks", headers=hdr(client, role), json={"url": URL, "events": ["alert"]}
        )
        assert r.status_code == 403
    assert (
        await client.delete(f"/v1/webhooks/{made['id']}", headers=hdr(client, "operator"))
    ).status_code == 204
    assert (
        await client.delete(f"/v1/webhooks/{made['id']}", headers=hdr(client, "operator"))
    ).status_code == 404
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        acts = [a for (a,) in (await s.execute(select(AuditLog.action))).all()]
    assert "webhook.create" in acts and "webhook.delete" in acts
    assert made["secret"] not in json.dumps(acts)


async def test_unsafe_or_malformed_registrations_are_refused(client):  # type: ignore[no-untyped-def]
    use_resolver(
        client, {"consumer.example": PUBLIC, "inside.example": ["10.0.0.5"], "gone.example": []}
    )
    op = hdr(client, "operator")
    for body in (
        {"url": "http://consumer.example/h", "events": ["alert"]},
        {"url": "https://127.0.0.1/h", "events": ["alert"]},
        {"url": "https://inside.example/h", "events": ["alert"]},  # a name that points inside
        {"url": "https://gone.example/h", "events": ["alert"]},
        {"url": URL, "events": []},
        {"url": URL, "events": ["everything"]},
        {"url": URL, "events": ["alert"], "bbox": [1, 2, 3]},
        {"url": URL, "events": ["alert"], "bbox": [28, 43, 27, 44]},
        {"url": URL, "events": ["alert"], "min_level": "loud"},
        {"url": URL, "events": ["alert"], "secret": "mine"},
    ):
        r = await client.post("/v1/webhooks", headers=op, json=body)
        assert r.status_code == 422, body


# ---- delivery ---------------------------------------------------------------------------------


async def add_alert(c: httpx.AsyncClient, n: int, **kw: object) -> int:
    values = dict(
        level="warning",
        kind="hotspot_new",
        area=VARNA_AREA,
        message=f"alert {n}",
        sim=False,
        dedupe_key=f"t{n}",
    )
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        a = Alert(**{**values, **kw})
        s.add(a)
        await s.commit()
        return int(a.id)


class Consumer:
    """A fake receiving system: records requests, answers with a scripted status."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.statuses: list[int] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.statuses.pop(0) if self.statuses else 200)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def run(
    c: httpx.AsyncClient,
    consumer: Consumer,
    now: datetime,
    table: dict[str, list[str]] | None = None,
):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return (table or {"consumer.example": PUBLIC}).get(host, [])

    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await dispatch(s, now)
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        return await deliver_due(s, now, kek="ab" * 32, client=consumer.client(), resolver=resolve)


async def test_matching_alerts_are_delivered_signed_once_and_only_those(client):  # type: ignore[no-untyped-def]
    use_resolver(client, {"consumer.example": PUBLIC})
    old = await add_alert(client, 0)  # raised before the webhook exists: never sent to it
    made = await create(
        client, events=["hotspot"], bbox=[27.0, 43.0, 28.5, 43.5], min_level="warning"
    )
    wanted = await add_alert(client, 1)
    await add_alert(client, 2, level="info")  # below min_level
    await add_alert(client, 3, sim=True)  # simulated
    await add_alert(
        client, 4, area={"type": "Point", "coordinates": [23.3, 42.7]}
    )  # outside the box
    await add_alert(client, 5, kind="route_affected")  # a different event class
    consumer = Consumer()
    stats = await run(client, consumer, datetime.now(UTC))
    assert stats == {"ok": 1, "retry": 0, "failed": 0}
    (req,) = consumer.requests
    body = req.content
    assert req.headers["x-stozher-event"] == "hotspot"
    assert req.headers["x-stozher-signature"] == sign(made["secret"], body)
    payload = json.loads(body)
    assert payload["alert"]["id"] == wanted and payload["alert"]["message"] == "alert 1"
    assert payload["delivery_id"] == req.headers["x-stozher-delivery"]
    assert old != wanted
    again = Consumer()
    assert await run(client, again, datetime.now(UTC)) == {"ok": 0, "retry": 0, "failed": 0}
    assert again.requests == []  # nothing is sent twice
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        a = await s.get(Alert, wanted)
        assert a is not None and a.delivered[f"webhook:{made['id']}"] == "ok"
    listed = (await client.get("/v1/webhooks", headers=hdr(client, "operator"))).json()
    assert listed["items"][0]["deliveries"] == {"pending": 0, "ok": 1, "failed": 0}


async def test_a_failing_consumer_is_retried_with_backoff_then_succeeds(client):  # type: ignore[no-untyped-def]
    use_resolver(client, {"consumer.example": PUBLIC})
    made = await create(client)
    aid = await add_alert(client, 1)
    t0 = datetime.now(UTC)
    consumer = Consumer()
    consumer.statuses = [500, 503]
    assert await run(client, consumer, t0) == {"ok": 0, "retry": 1, "failed": 0}
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        d = (await s.execute(select(WebhookDelivery))).scalar_one()
        assert (d.state, d.attempts, d.last_status) == ("pending", 1, 500)
        assert d.next_attempt_at - t0 == RETRY_DELAYS[0]
        a = await s.get(Alert, aid)
        assert a is not None and a.delivered[f"webhook:{made['id']}"] == "pending"
    assert await run(client, consumer, t0 + timedelta(seconds=5)) == {
        "ok": 0,
        "retry": 0,
        "failed": 0,
    }  # not due yet
    assert await run(client, consumer, t0 + timedelta(seconds=11)) == {
        "ok": 0,
        "retry": 1,
        "failed": 0,
    }
    assert await run(client, consumer, t0 + timedelta(minutes=5)) == {
        "ok": 1,
        "retry": 0,
        "failed": 0,
    }
    ids = {r.headers["x-stozher-delivery"] for r in consumer.requests}
    assert (
        len(consumer.requests) == 3 and len(ids) == 1
    )  # the delivery id is stable across retries (consumers dedupe)


async def test_a_consumer_that_never_answers_is_given_up_after_the_last_retry(client):  # type: ignore[no-untyped-def]
    use_resolver(client, {"consumer.example": PUBLIC})
    await create(client)
    await add_alert(client, 1)
    consumer = Consumer()
    consumer.statuses = [500] * 20
    now = datetime.now(UTC)
    for _ in range(len(RETRY_DELAYS) + 3):
        await run(client, consumer, now)
        async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
            d = (await s.execute(select(WebhookDelivery))).scalar_one()
        if d.state != "pending":
            break
        now = d.next_attempt_at  # wait for the next scheduled try
    assert (
        len(consumer.requests) == len(RETRY_DELAYS) + 1
    )  # the first try and every scheduled retry
    assert d.state == "failed" and d.attempts == len(RETRY_DELAYS) + 1
    assert now - d.created_at < MAX_AGE  # all of it inside the 24 hours
    after = await run(client, consumer, now + timedelta(days=2))
    assert after == {"ok": 0, "retry": 0, "failed": 0}  # a failed delivery is not tried again


async def test_a_name_that_later_points_inside_is_not_called(client):  # type: ignore[no-untyped-def]
    use_resolver(client, {"consumer.example": PUBLIC})
    await create(client)
    await add_alert(client, 1)
    consumer = Consumer()
    stats = await run(client, consumer, datetime.now(UTC), {"consumer.example": ["10.0.0.5"]})
    assert stats["ok"] == 0 and consumer.requests == []  # DNS changed after registration
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        d = (await s.execute(select(WebhookDelivery))).scalar_one()
        assert d.last_error is not None and d.last_error.startswith("unsafe")


async def test_deleting_a_webhook_stops_its_deliveries(client):  # type: ignore[no-untyped-def]
    use_resolver(client, {"consumer.example": PUBLIC})
    made = await create(client)
    await add_alert(client, 1)
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await dispatch(s, datetime.now(UTC))
        assert (await s.execute(select(WebhookDelivery))).scalars().all()
    await client.delete(f"/v1/webhooks/{made['id']}", headers=hdr(client, "operator"))
    consumer = Consumer()
    assert await run(client, consumer, datetime.now(UTC)) == {"ok": 0, "retry": 0, "failed": 0}
    assert consumer.requests == []
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(Webhook))).scalars().all() == []
        assert (await s.execute(select(WebhookDelivery))).scalars().all() == []
