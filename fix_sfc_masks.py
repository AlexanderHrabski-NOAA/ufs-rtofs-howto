#!/usr/bin/env python3
"""
Reconcile FV3 warm-start surface restarts (sfc_data.tileN.nc) with a new land/sea mask.

The 8 km coupled ICs pair surface restarts from a 25 km ocean run, whose land/sea mask
comes from C384.mx025_oro_data, with the C384.mx008_oro_data the 8 km run reads. The
finer ocean moves the coastline, so some C384 cells gain land, or gain a water part,
that the restarts never computed. global_cycle aborts on the first cell with
land_frac == 0 but slmsk == 1 (UFS_UTILS global_cycle.fd/cycle.F90:587).

For every cell (see sfc_mask_method.md for the reasoning):

  * gains land  (25 km land_frac == 0, mx008 land_frac > 0): read the cell's vegetation
    and soil type from the mx008 surface climatology; find the nearest cell that had
    land in the 25 km run with the same two types (great-circle distance, all six
    tiles); copy every land-state field from it; set the climatological fields from
    the mx008 files, with monthly fields interpolated to the restart date as
    chgres_cube does; shift soil temperature by chgres_cube's lapse rate where the
    terrain differs by more than 100 m.
  * gains water (25 km land_frac == 1, mx008 land_frac < 1): copy every water and
    sea-ice field from the nearest cell that had water in the 25 km run. When the
    orography files carry lake_frac, lakes take lake donors and ocean takes ocean.
  * gains nothing: left untouched.

The composites (snwdph, sheleg, zorl, zorlwav, tsfc) are then recomputed with the
model's fractional-grid formulas, and slmsk is set from land_frac. Cells, and sides
of cells, that gain nothing stay bit-for-bit identical.

Subcommands:
  report  print what apply would do: cell counts and donor distances. Writes nothing.
  apply   fix every surface restart set under an IC tree, in place.
  check   report cells where surface restarts disagree with an orography.

The atmosphere grids of both orography directories must be identical, and a cell with
no donor within --max-distance-km is an error.
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime

try:
    import numpy as np
    from netCDF4 import Dataset
except ImportError as exc:  # pragma: no cover
    sys.exit(f"error: needs numpy and netCDF4 ({exc}).\n"
             "       In a global-workflow shell: source global-workflow/dev/ush/load_modules.sh")

NTILES = 6
HORIZ = ("yaxis_1", "xaxis_1")
EARTH_RADIUS_KM = 6371.2      # physcons con_rerth
LAPSE_RATE = 6.5e-3           # K/m, chgres_cube surface.F90 adjust_soilt_for_terrain
TERRAIN_DIFF_MIN = 100.0      # m, the same routine's threshold
VEG_TYPE_LANDICE = 15         # IGBP permanent snow and ice (chgres_cube surface.F90)
LANDICE_TMAX = 273.16         # K, soil temperature cap under land ice
CON_TICE = 271.2              # K, physcons con_tice
FIX_ATTR = "sfc_mask_fix"     # global attribute marking a fixed file
REPORT_BINS_KM = (30, 60, 120, 250, 500)

# Every gridded sfc_data field belongs to exactly one family. apply refuses to touch a
# file holding a field that isn't listed, rather than guess which side it belongs to.
LAND = {
    # Noah and shared land state
    "stc", "smc", "slc", "tsfcl", "canopy", "weasdl", "snodl", "zorll", "sncovr", "lai",
    "scolor", "albdirvis_lnd", "albdirnir_lnd", "albdifvis_lnd", "albdifnir_lnd",
    "emis_lnd", "sfalb_lnd", "sfalb_lnd_bck", "tsnow_land", "snowfall_acc_land",
    "qwv_surf_land", "clw_surf_land",
    # Noah-MP
    "snowxy", "snicexy", "snliqxy", "tsnoxy", "zsnsoxy", "smoiseq", "tvxy", "tgxy",
    "tahxy", "eahxy", "canliqxy", "canicexy", "fwetxy", "sneqvoxy", "alboldxy", "qsnowxy",
    "wslakexy", "taussxy", "waxy", "wtxy", "zwtxy", "smcwtdxy", "deeprechxy", "rechxy",
    "lfmassxy", "rtmassxy", "stmassxy", "woodxy", "stblcpxy", "fastcpxy", "xlaixy",
    "xsaixy", "cmxy", "chxy",
    # RUC
    "wetness", "tslb", "smois", "sh2o", "smfr", "flfr",
}
WATER = {
    # open water and NSST
    "tsea", "zorlw", "tref", "xt", "xs", "xu", "xv", "xz", "zm", "xtts", "xzts",
    "dt_cool", "z_c", "c_0", "c_d", "w_0", "w_d", "d_conv", "ifd", "qrain",
    # sea ice
    "fice", "hice", "tisfc", "tiice", "weasdi", "snodi", "zorli", "sncovr_ice",
    "albdirvis_ice", "albdirnir_ice", "albdifvis_ice", "albdifnir_ice", "emis_ice",
    "sfalb_ice", "tsnow_ice", "snowfall_acc_ice", "qwv_surf_ice", "clw_surf_ice",
    # FLake
    "t_bot1", "t_bot2", "t_mnw", "t_talb", "h_talb", "c_t",
}
CLIMO = {"vtype", "stype", "slope", "tg3", "snoalb", "facsf", "facwf",
         "alvsf", "alvwf", "alnsf", "alnwf", "vfrac", "shdmin", "shdmax"}
COMPOSITES = ("snwdph", "sheleg", "zorl", "zorlwav", "tsfc")     # computed in this order
DIAGNOSTIC = {"t2m", "q2m", "f10m", "uustar", "ffmm", "ffhh", "tprcp", "srflag"}
MASK = {"slmsk"}
KNOWN = LAND | WATER | CLIMO | set(COMPOSITES) | DIAGNOSTIC | MASK

# Restart field <- (fix file, variable in it, how to reduce its time records).
# Mirrors chgres_cube static_data.F90; facwf is derived from facsf below.
CLIMO_SOURCES = {
    "vtype":  ("vegetation_type", "vegetation_type", "date"),
    "stype":  ("soil_type", "soil_type", "date"),
    "slope":  ("slope_type", "slope_type", "date"),
    "tg3":    ("substrate_temperature", "substrate_temperature", "date"),
    "snoalb": ("maximum_snow_albedo", "maximum_snow_albedo", "date"),
    "facsf":  ("facsf", "facsf", "date"),
    "alvsf":  ("snowfree_albedo", "visible_black_sky_albedo", "date"),
    "alvwf":  ("snowfree_albedo", "visible_white_sky_albedo", "date"),
    "alnsf":  ("snowfree_albedo", "near_IR_black_sky_albedo", "date"),
    "alnwf":  ("snowfree_albedo", "near_IR_white_sky_albedo", "date"),
    "vfrac":  ("vegetation_greenness", "vegetation_greenness", "date"),
    "shdmax": ("vegetation_greenness", "vegetation_greenness", "max"),
    "shdmin": ("vegetation_greenness", "vegetation_greenness", "min"),
}


class FixError(Exception):
    """A problem that stops apply before any restart is replaced."""


# --------------------------------------------------------------------------- input

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Reconcile FV3 surface restarts with a new land/sea mask.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  %(prog)s report ICs/coupled-8km-ics fix/orog/20240917/C384 FIX_hr/OROG/C384\n"
               "  %(prog)s apply  ICs/coupled-8km-ics fix/orog/20240917/C384 FIX_hr/OROG/C384 \\\n"
               "      --max-distance-km 250\n"
               "  %(prog)s check  ICs/coupled-8km-ics FIX_hr/OROG/C384\n")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, both=True):
        sp.add_argument("ic_dir", help="IC tree; every *.sfc_data.tile1.nc under it is processed")
        if both:
            sp.add_argument("orog_dir_input",
                            help="orog/<CASE> directory the restarts were made with: "
                                 "<CASE>.mx<OCNRES_INPUT>_oro_data.tile*.nc and <CASE>_grid.tile*.nc")
        sp.add_argument("orog_dir_target",
                        help="orog/<CASE> directory the run uses: <CASE>.mx<OCNRES_TARGET>_oro_data.tile*.nc, "
                             "<CASE>_grid.tile*.nc and the surface climatology in sfc/")
        sp.add_argument("--case", default="C384", help="atmosphere resolution (default: C384)")
        sp.add_argument("--ocnres-target", default="008",
                        help="ocean mask of the run (default: 008)")
        if both:
            sp.add_argument("--ocnres-input", default="025",
                            help="ocean mask of the restarts (default: 025)")

    r = sub.add_parser("report", help="print what apply would do; writes no restarts")
    common(r)
    r.add_argument("--cells-csv", metavar="PATH",
                   help="also write one row per cell that gains land or water, for plotting")
    a = sub.add_parser("apply", help="fix the surface restarts in place")
    common(a)
    a.add_argument("--max-distance-km", type=float, required=True,
                   help="largest allowed donor distance; pick it from the report")
    c = sub.add_parser("check", help="report mask disagreements; exit 1 on a global_cycle abort")
    common(c, both=False)
    return p.parse_args(argv)


def read_tiles(path_fmt, var):
    """One 2D variable from six tile files, as an array of shape (6, ny, nx)."""
    out = []
    for t in range(1, NTILES + 1):
        path = path_fmt.format(t=t)
        if not os.path.exists(path):
            raise FixError(f"missing {path}")
        with Dataset(path) as ds:
            if var not in ds.variables:
                raise FixError(f"{path} has no variable '{var}'")
            ds.set_auto_maskandscale(False)
            out.append(np.asarray(ds.variables[var][:], dtype="f8"))
    return np.stack(out)


def grid_centres(orog_dir, case):
    """Cell-centre lon/lat (degrees) from the supergrid files, shape (6, ny, nx) each."""
    fmt = os.path.join(orog_dir, f"{case}_grid.tile{{t}}.nc")
    lon = read_tiles(fmt, "x")[:, 1::2, 1::2]
    lat = read_tiles(fmt, "y")[:, 1::2, 1::2]
    return lon, lat


def unit_vectors(lon, lat):
    lam, phi = np.radians(lon.ravel()), np.radians(lat.ravel())
    return np.column_stack((np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)))


def optional_tiles(path_fmt, var):
    """Like read_tiles, but None when any tile lacks the variable."""
    for t in range(1, NTILES + 1):
        with Dataset(path_fmt.format(t=t)) as ds:
            if var not in ds.variables:
                return None
    return read_tiles(path_fmt, var)


class Orography:
    """Land fraction, terrain and (when present) lake fraction for one ocean mask."""

    def __init__(self, orog_dir, case, ocnres):
        self.name = f"{case}.mx{ocnres}_oro_data"
        fmt = os.path.join(orog_dir, self.name + ".tile{t}.nc")
        self.fmt = fmt
        self.land_frac = read_tiles(fmt, "land_frac")
        self.terrain = read_tiles(fmt, "orog_raw")
        self.lake_frac = optional_tiles(fmt, "lake_frac")


def climatology_date_weights(times, when):
    """chgres_cube's interpolation between monthly records (static_data.F90 read_static_file).

    times are days since Jan 1 of the climatology year. Returns (rec1, rec2, w1, w2).
    """
    day = min(when.day, 28) if when.month == 2 else when.day        # 2015 has no Feb 29
    rinc = (datetime(2015, when.month, day, when.hour) - datetime(2015, 1, 1)).total_seconds() / 86400.0
    n = len(times)
    k = next((i for i, t in enumerate(times) if rinc <= t), n)       # first record not before the date
    b2 = 0 if k == n else k
    b1 = n - 1 if k == 0 else k - 1
    if b2 != 0:
        w2 = (rinc - times[b1]) / (times[b2] - times[b1])
    else:                                                            # wrap across the year end
        span = (times[b2] + 1.0) + (365.0 - times[b1] - 1.0)
        into = rinc - times[b1] if rinc >= times[b1] else (365.0 - times[b1]) + rinc
        w2 = into / span
    return b1, b2, 1.0 - w2, w2


class Climatology:
    """The target grid's surface climatology, read lazily and reduced to one date."""

    def __init__(self, orog_dir, case, ocnres):
        self.dir = os.path.join(orog_dir, "sfc")
        self.prefix = f"{case}.mx{ocnres}"
        self._raw = {}

    def path(self, fixfile, t):
        return os.path.join(self.dir, f"{self.prefix}.{fixfile}.tile{t}.nc")

    def preflight(self):
        missing = [self.path(f, t) for f in sorted({s[0] for s in CLIMO_SOURCES.values()})
                   for t in range(1, NTILES + 1) if not os.path.exists(self.path(f, t))]
        if missing:
            raise FixError("missing surface climatology:\n  " + "\n  ".join(missing))

    def raw(self, fixfile, var):
        """(records, (6, ntime, ny, nx)) for one variable."""
        key = (fixfile, var)
        if key not in self._raw:
            data, times = [], None
            for t in range(1, NTILES + 1):
                with Dataset(self.path(fixfile, t)) as ds:
                    ds.set_auto_maskandscale(False)
                    if var not in ds.variables:
                        raise FixError(f"{self.path(fixfile, t)} has no variable '{var}'")
                    a = np.asarray(ds.variables[var][:], dtype="f8")
                    data.append(a if a.ndim == 3 else a[None])
                    if times is None and "time" in ds.variables and data[-1].shape[0] > 1:
                        times = np.asarray(ds.variables["time"][:], dtype="f8")
            self._raw[key] = (times, np.stack(data))
        return self._raw[key]

    def field(self, name, when):
        """One restart field on all tiles, shape (6, ny, nx), for the date `when`."""
        if name == "facwf":
            facsf = self.field("facsf", when)
            return np.where(facsf >= 0.0, 1.0 - facsf, facsf)
        fixfile, var, how = CLIMO_SOURCES[name]
        times, data = self.raw(fixfile, var)
        if how == "max":
            return data.max(axis=1)
        if how == "min":
            return data.min(axis=1)
        if data.shape[1] == 1:
            return data[:, 0]
        if times is None:
            raise FixError(f"{fixfile} has {data.shape[1]} records but no 'time' variable")
        b1, b2, w1, w2 = climatology_date_weights(list(times), when)
        return w1 * data[:, b1] + w2 * data[:, b2]


