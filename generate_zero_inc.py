#!/usr/bin/env python3
"""
Generate a ZERO-valued MOM6 ODA increment on the 0.08-deg (008) grid.
Placeholder to satisfy MOM6's ODA_INCUPD size check while the SOCA 008 geometry is built.
Temp/Salt/u/v/ave_ssh = 0 (increments are added -> no-op); h > 0 (apply path divides by sum(h)).
Written in Z-slabs so peak RAM ~240 MB even though the file is ~24 GB.
"""
import os
import argparse
import numpy as np
from netCDF4 import Dataset

# Parse command line arguments
parser = argparse.ArgumentParser(description="Generate a ZERO-valued MOM6 ODA increment file.")
parser.add_argument("outdir", type=str, help="Directory in which to place the generated file")
parser.add_argument("cycle", type=str, help="Cycle time string (e.g., 00, 06, 12, 18)")
args = parser.parse_args()

# Ensure the output directory exists
os.makedirs(args.outdir, exist_ok=True)

NX, NY, NZ = 4500, 3297, 41     # xaxis_1 (lonh), yaxis_1 (lath), zaxis_1
HREF = 100.0                    # any > 0 thickness [m]; value irrelevant since increments are 0
OUT  = os.path.join(args.outdir, f"gdas.t{args.cycle}z.mom6_increment.i006.nc")
COMPRESS = False                # set True for zlib (all-zeros -> tiny file) if your FMS reads it

nc = Dataset(OUT, "w", format="NETCDF4")
nc.createDimension("xaxis_1", NX)
nc.createDimension("yaxis_1", NY)
nc.createDimension("zaxis_1", NZ)
nc.createDimension("Time", None)

def coord(name, n, axis, units="none"):
    v = nc.createVariable(name, "f8", (name,), fill_value=False)
    v.long_name = name; v.units = units; v.cartesian_axis = axis
    v[:] = np.arange(1, n + 1, dtype="f8")

coord("xaxis_1", NX, "X")
coord("yaxis_1", NY, "Y")
coord("zaxis_1", NZ, "Z")
tvar = nc.createVariable("Time", "f8", ("Time",), fill_value=False)
tvar.long_name = "Time"; tvar.units = "time level"; tvar.cartesian_axis = "T"
tvar[0] = 1.0

dims3d = ("Time", "zaxis_1", "yaxis_1", "xaxis_1")
dims2d = ("Time", "yaxis_1", "xaxis_1")
def datavar(name, dims):
    v = nc.createVariable(name, "f8", dims, fill_value=False,
                          zlib=COMPRESS, complevel=1 if COMPRESS else 0,
                          chunksizes=(1, 1, NY, NX) if len(dims) == 4 else (1, NY, NX))
    v.long_name = name; v.units = "none"
    return v

Temp = datavar("Temp", dims3d); Salt = datavar("Salt", dims3d)
uvar = datavar("u", dims3d);    vvar = datavar("v", dims3d)
hvar = datavar("h", dims3d);    assh = datavar("ave_ssh", dims2d)

zero_slab = np.zeros((NY, NX), dtype="f8")
h_slab    = np.full((NY, NX), HREF, dtype="f8")
for k in range(NZ):
    Temp[0, k, :, :] = zero_slab
    Salt[0, k, :, :] = zero_slab
    uvar[0, k, :, :] = zero_slab
    vvar[0, k, :, :] = zero_slab
    hvar[0, k, :, :] = h_slab
assh[0, :, :] = zero_slab

nc.filename = "zero_increment_008_placeholder"
nc.close()
print(f"wrote {OUT}: {NX}x{NY}x{NZ}, h={HREF} (>0), Temp/Salt/u/v/ave_ssh = 0")
