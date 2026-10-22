#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# 1. Validate arguments
if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <source_dir> <base_dir> <overlay_dir> <chgres_dir>"
    echo "  <source_dir>:  RTOFS directory holding the correct restarts (e.g., /path/to/rtofs.20251225)."
    echo "  <base_dir>:    Directory providing the directory structure and analysis files"
    echo "                 (e.g., cp4.03-parallel-3dvar)."
    echo "  <overlay_dir>: New location. A mirror of <base_dir> is built here (directories recreated,"
    echo "                 files symlinked), then files from <source_dir> are overlaid on top of it."
    echo "  <chgres_dir>:  chgres_cube run directory holding gfs_ctrl.nc, out.atm.tile*.nc and"
    echo "                 out.sfc.tile*.nc (the atmosphere cold-start ICs, see readme.md)."
    exit 1
fi

SOURCE_DIR="$1"
BASE_DIR="$2"
OVERLAY_DIR="$3"
CHGRES_DIR="$4"

# 2. Verify the input directories exist
if [ ! -d "$SOURCE_DIR" ] || [ ! -d "$BASE_DIR" ]; then
    echo "Error: Both <source_dir> and <base_dir> must exist."
    exit 1
fi
if [ ! -f "$CHGRES_DIR/gfs_ctrl.nc" ]; then
    echo "Error: '$CHGRES_DIR' holds no gfs_ctrl.nc; run chgres_cube first."
    exit 1
fi

SOURCE_DIR="$(realpath "$SOURCE_DIR")"
BASE_DIR="$(realpath "$BASE_DIR")"
CHGRES_DIR="$(realpath "$CHGRES_DIR")"

# 3. Helper functions

# Mirror <base_dir> into <overlay_dir>: directories are recreated, every file is symlinked.
mirror_base() {
    mkdir -p "$OVERLAY_DIR"
    # -r recurse, -s symlink instead of copy, -f overwrite. Absolute source => absolute links.
    cp -rsf "${BASE_DIR}/." "${OVERLAY_DIR}/"
    echo "Mirrored base tree: $BASE_DIR -> $OVERLAY_DIR"
}

# Overlay a file from the source onto the mirror, replacing whatever the mirror had there.
# Usage: replace_link "source_relative_path" "overlay_relative_path"
replace_link() {
    local source_file="${SOURCE_DIR}/$1"
    local overlay_file="${OVERLAY_DIR}/$2"

    mkdir -p "$(dirname "$overlay_file")"

    if [ ! -e "$source_file" ]; then
        echo "Warning: Source '$source_file' not found. Skipping."
        return
    fi

    # The -f flag forces the overwrite of the existing destination file/link
    ln -sf "$(realpath "$source_file")" "$overlay_file"
    echo "Linked: $2 -> $1"
}

# Copy (not link) a file from the source into the overlay, preserving the source original.
# Usage: copy_from_source "source_relative_path" "overlay_relative_path"
copy_from_source() {
    local source_file="${SOURCE_DIR}/$1"
    local overlay_file="${OVERLAY_DIR}/$2"

    mkdir -p "$(dirname "$overlay_file")"

    if [ ! -e "$source_file" ]; then
        echo "Warning: Source '$source_file' not found. Skipping."
        return
    fi

    # Drop any symlink the mirror left here before writing a real copy.
    rm -f "$overlay_file"
    cp "$source_file" "$overlay_file"
    echo "Copied: $2 <- $1"
}

# Link one overlay path to another overlay path (both relative to OVERLAY_DIR).
# Usage: link_within_overlay "overlay_target_relative_path" "overlay_link_relative_path"
link_within_overlay() {
    local overlay_target="${OVERLAY_DIR}/$1"
    local overlay_link="${OVERLAY_DIR}/$2"

    mkdir -p "$(dirname "$overlay_link")"
    ln -sf "$(realpath "$overlay_target")" "$overlay_link"
    echo "Linked (overlay): $2 -> $1"
}

# Link a chgres_cube output file into the overlay under the name FV3 reads on a cold start.
# Usage: link_chgres "chgres_file" "overlay_relative_path"
link_chgres() {
    local chgres_file="${CHGRES_DIR}/$1"
    local overlay_file="${OVERLAY_DIR}/$2"

    mkdir -p "$(dirname "$overlay_file")"

    if [ ! -e "$chgres_file" ]; then
        echo "Error: chgres_cube output '$chgres_file' not found."
        exit 1
    fi

    ln -sf "$chgres_file" "$overlay_file"
    echo "Linked (chgres): $2 -> $1"
}