# --------------------------------------------------------------------------- restarts

PREFIX_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.(\d{2})(\d{2})(\d{2})$")


def find_restart_sets(ic_dir):
    """[(directory, prefix, valid time)] for every *.sfc_data.tile1.nc under ic_dir."""
    sets = []
    for dirpath, dirnames, filenames in os.walk(ic_dir, followlinks=False):
        dirnames.sort()
        for f in sorted(filenames):
            if f.endswith(".sfc_data.tile1.nc"):
                prefix = f[: -len(".sfc_data.tile1.nc")]
                m = PREFIX_RE.match(prefix)
                if not m:
                    raise FixError(f"cannot read a date from {os.path.join(dirpath, f)}; "
                                   "expected YYYYMMDD.HHMMSS.sfc_data.tile1.nc")
                sets.append((dirpath, prefix, datetime(*map(int, m.groups()))))
    if not sets:
        raise FixError(f"no *.sfc_data.tile1.nc under {ic_dir}")
    return sets


def tile_paths(directory, prefix):
    return [os.path.join(directory, f"{prefix}.sfc_data.tile{t}.nc") for t in range(1, NTILES + 1)]


def stack_var(dss, name):
    """(6, *lead, ny, nx) -> (*lead, 6*ny*nx), tile-major like the flat cell index."""
    a = np.stack([np.asarray(ds.variables[name][:]) for ds in dss])
    a = np.moveaxis(a, 0, -3)
    return a.reshape(a.shape[:-3] + (-1,))


