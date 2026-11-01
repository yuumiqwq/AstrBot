"""Export an explicitly reviewed plugin view into an immutable static release."""

import argparse
import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

ASSET_SUFFIXES = {
    ".js",
    ".mjs",
    ".css",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".gif",
    ".jpg",
    ".jpeg",
    ".png",
    ".svg",
    ".webp",
    ".ico",
    ".wasm",
    ".glb",
    ".gltf",
    ".bin",
}
LICENSE_PATHS = {
    "assets/LUCIDE-LICENSE.txt",
    "assets/THREE-LICENSE.txt",
    "assets/NOTICE.txt",
    "assets/MiSans-license.pdf",
}


def export_assets(args: argparse.Namespace) -> dict:
    """Create local snapshots, a candidate manifest, and a cloud upload archive.

    Args:
        args: Reviewed view, dashboard, data, and output paths from the CLI.

    Returns:
        Release identity and paths to the generated deployment artifacts.

    Raises:
        ValueError: A source contains symlinks or a published file differs.
    """
    source = args.view_dir.resolve(strict=True)
    data_dir = args.data_dir.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assets = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic link in the reviewed view: {path}")
        if path.is_file() and (
            path.suffix.lower() in ASSET_SUFFIXES
            or path.relative_to(source).as_posix() in LICENSE_PATHS
        ):
            assets.append(
                {
                    "path": path.relative_to(source).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    if not assets:
        raise ValueError("The reviewed view contains no static assets")
    assets.sort(key=lambda asset: asset["path"])
    canonical = json.dumps(assets, ensure_ascii=False, separators=(",", ":"))
    release = hashlib.sha256(canonical.encode()).hexdigest()
    snapshot = data_dir / "fish-static" / release
    snapshot.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        original = source / asset["path"]
        destination = snapshot / asset["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if (
                destination.is_symlink()
                or hashlib.sha256(destination.read_bytes()).hexdigest()
                != asset["sha256"]
            ):
                raise ValueError(f"Published asset changed: {destination}")
        else:
            shutil.copyfile(original, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError(f"Source changed during export: {original}")
        if destination.suffix in {".js", ".mjs", ".css", ".svg"}:
            destination.with_suffix(destination.suffix + ".gz").write_bytes(
                gzip.compress(destination.read_bytes(), compresslevel=9, mtime=0)
            )

    manifest_path = data_dir / "fish-static" / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text("utf-8"))
        if manifest_path.exists()
        else {"version": 1, "views": {}}
    )
    manifest["views"][f"{args.plugin_id}/{args.view_name}"] = {
        "release": release,
        "files": [asset["path"] for asset in assets],
    }
    candidate = output / "manifest.candidate.json"
    candidate.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    inventory = {"release": release, "plugin_assets": assets, "dashboard_assets": []}
    archive = output / f"fish-static-{release}.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        for path in sorted(snapshot.rglob("*")):
            if path.is_file():
                package.add(
                    path,
                    arcname=f"releases/{release}/{path.relative_to(snapshot).as_posix()}",
                )
        if args.dashboard_dist:
            dashboard = args.dashboard_dist.resolve(strict=True) / "assets"
            for path in sorted(dashboard.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"Symbolic link in dashboard assets: {path}")
                if path.is_file() and path.suffix.lower() in ASSET_SUFFIXES:
                    relative = path.relative_to(dashboard).as_posix()
                    inventory["dashboard_assets"].append(
                        {
                            "path": relative,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                    )
                    package.add(
                        path, arcname=f"staging/{release}/astrbot/assets/{relative}"
                    )
    inventory_path = output / "inventory.json"
    inventory_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = {
        "release": release,
        "snapshot": str(snapshot),
        "candidate_manifest": str(candidate),
        "inventory": str(inventory_path),
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "plugin_files": len(assets),
        "dashboard_files": len(inventory["dashboard_assets"]),
    }
    (output / "release.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view-dir", required=True, type=Path)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--view-name", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--dashboard-dist", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    print(json.dumps(export_assets(parser.parse_args()), ensure_ascii=False))
