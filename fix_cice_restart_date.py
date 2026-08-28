#!/usr/bin/env python3
"""
Retime a CICE6 restart file so it matches the cycle the UFS driver will start at.

CICE stores its clock in five integer global attributes -- istep1, myear, mmonth,
mday, msec (see CICE cicecore/.../io_pio2/ice_restart.F90:250-258).  With
runtype='continue' the cap reads them back at ice_restart.F90:91-118 and adopts
them as the model clock, then aborts at ice_comp_nuopc.F90:1193 if they disagree
with the ESMF clock.  This script rewrites those attributes in place.

Nothing else in the file is touched -- no ice state is modified.

Under IAU (DOIAU=YES) the model starts at cycle - assim_freq/2, NOT at the cycle
time, so --half-window defaults to 3.  Pass --half-window 0 for a non-IAU run.
"""

import argparse
import shutil
import sys
from datetime import datetime, timedelta

try:
    import numpy as np
    from netCDF4 import Dataset
except ImportError as exc:  # pragma: no cover
    sys.exit(f"error: needs numpy and netCDF4 ({exc}).\n"
             "       In a global-workflow shell: source global-workflow/dev/ush/load_modules.sh")

# Attributes CICE writes, in the order it writes them.
CANON = ("istep1", "myear", "mmonth", "mday", "msec")
# Read-only fallbacks CICE accepts for older files.  Kept in sync when present so
# the file never carries two contradictory dates.
ALIASES = {"myear": "nyr", "mmonth": "month", "msec": "sec"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Retime a CICE6 restart file to the model start of a cycle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  %(prog)s cice_model.res.nc --cycle 2026012700 --dry-run\n"
               "  %(prog)s cice_model.res.nc --cycle 2026012700 --backup\n"
               "  %(prog)s cice_model.res.nc --date 2026012621\n")
    p.add_argument("restart", help="path to cice_model.res.nc")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--cycle", metavar="YYYYMMDDHH",
                      help="workflow cycle; model start is derived from it")
    when.add_argument("--date", metavar="YYYYMMDDHH",
                      help="model start time, used verbatim (no IAU arithmetic)")
    p.add_argument("--half-window", type=int, default=3, metavar="H",
                   help="hours subtracted from --cycle for IAU (default: 3; "
                        "use 0 when DOIAU=NO)")
    p.add_argument("--istep1", type=int, default=None, metavar="N",
                   help="also set istep1 (default: leave as is; it only seeds "
                        "diagnostic counters)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would change and exit without writing")
    p.add_argument("--backup", action="store_true",
                   help="copy to <file>.bak first (restart files are large)")
    p.add_argument("--allow-header-growth", action="store_true",
                   help="permit adding a missing attribute, which can force the "
                        "netCDF library to rewrite the whole file")
    return p.parse_args(argv)


def to_dt(stamp, flag):
    if len(stamp) != 10 or not stamp.isdigit():
        sys.exit(f"error: {flag} must be exactly 10 digits (YYYYMMDDHH), "
                 f"got {stamp!r}")
    try:
        return datetime.strptime(stamp, "%Y%m%d%H")
    except ValueError as exc:
        sys.exit(f"error: {flag}={stamp!r} is not a valid date ({exc})")


def read_current(path):
    try:
        ds = Dataset(path, "r")
    except OSError as exc:
        sys.exit(f"error: cannot open {path} as netCDF ({exc})")
    with ds:
        if not any(v in ds.variables for v in ("aicen", "vicen", "vsnon")):
            sys.exit(f"error: {path} has no aicen/vicen/vsnon -- "
                     "this does not look like a CICE restart.")
        present = {}
        for name in CANON + tuple(ALIASES.values()):
            if name in ds.ncattrs():
                val = ds.getncattr(name)
                present[name] = (int(np.asarray(val).item()),
                                 np.asarray(val).dtype)
        fmt = ds.file_format
    return present, fmt


