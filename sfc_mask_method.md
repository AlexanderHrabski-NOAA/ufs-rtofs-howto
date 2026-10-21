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

Then, at every cell whose land fraction changed, recompute the composites with the model's fractional-grid formulas (UFSATM `io/fv3atm_sfc_io.F90`) and set `slmsk`. This includes cells that go from part land to no land. They gain nothing, but their `slmsk` of 1 would still make `global_cycle` abort. Apart from that, cells that gain nothing, and the side a cell already had, stay bit-for-bit identical.

### Why copy from one donor, instead of interpolating

- Changed cells sit on the coastline, so all the valid donors are on one side. Any weighted scheme there is one-sided extrapolation.
- A cell's state is a set of linked values: vegetation and soil types, soil moisture that must lie between the soil type's wilting point and saturation, liquid water that depends on temperature, snow layers that must add up to the snow depth. Averaging breaks those links, while a copied column is one the model actually produced.
- Matching the donor's vegetation and soil type to the 8 km climatology keeps those links intact when the climatology sets the types. Soil moisture needs no rescaling, Noah-MP's leaf, stem and wood pools belong to the right vegetation type, and land ice takes land-ice donors.
- These ICs are constructed by hand anyway, and the land states re-equilibrate within the first cycles. A nearby real column is all the first forecast needs.

GFDL's FRE-NCtools `remap_land` uses the same rule for its land restarts: the nearest same-kind point by great-circle distance, whole column copied. It only reads LM3/LM4 files, so it can't be used here.

### Why not chgres_cube on the surface only

`chgres_cube` (convert_sfc only, mx025 to mx008) also fixes the mask, and an earlier version of this step used it. It isn't a good fit for patching a warm restart:

- **Cold-start output.** It writes a cold-start file with no Noah-MP state, so its output can't replace `sfc_data`. It has to be merged into the warm restart, and the Noah-MP fields then come from somewhere else.
- **Several donors per cell.** It picks donors separately for each group of fields. ESMF nearest neighbour supplies soil and canopy, while a ring search in index space on one tile supplies SST, NSST, ice fraction and snow. One new-land cell can get its soil from one neighbour and its snow from another.
- **Not fractional-grid aware.** It reads only the combined fields (`tsea`, `zorl`, `sheleg`, `snwdph`). It also takes donors from `slmsk`, so a part-land cell can donate only land.
- **Heavy to run.** It needs MPI, a Slurm allocation and a namelist.

`chgres_cube` is the right tool for cold starts and for changing grids. Converting the whole atmosphere and cold starting it is a real alternative to this method, compared in the next section.

## Compared with a cold start of the atmosphere

The alternative is to stop patching the warm restarts altogether. `chgres_cube` converts the whole CATM atmosphere (atmosphere, surface and NSST) onto the 8 km run's orography, and the experiment is set up as a cold start (`start: cold` in `coupled.exp.yaml`). The ocean and ice still start from their restarts. This is a separate recipe, drafted on this repository's `main` branch.

global-workflow already supports pairing a cold atmosphere with warm ocean and ice. Every coupled cold start runs this way (the GFSv17 prototypes, GEFS, SFS), because the start type only changes how FV3 starts:

- **Staging.** With `EXP_WARM_START=.false.`, FV3 gets `gfs_ctrl.nc`, `gfs_data.tile*.nc` and `sfc_data.tile*.nc` from `model/atmos/input`. The ocean and ice restarts are staged either way, and the mediator restart only for a warm start (`parm/stage/master_gfs.yaml.j2`).
- **FV3** starts from those files (`external_ic`, `nggps_ic`, `na_init = 1`, `mountain = .false.`).
- **MOM6** reads `MOM.res*.nc` as before. How it reads them is set by resolution (`MOM6_RESTART_SETTING = 'r'` at 008 in `config.ufs`), not by the start type.
- **CICE** reads the same `cice_model.res.nc`, with `runtype = 'initial'` and `use_restart_time = .false.`, so its embedded date no longer matters.
- **The mediator** starts up from nothing, as it already does here.
- **The first cycle** has no IAU. The model starts at the cycle time (00Z), not at 21Z, so the ocean and ice files are named for 00Z. The CATM run has atmosphere restarts at 00Z, so nothing needs relabelling.

"Cold start" is a misleading name for this. In FV3 it means starting from converted initial-condition files without the physics restart, not from an atmosphere that hasn't been spun up: the state is still the CATM run's, converted and simplified.

| | This method (warm start, patched) | Cold start of the atmosphere |
|---|---|---|
| What changes | Only cells whose land fraction changes (28,812 of 884,736). Everything else is bit-for-bit identical. | Every cell is rewritten by `chgres_cube`. On the same C384 grid, most fields come back almost unchanged. |
| Mask consistency | Patched, then confirmed by `check` | By construction: `chgres_cube` builds `sfc_data` on the 8 km mask |
| New land | One donor column with the same vegetation and soil types, Noah-MP included. An error if there's no donor within the limit. | Soil and canopy from the ESMF nearest neighbour; snow, SST and ice from a ring search on the same tile; built-in defaults where nothing is found. Soil moisture is rescaled to the soil type. It never fails. |
| Remote islands (364 cells more than 250 km from any 25 km land) | An error, for now (see Status) | Filled without a warning |
| Atmosphere dynamics | The restart state | Remapped from L128 to L128 (almost an identity), then one initialization step and a short adjustment |
| Physics restart (`phy_data`: convection, PBL TKE, cloud fraction, radiation) | Kept | Dropped everywhere, so these spin up over the first hours |
| Noah-MP | Kept everywhere except at new-land cells | Rebuilt everywhere by the model's cold-start rules. Soil temperature and moisture, SWE and canopy water carry over. The aquifer (`zwt = 2.5 m`, `waxy = 4900 mm`), snow layers, canopy temperatures and vegetation pools are reset. |
| NSST | Kept | Converted (`convert_nst`), so no NSST spin-up |
| First cycle | IAU window from 21Z | No IAU; starts at 00Z |
| Ocean, ice, mediator | MOM6 restart; CICE `continue`; mediator starts up | The same, except that CICE runs as `initial` |
| Second cycle onward | Warm start from the model's own restarts | The same |
| Tooling | This script, on a login node | `chgres_cube` under MPI in a batch job: the UFS_UTILS-supported path |

