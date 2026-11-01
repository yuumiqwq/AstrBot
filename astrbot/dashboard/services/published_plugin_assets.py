"""Serve explicitly published plugin assets under immutable, token-free URLs."""

from __future__ import annotations

import html
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from astrbot.core.utils.astrbot_path import get_astrbot_data_path

_RELEASE_RE = re.compile(r"[0-9a-f]{64}")
_ASSET_ATTR_RE = re.compile(
    r"(?P<attr>\bsrc|\bhref|\bdata-room-boot-gif)\s*=\s*"
    r"(?P<quote>[\"\'])(?P<url>.*?)(?P=quote)",
    re.IGNORECASE,
)
PUBLIC_ASSET_SUFFIXES = frozenset(
    {
        ".js",
        ".mjs",
        ".css",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".avif",
        ".svg",
        ".ico",
        ".glb",
        ".bin",
        ".wasm",
    }
)
PUBLIC_ASSET_LICENSES = frozenset(
    {
        "assets/LUCIDE-LICENSE.txt",
        "assets/THREE-LICENSE.txt",
        "assets/NOTICE.txt",
        "assets/MiSans-license.pdf",
    }
)


class PublishedPluginAssets:
    """Read an operator-published manifest without exposing plugin directories."""

    def __init__(self, root: Path | None = None):
        self.root = root or Path(get_astrbot_data_path()) / "fish-static"
        self._manifest_stamp: tuple[int, int] | None = None
        self._views: dict = {}

    def view_release(self, plugin_name: str, view_name: str) -> dict:
        """Return the configured release, refreshing only when the manifest changes.

        Args:
            plugin_name: Registered plugin name.
            view_name: Registered view name.

        Returns:
            Release metadata, or an empty mapping for unpublished views.
        """
        manifest = self.root / "manifest.json"
        try:
            stat = manifest.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp != self._manifest_stamp:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                self._views = data.get("views", {}) if data.get("version") == 1 else {}
                if not isinstance(self._views, dict):
                    self._views = {}
                self._manifest_stamp = stamp
        except (OSError, ValueError, AttributeError):
            self._manifest_stamp = None
            self._views = {}
        release = self._views.get(f"{plugin_name}/{view_name}", {})
        return release if isinstance(release, dict) else {}

    def resolve_file(self, release: str, asset_path: str) -> Path | None:
        """Resolve a public asset inside a dedicated immutable snapshot.

        Args:
            release: SHA-256 identifier assigned when publishing the snapshot.
            asset_path: POSIX path relative to that snapshot.

        Returns:
            Published file path, excluding documents and data files.
        """
        if not _RELEASE_RE.fullmatch(release) or not asset_path:
            return None
        parts = asset_path.replace("\\", "/").split("/")
        if asset_path.startswith(("/", "\\")) or any(
            not part or part.startswith(".") for part in parts
        ):
            return None
        if (
            Path(asset_path).suffix.lower() not in PUBLIC_ASSET_SUFFIXES
            and asset_path not in PUBLIC_ASSET_LICENSES
        ):
            return None
        root = (self.root / release).resolve()
        if not root.is_relative_to(self.root.resolve()):
            return None
        target = (root / asset_path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        return target

    def asset_url(
        self, plugin_name: str, view_name: str, asset_path: str
    ) -> str | None:
        """Find a stable URL only for files listed in the view's publication.

        Args:
            plugin_name: Registered plugin name.
            view_name: Registered view name.
            asset_path: Normalized path relative to the view directory.

        Returns:
            Same-origin public URL, or None to retain authenticated delivery.
        """
        entry = self.view_release(plugin_name, view_name)
        release, files = entry.get("release"), entry.get("files")
        if not isinstance(release, str) or not isinstance(files, list):
            return None
        if asset_path not in files or self.resolve_file(release, asset_path) is None:
            return None
        encoded = "/".join(quote(part, safe="") for part in asset_path.split("/"))
        return f"/fish-static/{release}/{encoded}"

    def frame_policy(self, plugin_name: str, view_name: str) -> dict:
        """Return explicitly configured origins for an isolated view document.

        Args:
            plugin_name: Registered plugin name.
            view_name: Registered view name.

        Returns:
            HTTPS document origin and exact parent origins, or an empty mapping.
        """
        entry = self.view_release(plugin_name, view_name)
        document_origin = entry.get("document_origin")
        parent_origins = entry.get("parent_origins")
        if not isinstance(document_origin, str) or not isinstance(parent_origins, list):
            return {}
        origins = [document_origin, *parent_origins]
        if not parent_origins:
            return {}
        for origin in origins:
            if not isinstance(origin, str):
                return {}
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError:
                return {}
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in parsed.netloc
                or (port is not None and not 1 <= port <= 65535)
            ):
                return {}
        if document_origin in parent_origins:
            return {}
        return {
            "document_origin": document_origin,
            "parent_origins": parent_origins.copy(),
        }

    def rewrite_html(
        self,
        content: str,
        plugin_name: str,
        view_name: str,
        document_path: str,
    ) -> str:
        """Retarget published resources while leaving document and API URLs intact.

        Args:
            content: Authenticated HTML, including any lazy template elements.
            plugin_name: Registered plugin name.
            view_name: Registered view name.
            document_path: Relative path of the current HTML document.

        Returns:
            HTML whose published static references use stable versioned URLs.
        """
        if not self.view_release(plugin_name, view_name):
            return content

        def replace(match: re.Match[str]) -> str:
            raw = html.unescape(match.group("url"))
            parsed = urlsplit(raw)
            if parsed.scheme or parsed.netloc or raw.startswith(("/", "#")):
                return match.group(0)
            path = posixpath.normpath(
                posixpath.join(posixpath.dirname(document_path), unquote(parsed.path))
            )
            replacement = self.asset_url(plugin_name, view_name, path)
            if replacement is None:
                return match.group(0)
            replacement = urlunsplit(
                ("", "", replacement, parsed.query, parsed.fragment)
            )
            return (
                f"{match.group('attr')}={match.group('quote')}"
                f"{html.escape(replacement, quote=True)}{match.group('quote')}"
            )

        return _ASSET_ATTR_RE.sub(replace, content)
