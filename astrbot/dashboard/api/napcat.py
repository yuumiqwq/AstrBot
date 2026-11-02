"""Administrator-only QQ login controls for the dashboard."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool

from astrbot.core.workspace import API_KEY_USERNAME_PREFIX
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.napcat_service import NapCatService

from .auth import require_dashboard_user

router = APIRouter(tags=["NapCat"])


class NapCatQrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: StrictBool = False


def get_service(request: Request) -> NapCatService:
    """Resolve the application's login service.

    Args:
        request: Authenticated dashboard request.

    Returns:
        Configured login service.
    """
    return request.app.state.services.napcat


@router.get("/napcat/status")
async def get_napcat_status(
    _username: str = Depends(require_dashboard_user),
    service: NapCatService = Depends(get_service),
):
    if _username.startswith(API_KEY_USERNAME_PREFIX):
        raise ApiError("请登录管理后台后操作", status_code=403)
    return JSONResponse(
        ok(await service.status()), headers={"Cache-Control": "no-store"}
    )


@router.post("/napcat/qrcode")
async def request_napcat_qrcode(
    payload: NapCatQrRequest,
    _username: str = Depends(require_dashboard_user),
    service: NapCatService = Depends(get_service),
):
    if _username.startswith(API_KEY_USERNAME_PREFIX):
        raise ApiError("请登录管理后台后操作", status_code=403)
    return JSONResponse(
        ok(await service.qrcode(refresh=payload.refresh)),
        headers={"Cache-Control": "no-store"},
    )
