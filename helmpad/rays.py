"""Richtungs- und Raycasting-Hilfen."""
import numpy as np
import trimesh


def dirs_from_az_el(az_deg, el_deg):
    """Azimut 0° = vorne (+Y), 90° = rechts (+X); Elevation 90° = oben."""
    az = np.radians(az_deg)
    el = np.radians(el_deg)
    return np.stack([np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)], axis=-1)


def az_el_from_dirs(d):
    d = np.asarray(d, float)
    az = np.degrees(np.arctan2(d[..., 0], d[..., 1]))
    el = np.degrees(np.arcsin(np.clip(d[..., 2], -1.0, 1.0)))
    return az, el


def fibonacci_sphere(n):
    i = np.arange(n) + 0.5
    z = 1.0 - 2.0 * i / n
    phi = np.pi * (1.0 + 5 ** 0.5) * i
    r = np.sqrt(1.0 - z * z)
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)


def first_hits(mesh: trimesh.Trimesh, origin, dirs):
    """Abstand vom Ursprung zum ersten Treffer je Richtung (NaN = kein Treffer)."""
    dirs = np.asarray(dirs, float)
    shape = dirs.shape[:-1]
    dirs = dirs.reshape(-1, 3)
    origins = np.repeat(np.asarray(origin, float).reshape(1, 3), len(dirs), axis=0)
    locs, idx_ray, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    dist = np.full(len(dirs), np.nan)
    if len(idx_ray):
        dist[idx_ray] = np.einsum("ij,ij->i", locs - origins[idx_ray], dirs[idx_ray])
    return dist.reshape(shape)


class RadialTable:
    """Radius eines sternförmigen Körpers (Kopf) je Richtung, als Az/El-Tabelle zum schnellen Nachschlagen."""

    def __init__(self, mesh, origin=(0, 0, 0), step_deg=1.5):
        from scipy.interpolate import RegularGridInterpolator
        from scipy.ndimage import distance_transform_edt

        self.az = np.arange(-180.0, 180.0 + 1e-9, step_deg)
        self.el = np.arange(-90.0, 90.0 + 1e-9, step_deg)
        AZ, EL = np.meshgrid(self.az, self.el)
        r = first_hits(mesh, origin, dirs_from_az_el(AZ, EL))
        missing = np.isnan(r)
        if missing.any():
            _, (ii, jj) = distance_transform_edt(missing, return_indices=True)
            r = r[ii, jj]
        self._interp = RegularGridInterpolator((self.el, self.az), r)

    def __call__(self, dirs):
        az, el = az_el_from_dirs(dirs)
        return self._interp(np.stack([el, az], axis=-1))