def main(argv=None):
    args = parse_args(argv)

    if args.date:
        start = to_dt(args.date, "--date")
        derivation = "given directly"
    else:
        cycle = to_dt(args.cycle, "--cycle")
        start = cycle - timedelta(hours=args.half_window)
        derivation = (f"{args.cycle} minus {args.half_window}h"
                      if args.half_window else f"{args.cycle} (no IAU offset)")

    target = {
        "myear": start.year,
        "mmonth": start.month,
        "mday": start.day,
        "msec": start.hour * 3600 + start.minute * 60 + start.second,
    }
    if args.istep1 is not None:
        target["istep1"] = args.istep1

    present, fmt = read_current(args.restart)

    print(f"file          : {args.restart}")
    print(f"format        : {fmt}")
    print(f"model start   : {start:%Y-%m-%d %H:%M:%S}  ({derivation})")
    print()
    print(f"  {'attribute':<10} {'current':>12} {'type':>8}    {'new':>12}")
    print(f"  {'-'*10} {'-'*12:>12} {'-'*8:>8}    {'-'*12:>12}")

    changes, missing = {}, []
    for name in CANON:
        cur, dtype = present.get(name, (None, None))
        new = target.get(name)
        alias = ALIASES.get(name)
        # CICE falls back to the alias only when the canonical name is absent,
        # so an alias-only file is fine as it stands -- retime the alias in place
        # rather than adding a canonical attribute and growing the header.
        alias_covers = name not in present and alias in present
        if name not in present:
            shown_cur, shown_type = "ABSENT", "-"
            if not alias_covers:
                missing.append(name)
        else:
            shown_cur, shown_type = str(cur), str(dtype)
        if new is None:
            shown_new = "(unchanged)"
        elif alias_covers:
            shown_new = f"via {alias}"
        elif cur == new and name in present:
            shown_new = "(same)"
        else:
            shown_new = str(new)
            if name in present:
                changes[name] = new
        print(f"  {name:<10} {shown_cur:>12} {shown_type:>8}    {shown_new:>12}")

    for canon, alias in ALIASES.items():
        if alias in present and canon in target:
            cur = present[alias][0]
            if cur != target[canon]:
                changes[alias] = target[canon]
                note = "read by CICE" if canon not in present else "kept in sync"
                print(f"  {alias:<10} {cur:>12} {str(present[alias][1]):>8}"
                      f"    {target[canon]:>12}   (legacy alias, {note})")

    if "istep1" in missing and args.istep1 is None:
        print("\nerror: istep1 is absent. CICE reads it with no fallback and will "
              "abort.\n       Re-run with --istep1 0 --allow-header-growth.")
        return 1
    if missing and not args.allow_header_growth:
        print(f"\nerror: would need to add {', '.join(missing)}, which can force a "
              "full\n       rewrite of this file. Re-run with "
              "--allow-header-growth if that is acceptable.")
        return 1
    for name in missing:
        if name in target:
            changes[name] = target[name]

    if not changes:
        print("\nNothing to do -- the file already carries this date.")
        return 0

    was = {n: present[n][0] for n in changes if n in present}
    undo = " ".join(f"{n}={v}" for n, v in sorted(was.items()))

    if args.dry_run:
        print(f"\n--dry-run: {len(changes)} attribute(s) would be written.")
        return 0

    print(f"\nprevious values (your undo): {undo}")

    if args.backup:
        dest = args.restart + ".bak"
        print(f"backing up to {dest} ...", flush=True)
        shutil.copy2(args.restart, dest)

    with Dataset(args.restart, "a") as ds:
        for name, val in changes.items():
            ds.setncattr(name, np.int32(val))

    verify, _ = read_current(args.restart)
    bad = [n for n, v in changes.items()
           if n not in verify or verify[n][0] != v]
    wrong_type = [n for n in changes
                  if n in verify and verify[n][1] != np.dtype("int32")]
    if bad:
        print(f"\nERROR: verification failed for {', '.join(bad)}")
        return 1
    if wrong_type:
        print(f"\nERROR: not written as NC_INT: {', '.join(wrong_type)}")
        return 1

    print(f"wrote {len(changes)} attribute(s); verified as NC_INT.")
    print(f"CICE will now start at {start:%Y-%m-%d %H:%M:%S}.")
    print("\nRemember: this only helps with runtype='continue'. Revert the "
          "parsing_namelists_CICE.sh\nedit so ice_in gets runtype='continue' "
          "and use_restart_time=.true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
