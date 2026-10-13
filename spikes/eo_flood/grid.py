"""A UTM grid over a lon/lat bbox: the common raster frame of the spike (taken from the CDSE composite)."""

import math
from dataclasses import dataclass

import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin


@dataclass(frozen=True)
class Grid:
    """A UTM grid covering a lon/lat bbox."""

    crs: str
    transform: rasterio.Affine
    width: int
    height: int
    res: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            self.transform.c,
            self.transform.f - self.height * self.res,
            self.transform.c + self.width * self.res,
            self.transform.f,
        )


def make_grid(bbox: tuple[float, float, float, float], res: float) -> Grid:
    lon0, lat0, lon1, lat1 = bbox
    zone = int(((lon0 + lon1) / 2 + 180) // 6) + 1
    crs = f"EPSG:{32600 + zone}" if (lat0 + lat1) / 2 >= 0 else f"EPSG:{32700 + zone}"
    t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xs, ys = zip(*(t.transform(lo, la) for lo in (lon0, lon1) for la in (lat0, lat1)), strict=True)
    x0, y1 = math.floor(min(xs) / res) * res, math.ceil(max(ys) / res) * res
    w, h = math.ceil((max(xs) - x0) / res), math.ceil((y1 - min(ys)) / res)
    return Grid(crs, from_origin(x0, y1, res, res), w, h, res)
