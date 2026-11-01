"""Verify immutable asset publication without exposing dynamic plugin documents."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from starlette.requests import Request

import astrbot.api  # noqa: F401
from astrbot.dashboard.api.plugins import _plugin_page_payload_response
from astrbot.dashboard.api.static_files import serve_published_plugin_asset
from astrbot.dashboard.services.plugin_page_service import PluginPage, PluginPageService
from astrbot.dashboard.services.published_plugin_assets import PublishedPluginAssets


class PublishedAssetsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = "a" * 64
        self.snapshot = self.root / self.release
        self.snapshot.mkdir()
        self.files = ["app.js", "bundle.css", "fonts/chinese.woff2", "assets/room.gif"]
        for name in self.files:
            path = self.snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"published bytes")
        self.manifest = {
            "version": 1,
            "views": {"example/house": {"release": self.release, "files": self.files}},
        }
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        self.assets = PublishedPluginAssets(self.root)

    def test_rewrites_templates_and_queries_but_keeps_documents_and_api(self):
        source = (
            '<link href="bundle.css?v=1&amp;theme=2">'
            '<template><script src="app.js?v=2"></script></template>'
            '<template data-room-boot-gif="assets/room.gif"></template>'
            '<img src="assets/room.gif">'
            '<a href="other.html">Page</a>'
            '<script src="/api/plugin/page/bridge-sdk.js"></script>'
            '<img src="https://example.com/image.png">'
            '<a href="#life">Life</a>'
        )
        actual = self.assets.rewrite_html(source, "example", "house", "index.html")
        self.assertIn(f"/fish-static/{self.release}/bundle.css?v=1&amp;theme=2", actual)
        self.assertIn(f"/fish-static/{self.release}/app.js?v=2", actual)
        self.assertEqual(
            actual.count(f"/fish-static/{self.release}/assets/room.gif"), 2
        )
        self.assertIn('href="other.html"', actual)
        self.assertIn('src="/api/plugin/page/bridge-sdk.js"', actual)
        self.assertIn('src="https://example.com/image.png"', actual)
        self.assertIn('href="#life"', actual)

    def test_nested_document_relative_reference_uses_same_snapshot(self):
        result = self.assets.rewrite_html(
            '<script src="../app.js"></script>', "example", "house", "nested/page.html"
        )
        self.assertIn(f"/fish-static/{self.release}/app.js", result)

    def test_unpublished_view_or_file_keeps_authenticated_reference(self):
        source = '<script src="app.js"></script><img src="private.png">'
        self.assertEqual(self.assets.rewrite_html(source, "other", "house", ""), source)
        actual = self.assets.rewrite_html(source, "example", "house", "")
        self.assertIn('src="private.png"', actual)

    def test_missing_or_invalid_manifest_retains_original_delivery(self):
        source = '<script src="app.js"></script>'
        manifest = self.root / "manifest.json"
        for content in ("{invalid", "[]", '{"version": 2}', '{"version":1,"views":[]}'):
            manifest.write_text(content)
            self.assertEqual(
                self.assets.rewrite_html(source, "example", "house", ""), source
            )
        manifest.unlink()
        self.assertEqual(
            self.assets.rewrite_html(source, "example", "house", ""), source
        )

    def test_release_switch_changes_url_and_preserves_previous_snapshot(self):
        old_url = self.assets.asset_url("example", "house", "app.js")
        release = "b" * 64
        (self.root / release).mkdir()
        (self.root / release / "app.js").write_bytes(b"new release")
        self.manifest["views"]["example/house"]["release"] = release
        self.manifest["views"]["example/house"]["files"] = ["app.js"]
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        self.assertNotEqual(
            self.assets.asset_url("example", "house", "app.js"), old_url
        )
        self.assertEqual(
            self.assets.resolve_file(self.release, "app.js"), self.snapshot / "app.js"
        )

    def test_documents_hidden_files_and_traversal_cannot_be_published(self):
        for name in ("index.html", "secret.json", ".hidden.js"):
            (self.snapshot / name).write_text("private")
        for path in (
            "index.html",
            "secret.json",
            ".hidden.js",
            "../app.js",
            "/app.js",
            "..\\app.js",
        ):
            with self.subTest(path=path):
                self.assertIsNone(self.assets.resolve_file(self.release, path))
        self.assertIsNone(self.assets.resolve_file("../", "app.js"))

    def test_symlink_cannot_publish_files_outside_snapshot(self):
        outside = self.root / "outside.js"
        outside.write_text("private")
        try:
            (self.snapshot / "escape.js").symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks unavailable")
        self.assertIsNone(self.assets.resolve_file(self.release, "escape.js"))

    def test_only_named_distribution_licenses_are_public_documents(self):
        license_path = self.snapshot / "assets" / "MiSans-license.pdf"
        license_path.write_bytes(b"license")
        private_path = self.snapshot / "assets" / "notes.pdf"
        private_path.write_bytes(b"private")
        self.assertEqual(
            self.assets.resolve_file(self.release, "assets/MiSans-license.pdf"),
            license_path,
        )
        self.assertIsNone(self.assets.resolve_file(self.release, "assets/notes.pdf"))

    async def test_public_local_fallback_has_immutable_cache_and_font_cors(self):
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        with patch("astrbot.dashboard.api.static_files.published_assets", self.assets):
            response = await serve_published_plugin_asset(
                request, self.release, "fonts/chinese.woff2"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers["cache-control"], "public, max-age=31536000, immutable"
            )
            self.assertEqual(response.headers["access-control-allow-origin"], "*")
            self.assertEqual(
                response.headers["cross-origin-resource-policy"], "cross-origin"
            )
            missing = await serve_published_plugin_asset(
                request, self.release, "secret.json"
            )
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.headers["cache-control"], "no-store")

    async def test_authenticated_html_keeps_bridge_and_uncached_dynamic_context(self):
        html_file = self.root / "index.html"
        html_file.write_text(
            '<html><head></head><body><script src="app.js"></script></body></html>'
        )
        service = PluginPageService.__new__(PluginPageService)
        service.published_assets = self.assets
        service.get_plugin_metadata_by_name = Mock(
            return_value=SimpleNamespace(activated=True)
        )
        service.get_plugin_page = AsyncMock(return_value=PluginPage("house", "House"))
        service.resolve_plugin_page_file = AsyncMock(return_value=html_file)
        payload = await service.serve_page_content(
            plugin_name="example",
            view_name="house",
            asset_path="",
            asset_token="token",
            username="admin",
            locale="en-US",
            theme="dark",
        )
        response = _plugin_page_payload_response(payload)
        self.assertIn(f"/fish-static/{self.release}/app.js", payload.content)
        self.assertIn("/api/plugin/page/bridge-sdk.js?", payload.content)
        self.assertIn("asset_token=token", payload.content)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIsNone(payload.redirect_url)

    async def test_authenticated_old_asset_redirects_to_published_snapshot(self):
        service = PluginPageService.__new__(PluginPageService)
        service.published_assets = self.assets
        service.get_plugin_metadata_by_name = Mock(
            return_value=SimpleNamespace(activated=True)
        )
        service.get_plugin_page = AsyncMock(return_value=PluginPage("house", "House"))
        service.resolve_plugin_page_file = AsyncMock(
            return_value=self.snapshot / "app.js"
        )
        payload = await service.serve_page_content(
            plugin_name="example",
            view_name="house",
            asset_path="app.js",
            asset_token="token",
            username="admin",
            locale="en-US",
            theme=None,
        )
        response = _plugin_page_payload_response(payload)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"], f"/fish-static/{self.release}/app.js"
        )
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
