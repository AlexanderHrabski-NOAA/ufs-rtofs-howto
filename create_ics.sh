#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# 1. Validate arguments
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <target_dir> <dest_dir>"
    echo "  <target_dir>: Base RTOFS directory (e.g., /path/to/rtofs.20251225)."
    echo "  <dest_dir>: Directory containing the wrong ICs to be replaced (e.g., cp4.03-parallel-3dvar)."
    exit 1
fi

TARGET_DIR="$1"
DEST_DIR="$2"

# 2. Verify directories exist
if [ ! -d "$TARGET_DIR" ] || [ ! -d "$DEST_DIR" ]; then
    echo "Error: Both target and destination directories must exist."
    exit 1
fi

# 3. Helper function to process individual links
# Usage: replace_link "target_relative_path" "destination_relative_path"
replace_link() {
    local target_file="${TARGET_DIR}/$1"
    local dest_file="${DEST_DIR}/$2"

    # Ensure destination parent directory exists
    mkdir -p "$(dirname "$dest_file")"

    if [ ! -e "$target_file" ]; then
        echo "Warning: Target '$target_file' not found. Skipping."
        return
    fi

    # The -f flag forces the overwrite of the existing destination file/link
    ln -sf "$(realpath "$target_file")" "$dest_file"
    echo "Linked: $2 -> $1"
}

echo "Starting IC link replacement (Force Overwrite)..."
echo "Target Base (Correct ICs): $TARGET_DIR"
echo "Dest Base (To be replaced): $DEST_DIR"
echo "----------------------------------------"

# --- A. Ice and Med Files ---
replace_link "RESTART/iced.2025-12-25-00000.nc" "gdas.20260126/18/analysis/ice/20260126.150000.analysis.cice_model.res.nc"
replace_link "RESTART/iced.2025-12-25-00000.nc" "gdas.20260127/00/analysis/ice/20260126.210000.analysis.cice_model.res.nc"
replace_link "RESTART/iced.2025-12-25-00000.nc" "gdas.20260127/00/model/ice/restart/20260127.030000.cice_model.res.nc"

replace_link "RESTART/datm.gfs.cpl.r.2025-12-25-00000.nc" "gdas.20260126/18/model/med/restart/20260126.210000.ufs.cpld.cpl.r.nc"

# --- B. MOM6 Ocean Files ---
# Base MOM file
replace_link "RESTART/20251229.000000.MOM.res.nc" "gdas.20260126/18/model/ocean/restart/20260126.210000.MOM.res.nc"

# Loop dynamically through MOM files 1 to 16
for i in {1..16}; do
    replace_link "RESTART/20251229.000000.MOM.res_${i}.nc" "gdas.20260126/18/model/ocean/restart/20260126.210000.MOM.res_${i}.nc"
done

echo "----------------------------------------"

echo "Fixing CICE restart date..."
./fix_cice_restart_date.py "${TARGET_DIR}/RESTART/iced.2025-12-25-00000.nc" --cycle 2026012700

echo "Generating zero MOM6 increment..."
./generate_zero_inc.py "${DEST_DIR}/gdas.20260126/18/analysis/ocean/" "18"
./generate_zero_inc.py "${DEST_DIR}/gdas.20260127/00/analysis/ocean" "00"


echo "Done!"
