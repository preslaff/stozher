"""Shared helpers for situational endpoints."""

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Query

from ..problems import ApiError


def parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    """minLon,minLat,maxLon,maxLat"""
    if bbox is None:
        return None
    try:
        a, b, c, d = (float(x) for x in bbox.split(","))
    except ValueError as exc:
        raise ApiError(
            422, "validation-error", "Invalid bbox", "use minLon,minLat,maxLon,maxLat"
        ) from exc
    if not (-180 <= a < c <= 180 and -90 <= b < d <= 90):
        raise ApiError(
            422, "validation-error", "Invalid bbox", "coordinates out of range or inverted"
        )
    return a, b, c, d


def age_s(newest: datetime | None, now: datetime | None = None) -> int | None:
    if newest is None:
        return None
    return max(0, int(((now or datetime.now(UTC)) - newest).total_seconds()))


def geojson(value: str | None) -> dict[str, Any] | None:
    return json.loads(value) if value else None


BBOX = Query(default=None, description="minLon,minLat,maxLon,maxLat")
