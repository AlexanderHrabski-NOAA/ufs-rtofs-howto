## Build and Prepare a Coupled 3DVar FGAT (C384 Atmosphere + 1/12 Ocean)

Edit the `coupled.exp.yaml` configuration, `icdir` should point to your prepared initial conditions and `yaml` to your `marine3dvar.yaml`. Once done, clone and build the workflow:

```bash
git clone --recursive https://github.com/NOAA-EMC/global-workflow.git
./generate_workflows.sh -u  # Check available options
```
configure the hi-res MOM6 experiment:
```bash
export PSLOT=ufs-rtofs
export EXPERIMENT_FILESYSTEM_ROOT="/path/to/scratch-or-project-filesystem/runs"
export RUNTESTS="$EXPERIMENT_FILESYSTEM_ROOT/$PSLOT"
export HPC_ACCOUNT=da-cpu

source ./gw-torch/dev/ush/gw_setup.sh
./global-workflow/dev/workflow/create_experiment.py --yaml coupled.exp.yaml
```

Once the experiment is successfully created, start rocoto:
```bash
rocotorun -d $RUNTESTS/EXP/$PSLOT/$PSLOT.db -w $RUNTESTS/EXP/$PSLOT/$PSLOT.xml
```
You can use the rocoto `GUI` to cycle the experiment:
```bash
./global-workflow/dev/workflow/rocoto_viewer.py -d $RUNTESTS/EXP/$PSLOT/$PSLOT.db -w $RUNTESTS/EXP/$PSLOT/$PSLOT.xml
```