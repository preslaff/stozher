"""Webhook rules with no database and no network: which alerts a webhook gets, how a delivery is signed and retried,
and which URLs may be called (docs/api.md section 6). Pure, so every rule is tested directly."""

import hashlib
import hmac
import ipaddress
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

EVENTS = (
    "alert",
    "hotspot",
    "trust_change",
    "route",
)  # trust_change is reserved: nothing raises it yet
LEVELS = {"info": 0, "warning": 1, "critical": 2}
# Delay before each retry; after the last one fails the delivery is given up (about 17 h in all, under the 24 h limit).
RETRY_DELAYS = tuple(timedelta(seconds=s) for s in (10, 30, 120, 600, 3600, 4 * 3600, 12 * 3600))
MAX_AGE = timedelta(hours=24)


def category(kind: str, route_id: object) -> str | None:
    """The specific event class an alert belongs to: `hotspot`, `route` or none (a plain alert)."""
    if kind.startswith("hotspot_"):
        return "hotspot"
    if route_id is not None or kind.startswith("route"):
        return "route"
    if kind == "trust_change":
        return "trust_change"
    return None


def matched_event(events: Iterable[str], kind: str, route_id: object) -> str | None:
    """Which subscribed event this alert is delivered as (`alert` takes everything), or None."""
    subscribed = set(events)
    if "alert" in subscribed:
        return "alert"
    cat = category(kind, route_id)
    return cat if cat in subscribed else None


def _coords(node: Any) -> Iterable[tuple[float, float]]:
    if isinstance(node, (list, tuple)):
        if len(node) >= 2 and all(isinstance(v, (int, float)) for v in node[:2]):
            yield float(node[0]), float(node[1])
        else:
            for child in node:
                yield from _coords(child)


def area_overlaps(area: dict[str, Any] | None, bbox: list[float]) -> bool:
    """Does the alert's area touch the box? Uses the area's own bounding box: generous, never misses an affected area."""
    if not area:
        return False
    pts = list(_coords(area.get("coordinates")))
    if not pts:
        return False
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    return not (
        max(lons) < bbox[0] or min(lons) > bbox[2] or max(lats) < bbox[1] or min(lats) > bbox[3]
    )


def wants(
    *,
    events: list[str],
    bbox: list[float] | None,
    min_level: str,
    include_sim: bool,
    kind: str,
    level: str,
    route_id: object,
    sim: bool,
    area: dict[str, Any] | None,
) -> str | None:
    """The event name to deliver this alert as, or None if the webhook does not want it."""
    if sim and not include_sim:
        return None  # simulated data never reaches a consumer unless it asked for it (and it is labelled)
    if LEVELS.get(level, 0) < LEVELS[min_level]:
        return None
    event = matched_event(events, kind, route_id)
    if event is None:
        return None
    if bbox is not None and not area_overlaps(area, bbox):
        return None
    return event


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 of the raw body under the secret exactly as the consumer was given it, hex."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def next_attempt(attempts_done: int, created_at: datetime, now: datetime) -> datetime | None:
    """When to try again after `attempts_done` failures (1 = the first try failed); None = give up."""
    if attempts_done > len(RETRY_DELAYS) or now - created_at >= MAX_AGE:
        return None
    due = now + RETRY_DELAYS[attempts_done - 1]
    return due if due - created_at <= MAX_AGE else None


class UnsafeUrl(ValueError):
    pass


def check_url(url: str) -> tuple[str, int]:
    """Syntax rules for a target: https, a host name or public address, no credentials. Returns (host, port)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeUrl("the URL must use https")
    if not parts.hostname:
        raise UnsafeUrl("the URL has no host")
    if parts.username or parts.password:
        raise UnsafeUrl("the URL must not contain credentials")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise UnsafeUrl("the URL has a bad port") from exc
    host = parts.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host.lower() in ("localhost",) or host.lower().endswith(
            (".local", ".internal", ".localhost")
        ):
            raise UnsafeUrl("the host is not a public name") from None
    else:
        check_address(str(ip))
    return host, port


def check_address(address: str) -> None:
    """A resolved address must be globally routable: no loopback, private, link-local, multicast or reserved range."""
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast:
        raise UnsafeUrl(f"the host resolves to a non-public address ({ip.version})")
