# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "rasterio", "numpy", "scipy", "shapely", "pyproj", "geopandas", "pyogrio"]
# ///
"""How separable is the reference flood from everything else, with each simple SAR feature on its own?

AUC 0.5 = no information, 1.0 = perfect. It bounds what ANY single-feature threshold can achieve,
independently of how a threshold is chosen. Also: how the missed flooded pixels differ from the caught ones.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import events as ev
import flood
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy.stats import rankdata

cfg = ev.EVENTS["karditsa"]
with rasterio.open(sorted((ev.DATA / "karditsa" / "cdse").glob("*.tif"))[0]) as ds:
    a = ds.read().astype("float32")
    tf, crs, res = ds.transform, str(ds.crs), abs(ds.transform.a)
a[:4] = np.where(np.isfinite(a[:4]) & (a[:4] > -60) & (a[:4] < 30), a[:4], np.nan)
pre_vv, pre_vh, post_vv, post_vh, dem = a
root = Path(cfg["reference"])
lay = lambda n: gpd.read_file(next(root.glob(f"*_{n}_v*.shp"))).to_crs(crs)  # noqa: E731
rast = lambda g: rasterize(
    [(x, 1) for x in g.geometry], out_shape=pre_vv.shape, transform=tf, dtype="uint8"
).astype(bool)  # noqa: E731
obs = lay("observedEventA")
ref = rast(obs[obs["notation"] == "Flooded area"])
fp = lay("imageFootprintA")
valid = (
    np.isfinite(pre_vv)
    & np.isfinite(post_vv)
    & rast(lay("areaOfInterestA"))
    & ~rast(fp[fp["obj_type"].str.contains("Not Analysed")])
)
valid &= flood.slope_deg(dem, res) < flood.MAX_SLOPE_DEG  # the flat land where floods happen

sm = flood.despeckle
d_vv, d_vh = sm(post_vv) - sm(pre_vv), sm(post_vh) - sm(pre_vh)
features = {
    "post VV (dB)": -sm(post_vv),
    "post VH (dB)": -sm(post_vh),
    "VV change (drop = +)": -d_vv,
    "VH change (drop = +)": -d_vh,
    "VV increase (+)": d_vv,
    "post VH-VV ratio": sm(post_vh) - sm(post_vv),
}
rng = np.random.default_rng(1)
pos = np.flatnonzero((ref & valid).ravel())
neg = np.flatnonzero((~ref & valid).ravel())
pos, neg = rng.choice(pos, 150_000, replace=False), rng.choice(neg, 150_000, replace=False)
print(
    f"flooded pixels {int((ref & valid).sum()):,}  other flat valid pixels {int((~ref & valid).sum()):,}\n"
)
print(f"{'feature (higher = more flood-like)':38} {'AUC':>6}")
for k, f in features.items():
    x = np.concatenate([f.ravel()[pos], f.ravel()[neg]])
    ok = np.isfinite(x)
    r = rankdata(x[ok])
    n1 = ok[: len(pos)].sum()
    n0 = ok[len(pos) :].sum()
    print(f"{k:38} {(r[:n1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0):6.3f}")

print(
    "\nDrop-threshold sweep (rule: post VV < -14 dB, new water, VV drop >= X dB; flat land only):"
)
print(f"{'drop X':>7} {'flagged km2':>12} {'prec':>6} {'recall':>7} {'F1':>6}")
for x in (1.0, 1.5, 2.0, 3.0, 4.0, 5.0):
    m = valid & (sm(post_vv) < -14) & ~(sm(pre_vv) < -14) & (d_vv < -x)
    sc = flood.score(m, ref, valid, res)
    print(f"{x:7.1f} {sc['pred_km2']:12} {sc['precision']:6} {sc['recall']:7} {sc['f1']:6}")

m4 = valid & (sm(post_vv) < -14) & ~(sm(pre_vv) < -14) & (d_vv < -3.0)
miss, hit = ref & valid & ~m4, ref & valid & m4
print("\nMissed flooded pixels vs caught ones vs dry land (median):")
for label, m in (("caught", hit), ("missed", miss), ("not flooded", ~ref & valid)):
    print(
        f"  {label:12} n={int(m.sum()):>9,}  post VV {np.nanmedian(post_vv[m]):6.1f}  VV change {np.nanmedian(d_vv[m]):5.1f}  post VH {np.nanmedian(post_vh[m]):6.1f}  VH change {np.nanmedian(d_vh[m]):5.1f}"
    )
