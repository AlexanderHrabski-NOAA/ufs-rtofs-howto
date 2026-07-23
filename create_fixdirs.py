#!/usr/bin/env python3

import os
import sys
import shutil
import argparse
import yaml
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
    """Replicates `cp -rsf src_dir/* dest_dir/`."""
    if not src_dir.exists():
        print(f"Warning: Source directory '{src_dir}' does not exist. Skipping.")
        return
    
    shutil.copytree(
        src=src_dir, 
        dst=dest_dir, 
        copy_function=force_symlink, 
        dirs_exist_ok=True
    )

def link_file(src_file: Path, dest_file: Path) -> None:
    """Replicates `ln -sf src_file dest_file`."""
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
    parser.add_argument("config", type=Path, help="Path to the YAML configuration file")
    parser.add_argument("destination", type=Path, help="Path to the destination directory")
    args = parser.parse_args()

    # Load YAML config
    if not args.config.exists():
        sys.exit(f"Error: Configuration file '{args.config}' not found.")
        
    with open(args.config, 'r') as f:
        try:
            config = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            sys.exit(f"Error parsing YAML: {exc}")

    dest_dir = args.destination.resolve()
    if dest_dir.exists() and not dest_dir.is_dir():
        sys.exit(f"Error: '{dest_dir}' already exists but is a file, not a directory.")
    dest_dir.mkdir(parents=True, exist_ok=True)

    overlay_base = Path(config.get("overlay_dir", ""))

    # --- 3. Build the Base Tree ---
    print(f"Building base tree in {dest_dir}...")
    bases = config.get("bases", {})
    for name, paths in bases.items():
        print(f"  -> Linking {name} base...")
        src = Path(paths["src"])
        dst = dest_dir / paths["dst"]
        
        # Make sure parent base directories exist before linking
        dst.mkdir(parents=True, exist_ok=True)
        link_tree(src, dst)

    # --- 4. Apply the Overlays ---
    print("\nApplying overlays...")
    overlays = config.get("overlays", [])
    for overlay in overlays:
        print(f"  -> Overlaying {overlay['name']}...")
        
        # Resolve source path
        src = Path(overlay["src"])
        if not src.is_absolute() and overlay_base.name:
            src = (overlay_base / src).resolve()

        # Resolve destination path
        dst = dest_dir / overlay["dst"]

        if overlay["type"] == "tree":
            link_tree(src, dst)
        elif overlay["type"] == "file":
            link_file(src, dst)
        else:
            print(f"  [!] Unknown overlay type '{overlay['type']}'. Skipping.")

    print(f"\nSuccess! Base trees and overlays linked to {dest_dir}.")

if __name__ == "__main__":
    main()
