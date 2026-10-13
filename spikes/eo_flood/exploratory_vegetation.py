# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "rasterio", "numpy", "scipy", "shapely", "pyproj", "geopandas", "pyogrio"]
# ///
"""EXPLORATORY: does flooded vegetation (backscatter increase near open water) explain the missed flood area?

Chosen after looking at the reference, so the numbers are optimistic. They test a hypothesis, they are not a
validated method. Thresholds are symmetric with the main method (3 dB) and set before running.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import events as ev
import flood
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage

name = "karditsa"
cfg = ev.EVENTS[name]
tif = sorted((ev.DATA / name / "cdse").glob("*.tif"))[0]
with rasterio.open(tif) as ds:
    a = ds.read().astype("float32")
    tf, crs, res = ds.transform, str(ds.crs), abs(ds.transform.a)
a[:4] = np.where(
    np.isfinite(a[:4]) & (a[:4] > -60) & (a[:4] < 30), a[:4], np.nan
)  # dB bands only, not elevation
a[4] = np.where(np.isfinite(a[4]), a[4], np.nan)
pre, post, dem = {"vv": a[0], "vh": a[1]}, {"vv": a[2], "vh": a[3]}, a[4]
slope = flood.slope_deg(dem, res)
t_vv, t_vh = flood.thresholds(flood.despeckle(post["vv"]), flood.despeckle(post["vh"]))
masks = flood.classify(pre, post, slope, res, t_vv, t_vh)
water = masks["M4 + terrain, opening, size"]

root = Path(cfg["reference"])
lay = lambda n: gpd.read_file(next(root.glob(f"*_{n}_v*.shp"))).to_crs(crs)  # noqa: E731
rast = lambda g: rasterize(
    [(x, 1) for x in g.geometry], out_shape=a[0].shape, transform=tf, dtype="uint8"
).astype(bool)  # noqa: E731
obs = lay("observedEventA")
ref = rast(obs[obs["notation"] == "Flooded area"])
valid = np.isfinite(pre["vv"]) & np.isfinite(post["vv"]) & rast(lay("areaOfInterestA"))
fp = lay("imageFootprintA")
valid &= ~rast(fp[fp["obj_type"].str.contains("Not Analysed")])

d = flood.despeckle(post["vv"]) - flood.despeckle(pre["vv"])
gentle = slope < flood.MAX_SLOPE_DEG
inc = valid & gentle & (d > 3.0)
inc = flood.remove_small(
    ndimage.binary_opening(inc, structure=np.ones((3, 3))), res, flood.MIN_AREA_HA
)
near = ndimage.binary_dilation(
    water, iterations=int(500 / res)
)  # within about 500 m of open flood water
inc_near = inc & near
rows = {
    "M4 open water (drop)": water,
    "increase >= 3 dB anywhere": inc,
    "increase >= 3 dB within 500 m of open water": inc_near,
    "M4 + increase within 500 m of water": water | inc_near,
}
print(f"{'variant':46} {'flagged':>9} {'prec':>6} {'recall':>7} {'F1':>6}")
out = {}
for k, m in rows.items():
    sc = flood.score(m, ref, valid, res)
    out[k] = sc
    print(f"{k:46} {sc['pred_km2']:>7} km2 {sc['precision']:>6} {sc['recall']:>7} {sc['f1']:>6}")
(ev.DATA / name / "exploratory_vegetation.json").write_text(json.dumps(out, indent=2))
