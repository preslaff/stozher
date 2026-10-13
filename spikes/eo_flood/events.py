"""The archived floods used in the spike."""

from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "spike" / "eo-flood"

EVENTS = {
    # Thessaly plain, Greece (Copernicus EMS EMSR692, AOI 5 Karditsa). Flat farmland, a large and slow flood.
    # Reference: SPOT-6/7 optical delineation of 2023-09-12 08:54 UTC; Sentinel-1 passed at 16:32 UTC the same day.
    "karditsa": {
        "name": "Thessaly plain flood (EMSR692, Karditsa AOI)",
        "bbox": (21.743, 39.193, 22.039, 39.677),
        "res": 20.0,
        "orbit": 175,
        "pre": "2023-08-31",  # 12 days before: the CDSE archive has it (the Planetary Computer one does not)
        "post": "2023-09-12",
        "reference": DATA / "ems" / "EMSR692_AOI05_DEL_MONIT02_v3",
        "reference_note": "SPOT-6/7 2023-09-12 08:54 UTC, 7.5 h before the Sentinel-1 pass",
    },
    # Tsarevo, Bulgaria (EMSR693): a flash flood in steep river valleys on 2023-09-05. The EMS did NOT produce a
    # delineation ("remote sensing limitations"), so this event has no reference extent.
    "tsarevo": {
        "name": "Tsarevo flash flood, Bulgaria (EMSR693)",
        "bbox": (27.80, 41.98, 28.06, 42.18),
        "res": 10.0,
        "orbit": 131,
        "pre": "2023-08-28",
        "post": "2023-09-09",
        "reference": None,
        "reference_note": "none: Copernicus EMS could not produce a delineation",
    },
}
