"""OpenStreetMap roads (Overpass API, ODbL) and their overlap with a flood mask."""

import json
import time
from pathlib import Path

import httpx
import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import LineString
from shapely.ops import transform

OVERPASS = "https://overpass-api.de/api/interpreter"
CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential")


ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
HEADERS = {"User-Agent": "stozher-eo-spike/0.1 (CASSINI hackathon prototype; one-off query)"}


def fetch(bbox: tuple[float, float, float, float], cache: Path) -> list[dict]:
    """Road ways in the bbox from public Overpass servers (retries; cached on success)."""
    if cache.exists():
        return json.loads(cache.read_text())
    w, s, e, n = bbox
    q = f'[out:json][timeout:240];way["highway"~"^({"|".join(CLASSES)})$"]({s},{w},{n},{e});out geom tags;'
    last: Exception | None = None
    for attempt in range(4):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            r = httpx.post(url, data={"data": q}, timeout=300, headers=HEADERS)
            r.raise_for_status()
            ways = [
                {
                    "id": el["id"],
                    "highway": el["tags"].get("highway"),
                    "name": el["tags"].get("name") or el["tags"].get("ref"),
                    "coords": [(p["lon"], p["lat"]) for p in el["geometry"]],
                }
                for el in r.json()["elements"]
                if "geometry" in el
            ]
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(ways))
            return ways
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"Overpass unavailable: {last}")


def road_rasters(
    ways: list[dict], crs: str, transform_, shape: tuple[int, int]
) -> dict[str, np.ndarray]:
    """One boolean raster per highway class: pixels the roads pass through."""
    tf = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    out: dict[str, np.ndarray] = {}
    for cls in CLASSES:
        geoms = [
            transform(tf, LineString(w["coords"]))
            for w in ways
            if w["highway"] == cls and len(w["coords"]) > 1
        ]
        out[cls] = (
            rasterize(
                [(g, 1) for g in geoms],
                out_shape=shape,
                transform=transform_,
                all_touched=True,
                dtype="uint8",
            ).astype(bool)
            if geoms
            else np.zeros(shape, bool)
        )
    return out


def affected_km(
    roads: dict[str, np.ndarray], flood: np.ndarray, valid: np.ndarray, res: float
) -> dict[str, dict[str, float]]:
    """Approximate road length (km) in each class and how much of it lies in `flood`."""
    out = {}
    for cls, m in roads.items():
        total = float((m & valid).sum()) * res / 1000
        hit = float((m & flood & valid).sum()) * res / 1000
        out[cls] = {"road_km": round(total, 1), "flooded_km": round(hit, 2)}
    return out


def agreement(
    roads: dict[str, np.ndarray], pred: np.ndarray, ref: np.ndarray, valid: np.ndarray
) -> dict[str, float]:
    any_road = np.zeros_like(pred)
    for m in roads.values():
        any_road |= m
    p, r = pred & any_road & valid, ref & any_road & valid
    tp, fp, fn = int((p & r).sum()), int((p & ~r).sum()), int((~p & r).sum())
    return {
        "precision": round(tp / max(tp + fp, 1), 3),
        "recall": round(tp / max(tp + fn, 1), 3),
        "ref_flooded_road_px": int(r.sum()),
        "pred_flooded_road_px": int(p.sum()),
    }
