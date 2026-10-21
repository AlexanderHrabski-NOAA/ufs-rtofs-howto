# Reconciling the surface restarts with the 8 km land/sea mask

This note explains what `fix_sfc_masks.py` does to the atmospheric surface restarts, and why. `create_ics.sh` runs it as its last step (section E).

## The problem

On a warm start, FV3 reads its surface state from `<date>.sfc_data.tile{1..6}.nc`: one field per variable on each C384 tile, plus soil, snow and ice layers. These are the values the land model (Noah-MP), NSST, sea ice and surface-layer schemes left behind at the end of the previous forecast. The model continues from them as they are. On a warm start it does not re-initialize Noah-MP.

The grid is fractional. Each cell has a `land_frac` between 0 and 1, read from the orography file (`C384.mx<ocean>_oro_data`), not from the restart. The land part of a cell carries land states. The rest is water, possibly ice-covered, and carries water and ice states. A part-land cell carries both. The restart also stores `slmsk`, one flag per cell: 0 water, 1 land, 2 sea ice.

The CATM restarts were made with `C384.mx025_oro_data`. This workflow runs with `C384.mx008_oro_data`, whose finer ocean draws a different coastline. Both use the same C384 atmosphere grid: "25 km" and "8 km" are ocean resolutions. So each cell is simply compared with itself, and there are three things that can happen to it:

| 25 km `land_frac` | 8 km `land_frac` | The cell...                     |
|-------------------|------------------|---------------------------------|
| 0                 | > 0              | gains land                      |
| 1                 | < 1              | gains water (all of it or part) |
| anything else     |                  | gains nothing                   |

A cell that gains a surface type has no valid state for it, because the CATM run never computed one there. Left alone, this causes four problems:

- `global_cycle` reads `slmsk` from the restart, and aborts at the first cell with `land_frac = 0` and `slmsk = 1` (`LAND FRAC AND SLMASK MISMATCH`, UFS_UTILS `global_cycle.fd/cycle.F90:587`). This is what first exposed the issue.
- New land has no soil, snow or Noah-MP state, only whatever a water cell carried.
- New water starts with land values. On an all-land cell the model sets the open-water temperature `tsea` to the land skin temperature, and the NSST fields hold land values. Coupled SST from MOM6 overwrites `tsfco` on ocean cells, but not the NSST profile or lakes.
- New land can disappear. A water cell has soil type 0, and the model zeroes `land_frac` wherever `stype` is 0 or 14.

Losing a surface type needs nothing: those states simply go unused.

## The fields

Every gridded field in `sfc_data` belongs to one family. The script lists all of them and refuses to touch a file that has a field it doesn't know, rather than guess which side it belongs to.

| Family      | Fields (examples)                                                                 | At a cell that gains this side                     |
|-------------|------------------------------------------------------------------------------------|----------------------------------------------------|
| Land state  | `stc smc slc tsfcl canopy weasdl snodl zorll`, Noah-MP `*xy` fields, land albedo and emissivity | copied from one land donor                |
| Water state | `tsea zorlw`, NSST (`tref xt xs xu xv xz zm dt_cool z_c ...`), sea ice (`fice hice tisfc tiice weasdi snodi zorli`), FLake | copied from one water donor |
| Climatology | `vtype stype slope tg3 snoalb facsf facwf alvsf alvwf alnsf alnwf vfrac shdmin shdmax` | from the 8 km surface climatology (new land only) |
| Composites  | `tsfc zorl sheleg snwdph zorlwav`                                                   | recomputed from their parts                        |
| Diagnostics | `t2m q2m f10m uustar ffmm ffhh tprcp srflag`                                        | left alone; recomputed in the first time step      |
| Mask        | `slmsk`                                                                             | set to `ceiling(land_frac)`, or 2 where there is ice |

## The method

For each cell that gains land:

1. Read the cell's vegetation and soil type from the 8 km surface climatology (`<orog dir>/sfc/C384.mx008.{vegetation_type,soil_type}.tile*.nc`).
2. Find the nearest cell that had land in the CATM run (25 km `land_frac > 0`, with a real soil type) and has the **same vegetation and soil type**. Distance is great-circle distance between cell centres, searched over all six tiles.
3. Copy every land-state field from that one donor, Noah-MP included.
4. Set the climatological fields from the 8 km files. Monthly fields (snow-free albedo, vegetation fraction) are interpolated linearly to the restart's valid time, the same way `chgres_cube` does it (`static_data.F90`). `shdmin`/`shdmax` are the year's minimum and maximum vegetation fraction, and `facwf = 1 − facsf`.
5. Where the donor's terrain differs from the cell's by more than 100 m, shift soil temperature by 6.5 K/km, capped at 273.16 K under land ice. This is `chgres_cube`'s correction (`surface.F90`, `adjust_soilt_for_terrain`).