# Remove every mediator restart the base mirror linked into the overlay, so the UFS
# driver finds no usable mediator restart and cold starts. The source mediator is
# re-staged (disabled, under the base's name) just below.
drop_mirrored_med_restarts() {
    local link
    while IFS= read -r -d '' link; do
        rm -f "$link"
        echo "Dropped mirrored mediator restart: ${link#"${OVERLAY_DIR}/"}"
    done < <(find "$OVERLAY_DIR" -path '*/model/med/restart/*.ufs.cpld.cpl.r.nc' -print0)
}

echo "Starting IC overlay construction..."
echo "Source (correct restarts): $SOURCE_DIR"
echo "Base   (structure + anl) : $BASE_DIR"
echo "Overlay (built here)     : $OVERLAY_DIR"
echo "chgres (atm cold start)  : $CHGRES_DIR"
echo "----------------------------------------"

# The atmosphere cold starts, so the first cycle (2026012700) has no IAU and the model
# starts at the cycle time. The ocean and ice files are therefore named for 00Z, not
# for the 21Z start of an IAU window.

# --- A. Build the mirror of the base ---
mirror_base
echo "----------------------------------------"

# --- B. CICE restart handling ---
# The CICE restart is copied (not linked) from the source into the overlay so that an
# untouched original is preserved in the source. With marine DA on, the forecast reads
# the analysis file; the restart name points at the same copy. On a cold start CICE
# runs with runtype = 'initial' and use_restart_time = .false., so the date embedded in
# the file is ignored and no longer needs fixing.
CICE_RESTART_SRC="RESTART/iced.2025-12-25-00000.nc"
CICE_RESTART_OVERLAY="gdas.20260127/00/analysis/ice/20260127.000000.analysis.cice_model.res.nc"

copy_from_source "$CICE_RESTART_SRC" "$CICE_RESTART_OVERLAY"
link_within_overlay "$CICE_RESTART_OVERLAY" "gdas.20260126/18/model/ice/restart/20260127.000000.cice_model.res.nc"

# --- C. Mediator file ---
# Neither source holds a usable mediator restart (the DATM one lacks coupling
# fields, the CATM one is too coarse), so the mediator must cold start. Drop the
# mediator restarts the base carried, then link the source (DATM) mediator under
# the 18Z cycle's name -- same name as in the base, up to a .exclude suffix that
# keeps the driver from picking it up while leaving the intended file identifiable.
drop_mirrored_med_restarts
replace_link "RESTART/datm.gfs.cpl.r.2025-12-25-00000.nc" \
    "gdas.20260126/18/model/med/restart/20260127.000000.ufs.cpld.cpl.r.nc.exclude"

# --- D. MOM6 Ocean Files ---
# Base MOM file
replace_link "RESTART/20251229.000000.MOM.res.nc" "gdas.20260126/18/model/ocean/restart/20260127.000000.MOM.res.nc"

# Loop dynamically through MOM files 1 to 16
for i in {1..16}; do
    replace_link "RESTART/20251229.000000.MOM.res_${i}.nc" "gdas.20260126/18/model/ocean/restart/20260127.000000.MOM.res_${i}.nc"
done

# --- E. Atmosphere cold-start files ---
# chgres_cube converted the CATM restarts valid at 00Z onto the 8 km run's orography, so
# its sfc_data already follows the 8 km land/sea mask. The base's warm atmosphere
# restarts stay in the mirror, but a cold start never stages them.
ATM_INPUT_OVERLAY="gdas.20260127/00/model/atmos/input"
link_chgres "gfs_ctrl.nc" "${ATM_INPUT_OVERLAY}/gfs_ctrl.nc"
for tile in {1..6}; do
    link_chgres "out.atm.tile${tile}.nc" "${ATM_INPUT_OVERLAY}/gfs_data.tile${tile}.nc"
    link_chgres "out.sfc.tile${tile}.nc" "${ATM_INPUT_OVERLAY}/sfc_data.tile${tile}.nc"
done

echo "----------------------------------------"

echo "Generating zero MOM6 increment (into the overlay)..."
# If the mirror linked an increment back to the base, drop the link first so the
# generator writes a real file in the overlay instead of clobbering the base original.
rm -f "${OVERLAY_DIR}/gdas.20260126/18/analysis/ocean/gdas.t18z.mom6_increment.i006.nc"
rm -f "${OVERLAY_DIR}/gdas.20260127/00/analysis/ocean/gdas.t00z.mom6_increment.i006.nc"
./generate_zero_inc.py "${OVERLAY_DIR}/gdas.20260126/18/analysis/ocean/" "18"
./generate_zero_inc.py "${OVERLAY_DIR}/gdas.20260127/00/analysis/ocean" "00"


echo "Done!"
