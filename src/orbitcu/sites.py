"""Smelter sites and their areas of interest (AOIs).

Each site has three nested geometries, all derived from one furnace point:

* ``point``   - the smelting furnace / converter aisle (lat, lon);
* ``thermal`` - a square box of half-side ``thermal_half_km`` (default 1.5 km)
  centred on the point, used for SWIR / thermal pixel extraction
  (Sentinel-2, Landsat 8/9, VIIRS);
* ``so2``     - a disc of radius ``so2_radius_km`` (default 50 km), used for
  Sentinel-5P SO2 retrievals and the ERA5 wind box.

Distances are computed in the local UTM zone so that boxes are metric
everywhere (including Norilsk at 69 N).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pyproj import CRS, Transformer
from shapely.geometry import Point, box, mapping
from shapely.ops import transform

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = REPO_ROOT / "data" / "registry"
SITES_CSV = REGISTRY_DIR / "smelter_sites.csv"

DEFAULT_THERMAL_HALF_KM = 1.5
DEFAULT_SO2_RADIUS_KM = 50.0


def utm_crs(lat: float, lon: float) -> CRS:
    """Return the WGS84 / UTM CRS whose zone contains (lat, lon)."""
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    country: str
    lat: float
    lon: float
    capacity_kt: float | None = None
    operator_check: str | None = None
    thermal_half_km: float = DEFAULT_THERMAL_HALF_KM
    so2_radius_km: float = DEFAULT_SO2_RADIUS_KM

    @property
    def crs(self) -> CRS:
        return utm_crs(self.lat, self.lon)

    def _to_utm(self) -> Transformer:
        return Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    def _to_wgs(self) -> Transformer:
        return Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    def point_utm(self) -> tuple[float, float]:
        return self._to_utm().transform(self.lon, self.lat)

    def thermal_box_utm(self) -> tuple[float, float, float, float]:
        x, y = self.point_utm()
        h = self.thermal_half_km * 1000.0
        return (x - h, y - h, x + h, y + h)

    def thermal_bbox(self) -> tuple[float, float, float, float]:
        """Thermal AOI as a WGS84 (west, south, east, north) bbox."""
        poly = transform(self._to_wgs().transform, box(*self.thermal_box_utm()))
        return tuple(round(v, 6) for v in poly.bounds)  # type: ignore[return-value]

    def so2_polygon(self):
        """SO2 AOI disc as a WGS84 shapely polygon."""
        x, y = self.point_utm()
        disc = Point(x, y).buffer(self.so2_radius_km * 1000.0, quad_segs=32)
        return transform(self._to_wgs().transform, disc)

    def so2_bbox(self) -> tuple[float, float, float, float]:
        return tuple(round(v, 4) for v in self.so2_polygon().bounds)  # type: ignore[return-value]

    def geojson_point(self) -> dict:
        return mapping(Point(self.lon, self.lat))


def load_sites(path: Path | str = SITES_CSV, ids: list[str] | None = None) -> list[Site]:
    """Load geolocated sites (output of :mod:`orbitcu.geolocate`)."""
    df = pd.read_csv(path)
    if ids:
        missing = set(ids) - set(df["id"])
        if missing:
            raise KeyError(f"unknown site ids: {sorted(missing)}")
        df = df[df["id"].isin(ids)]
    sites = []
    for r in df.itertuples(index=False):
        cap = getattr(r, "capacity_kt_cu", None)
        sites.append(
            Site(
                id=r.id,
                name=r.smelter,
                country=r.country,
                lat=float(r.lat),
                lon=float(r.lon),
                capacity_kt=None if pd.isna(cap) else float(cap),
                operator_check=getattr(r, "operator_check", None),
            )
        )
    return sites
