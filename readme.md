# Build and Prepare a Coupled 3DVar FGAT (C384 Atmosphere + 1/12 Ocean)

## Setup

In order to use this workflow, a series of fix files, ICs, and configuration files must be correctly set up. This repository contains the needed files and utilities to do so. Begin by cloning this repository to the location you would like to keep the experiment configuration and entering it:

```bash
git clone https://github.com/AlexanderHrabski-NOAA/ufs-rtofs-howto.git
cd ufs-rtofs-howto
```

Note the two global workflow configuration files `coupled.exp.yaml` and `marine3dvar.yaml` which will edited as part of this setup. In the next subsection, we'll cover how to install the global workflow. 

### Global Workflow Setup

The global workflow is configured via the two files `coupled.exp.yaml` and `marine3dvar.yaml`, and the former reference the latter. You'll need to edit the `coupled.exp.yaml` configuration so that `yaml: /path/to/marine3dvar.yaml`. Once this has been done, clone the following branch of Alex's fork of the global workflow _in a space large enough to store the GFS/GDAS executables_:

```bash
git clone https://github.com/AlexanderHrabski-NOAA/global-workflow.git -b feature/highres_mom6cic6 --recurse-submodules -j 4
```

You'll need to build and install the global workflow before it can be used. See `./build_all.sh -h` or [the documentation](https://global-workflow.readthedocs.io/en/latest/user_guide/clone.html#build-components) for more options. This may take a while.

```bash
cd global-workflow/sorc/
./build_all.sh gfs gdas gsi
./link_workflow.sh
```

### Fix file setup

Due to the increase in resolution and the difference in MOM6 configuration, this workflow needs fix files that are not present in the standard system fix file paths. `./create_fixdirs.py` is a utility that stages these additional files from a known location into a mirror of the relevant system fix file directories. We will call the mirror with the additional files an "overlay". The `fixdirs.<machine>.yaml` files are configuration files that contain the instructions for generting these overlays on each supported machine. Note that this script only links to existing high resolution fix files. It does not generate new fix files.

Run the overlay generator via `./create_fixdirs.py fixdirs.<machine>.yaml <dest>`. I choose to put these directories alongside my experiment configuration with the below command. 

```bash
./create_fixdirs.py fixdirs.<machine>.yaml .
```

Once these directories are generated, you will need to provide their path in `marine3dvar.yaml`. The corresponding lines should read:

```yaml
base:
  FIXorog: /path/to/FIXorog
  FIXcpl: /path/to/FIXcpl
  FIXmom: /path/to/FIXmom
  FIXcice: /path/to/FIXcice
```

### IC setup

Next, we will cover the setup of warm start ICs for this workflow. Because this workflow hasn't been run to produce useable ICs for a warm start, we must construct warm start ICs by hand. 

There are many options for how to do this. Here, I'll outline the procedure I've used to create a suffeciently valid set of ICs such that the first half-cycle of the workflow runs to completion. While creating these ICs were necissary to ensure the workflow would run when a physically consistent set of ICs are provided, this procedure does not produce ICs suitable for science and should be replaced with a better one.

We will take files from two sources:
- A 25km ocean/ice coupled atmosphere runs (verify exactly what this is)
- A 8km ocean/ice data atmosphere runs (RTOFS)

The coupled atmosphere (CATM) runs will provide the necissary directory structure and the atmospheric restart files. The data atmosphere (DATM) runs will provide the necissary high-resolution ocean/ice restart files that are not present in the CATM data. I've staged these two sets of restarts in the following directories:

On GAEA C6:
- CATM: `/gpfs/f6/sfs-cpu/world-shared/Alexander.Hrabski/IC/cp4.03-parallel-3dvar`
- DATM: `/gpfs/f6/sfs.cpu/world-shared/Alexander.Hrabski/IC/rtofs.20251225`

On Ursa:
- CATM: `/scratch4/NCEPDEV/marine/Alexander.Hrabski/IC/cp4.03-parallel-3dvar`
- DATM: `/scratch4/NCEPDEV/marine/Alexander.Hrabski/IC/rtofs.20251225`

Note that these two sources correspond to different dates. Checks exist within UFS to abort if the needed files have embedded dates that differ from the running cycle. MOM6 has an override that I am currently using to turn these checks off. CICE does not have such an override, so it is necissary to modify the embedded date in the CICE restart file (actually, analysis, see below).