For each cell that gains water, copy every water and sea-ice field from the nearest cell that had water in the CATM run (25 km `land_frac < 1`). If the orography files carry `lake_frac`, lakes take lake donors and ocean takes ocean donors.

Then, at every changed cell, recompute the composites with the model's fractional-grid formulas (UFSATM `io/fv3atm_sfc_io.F90`) and set `slmsk`. Cells that gain nothing, and the side a cell already had, stay bit-for-bit identical.

### Why copy from one donor, instead of interpolating

- Changed cells sit on the coastline, so all the valid donors are on one side. Any weighted scheme there is one-sided extrapolation.
- A cell's state is a set of linked values: vegetation and soil types, soil moisture that must lie between the soil type's wilting point and saturation, liquid water that depends on temperature, snow layers that must add up to the snow depth. Averaging breaks those links, while a copied column is one the model actually produced.
- Matching the donor's vegetation and soil type to the 8 km climatology keeps those links intact when the climatology sets the types. Soil moisture needs no rescaling, Noah-MP's leaf, stem and wood pools belong to the right vegetation type, and land ice takes land-ice donors.
- These ICs are constructed by hand anyway, and the land states re-equilibrate within the first cycles. A nearby real column is all the first forecast needs.

GFDL's FRE-NCtools `remap_land` uses the same rule for its land restarts: the nearest same-kind point by great-circle distance, whole column copied. It only reads LM3/LM4 files, so it can't be used here.

### Why not chgres_cube

`chgres_cube` (convert_sfc only, mx025 to mx008) also fixes the mask, and an earlier version of this step used it. It isn't a good fit:

- **Cold-start output.** It writes a cold-start file with no Noah-MP state, so its output can't replace `sfc_data`. It has to be merged into the warm restart, and the Noah-MP fields then come from somewhere else.
- **Several donors per cell.** It picks donors separately for each group of fields. ESMF nearest neighbour supplies soil and canopy, while a ring search in index space on one tile supplies SST, NSST, ice fraction and snow. One new-land cell can get its soil from one neighbour and its snow from another.
- **Not fractional-grid aware.** It reads only the combined fields (`tsea`, `zorl`, `sheleg`, `snwdph`). It also takes donors from `slmsk`, so a part-land cell can donate only land.
- **Heavy to run.** It needs MPI, a Slurm allocation and a namelist.

`chgres_cube` is the right tool for cold starts and for changing grids, neither of which applies here.

## Safeguards

- **Identical grids.** The cell centres in `C384_grid.tile*.nc` must match between the two orography directories, or the script stops. The rest of the IC construction already reuses the CATM atmosphere restarts on the 8 km run's grid, so this is a precondition anyway.
- **No donor in reach.** A cell with no donor within `--max-distance-km` (from `SFC_MAX_DONOR_KM` in `create_ics.sh`, 250 km by default) is an error. The error lists the cells, with their position and the distance found.
- **Outdated climatology.** A new-land cell where the 8 km climatology has no land values (vegetation or soil type 0, soil type 14, or −999) is an error. That usually means static files made before UFS_UTILS supported fractional land.
- **Never writes through links.** The overlay's restarts are symlinks into the CATM run. The script copies each tile and edits the copy. It checks the result, and only after all six tiles pass does it rename the copies over the links. The CATM files are only ever read.
- **Re-running is safe.** Fixed files carry a `sfc_mask_fix` global attribute, and the script skips them. `create_ics.sh` re-links the base files on every run, so it redoes the fix from scratch.

## Usage

```bash
source /path/to/global-workflow/dev/ush/load_modules.sh

# 1. Look before writing anything: cells per tile, donor distances, vegetation/soil classes.
./fix_sfc_masks.py report <ic or CATM dir> <25 km orog/C384 dir> <8 km orog/C384 dir>

# 2. Fix every *.sfc_data.tile1.nc set under the IC tree (create_ics.sh does this).
./fix_sfc_masks.py apply <ic dir> <25 km orog/C384 dir> <8 km orog/C384 dir> --max-distance-km 250

# 3. Confirm nothing would make global_cycle abort.
./fix_sfc_masks.py check <ic dir> <8 km orog/C384 dir>
```

The report also prints the untyped donor distance next to the type-matched one, so you can see what the type matching costs in distance. Pick `--max-distance-km` from it.

## Limitations

- Type matching is exact. A rare vegetation and soil combination can push the donor far away, or leave no donor at all. If the report shows that happening often, the next step is a fallback, for example matching soil type only and rescaling soil moisture as `chgres_cube` does.
- Only soil temperature gets the lapse-rate correction, as in `chgres_cube`. The other land temperatures (skin, vegetation, snow) are copied as they are.
- The vegetation and soil types of cells that keep their land still come from the CATM run. `global_cycle` keeps restart vegetation and soil types (`FVETL = FSOTL = 99999`), but it does refresh albedo, vegetation fraction and the other climatological fields every cycle.
- So far this has been tested only on a synthetic six-tile fixture, not on the real restarts.
