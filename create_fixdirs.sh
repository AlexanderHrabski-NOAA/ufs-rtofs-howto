#!/usr/bin/env bash

set -euo pipefail

# ==========================================
# 1. Argument Parsing & Validation
# ==========================================
if [[ $# -ne 1 ]]; then
    echo "Error: Invalid number of arguments."
    echo "Usage: $0 <destination_directory>"
    echo "Example: $0 /absolute/path/to/destination"
    exit 1
fi

if [[ -e "$1" && ! -d "$1" ]]; then
    echo "Error: '$1' already exists but is a file, not a directory."
    exit 1
fi

# ==========================================
# 2. Configuration
# ==========================================
BASE_DIR_OROG="/scratch3/NCEPDEV/global/role.glopara/fix/orog/20240917"
BASE_DIR_CPL="/scratch3/NCEPDEV/global/role.glopara/fix/cpl/20230526"

# TODO: Update these with the actual base directory paths for MOM and CICE
BASE_DIR_MOM="/scratch3/NCEPDEV/global/role.glopara/fix/mom6/20250128" 
BASE_DIR_CICE="/scratch3/NCEPDEV/global/role.glopara/fix/cice/20240416"

OVERLAY_DIR="/scratch4/NCEPDEV/marine/Alexander.Hrabski/FIX_highres_mom6_cic6"

# Create the root destination directory, get absolute path, and set a base variable
mkdir -p "$1"
DEST_DIR="$(realpath "$1")"

# Define subdirectories
DEST_DIR_OROG="$DEST_DIR/FIXorog"
DEST_DIR_CPL="$DEST_DIR/FIXcpl"
DEST_DIR_MOM="$DEST_DIR/FIXmom"
DEST_DIR_CICE="$DEST_DIR/FIXcice"

# Create the specific subdirectories required by the cp commands
mkdir -p "$DEST_DIR_OROG" "$DEST_DIR_CPL" "$DEST_DIR_MOM" "$DEST_DIR_CICE"

# Set overlay sources and targets for OROG and CPL
OVERLAY_TARGET_OROG="$DEST_DIR_OROG"

# hardcoded resolution
OVERLAY_SOURCE_CPL="$OVERLAY_DIR/384/C384_mosaic.nc"
OVERLAY_TARGET_CPL="$DEST_DIR_CPL/aC384o008/grid_spec.nc"

# TODO: Update these with the actual specific file paths you need to overlay for MOM and CICE
OVERLAY_SOURCE_MOM="$OVERLAY_DIR/MOM/specific_mom_file.nc"
OVERLAY_TARGET_MOM="$DEST_DIR_MOM/specific_mom_file.nc"

OVERLAY_SOURCE_CICE="$OVERLAY_DIR/CICE/specific_cice_file.nc"
OVERLAY_TARGET_CICE="$DEST_DIR_CICE/specific_cice_file.nc"

# ==========================================
# 3. Build the Base Tree
# ==========================================
echo "Building base tree in $DEST_DIR..."

echo "  -> Linking OROG base..."
# -r: recursive, -s: symbolic link, -f: force overwrite
cp -rsf "$BASE_DIR_OROG"/* "$DEST_DIR_OROG/"

echo "  -> Linking CPL base..."
cp -rsf "$BASE_DIR_CPL"/* "$DEST_DIR_CPL/"

echo "  -> Linking MOM base..."
cp -rsf "$BASE_DIR_MOM"/* "$DEST_DIR_MOM/"

echo "  -> Linking CICE base..."
cp -rsf "$BASE_DIR_CICE"/* "$DEST_DIR_CICE/"

# ==========================================
# 4. Apply the Overlay
# ==========================================
echo "Applying overlays from $OVERLAY_DIR..."

echo "  -> Overlaying OROG files..."
# Apply the OROG overlay directory
cp -rsf "$OVERLAY_DIR"/* "$OVERLAY_TARGET_OROG/"

echo "  -> Overlaying CPL grid_spec.nc..."
# Safely create the parent directory for the CPL file, then link it
mkdir -p "$(dirname "$OVERLAY_TARGET_CPL")"
ln -sf "$OVERLAY_SOURCE_CPL" "$OVERLAY_TARGET_CPL"

echo "  -> Overlaying MOM specific files..."
# Safely create the parent directory for the MOM file, then link it
mkdir -p "$(dirname "$OVERLAY_TARGET_MOM")"
ln -sf "$OVERLAY_SOURCE_MOM" "$OVERLAY_TARGET_MOM"

echo "  -> Overlaying CICE specific files..."
# Safely create the parent directory for the CICE file, then link it
mkdir -p "$(dirname "$OVERLAY_TARGET_CICE")"
ln -sf "$OVERLAY_SOURCE_CICE" "$OVERLAY_TARGET_CICE"

echo "Success! Base trees and overlays linked to $DEST_DIR."
