import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.napcat import router
from astrbot.dashboard.responses import ApiError, error
from astrbot.dashboard.services.napcat_service import NapCatService


@pytest.fixture
def napcat(monkeypatch, tmp_path):
    config = tmp_path / "webui.json"
    config.write_text(json.dumps({"token": "server-secret"}), encoding="utf-8")
    monkeypatch.setenv("NAPCAT_WEBUI_URL", "http://napcat:6099")
    monkeypatch.setenv("NAPCAT_WEBUI_CONFIG", str(config))
    requests = []
    online = {"value": False}
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "login":
            assert (
                json.loads(request.content)["hash"]
                == hashlib.sha256(b"server-secret.napcat").hexdigest()
            )
            return httpx.Response(
                200, json={"code": 0, "data": {"Credential": "session-secret"}}
            )
        if request.headers.get("Authorization") == "Bearer expired":
            return httpx.Response(200, json={"code": -1, "message": "Unauthorized"})
        assert request.headers["Authorization"] == "Bearer session-secret"
        data = {
            "CheckLoginStatus": {
                "isLogin": online["value"],
                "loginPhase": "ready" if online["value"] else "waiting_qrcode",
            },
            "GetQQLoginInfo": {"uin": "123", "nick": "Bot"},
            "GetQQLoginQrcode": {"qrcode": "https://txz.qq.com/p?k=current"},
            "RefreshQRcode": {"qrcodeurl": "https://txz.qq.com/p?k=refreshed"},
        }[path]
        return httpx.Response(200, json={"code": 0, "data": data})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return NapCatService(), requests, online


@pytest.mark.asyncio
async def test_offline_status_never_generates_or_refreshes_qrcode(napcat):
    service, requests, _ = napcat
    status = await service.status()
    assert status["online"] is False
    assert status["phase"] == "waiting_qrcode"
    assert [r.url.path for r in requests] == [
        "/api/auth/login",
        "/api/QQLogin/CheckLoginStatus",
    ]
    assert "secret" not in json.dumps(status)


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", [False, True])
async def test_online_qrcode_request_preserves_existing_login(napcat, refresh):
    service, requests, online = napcat
    online["value"] = True
    result = await service.qrcode(refresh=refresh)
    assert result["online"] is True and result["account"] == "123"
    assert result["qrcode"] == ""
    assert all(
        "QRcode" not in r.url.path and "Qrcode" not in r.url.path for r in requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh,tail", [(False, "current"), (True, "refreshed")])
async def test_qrcode_keeps_current_code_until_explicit_refresh(napcat, refresh, tail):
    service, requests, _ = napcat
    result = await service.qrcode(refresh=refresh)
    assert result["qrcode"] == "https://txz.qq.com/p?k=" + tail
    assert requests[-1].url.path.endswith(
        "RefreshQRcode" if refresh else "GetQQLoginQrcode"
    )
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_expired_webui_credential_is_replaced(napcat):
    service, requests, _ = napcat
    service.credential = "expired"
    assert (await service.status())["configured"] is True
    assert [r.url.path for r in requests].count("/api/QQLogin/CheckLoginStatus") == 2
    assert service.credential == "session-secret"


@pytest.mark.asyncio
async def test_unconfigured_service_does_not_attempt_network(monkeypatch):
    monkeypatch.delenv("NAPCAT_WEBUI_URL", raising=False)
    monkeypatch.delenv("NAPCAT_WEBUI_CONFIG", raising=False)
    service = NapCatService()
    assert (await service.status())["configured"] is False
    with pytest.raises(ApiError) as exc:
        await service.qrcode(refresh=True)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_login_routes_require_dashboard_identity_and_never_cache_qr():
    app = FastAPI()
    app.state.jwt_secret = "napcat-login-test-secret-32-characters"
    service = SimpleNamespace(
        status=AsyncMock(return_value={"configured": True, "online": False}),
        qrcode=AsyncMock(return_value={"qrcode": "https://txz.qq.com/p?k=test"}),
    )
    app.state.services = SimpleNamespace(napcat=service)
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def handle_error(_request, exc):
        return JSONResponse(error(exc.message), status_code=exc.status_code)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/napcat/qrcode", json={"refresh": True})
        assert response.status_code == 401
        service.qrcode.assert_not_awaited()
        token = jwt.encode(
            {"username": "admin"}, app.state.jwt_secret, algorithm="HS256"
        )
        client.headers["Authorization"] = "Bearer " + token
        response = await client.get("/api/v1/napcat/status")
        assert (
            response.status_code == 200
            and response.headers["Cache-Control"] == "no-store"
        )
        response = await client.post("/api/v1/napcat/qrcode", json={"refresh": False})
        assert (
            response.status_code == 200
            and response.headers["Cache-Control"] == "no-store"
        )
        service.qrcode.assert_awaited_once_with(refresh=False)
        for body in ({"refresh": "false"}, {"url": "http://other-host"}):
            assert (
                await client.post("/api/v1/napcat/qrcode", json=body)
            ).status_code == 422
        app.dependency_overrides[require_dashboard_user] = lambda: "api_key:developer"
        assert (await client.get("/api/v1/napcat/status")).status_code == 403