The ICs must also include analysis files. For now, we take the increment to be zero, and we construct a zero increment file for MOM6 to read. For CICE, the increment is already applied to the restart, so we simply link the analysis file to the restart file to achieve a zero increment.

Finally, the mediator also has a warm start option, in which it reads a mediator restart file. In this case, neither source of restarts contains a suitable file. The DATM mediator restart is missing coupling fields, and the CATM restart is at to coarse a resolution. As a temporary workaround, we cold start the mediator, which is done automatically if a mediator restart isn't found in the ICs.

The atmospheric surface restarts need one more fix. They come from the CATM run, so their land/sea mask follows the 25 km ocean (`C384.mx025_oro_data`), while this workflow uses the 8 km ocean (`C384.mx008_oro_data`). Along the coast, some cells gain land, or gain a water fraction, that the CATM run never computed a state for, and `global_cycle` aborts with `LAND FRAC AND SLMASK MISMATCH`. `fix_sfc_masks.py` fixes this by copying the missing side of each of these cells from a single nearby cell that had it in the CATM run. For new land, the donor must also have the same vegetation and soil type as the 8 km surface climatology, which then fills in the remaining climatological fields. Everything else in the restarts is left untouched. See `sfc_mask_method.md` for the details.

The fix refuses to touch a cell with no donor within `SFC_MAX_DONOR_KM` (250 km by default), so it's worth checking the donor distances first. The report mode only reads files, so it can be run on the CATM directory directly:

```bash
source /path/to/global-workflow/dev/ush/load_modules.sh
./fix_sfc_masks.py report /path/to/IC/cp4.03-parallel-3dvar \
    /scratch3/NCEPDEV/global/role.glopara/fix/orog/20240917/C384 \
    /scratch4/NCEPDEV/marine/Alexander.Hrabski/FIX_highres_mom6_cic6/OROG/C384
```

The `create_ics.sh` script constructs the ICs directly from these two sources according to the above description. It can be called via (shown for Ursa):

```bash
source /path/to/global-workflow/dev/ush/load_modules.sh
./create_ics.sh /path/to/IC/rtofs.20251225 /path/to/IC/cp4.03-parallel-3dvar /path/to/IC/coupled-8km-ics \
    /scratch3/NCEPDEV/global/role.glopara/fix/orog/20240917/C384 \
    /scratch4/NCEPDEV/marine/Alexander.Hrabski/FIX_highres_mom6_cic6/OROG/C384
```

On GAEA C6, the two orography directories are `/gpfs/f6/drsa-precip3/world-shared/role.glopara/fix/orog/20240917/C384` and `/gpfs/f6/sfs-cpu/world-shared/Alexander.Hrabski/FIX_highres_mom6_cic6/OROG/C384`. Re-running `create_ics.sh` is safe and gives the same ICs: each run re-links the base files into the overlay and redoes every step, including the surface fix. Afterwards, `./fix_sfc_masks.py check /path/to/IC/coupled-8km-ics <8 km orog dir>` confirms that no surface restart would make `global_cycle` abort.

Once this is generated, edit the `coupled.exp.yaml` configuration so that `icdir: /path/to/IC/coupled-8km-ics` points to your prepared initial conditions.

## Experiment Generation

We are now ready to generate the experiment. First, you'll want to source the global workflow setup script, and then return to the `ufs-rtofs-howto` directory:
```bash
source /path/to/global-workflow/dev/ush/gw_setup.sh
cd /path/to/ufs-rtofs-howto
```

Next, you'll need to configure your user settings for experiment generation. This can be done via editing the `sourceme.sh` and sourcing it or via setting directly (shown here for Ursa):
```bash
export pslot=ufs-rtofs-test
export RUNDIR="/scratch4/NCEPDEV/stmp/First.Last"
export HPC_ACCOUNT=marine-cpu
```
Note that these variables will need to be assigned before any call to `create_experiment.py` (i.e., once per session). The experiment can then be created via:
```
/path/to/global-workflow/dev/workflow/create_experiment.py -y coupled.exp.yaml
```

## Run the Experiment

Once the experiment is successfully created, start rocoto:
```bash
rocotorun -d $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.db -w $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.xml
```
You can use the rocoto `GUI` to cycle the experiment:
```bash
/path/to/global-workflow/dev/workflow/rocoto_viewer.py -d $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.db -w $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.xml
```
It is also advisable to add the contents of `$RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.crontab` to your crontab via:
```bash
crontab -e
```
Or to your scrontab on Gaea, where the same procedure applies.
