# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "rasterio", "numpy", "scipy", "shapely", "pyproj", "geopandas", "pyogrio", "matplotlib"]
# ///
"""Run the SAR flood spike for one event: classify, score against the reference (if any), roads, figures, findings."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import events as ev
import flood
import geopandas as gpd
import grid as s
import matplotlib
import numpy as np
import roads as rd
from pyproj import Transformer
from rasterio.features import rasterize, shapes
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import transform

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

name = sys.argv[1]
cfg = ev.EVENTS[name]
out_dir = ev.DATA / name
out_dir.mkdir(parents=True, exist_ok=True)
t0 = time.time()
# ---- scenes and terrain: the composites CDSE built for us (spikes/eo_flood/cdse_composites.py) ---------------------------
import rasterio

tifs = sorted((out_dir / "cdse").glob("*.tif"))
if not tifs:
    sys.exit(
        f"no CDSE composite for {name}: run `uv run spikes/eo_flood/cdse_composites.py {name}` first"
    )
with rasterio.open(tifs[0]) as ds:
    bands = {i + 1: (ds.descriptions[i] or f"band{i + 1}") for i in range(ds.count)}
    nodata = ds.nodata
    arr = ds.read().astype("float32")
    grid = s.Grid(str(ds.crs), ds.transform, ds.width, ds.height, abs(ds.transform.a))
arr = np.where(
    np.isfinite(arr) & (arr != nodata) if nodata is not None else np.isfinite(arr), arr, np.nan
)
print(
    "CDSE composite:",
    tifs[0].name,
    f"{grid.width}x{grid.height} at {grid.res:g} m",
    grid.crs,
    "bands:",
    bands,
    flush=True,
)
shape_hw = (grid.height, grid.width)
order = ["VV_pre", "VH_pre", "VV_post", "VH_post", "DEM"]
names = [bands[i + 1] for i in range(len(bands))]
idx = (
    {n: names.index(n) for n in order}
    if all(n in names for n in order)
    else {n: i for i, n in enumerate(order)}
)
pre = {"vv": arr[idx["VV_pre"]], "vh": arr[idx["VH_pre"]]}
post = {"vv": arr[idx["VV_post"]], "vh": arr[idx["VH_post"]]}
dem = arr[idx["DEM"]]
for label, d in (("pre", pre), ("post", post)):
    for pol, a_ in d.items():
        d[pol] = np.where(
            np.isfinite(a_) & (a_ > -60) & (a_ < 30), a_, np.nan
        )  # drop -inf/outliers from log of zero
        print(
            f"  {label} {pol.upper()}: valid {100 * np.isfinite(d[pol]).mean():.0f}%, dB p2/p50/p98 {np.nanpercentile(d[pol], [2, 50, 98]).round(1)}",
            flush=True,
        )
slope = flood.slope_deg(dem, grid.res)
print(f"loaded ({time.time() - t0:.0f} s): pre {cfg['pre']}, post {cfg['post']}", flush=True)

# ---- classification ---------------------------------------------------------------------------------------------------
t_vv, t_vh = flood.thresholds(flood.despeckle(post["vv"]), flood.despeckle(post["vh"]))
masks = flood.classify(pre, post, slope, grid.res, t_vv, t_vh)
valid = np.isfinite(pre["vv"]) & np.isfinite(post["vv"])
px_km2 = grid.res * grid.res / 1e6
print(
    f"thresholds: VV {t_vv:.1f} dB, VH {t_vh:.1f} dB (Otsu on the post scene, clipped; no reference used)",
    flush=True,
)

result: dict = {
    "event": cfg["name"],
    "grid": {"crs": grid.crs, "res_m": grid.res, "px": [grid.width, grid.height]},
    "scenes": {
        "pre": cfg["pre"],
        "post": cfg["post"],
        "orbit": cfg["orbit"],
        "source": "CDSE openEO SENTINEL1_GRD, sigma0-ellipsoid, Copernicus DEM 30 m",
    },
    "thresholds_db": {"vv": round(t_vv, 1), "vh": round(t_vh, 1)},
    "reference": cfg["reference_note"],
    "valid_area_km2": round(float(valid.sum()) * px_km2, 1),
    "methods": {
        k: {"flagged_km2": round(float((m & valid).sum()) * px_km2, 1)} for k, m in masks.items()
    },
}

# ---- reference ------------------------------------------------------------------------------------------------------
ref = ref_water = None
if cfg["reference"]:
    root = Path(cfg["reference"])
    layer = lambda n: gpd.read_file(next(root.glob(f"*_{n}_v*.shp"))).to_crs(grid.crs)  # noqa: E731
    raster = lambda g: rasterize(
        [(x, 1) for x in g.geometry if x is not None],
        out_shape=shape_hw,
        transform=grid.transform,
        dtype="uint8",
    ).astype(bool)  # noqa: E731
    observed = layer("observedEventA")
    ref = raster(observed[observed["notation"] == "Flooded area"])
    ref_trace = raster(observed[observed["notation"] == "Flood trace"])
    ref_water = raster(layer("waterExtentA"))
    aoi = raster(layer("areaOfInterestA"))
    fp = layer("imageFootprintA")
    no_data = (
        raster(fp[fp["obj_type"].str.contains("Not Analysed")])
        if len(fp)
        else np.zeros(shape_hw, bool)
    )
    valid &= aoi & ~no_data
    result["valid_area_km2"] = round(float(valid.sum()) * px_km2, 1)
    result["reference_km2"] = {
        "flooded_area": round(float((ref & valid).sum()) * px_km2, 1),
        "flood_traces_receded": round(float((ref_trace & valid).sum()) * px_km2, 1),
        "water_at_acquisition": round(float((ref_water & valid).sum()) * px_km2, 1),
    }
    for k, m in masks.items():
        result["methods"][k].update(flood.score(m, ref, valid, grid.res))
        result["methods"][k]["vs_water_extent"] = flood.score(m, ref_water, valid, grid.res)
    best = max(result["methods"], key=lambda k: result["methods"][k]["f1"])
    result["best_method"] = best
    result["recall_by_size"] = flood.recall_by_size(masks[best], ref, valid, grid.res)
    # sensitivity to the water threshold, shown for transparency (NOT used to pick the threshold)
    sens = {}
    for t in (-22, -20, -18, -16, -14, -12):
        mm = flood.classify(pre, post, slope, grid.res, float(t), t_vh)[
            "M4 + terrain, opening, size"
        ]
        sens[str(t)] = flood.score(mm, ref, valid, grid.res)["f1"]
    result["threshold_sensitivity_f1_M4"] = sens
else:
    result["best_method"] = "M4 + terrain, opening, size"
final = masks[result["best_method"]]

# ---- roads ----------------------------------------------------------------------------------------------------------
try:
    ways = rd.fetch(cfg["bbox"], ev.DATA / "cache" / f"osm_roads_{name}.json")
    rr = rd.road_rasters(ways, grid.crs, grid.transform, shape_hw)
    result["roads"] = {"ways": len(ways), "predicted": rd.affected_km(rr, final, valid, grid.res)}
    if ref is not None:
        result["roads"]["reference"] = rd.affected_km(rr, ref, valid, grid.res)
        result["roads"]["agreement_on_road_pixels"] = rd.agreement(rr, final, ref, valid)
except Exception as exc:  # the road overlay is secondary: never lose the flood result over it
    result["roads"] = {"unavailable": str(exc)[:200]}
    print("roads skipped:", str(exc)[:120], flush=True)

# ---- findings (the eo_findings shape) ---------------------------------------------------------------------------------
lab, n = ndimage.label(final)
drop = flood.despeckle(post["vv"]) - flood.despeckle(pre["vv"])
mean_drop = ndimage.mean(drop, lab, index=np.arange(1, n + 1)) if n else []
sizes = np.bincount(lab.ravel(), minlength=n + 1)[1:] * px_km2 * 100  # hectares
to_ll = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform
feats = []
for geom, v in shapes(lab.astype("int32"), mask=lab > 0, transform=grid.transform):
    i = int(v) - 1
    if sizes[i] < 1.0:  # report objects of at least 1 ha
        continue
    g = transform(to_ll, shape(geom).simplify(grid.res / 2))
    feats.append(
        {
            "type": "Feature",
            "geometry": mapping(g),
            "properties": {
                "kind": "flood_water",
                "sensor": "Sentinel-1 GRD sigma0 VV/VH (CDSE openEO)",
                "method": result["best_method"],
                "pre_scene": cfg["pre"],
                "post_scene": cfg["post"],
                "orbit": cfg["orbit"],
                "area_ha": round(float(sizes[i]), 1),
                "mean_vv_drop_db": round(float(mean_drop[i]), 1),
            },
        }
    )
feats.sort(key=lambda f: -f["properties"]["area_ha"])
(out_dir / "findings.geojson").write_text(
    json.dumps({"type": "FeatureCollection", "features": feats[:500]})
)
result["findings"] = {
    "objects_ge_1ha": len(feats),
    "total_ha": round(sum(f["properties"]["area_ha"] for f in feats), 0),
    "largest_ha": [f["properties"]["area_ha"] for f in feats[:5]],
}

# ---- plausibility (works without a reference) ---------------------------------------------------------------------------
fl = final & valid
if fl.any():
    result["flagged_terrain"] = {
        "median_elevation_m": round(float(np.nanmedian(dem[fl])), 0),
        "p90_elevation_m": round(float(np.nanpercentile(dem[fl], 90)), 0),
        "median_slope_deg": round(float(np.nanmedian(slope[fl])), 1),
        "scene_median_elevation_m": round(float(np.nanmedian(dem[valid])), 0),
    }

# ---- figures --------------------------------------------------------------------------------------------------------
k = max(1, grid.width // 800)
sl = (slice(None, None, k), slice(None, None, k))
fig, ax = plt.subplots(
    2, 2, figsize=(13, 13 * grid.height / max(grid.width, 1) * 0.55 + 2), constrained_layout=True
)
for a, (title, img, cmap, lim) in zip(
    ax.ravel(),
    [
        (f"VV before ({cfg['pre']})", pre["vv"][sl], "gray", (-25, 0)),
        (f"VV after ({cfg['post']})", post["vv"][sl], "gray", (-25, 0)),
        ("VV change (dB)", (post["vv"] - pre["vv"])[sl], "RdBu", (-8, 8)),
        (f"Result: {result['best_method']}", None, None, None),
    ],
    strict=True,
):
    a.set_title(title, fontsize=10)
    a.set_axis_off()
    if img is not None:
        a.imshow(img, cmap=cmap, vmin=lim[0], vmax=lim[1], interpolation="nearest")
    else:
        rgb = np.full(final[sl].shape + (3,), 0.12)
        p, r_ = final[sl], (ref[sl] if ref is not None else np.zeros_like(final[sl]))
        rgb[p & r_] = (0.2, 0.75, 0.3)
        rgb[p & ~r_] = (0.9, 0.25, 0.2)
        rgb[~p & r_] = (0.25, 0.5, 0.95)
        a.imshow(rgb, interpolation="nearest")
        a.text(
            0.01,
            0.01,
            "green = agrees with reference · red = flagged only by us · blue = reference only"
            if ref is not None
            else "red = flagged (no reference exists)",
            transform=a.transAxes,
            color="white",
            fontsize=8,
            va="bottom",
        )
fig.savefig(out_dir / "overview.png", dpi=90)
(out_dir / "results.json").write_text(json.dumps(result, indent=2))
result["runtime_s"] = round(time.time() - t0)

print(json.dumps({k: v for k, v in result.items() if k not in ("scenes",)}, indent=2))
