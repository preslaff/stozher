"""SAR flood mapping by change detection: deliberately simple, transparent and deterministic.

Each step is one rule, so the spike can show which rules matter:
  M1 post-event water      VV below a threshold in the post scene
  M2 + new water           and not already water in the pre scene (permanent water removed)
  M3 + backscatter drop    and the VV dropped by at least 3 dB against the pre scene
  M4 + terrain and size    and slope < 5 degrees, opened, and objects of at least 0.5 ha
  M5 + cross-polarisation  and VH also below its own threshold
"""

import numpy as np
from scipy import ndimage

DROP_DB = 3.0
MAX_SLOPE_DEG = 5.0
MIN_AREA_HA = 0.5


def otsu(values: np.ndarray, lo: float, hi: float, bins: int = 256) -> float:
    """Otsu's threshold of a 1-D sample (the cut that best separates two populations)."""
    v = values[np.isfinite(values)]
    hist, edges = np.histogram(v, bins=bins, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist).astype(float)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1)
    m1 = (np.sum(hist * centers) - np.cumsum(hist * centers)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centers[np.argmax(between)])


def despeckle(db: np.ndarray, size: int = 3) -> np.ndarray:
    """Median filter that leaves no-data as no-data."""
    nan = ~np.isfinite(db)
    out = ndimage.median_filter(np.where(nan, 0.0, db), size=size)
    return np.where(nan, np.nan, out).astype("float32")


def slope_deg(dem: np.ndarray, res: float) -> np.ndarray:
    z = ndimage.gaussian_filter(np.where(np.isfinite(dem), dem, np.nanmedian(dem)), 1.0)
    dy, dx = np.gradient(z, res)
    return np.degrees(np.arctan(np.hypot(dx, dy))).astype("float32")


def remove_small(mask: np.ndarray, res: float, min_ha: float) -> np.ndarray:
    min_px = max(1, int(round(min_ha * 10_000 / (res * res))))
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_px
    keep[0] = False
    return keep[lab]


def thresholds(post_vv: np.ndarray, post_vh: np.ndarray) -> tuple[float, float]:
    """Automatic water thresholds from the post scene alone (no reference data involved)."""
    t_vv = float(np.clip(otsu(post_vv, -30, 0), -24.0, -12.0))  # sigma0 sits a little below gamma0
    t_vh = float(np.clip(otsu(post_vh, -35, -5), -32.0, -18.0))
    return t_vv, t_vh


def classify(
    pre: dict, post: dict, slope: np.ndarray, res: float, t_vv: float, t_vh: float
) -> dict[str, np.ndarray]:
    pre_vv, post_vv, post_vh = despeckle(pre["vv"]), despeckle(post["vv"]), despeckle(post["vh"])
    valid = np.isfinite(pre_vv) & np.isfinite(post_vv)
    m1 = valid & (post_vv < t_vv)
    m2 = m1 & ~(pre_vv < t_vv)
    m3 = m2 & ((post_vv - pre_vv) < -DROP_DB)
    m4 = m3 & (slope < MAX_SLOPE_DEG)
    m4 = ndimage.binary_opening(m4, structure=np.ones((3, 3)))
    m4 = remove_small(m4, res, MIN_AREA_HA)
    m5 = m4 & np.isfinite(post_vh) & (post_vh < t_vh)
    m5 = remove_small(m5, res, MIN_AREA_HA)
    return {
        "M1 post-event water": m1,
        "M2 + new water only": m2,
        "M3 + drop >= 3 dB": m3,
        "M4 + terrain, opening, size": m4,
        "M5 + VH confirms": m5,
    }


def score(pred: np.ndarray, ref: np.ndarray, valid: np.ndarray, res: float) -> dict[str, float]:
    p, r = pred & valid, ref & valid
    tp, fp, fn = int((p & r).sum()), int((p & ~r).sum()), int((~p & r).sum())
    px_km2 = res * res / 1e6
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    return {
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(2 * prec * rec / max(prec + rec, 1e-9), 3),
        "iou": round(tp / max(tp + fp + fn, 1), 3),
        "pred_km2": round(float(p.sum()) * px_km2, 1),
        "ref_km2": round(float(r.sum()) * px_km2, 1),
    }


def recall_by_size(
    pred: np.ndarray, ref: np.ndarray, valid: np.ndarray, res: float
) -> dict[str, dict[str, float]]:
    """Of the reference flooded objects, how much area each size class we recover."""
    lab, n = ndimage.label(ref & valid)
    if n == 0:
        return {}
    px = np.bincount(lab.ravel(), minlength=n + 1).astype(float)
    hit = np.bincount(lab[pred & valid].ravel(), minlength=n + 1).astype(float)
    km2 = px * res * res / 1e6
    out = {}
    for name, lo, hi in (("< 0.5 km2", 0, 0.5), ("0.5 - 5 km2", 0.5, 5), ("> 5 km2", 5, 1e9)):
        sel = (km2 >= lo) & (km2 < hi)
        sel[0] = False
        out[name] = {
            "objects": int(sel.sum()),
            "ref_km2": round(float(km2[sel].sum()), 1),
            "recall": round(float(hit[sel].sum() / max(px[sel].sum(), 1)), 3),
        }
    return out