The cold start's cost is concentrated in the first half-cycle, whose forecast is the background for the first analysis, and in the slow land states (aquifer, snow layers), which take longer to re-equilibrate. That matters if the early cycles are used for comparisons. This method disturbs much less, but it is custom code, and it still needs a policy for the remote islands.

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

The report also prints the donor distance when matching soil type only, and when matching nothing, so you can see what type matching costs. It lists the farthest new-land cells, and `--cells-csv cells.csv` writes one row per changed cell, with position and the three distances, for plotting. Pick `--max-distance-km` from it.

The first orography directory must be the one the CATM run used (the system `orog/C384`), and the second the 8 km one. Passing the 8 km directory twice makes the identical-grid check compare a grid with itself.

## Status

As of 2026-10-21.

**Implemented.** `fix_sfc_masks.py` with its `report`, `apply` and `check` modes, this note, and section E of `create_ics.sh` (`SFC_MAX_DONOR_KM`, 250 km by default). Everything described above is in the code: the type-matched land donors, ocean and lake water donors, the climatology interpolated in time, the lapse-rate correction, the recomputed composites and `slmsk`, every safeguard, and the report's CSV output.

**Tested.** A synthetic six-tile fixture checks donor choice (including ties), lake and ocean classes, the lapse-rate correction, composites, `slmsk`, refusal of unknown fields, and re-runs. Running `create_ics.sh` twice in a row gives identical ICs.

**Report on the real restarts (Ursa).** `report` was run on the CATM directory. All six restart sets (2025-12-12 18Z and 2026-01-26 18Z, at 21Z, 00Z and 03Z) gave the same numbers:

| Cells                                   | Count |
|-----------------------------------------|-------|
| Gain land                               | 4,850 |
| Gain water                              | 2,029 |
| Lose all their land                     | 2,041 |
| Other land-fraction changes             | 19,892 |
| Would make `global_cycle` abort         | 2,044 (0.23% of 884,736) |

| New-land donor distance  | Median | 90th percentile | > 250 km | > 500 km | Farthest |
|--------------------------|--------|-----------------|----------|----------|----------|
| Same vegetation and soil | 48 km  | 736 km          | 973      | 636      | 8,353 km (1 cell has none) |
| Same soil only           | 28 km  | 253 km          | 488      | 297      | 4,774 km |
| Any land                 | 27 km  | 178 km          | 364      | 250      | 3,506 km |

- The water side is fine: the farthest water donor is 124 km away, and no new-water cell is a lake.
- 261 of the new-land cells are land ice (vegetation type 15).
- Exact type matching is what drives the distances up. For example, one Labrador cell's typed donor is 8,353 km away, against 25 km for any land.
- The one cell with no typed donor at all is on the Angola coast (vegetation 16, soil 10).
- The 364 cells more than 250 km from any land are remote islands the finer ocean mask now resolves: French Polynesia, Micronesia, the Marshall Islands, the Maldives, the South Sandwich Islands (land ice), Crozet, Amsterdam, Macquarie and Tristan da Cunha.
- The densest clusters of changed cells are the Aegean, the Bahamas, the Philippines, Indonesia and the Wadden Sea.

**Fixed after the report.** Cells that go from part land to no land weren't getting their `slmsk` reset, so `apply` would have failed its own check. Composites and `slmsk` are now redone at every land-fraction change (commit b10b0c6).

**Not done yet.**

- `apply` has not been run on the real ICs, and no `sfcanl` job has run on fixed restarts.
- The report runs so far passed the 8 km orography directory for both arguments, so they need repeating with the system `orog/C384` first, to really test that the grids are identical.

**Open decisions.** With the defaults, `apply` would refuse these ICs: 973 cells have no typed donor within 250 km. The choices are:

1. **Loosen the land rule.** Require only the same kind of land (ice or not ice), then adjust the copied column to the cell's 8 km types. Soil moisture would be rescaled as `chgres_cube` does, and the Noah-MP vegetation pools rebuilt with the model's cold-start rules from `noahmptable.tbl`.
2. **Copy from the nearest land of any type, then apply the model's cold-start rules at the new-land cells only.** This is the same as what `chgres_cube` plus a model cold start would give at those cells, while the rest of the restart stays warm.
3. **Give remote islands a local column, with a warning.** Under either rule, a cell beyond the limit would get a column built from its own `tsea` and climatology instead of an error.
4. **Drop the patch and cold start the atmosphere** (see the previous section).

## Limitations

- Type matching is exact. A rare vegetation and soil combination can push the donor far away, or leave no donor at all. The real report shows that this happens often (see Status).
- Only soil temperature gets the lapse-rate correction, as in `chgres_cube`. The other land temperatures (skin, vegetation, snow) are copied as they are.
- The vegetation and soil types of cells that keep their land still come from the CATM run. `global_cycle` keeps restart vegetation and soil types (`FVETL = FSOTL = 99999`), but it does refresh albedo, vegetation fraction and the other climatological fields every cycle.
- `apply` has been tested only on a synthetic six-tile fixture so far. On the real restarts, only `report` has been run.
