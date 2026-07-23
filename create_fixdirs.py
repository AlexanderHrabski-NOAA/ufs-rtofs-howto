#!/usr/bin/env python3

import os
import sys
import shutil
import argparse
from pathlib import Path

# ==========================================
# 1. Helper Functions
# ==========================================
def force_symlink(src: str, dst: str) -> None:
    """Creates a symlink, overwriting the destination if it already exists."""
    dst_path = Path(dst)
    if dst_path.exists() or dst_path.is_symlink():
        dst_path.unlink()
    os.symlink(src, dst)

def link_tree(src_dir: Path, dest_dir: Path) -> None:
    """
    Replicates `cp -rsf src_dir/* dest_dir/`.
    Recursively recreates the directory structure and symlinks all files.
    """
    if not src_dir.exists():
        print(f"Warning: Source directory '{src_dir}' does not exist. Skipping.")
        return
    
    # shutil.copytree with a custom copy_function perfectly mimics `cp -rsf`
    shutil.copytree(
        src=src_dir, 
        dst=dest_dir, 
        copy_function=force_symlink, 
        dirs_exist_ok=True
    )

def link_file(src_file: Path, dest_file: Path) -> None:
    """
    Replicates `ln -sf src_file dest_file` after safely creating parent directories.
    """
    if not src_file.exists():
        print(f"Warning: Overlay source '{src_file}' does not exist. Skipping.")
        return
        
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    force_symlink(src_file, dest_file)

# ==========================================
# 2. Main Execution
# ==========================================
def main():
    # --- Argument Parsing & Validation ---
    parser = argparse.ArgumentParser(description="Build and overlay directory trees via symlinks.")
    parser.add_argument("destination", type=Path, help="Absolute or relative path to the destination directory")
    args = parser.parse_args()

    dest_dir = args.destination.resolve() # Gets absolute path

    if dest_dir.exists() and not dest_dir.is_dir():
        sys.exit(f"Error: '{dest_dir}' already exists but is a file, not a directory.")

    dest_dir.mkdir(parents=True, exist_ok=True)

    # --- Configuration ---
    OVERLAY_DIR = Path("/scratch4/NCEPDEV/marine/Alexander.Hrabski/FIX_highres_mom6_cic6")

    # Define base configurations in a dictionary to prevent repeated code
    bases = {
        "OROG": {
            "src": Path("/scratch3/NCEPDEV/global/role.glopara/fix/orog/20240917"),
            "dst": dest_dir / "FIXorog"
        },
        "CPL": {
            "src": Path("/scratch3/NCEPDEV/global/role.glopara/fix/cpl/20230526"),
            "dst": dest_dir / "FIXcpl"
        },
        "MOM": {
            "src": Path("/scratch3/NCEPDEV/global/role.glopara/fix/mom6/20250128"),
            "dst": dest_dir / "FIXmom"
        },
        "CICE": {
            "src": Path("/scratch3/NCEPDEV/global/role.glopara/fix/cice/20240416"),
            "dst": dest_dir / "FIXcice"
        }
    }

    # Define overlays in a list of instructions
    overlays = [
        {
            "name": "OROG directory",
            "type": "tree",
            "src": OVERLAY_DIR,
            "dst": dest_dir / "FIXorog"
        },
        {
            "name": "CPL grid_spec.nc",
            "type": "file",
            "src": OVERLAY_DIR / "C384" / "C384_mosaic.nc",
            "dst": dest_dir / "FIXcpl" / "aC384o008" / "grid_spec.nc"
        },
        {
            "name": "MOM zgrid interpolant",
            "type": "file",
            "src": OVERLAY_DIR / "MOM" / "interpolate_zgrid_30L.nc",
            "dst": dest_dir / "FIXmom" / "008" / "interpolate_zgrid_30L.nc"
        },
        {
            "name": "oceanda zgrid interpolant",
            "type": "file",
            "src": OVERLAY_DIR / "MOM" / "oceanda_zgrid_75L.nc",
            "dst": dest_dir / "FIXmom" / "008" / "oceanda_zgrid_75L.nc"
        },
        #{
        #    "name": "CICE specific files",
        #    "type": "file",
        #    "src": OVERLAY_DIR / "CICE" / "specific_cice_file.nc",
        #    "dst": dest_dir / "FIXcice" / "specific_cice_file.nc"
        #}
    ]

    # --- 3. Build the Base Tree ---
    print(f"Building base tree in {dest_dir}...")
    for name, paths in bases.items():
        print(f"  -> Linking {name} base...")
        link_tree(paths["src"], paths["dst"])

    # --- 4. Apply the Overlays ---
    print(f"\nApplying overlays from {OVERLAY_DIR}...")
    for overlay in overlays:
        print(f"  -> Overlaying {overlay['name']}...")
        if overlay["type"] == "tree":
            link_tree(overlay["src"], overlay["dst"])
        elif overlay["type"] == "file":
            link_file(overlay["src"], overlay["dst"])

    print(f"\nSuccess! Base trees and overlays linked to {dest_dir}.")

if __name__ == "__main__":
    main()
