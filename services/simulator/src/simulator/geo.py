from pyproj import Geod

GEOD = Geod(ellps="WGS84")


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return float(GEOD.inv(lon1, lat1, lon2, lat2)[2])


def offset(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    lon2, lat2, _ = GEOD.fwd(lon, lat, bearing_deg, dist_m)
    return float(lat2), float(lon2)


def interpolate(track: list[tuple[float, float, float]], t: float) -> tuple[float, float]:
    """Position on a (t, lat, lon) track; clamps outside its time range."""
    if t <= track[0][0]:
        return track[0][1], track[0][2]
    for (t0, la0, lo0), (t1, la1, lo1) in zip(track, track[1:], strict=False):
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return la0 + f * (la1 - la0), lo0 + f * (lo1 - lo0)
    return track[-1][1], track[-1][2]