def unstack(flat, shape):
    """Inverse of stack_var for a (6, ny, nx) tile shape."""
    a = flat.reshape(flat.shape[:-1] + shape)
    return np.moveaxis(a, -3, 0)


def field_2d(dss, name):
    """A 2D field (dropping the Time axis) on all tiles, flattened."""
    return stack_var(dss, name).reshape(-1)


def mask_problems(slmsk, stype, land_frac):
    """Boolean cell masks (flat) where a restart disagrees with land_frac."""
    land = land_frac > 0
    s, st = np.rint(slmsk), np.rint(stype)
    return {
        "abort": ~land & (s == 1),                         # global_cycle stops here
        "slmsk": land & (s != 1),
        "stype": land & ((st <= 0) | (st == 14)),          # the model drops this land
    }


PROBLEM_TEXT = {
    "abort": "land_frac == 0, slmsk == 1 (global_cycle aborts)",
    "slmsk": "land_frac > 0, slmsk != 1",
    "stype": "land_frac > 0, stype water or missing (model drops the land)",
}


# --------------------------------------------------------------------------- donors

def nearest(xyz, targets, donors):
    """Nearest donor (flat index) and great-circle distance (km) for each target."""
    if targets.size == 0:
        return np.empty(0, dtype=np.intp), np.empty(0)
    if donors.size == 0:
        return np.full(targets.size, -1, dtype=np.intp), np.full(targets.size, np.inf)
    d = xyz[donors]
    chunk = max(1, int(2.0e7 // donors.size))                       # bounds memory to ~160 MB
    pick = np.empty(targets.size, dtype=np.intp)
    dist = np.empty(targets.size)
    for s in range(0, targets.size, chunk):
        dots = xyz[targets[s:s + chunk]] @ d.T
        k = dots.argmax(axis=1)
        pick[s:s + chunk] = donors[k]
        cosang = np.clip(dots[np.arange(k.size), k], -1.0, 1.0)
        dist[s:s + chunk] = np.arccos(cosang) * EARTH_RADIUS_KM
    return pick, dist


def find_donors(xyz, targets, target_class, donor_pool, donor_class):
    """Nearest donor of the same class for each target; classes are integer keys."""
    pick = np.full(targets.size, -1, dtype=np.intp)
    dist = np.full(targets.size, np.inf)
    for c in np.unique(target_class):
        sel = target_class == c
        pick[sel], dist[sel] = nearest(xyz, targets[sel], np.flatnonzero(donor_pool & (donor_class == c)))
    return pick, dist


class Plan:
    """Which cells gain what, and where each one's values come from."""

    def __init__(self, ctx, dss, when):
        self.ctx = ctx
        self.when = when
        old, new = ctx.old.land_frac.ravel(), ctx.new.land_frac.ravel()
        self.gains_land = np.flatnonzero((old <= 0.0) & (new > 0.0))
        self.gains_water = np.flatnonzero((old >= 1.0) & (new < 1.0))
        # Part land -> no land: nothing to copy, but slmsk must drop to 0 or 2.
        self.loses_land = np.flatnonzero((old > 0.0) & (old < 1.0) & (new <= 0.0))
        # Every cell whose land fraction moved needs its composites and slmsk redone.
        self.refresh = np.flatnonzero(old != new)

        vtype, stype = np.rint(field_2d(dss, "vtype")), np.rint(field_2d(dss, "stype"))

        # Land: type-matched to the target climatology.
        self.climo = {}
        if self.gains_land.size:
            for name in sorted(CLIMO):
                self.climo[name] = ctx.climo.field(name, when).ravel()
            cv = np.rint(self.climo["vtype"][self.gains_land])
            cs = np.rint(self.climo["stype"][self.gains_land])
            bad = (cv <= 0) | (cs <= 0) | (cs == 14)
            for name in sorted(CLIMO):
                bad |= self.climo[name][self.gains_land] <= -999.0
            if bad.any():
                cells = ", ".join(ctx.where(g) for g in self.gains_land[bad][:5])
                raise FixError(f"the {ctx.climo.prefix} climatology has no land values at "
                               f"{int(bad.sum())} cells that gain land (e.g. {cells}). It may "
                               "predate fractional land support in UFS_UTILS.")
            pool = (old > 0.0) & (stype > 0) & (stype != 14)
            key = (vtype * 100 + stype).astype(np.int64)
            tkey = (cv * 100 + cs).astype(np.int64)
            self.land_class = tkey
            self.land_src, self.land_dist = find_donors(ctx.xyz, self.gains_land, tkey, pool, key)
            # Looser matches, for the report only.
            self.land_soil_dist = find_donors(ctx.xyz, self.gains_land, cs.astype(np.int64), pool,
                                              stype.astype(np.int64))[1]
            self.land_any_dist = nearest(ctx.xyz, self.gains_land, np.flatnonzero(pool))[1]
        else:
            self.land_class = np.empty(0, dtype=np.int64)
            self.land_src = np.empty(0, dtype=np.intp)
            self.land_dist = self.land_soil_dist = self.land_any_dist = np.empty(0)

        # Water: lake from lake, ocean from ocean, when the orography distinguishes them.
        pool = old < 1.0
        if ctx.old.lake_frac is not None and ctx.new.lake_frac is not None:
            dclass = (ctx.old.lake_frac.ravel() > 0).astype(np.int64)
            tclass = (ctx.new.lake_frac.ravel()[self.gains_water] > 0).astype(np.int64)
        else:
            dclass = np.zeros(old.size, dtype=np.int64)
            tclass = np.zeros(self.gains_water.size, dtype=np.int64)
        self.water_class = tclass
        self.water_src, self.water_dist = find_donors(ctx.xyz, self.gains_water, tclass, pool, dclass)

    def too_far(self, max_km):
        """(description, flat cell, distance) for every cell without a donor in reach."""
        out = []
        for cells, dist, kind in ((self.gains_land, self.land_dist, "land"),
                                  (self.gains_water, self.water_dist, "water")):
            for g, d in zip(cells[dist > max_km], dist[dist > max_km]):
                out.append((kind, g, d))
        return out


# --------------------------------------------------------------------------- context

class Context:
    def __init__(self, args, need_input=True):
        self.case = args.case
        target_dir = args.orog_dir_target
        lon, lat = grid_centres(target_dir, args.case)
        self.lon, self.lat = lon, lat
        self.shape = lon.shape
        self.xyz = unit_vectors(lon, lat)
        self.new = Orography(target_dir, args.case, args.ocnres_target)
        self._check_shape(self.new.land_frac, self.new.name)
        if need_input:
            ilon, ilat = grid_centres(args.orog_dir_input, args.case)
            dlon = np.abs((ilon - lon + 180.0) % 360.0 - 180.0).max()
            dlat = np.abs(ilat - lat).max()
            if max(dlon, dlat) > 1.0e-6:
                raise FixError(f"the {args.case} grids in {args.orog_dir_input} and {target_dir} differ "
                               f"(max |dlon| {dlon:.3g}, |dlat| {dlat:.3g} deg). This method copies "
                               "cell for cell and needs identical grids.")
            self.old = Orography(args.orog_dir_input, args.case, args.ocnres_input)
            self._check_shape(self.old.land_frac, self.old.name)
            self.climo = Climatology(target_dir, args.case, args.ocnres_target)
            self.climo.preflight()

    def _check_shape(self, a, what):
        if a.shape != self.shape:
            raise FixError(f"{what} is {a.shape[1:]} per tile but the grid is {self.shape[1:]}")

    def where(self, g):
        t, j, i = np.unravel_index(g, self.shape)
        return (f"tile {t + 1} i={i + 1} j={j + 1} "
                f"({self.lat.ravel()[g]:.2f}N, {self.lon.ravel()[g] % 360:.2f}E)")


def open_set(paths, mode="r"):
    dss = []
    try:
        for p in paths:
            ds = Dataset(p, mode)
            ds.set_auto_maskandscale(False)
            dss.append(ds)
    except Exception:
        for ds in dss:
            ds.close()
        raise
    return dss


def check_set(dss, ctx):
    if dss[0].variables["slmsk"].shape[-2:] != ctx.shape[1:]:
        raise FixError("surface restart and orography have different tile sizes")


def unknown_fields(dss):
    return sorted({n for ds in dss for n, v in ds.variables.items()
                   if v.dimensions[-2:] == HORIZ and n not in KNOWN})


# --------------------------------------------------------------------------- report

def histogram(dist):
    finite = dist[np.isfinite(dist)]
    parts = [f"<= {b} km: {int((finite <= b).sum())}" for b in REPORT_BINS_KM]
    parts.append(f"none: {int((~np.isfinite(dist)).sum())}")
    worst = f"{finite.max():.0f} km" if finite.size else "-"
    return ", ".join(parts), worst


def report(args):
    ctx = Context(args)
    rows = []
    for directory, prefix, when in find_restart_sets(args.ic_dir):
        rel = os.path.relpath(os.path.join(directory, prefix), args.ic_dir)
        dss = open_set(tile_paths(directory, prefix))
        try:
            check_set(dss, ctx)
            print(f"== {rel}  (valid {when:%Y-%m-%d %H:%M})")
            if FIX_ATTR in dss[0].ncattrs():
                print(f"   already fixed: {dss[0].getncattr(FIX_ATTR)}")
            unknown = unknown_fields(dss)
            if unknown:
                print(f"   apply would refuse: fields with no known family: {' '.join(unknown)}")
            plan = Plan(ctx, dss, when)
            slmsk, stype = field_2d(dss, "slmsk"), field_2d(dss, "stype")
        finally:
            for ds in dss:
                ds.close()

        for name, lf in ((ctx.old.name, ctx.old.land_frac), (ctx.new.name, ctx.new.land_frac)):
            found = mask_problems(slmsk, stype, lf.ravel())
            print(f"   restart vs {name}: " + ", ".join(
                f"{int(found[k].sum())} {PROBLEM_TEXT[k]}" for k in found))
        per_tile = lambda cells: " ".join(
            str(int((cells // (ctx.shape[1] * ctx.shape[2]) == t).sum())) for t in range(NTILES))
        others = plan.refresh.size - plan.gains_land.size - plan.gains_water.size - plan.loses_land.size
        print(f"   cells that gain land : {plan.gains_land.size:6d}   per tile: {per_tile(plan.gains_land)}")
        print(f"   cells that gain water: {plan.gains_water.size:6d}   per tile: {per_tile(plan.gains_water)}")
        print(f"   cells that lose all land (part land -> 0): {plan.loses_land.size}; "
              f"other land_frac changes: {others}")
        if plan.gains_land.size:
            h, worst = histogram(plan.land_dist)
            print(f"   land donor, same vegetation and soil type: {h}; farthest {worst}")
            h, worst = histogram(plan.land_soil_dist)
            print(f"   land donor, same soil type only          : {h}; farthest {worst}")
            h, worst = histogram(plan.land_any_dist)
            print(f"   land donor, any type                     : {h}; farthest {worst}")
            classes, counts = np.unique(plan.land_class, return_counts=True)
            print(f"   {classes.size} vegetation/soil classes among new land (largest first): " + ", ".join(
                f"veg {c // 100} soil {c % 100}: {n}"
                for c, n in sorted(zip(classes, counts), key=lambda x: -x[1])[:15]) + ", ...")
            print("   farthest new-land cells (typed / soil only / any type):")
            order = np.argsort(-np.where(np.isfinite(plan.land_dist), plan.land_dist, 1e9))[:10]
            for k in order:
                c = plan.land_class[k]
                dists = " / ".join("none" if not np.isfinite(d) else f"{d:.0f}"
                                   for d in (plan.land_dist[k], plan.land_soil_dist[k], plan.land_any_dist[k]))
                print(f"     {ctx.where(plan.gains_land[k])}  veg {c // 100} soil {c % 100}: {dists} km")
        if plan.gains_water.size:
            h, worst = histogram(plan.water_dist)
            lakes = int(plan.water_class.sum())
            print(f"   water donor ({lakes} lake cells): {h}; farthest {worst}")
        if args.cells_csv:
            rows += cell_rows(rel, ctx, plan)
    if args.cells_csv:
        with open(args.cells_csv, "w") as f:
            f.write("restart,gains,tile,i,j,lat,lon,veg,soil,km_typed,km_soil_only,km_any\n")
            f.writelines(rows)
        print(f"wrote {len(rows)} rows to {args.cells_csv}")
    return 0


def cell_rows(rel, ctx, plan):
    """CSV rows, one per cell that gains land or water."""
    lat, lon = ctx.lat.ravel(), ctx.lon.ravel() % 360.0
    fmt = lambda d: "" if not np.isfinite(d) else f"{d:.1f}"
    out = []
    for k, g in enumerate(plan.gains_land):
        t, j, i = np.unravel_index(g, ctx.shape)
        c = plan.land_class[k]
        out.append(f"{rel},land,{t + 1},{i + 1},{j + 1},{lat[g]:.4f},{lon[g]:.4f},{c // 100},{c % 100},"
                   f"{fmt(plan.land_dist[k])},{fmt(plan.land_soil_dist[k])},{fmt(plan.land_any_dist[k])}\n")
    for k, g in enumerate(plan.gains_water):
        t, j, i = np.unravel_index(g, ctx.shape)
        d = fmt(plan.water_dist[k])
        out.append(f"{rel},water,{t + 1},{i + 1},{j + 1},{lat[g]:.4f},{lon[g]:.4f},,,{d},{d},{d}\n")
    return out


# --------------------------------------------------------------------------- apply

def copy_from(dss, names, targets, sources, shape, touched):
    """Copy each listed field from source cells to target cells across all tiles."""
    done = []
    for name in names:
        if name not in dss[0].variables or targets.size == 0:
            continue
        a = stack_var(dss, name)
        a[..., targets] = a[..., sources]
        write_back(dss, name, a, shape, touched)
        done.append(name)
    return done


def write_back(dss, name, flat, shape, touched):
    tiles = unstack(flat, shape)
    for t in touched:
        dss[t].variables[name][:] = tiles[t]


def apply_set(directory, prefix, when, ctx, max_km):
    paths = tile_paths(directory, prefix)
    with Dataset(paths[0]) as ds:
        if FIX_ATTR in ds.ncattrs():
            print(f"   already fixed ({ds.getncattr(FIX_ATTR)}); skipping.")
            return False

    dss = open_set(paths)
    try:
        check_set(dss, ctx)
        unknown = unknown_fields(dss)
        if unknown:
            raise FixError("fields with no known family (refusing to guess which side they "
                           f"belong to): {' '.join(unknown)}")
        plan = Plan(ctx, dss, when)
    finally:
        for ds in dss:
            ds.close()

    far = plan.too_far(max_km)
    if far:
        lines = [f"{kind:5s} {ctx.where(g)}: " + (f"{d:.0f} km" if np.isfinite(d) else "no donor of this type")
                 for kind, g, d in far[:10]]
        raise FixError(f"{len(far)} cells have no donor within {max_km:g} km:\n  " + "\n  ".join(lines))

    refresh = plan.refresh
    print(f"   {plan.gains_land.size} cells gain land, {plan.gains_water.size} gain water, "
          f"{plan.loses_land.size} lose all land; {refresh.size} land fractions change")
    if refresh.size == 0:
        return False
    n_cell = ctx.shape[1] * ctx.shape[2]
    touched = sorted(set((refresh // n_cell).tolist()))

    tmps = [p + ".sfcfix.tmp" for p in paths]
    try:
        for p, tmp in zip(paths, tmps):
            shutil.copyfile(p, tmp)                    # follows the link; the base file is only read
        dss = open_set(tmps, "r+")
        try:
            land_done = copy_from(dss, sorted(LAND), plan.gains_land, plan.land_src, ctx.shape, touched)
            water_done = copy_from(dss, sorted(WATER), plan.gains_water, plan.water_src, ctx.shape, touched)

            climo_done = []
            for name in sorted(CLIMO):
                if name in dss[0].variables and plan.gains_land.size:
                    a = stack_var(dss, name)
                    a[..., plan.gains_land] = plan.climo[name][plan.gains_land]
                    write_back(dss, name, a, ctx.shape, touched)
                    climo_done.append(name)

            if "stc" in dss[0].variables and plan.gains_land.size:
                stc = stack_var(dss, "stc")
                dz = ctx.old.terrain.ravel()[plan.land_src] - ctx.new.terrain.ravel()[plan.gains_land]
                shift = np.where(np.abs(dz) > TERRAIN_DIFF_MIN, dz * LAPSE_RATE, 0.0)
                cols = stc[..., plan.gains_land] + shift
                ice = np.rint(plan.climo["vtype"][plan.gains_land]) == VEG_TYPE_LANDICE
                cols = np.where(ice & (shift != 0.0), np.minimum(cols, LANDICE_TMAX), cols)
                stc[..., plan.gains_land] = cols
                write_back(dss, "stc", stc, ctx.shape, touched)
                n_lapse = int((shift != 0.0).sum())
            else:
                n_lapse = 0

            comp_done = recompute_composites(dss, ctx, refresh, touched)

            slmsk = stack_var(dss, "slmsk")
            lf = ctx.new.land_frac.ravel()[refresh]
            fice = field_2d(dss, "fice")[refresh] if "fice" in dss[0].variables else np.zeros(refresh.size)
            slmsk[..., refresh] = np.where(lf > 0.0, 1.0, np.where(fice > 0.0, 2.0, 0.0))
            write_back(dss, "slmsk", slmsk, ctx.shape, touched)

            found = mask_problems(field_2d(dss, "slmsk"), field_2d(dss, "stype"), ctx.new.land_frac.ravel())
            at_changed = {k: int(v[refresh].sum()) for k, v in found.items()}
            elsewhere = {k: int(v.sum()) - at_changed[k] for k, v in found.items()}
            if found["abort"].any() or any(at_changed.values()):
                raise FixError("the result still disagrees with the target mask: " + ", ".join(
                    f"{int(found[k].sum())} {PROBLEM_TEXT[k]}" for k in found if found[k].any()))

            note = (f"{plan.gains_land.size} cells gained land, {plan.gains_water.size} gained water; "
                    f"reconciled {ctx.old.name} -> {ctx.new.name} by fix_sfc_masks.py on "
                    f"{datetime.now():%Y-%m-%d}")
            for ds in dss:
                ds.setncattr(FIX_ATTR, note)
        finally:
            for ds in dss:
                ds.close()

        for tmp, p in zip(tmps, paths):
            os.replace(tmp, p)                         # replaces the link itself, not its target
    except BaseException:
        for tmp in tmps:
            if os.path.exists(tmp):
                os.remove(tmp)
        raise

    print(f"   land side from donors ({len(land_done)} fields); farthest "
          f"{plan.land_dist.max():.0f} km" if plan.gains_land.size else "   no cells gain land")
    print(f"   water side from donors ({len(water_done)} fields); farthest "
          f"{plan.water_dist.max():.0f} km" if plan.gains_water.size else "   no cells gain water")
    if climo_done:
        print(f"   climatology from {ctx.climo.prefix}: {' '.join(climo_done)}")
    print(f"   lapse-rate soil temperature at {n_lapse} cells; recomputed {' '.join(comp_done)}")
    for k, n in elsewhere.items():
        if n:
            print(f"   note: {n} unchanged cells already had {PROBLEM_TEXT[k]}")
    print("   replaced 6 tiles.")
    return True


def recompute_composites(dss, ctx, changed, touched):
    """Composites at changed cells, from their parts (UFSATM fv3atm_sfc_io.F90, frac_grid)."""
    have = set(dss[0].variables)

    def part(name):
        return field_2d(dss, name)[changed]

    lf = ctx.new.land_frac.ravel()[changed]
    done = []
    for name in COMPOSITES:
        if name not in have:
            continue
        needs = {"snwdph": ("fice", "snodi", "snodl"), "sheleg": ("fice", "weasdi", "weasdl"),
                 "zorl": ("fice", "zorli", "zorll", "zorlw"), "zorlwav": ("zorl",),
                 "tsfc": ("fice", "tsfcl", "tisfc", "tsea")}[name]
        if not set(needs) <= have:
            continue
        ice = part("fice") > 0.0 if "fice" in needs else None
        land = lf > 0.0
        if name == "snwdph":
            v = np.where(ice, part("snodi"), np.where(land, part("snodl"), 0.0))
        elif name == "sheleg":
            v = np.where(ice, part("weasdi"), np.where(land, part("weasdl"), 0.0))
        elif name == "zorl":
            v = np.where(ice, part("zorli"), np.where(land, part("zorll"), part("zorlw")))
        elif name == "zorlwav":
            v = part("zorl")
        else:
            water = 1.0 - lf
            ice_part = water * part("fice")
            v = (part("tsfcl") * lf + part("tisfc") * ice_part
                 + np.maximum(CON_TICE, part("tsea")) * (water - ice_part))
        a = stack_var(dss, name)
        a[..., changed] = v
        write_back(dss, name, a, ctx.shape, touched)
        done.append(name)
    return done


def apply(args):
    ctx = Context(args)
    sets = find_restart_sets(args.ic_dir)
    print(f"Reconciling {len(sets)} surface restart set(s): {ctx.old.name} -> {ctx.new.name}")
    fixed = 0
    for directory, prefix, when in sets:
        print(f"== {os.path.relpath(os.path.join(directory, prefix), args.ic_dir)}")
        fixed += apply_set(directory, prefix, when, ctx, args.max_distance_km)
    print(f"Fixed {fixed} of {len(sets)} set(s).")
    return 0


# --------------------------------------------------------------------------- check

def check(args):
    ctx = Context(args, need_input=False)
    status = 0
    for directory, prefix, _ in find_restart_sets(args.ic_dir):
        dss = open_set(tile_paths(directory, prefix))
        try:
            check_set(dss, ctx)
            found = mask_problems(field_2d(dss, "slmsk"), field_2d(dss, "stype"), ctx.new.land_frac.ravel())
            fixed = dss[0].getncattr(FIX_ATTR) if FIX_ATTR in dss[0].ncattrs() else None
        finally:
            for ds in dss:
                ds.close()
        rel = os.path.relpath(os.path.join(directory, prefix), args.ic_dir)
        verdict = "ABORTS global_cycle" if found["abort"].any() else "OK"
        print(f"{verdict:20s} {rel}" + (f"  [{fixed}]" if fixed else ""))
        for k, cells in found.items():
            if cells.any():
                print(f"   {int(cells.sum()):7d}  {PROBLEM_TEXT[k]}")
        status |= int(found["abort"].any())
    return status


def main(argv=None):
    sys.stdout.reconfigure(line_buffering=True)        # keep progress and errors in order
    args = parse_args(argv)
    try:
        return {"report": report, "apply": apply, "check": check}[args.cmd](args)
    except FixError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
