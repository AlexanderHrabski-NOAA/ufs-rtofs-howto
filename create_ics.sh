#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# 1. Validate arguments
if [ "$#" -ne 5 ]; then
    echo "Usage: $0 <source_dir> <base_dir> <overlay_dir> <orog_dir_base> <orog_dir_run>"
    echo "  <source_dir>:  RTOFS directory holding the correct restarts (e.g., /path/to/rtofs.20251225)."
    echo "  <base_dir>:    Directory providing the directory structure and atmospheric restarts"
    echo "                 (e.g., cp4.03-parallel-3dvar)."
    echo "  <overlay_dir>: New location. A mirror of <base_dir> is built here (directories recreated,"
    echo "                 files symlinked), then files from <source_dir> are overlaid on top of it."
    echo "  <orog_dir_base>: orog/C384 directory matching the base run's 25 km ocean mask"
    echo "                   (e.g. the system fix/orog/20240917/C384)."
    echo "  <orog_dir_run>:  orog/C384 directory with the 8 km ocean mask and its sfc/ climatology"
    echo "                   (e.g. FIX_highres_mom6_cic6/OROG/C384)."
    echo "Optional: SFC_MAX_DONOR_KM (default 250) caps the donor distance in step E. Choose it"
    echo "from './fix_sfc_masks.py report' (see sfc_mask_method.md)."
    exit 1
fi

SOURCE_DIR="$1"
BASE_DIR="$2"
OVERLAY_DIR="$3"
OROG_DIR_BASE="$4"
OROG_DIR_RUN="$5"

# 2. Verify the input directories exist
if [ ! -d "$SOURCE_DIR" ] || [ ! -d "$BASE_DIR" ]; then
    echo "Error: Both <source_dir> and <base_dir> must exist."
    exit 1
fi

SOURCE_DIR="$(realpath "$SOURCE_DIR")"
BASE_DIR="$(realpath "$BASE_DIR")"

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
echo "Base   (structure + atm) : $BASE_DIR"
echo "Overlay (built here)     : $OVERLAY_DIR"
echo "----------------------------------------"

# --- A. Build the mirror of the base ---
mirror_base
echo "----------------------------------------"

# --- B. CICE restart handling ---
# The CICE restart is copied (not linked) from the source into the overlay so that an
# untouched original is preserved in the source. The retiming below then modifies only
# the overlay copy. The analysis files point at that same copy so they pick up the
# modified date.
CICE_RESTART_SRC="RESTART/iced.2025-12-25-00000.nc"
CICE_RESTART_OVERLAY="gdas.20260127/00/model/ice/restart/20260127.030000.cice_model.res.nc"

copy_from_source "$CICE_RESTART_SRC" "$CICE_RESTART_OVERLAY"
link_within_overlay "$CICE_RESTART_OVERLAY" "gdas.20260126/18/analysis/ice/20260126.150000.analysis.cice_model.res.nc"
link_within_overlay "$CICE_RESTART_OVERLAY" "gdas.20260127/00/analysis/ice/20260126.210000.analysis.cice_model.res.nc"

# --- C. Mediator file ---
# Neither source holds a usable mediator restart (the DATM one lacks coupling
# fields, the CATM one is too coarse), so the mediator must cold start. Drop the
# mediator restarts the base carried, then link the source (DATM) mediator under
# the 18Z cycle's name -- same name as in the base, up to a .exclude suffix that
# keeps the driver from picking it up while leaving the intended file identifiable.
drop_mirrored_med_restarts
replace_link "RESTART/datm.gfs.cpl.r.2025-12-25-00000.nc" \
    "gdas.20260126/18/model/med/restart/20260126.210000.ufs.cpld.cpl.r.nc.exclude"

# --- D. MOM6 Ocean Files ---
# Base MOM file
replace_link "RESTART/20251229.000000.MOM.res.nc" "gdas.20260126/18/model/ocean/restart/20260126.210000.MOM.res.nc"

# Loop dynamically through MOM files 1 to 16
for i in {1..16}; do
    replace_link "RESTART/20251229.000000.MOM.res_${i}.nc" "gdas.20260126/18/model/ocean/restart/20260126.210000.MOM.res_${i}.nc"
done

echo "----------------------------------------"

echo "Fixing CICE restart date (on the overlay copy; source original untouched)..."
./fix_cice_restart_date.py "${OVERLAY_DIR}/${CICE_RESTART_OVERLAY}" --cycle 2026012700

echo "Generating zero MOM6 increment (into the overlay)..."
# If the mirror linked an increment back to the base, drop the link first so the
# generator writes a real file in the overlay instead of clobbering the base original.
rm -f "${OVERLAY_DIR}/gdas.20260126/18/analysis/ocean/gdas.t18z.mom6_increment.i006.nc"
rm -f "${OVERLAY_DIR}/gdas.20260127/00/analysis/ocean/gdas.t00z.mom6_increment.i006.nc"
./generate_zero_inc.py "${OVERLAY_DIR}/gdas.20260126/18/analysis/ocean/" "18"
./generate_zero_inc.py "${OVERLAY_DIR}/gdas.20260127/00/analysis/ocean" "00"

echo "----------------------------------------"

# --- E. Atmosphere surface restarts ---
# The surface restarts come from the base run, so their land/sea mask follows the
# 25 km ocean (mx025) while this run uses the 8 km mask (mx008); global_cycle aborts
# on the mismatch. Cells that gain land or water copy that side from one matching
# neighbour (see sfc_mask_method.md). The mirrored links are replaced by real
# files; the base run is not modified.
echo "Reconciling atmosphere surface restarts with the 8 km land/sea mask..."
./fix_sfc_masks.py apply "$OVERLAY_DIR" "$OROG_DIR_BASE" "$OROG_DIR_RUN" \
    --max-distance-km "${SFC_MAX_DONOR_KM:-250}"


echo "Done!"
