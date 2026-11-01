"""Check immutable exports and the explicit public asset boundary."""

import argparse
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "export_plugin_static_assets",
    Path(__file__).parents[1] / "scripts" / "export_plugin_static_assets.py",
)
assert SPEC is not None and SPEC.loader is not None
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def test_export_keeps_dynamic_documents_and_settings_outside_public_release(tmp_path):
    view = tmp_path / "view"
    data = tmp_path / "data"
    view.mkdir()
    data.mkdir()
    (view / "app.js").write_text("export const version = 1;")
    (view / "index.html").write_text("<html>authenticated entry</html>")
    (view / "settings.json").write_text('{"secret":"not-an-asset"}')
    (view / "app.js.map").write_text("source map")
    args = argparse.Namespace(
        view_dir=view,
        data_dir=data,
        output=tmp_path / "output",
        plugin_id="example",
        view_name="home",
        dashboard_dist=None,
    )
    result = exporter.export_assets(args)
    manifest = json.loads(Path(result["candidate_manifest"]).read_text())
    assert manifest["views"]["example/home"]["files"] == ["app.js"]
    assert not (data / "fish-static" / "manifest.json").exists()
    with tarfile.open(result["archive"]) as archive:
        assert set(archive.getnames()) == {
            f"releases/{result['release']}/app.js",
            f"releases/{result['release']}/app.js.gz",
        }


def test_new_content_gets_new_release_and_preserves_old_snapshot(tmp_path):
    view = tmp_path / "view"
    data = tmp_path / "data"
    view.mkdir()
    data.mkdir()
    script = view / "app.js"
    script.write_text("version one")
    args = argparse.Namespace(
        view_dir=view,
        data_dir=data,
        output=tmp_path / "output",
        plugin_id="example",
        view_name="home",
        dashboard_dist=None,
    )
    first = exporter.export_assets(args)
    repeated = exporter.export_assets(args)
    assert first["release"] == repeated["release"]
    script.write_text("version two")
    second = exporter.export_assets(args)
    assert first["release"] != second["release"]
    assert (Path(first["snapshot"]) / "app.js").read_text() == "version one"


def test_export_rejects_symlink_to_nonpublic_file(tmp_path):
    view = tmp_path / "view"
    data = tmp_path / "data"
    view.mkdir()
    data.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("private content")
    try:
        (view / "app.js").symlink_to(private)
    except OSError:
        pytest.skip("This system does not permit symbolic links")
    args = argparse.Namespace(
        view_dir=view,
        data_dir=data,
        output=tmp_path / "output",
        plugin_id="example",
        view_name="home",
        dashboard_dist=None,
    )
    with pytest.raises(ValueError, match="Symbolic link"):
        exporter.export_assets(args)
