# /// script
# requires-python = ">=3.12"
# dependencies = ["openeo"]
# ///
"""Re-download the result of a finished CDSE openEO job (no recomputation, no extra credits), with retries."""

import sys
import time
from pathlib import Path

import openeo

sys.path.insert(0, str(Path(__file__).parent))
import events as ev

name, job_id = sys.argv[1], sys.argv[2]
c = openeo.connect("openeo.dataspace.copernicus.eu")
c.authenticate_oidc_refresh_token()
out = ev.DATA / name / "cdse"
out.mkdir(parents=True, exist_ok=True)
for attempt in range(1, 6):
    try:
        for f in c.job(job_id).get_results().download_files(out):
            print("downloaded", Path(f).name, f"{Path(f).stat().st_size / 1e6:.1f} MB", flush=True)
        break
    except Exception as exc:
        print(f"attempt {attempt} failed: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        time.sleep(5 * attempt)
else:
    sys.exit("download failed")
