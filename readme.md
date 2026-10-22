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

Next, we will cover the setup of ICs for this workflow. Because this workflow hasn't been run to produce useable ICs for a warm start, we must construct ICs by hand. The experiment starts the atmosphere cold, from ICs converted with `chgres_cube`, while the ocean and ice warm start from restarts (this is why `coupled.exp.yaml` has `start: cold`). The global workflow already supports this mix: the start type only changes how FV3 starts, and the ocean and ice restarts are staged either way.

There are many options for how to do this. Here, I'll outline the procedure I've used to create a suffeciently valid set of ICs such that the first half-cycle of the workflow runs to completion. While creating these ICs were necissary to ensure the workflow would run when a physically consistent set of ICs are provided, this procedure does not produce ICs suitable for science and should be replaced with a better one.

We will take files from two sources:
- A 25km ocean/ice coupled atmosphere runs (verify exactly what this is)
- A 8km ocean/ice data atmosphere runs (RTOFS)

The coupled atmosphere (CATM) runs will provide the necissary directory structure, the analysis files, and the atmospheric restart files that `chgres_cube` converts. The data atmosphere (DATM) runs will provide the necissary high-resolution ocean/ice restart files that are not present in the CATM data. I've staged these two sets of restarts in the following directories:

On GAEA C6:
- CATM: `/gpfs/f6/sfs-cpu/world-shared/Alexander.Hrabski/IC/cp4.03-parallel-3dvar`
- DATM: `/gpfs/f6/sfs.cpu/world-shared/Alexander.Hrabski/IC/rtofs.20251225`

On Ursa:
- CATM: `/scratch4/NCEPDEV/marine/Alexander.Hrabski/IC/cp4.03-parallel-3dvar`
- DATM: `/scratch4/NCEPDEV/marine/Alexander.Hrabski/IC/rtofs.20251225`

Note that these two sources correspond to different dates. Checks exist within UFS to abort if the needed files have embedded dates that differ from the running cycle. MOM6 has an override that I am currently using to turn these checks off. CICE does not have such an override, but on a cold start the workflow runs CICE with `runtype = 'initial'` and `use_restart_time = .false.`, so the embedded date is ignored and the file can be used as is. Because there is no IAU on a cold start, the first cycle (2026012700) starts at 00Z rather than 21Z, and the ocean and ice files are named for 00Z.

The ICs must also include analysis files. For now, we take the increment to be zero, and we construct a zero increment file for MOM6 to read. For CICE, the increment is already applied to the restart, so we simply link the restart file to the analysis file to achieve a zero increment.

Finally, the mediator also has a warm start option, in which it reads a mediator restart file. In this case, neither source of restarts contains a suitable file. The DATM mediator restart is missing coupling fields, and the CATM restart is at to coarse a resolution. As a temporary workaround, we cold start the mediator, which is done automatically if a mediator restart isn't found in the ICs.

#### Atmosphere ICs from chgres_cube

The CATM atmospheric restarts can't be used as they are. Their land/sea mask follows the 25 km ocean (`C384.mx025_oro_data`), while this workflow uses the 8 km ocean (`C384.mx008_oro_data`). Along the coast, some cells gain land, or gain a water fraction, that the CATM run never computed a state for, and `global_cycle` aborts with `LAND FRAC AND SLMASK MISMATCH`. Instead, we convert the CATM restarts valid at 2026012700 with `chgres_cube` onto the 8 km run's orography and surface climatology. Its output follows the 8 km mask by construction. These are "cold start" files in FV3's sense, but the state is still the CATM run's: converted and simplified, not un-spun-up. The model rebuilds the physics and Noah-MP state it doesn't carry (aquifer, snow layers, canopy temperatures) during the first cycle.

The namelist is `chgres_cube.nml`. It is set up for Ursa; the comment at its top lists the paths to change on GAEA C6. The tracer list must match the tracers in the CATM restarts, so check it first:

```bash
ncdump -h /scratch4/NCEPDEV/marine/Alexander.Hrabski/IC/cp4.03-parallel-3dvar/gdas.20260126/18/model/atmos/restart/20260127.000000.fv_tracer.res.tile1.nc | grep float
```

`chgres_cube` is built with the global workflow (`exec/chgres_cube`) and runs under MPI, so it needs a batch job. From a fresh shell (shown for Ursa; on GAEA C6, load `build.gaeac6.intel` instead):

```bash
mkdir -p /path/to/IC/chgres-c384-mx008
cd /path/to/IC/chgres-c384-mx008
cp /path/to/ufs-rtofs-howto/chgres_cube.nml fort.41
module use /path/to/global-workflow/sorc/ufs_utils.fd/modulefiles
module load build.ursa.intelllvm
sbatch -A marine-cpu -t 00:30:00 -N 2 --ntasks-per-node=24 -J chgres_c384 -o chgres.%j.log \
    --wrap "srun /path/to/global-workflow/exec/chgres_cube"
```

`chgres_cube` reads `fort.41` from the directory it runs in, and writes `gfs_ctrl.nc`, `out.atm.tile{1..6}.nc` and `out.sfc.tile{1..6}.nc` there. A successful run ends its log with `- DONE.` It also checks that the CATM restarts agree with the 25 km orography, and stops with `INCONSISTENT LAND/SEA MASK BETWEEN RESTART AND OROG FILES` if the input orography doesn't match them.

#### Building the ICs

The `create_ics.sh` script constructs the ICs directly from these sources according to the above description, linking the `chgres_cube` output into the IC tree under the names FV3 reads on a cold start. It can be called via:

```bash
source /path/to/global-workflow/dev/ush/load_modules.sh
./create_ics.sh /path/to/IC/rtofs.20251225 /path/to/IC/cp4.03-parallel-3dvar /path/to/IC/coupled-8km-ics \
    /path/to/IC/chgres-c384-mx008
```

The IC tree links to the `chgres_cube` output, so keep that directory. At the start of the first forecast, the workflow's land check (`check_land_input_fields.py`, on by default) compares the converted `sfc_data` with the 8 km orography.
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
