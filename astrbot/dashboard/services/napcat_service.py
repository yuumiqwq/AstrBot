"""Dashboard access to the configured local NapCat login session."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx

from astrbot.dashboard.responses import ApiError


class NapCatService:
    def __init__(self) -> None:
        self.base_url = os.environ.get("NAPCAT_WEBUI_URL", "").rstrip("/")
        self.config_path = os.environ.get("NAPCAT_WEBUI_CONFIG", "")
        self.credential = ""
        self.lock = asyncio.Lock()

    async def _call(self, path: str) -> dict:
        """Call one fixed NapCat operation using server-held credentials.

        Args:
            path: Login endpoint selected by this service.

        Returns:
            The operation's data object.

        Raises:
            ApiError: The configured service cannot complete the operation.
        """
        if not self.base_url or not self.config_path:
            raise ApiError("QQ 登录服务尚未配置", status_code=503)
        async with self.lock:
            try:
                async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                    for attempt in range(2):
                        if not self.credential:
                            config = json.loads(
                                Path(self.config_path).read_text(encoding="utf-8-sig")
                            )
                            login = await client.post(
                                self.base_url + "/api/auth/login",
                                json={
                                    "hash": hashlib.sha256(
                                        (config["token"] + ".napcat").encode()
                                    ).hexdigest()
                                },
                            )
                            login.raise_for_status()
                            self.credential = login.json()["data"]["Credential"]
                        response = await client.post(
                            self.base_url + "/api/QQLogin/" + path,
                            json={},
                            headers={"Authorization": "Bearer " + self.credential},
                        )
                        if response.status_code in (401, 403) and attempt == 0:
                            self.credential = ""
                            continue
                        response.raise_for_status()
                        result = response.json()
                        if result.get("message") == "Unauthorized" and attempt == 0:
                            self.credential = ""
                            continue
                        if result.get("code") != 0:
                            raise ApiError(
                                "QQ 登录服务读取失败，请重试", status_code=502
                            )
                        return result.get("data") or {}
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
                raise ApiError("QQ 登录服务连接失败，请重试", status_code=503) from exc
        raise ApiError("QQ 登录服务认证失败，请重试", status_code=502)

    async def status(self) -> dict:
        """Read login state without refreshing or terminating the QQ session.

        Returns:
            Configuration, connectivity and public account state.
        """
        if not self.base_url or not self.config_path:
            return {"configured": False, "online": False, "phase": "unconfigured"}
        status = await self._call("CheckLoginStatus")
        online = status.get("isLogin") is True
        info = await self._call("GetQQLoginInfo") if online else {}
        return {
            "configured": True,
            "online": online,
            "phase": status.get("loginPhase") or "waiting_qrcode",
            "account": str(info.get("uin") or ""),
            "login_error": str(status.get("loginError") or ""),
        }

    async def qrcode(self, *, refresh: bool) -> dict:
        """Retrieve or refresh a QR code only while QQ is logged out.

        Args:
            refresh: Request a new QR code instead of retaining the current one.

        Returns:
            Login status and the current QR payload, if still logged out.
        """
        status = await self.status()
        if status["online"]:
            return {**status, "qrcode": ""}
        data = await self._call("RefreshQRcode" if refresh else "GetQQLoginQrcode")
        return {
            **status,
            "qrcode": str(data.get("qrcodeurl") or data.get("qrcode") or ""),
        }
