# /// script
# requires-python = ">=3.12"
# dependencies = ["openeo"]
# ///
"""Build pre/post Sentinel-1 backscatter composites for an event on CDSE, server-side, with openEO.

CDSE runs the heavy work: SAR backscatter processing with the Copernicus 30 m DEM, one same-orbit
acquisition before and one after the event, dB conversion, and a DEM band aligned to the same grid.
Only the small result (5 bands: VV/VH before, VV/VH after, elevation) is downloaded.

Sign-in: a refresh token saved by an earlier run is used silently; otherwise the device-code flow
prints a URL to open in a browser (you log in with your own CDSE account; no secret is handled here).
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import openeo

sys.path.insert(0, str(Path(__file__).parent))
import events as ev


def connect() -> openeo.Connection:
    c = openeo.connect("openeo.dataspace.copernicus.eu")
    try:
        c.authenticate_oidc_refresh_token()
        print("signed in with the saved refresh token", flush=True)
    except Exception:
        c.authenticate_oidc_device(store_refresh_token=True, max_poll_time=900)
        print("signed in (refresh token saved for later runs)", flush=True)
    return c


def s1_pass(c: openeo.Connection, extent: dict, day: str):
    """VV and VH sigma0 in dB for the acquisition(s) of one day (frames of a pass are averaged)."""
    d0 = date.fromisoformat(day)
    cube = c.load_collection(
        "SENTINEL1_GRD",
        spatial_extent=extent,
        temporal_extent=[d0.isoformat(), (d0 + timedelta(days=1)).isoformat()],
        bands=["VV", "VH"],
    )
    cube = cube.sar_backscatter(
        coefficient="sigma0-ellipsoid",
        elevation_model="COPERNICUS_30",
        noise_removal=True,
        mask=False,
        contributing_area=False,
        local_incidence_angle=False,
        ellipsoid_incidence_angle=False,
    )
    cube = cube.reduce_dimension(dimension="t", reducer="mean")  # mean in linear power, then dB
    return cube.apply(lambda x: 10 * x.log(base=10))


def build(c: openeo.Connection, name: str):
    cfg = ev.EVENTS[name]
    w, s, e, n = cfg["bbox"]
    extent = {"west": w, "south": s, "east": e, "north": n, "crs": "EPSG:4326"}
    epsg = (
        32600 + int(((w + e) / 2 + 180) // 6) + 1
    )  # the UTM zone of the area's centre (northern hemisphere)
    pre = s1_pass(c, extent, cfg["pre"]).rename_labels("bands", ["VV_pre", "VH_pre"])
    post = s1_pass(c, extent, cfg["post"]).rename_labels("bands", ["VV_post", "VH_post"])
    stack = pre.merge_cubes(post).resample_spatial(
        resolution=cfg["res"], projection=epsg, method="bilinear"
    )
    dem = c.load_collection("COPERNICUS_30", spatial_extent=extent, bands=["DEM"]).max_time()
    dem = dem.resample_cube_spatial(stack, method="bilinear")
    return stack.merge_cubes(dem)


if __name__ == "__main__":
    name = sys.argv[1]
    out = ev.DATA / name / "cdse"
    out.mkdir(parents=True, exist_ok=True)
    c = connect()
    cube = build(c, name)
    t0 = time.time()
    job = cube.create_job(title=f"stozher spike {name}", out_format="GTiff")
    print("job", job.job_id, flush=True)
    job.start_and_wait(
        print=lambda *a: print(*a, flush=True), max_poll_interval=15, connection_retry_interval=15
    )
    info = job.describe()
    print(
        f"finished in {time.time() - t0:.0f} s; status {info.get('status')}; costs {info.get('costs')}; usage {info.get('usage')}",
        flush=True,
    )
    for f in job.get_results().download_files(out):
        print("downloaded", f, f"{Path(f).stat().st_size / 1e6:.1f} MB", flush=True)
