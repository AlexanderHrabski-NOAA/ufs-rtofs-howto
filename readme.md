## Build and Prepare a Coupled 3DVar FGAT (C384 Atmosphere + 1/12 Ocean)

Clone this repository to the location you would like to keep the experiment configuration:

```bash
git clone https://github.com/AlexanderHrabski-NOAA/ufs-rtofs-howto.git
```

Edit the `coupled.exp.yaml` configuration:
- `icdir` should point to your prepared initial conditions
- `yaml` to your `marine3dvar.yaml`

Then, clone the following branch of Alex's fork of the global workflow _in a space large enough to store the GFS/GDAS executables_:

```bash
git clone https://github.com/AlexanderHrabski-NOAA/global-workflow.git -b feature/highres_mom6cic6 --recurse-submodules -j 4
```

You'll need to build and install the global workflow before it can be used. See `./build_all.sh -h` or [the documentation](https://global-workflow.readthedocs.io/en/latest/user_guide/clone.html#build-components) for more options. This may take a while.

```bash
cd global-workflow/sorc/
./build_all.sh gfs gdas
./link_workflow.sh
```

Next, you will need to setup the needed fix file overlays. Navigate back to `/path/to/ufs-rotfs-howto` and run the overlay generator via `./create_fixdirs.py fixdirs.<machine>.yaml <dest>`. I choose to put these directories alongside my experiment configuration with the below command. Note that this script only links to existing high resolution fix files in a known location. It does not generate new fix files.

```bash
./create_fixdirs.py fixdirs.<machine>.yaml .
```

Once these directories are generated, you will need to provide their path in `marine3dvar.yaml`. The corresponding lines should read:

```yaml
base:
  FIXorog: /path/to/FIXorog
  FIXcpl: /path/to/FIXcpl
```

We are now ready to generate the experiment. First, you'll want to source the global workflow setup script:
```bash
source /path/to/global-workflow/dev/ush/gw_setup.sh
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

Once the experiment is successfully created, start rocoto:
```bash
rocotorun -d $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.db -w $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.xml
```
You can use the rocoto `GUI` to cycle the experiment:
```bash
/path/to/global-workflow/dev/workflow/rocoto_viewer.py -d $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.db -w $RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.xml
```
It is also advisable to add the contests of `$RUNDIR/ufs-rtofs/EXPDIR/$pslot/$pslot.crontab` to your crontab via:
```bash
crontab -e
```
Or to your scrontab on Gaea, where the same procedure applies.