from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.persona_example_service import PersonaExampleService

from .auth import require_dashboard_user

router = APIRouter(prefix="/persona-example-reviews", tags=["Persona example reviews"])


class ExampleDialogs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    begin_dialogs: list[str] = Field(min_length=2, max_length=100)

    @field_validator("begin_dialogs")
    @classmethod
    def validate_dialogs(cls, value: list[str]) -> list[str]:
        if len(value) % 2 or any(not item.strip() for item in value):
            raise ValueError("预设对话须包含完整且非空的用户和助手发言")
        if sum(map(len, value)) > 200000:
            raise ValueError("候选对话过长")
        return value


class ExampleCreate(ExampleDialogs):
    persona_id: str = Field(min_length=1)


class ExampleDecision(ExampleDialogs):
    revision: int = Field(ge=1)
    action: Literal["save", "approve", "reject"]


class ExampleImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    context_rounds: int = Field(default=2, ge=0, le=10)


def get_service(request: Request) -> PersonaExampleService:
    return request.app.state.services.persona_examples


@router.get("/sources")
async def list_sources(
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    return ok(await service.sources())


@router.get("")
async def list_reviews(
    persona_id: str = "",
    state: Literal["pending", "approved", "rejected"] = "pending",
    page: int = Query(default=1, ge=1),
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    return ok(await service.list(persona_id, state, page))


@router.post("")
async def create_review(
    payload: ExampleCreate,
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    try:
        return ok(await service.create(payload.persona_id, payload.begin_dialogs))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


@router.post("/import")
async def import_reviews(
    payload: ExampleImport,
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    try:
        return ok(await service.import_conversation(**payload.model_dump()))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


@router.put("/{review_id}")
async def decide_review(
    review_id: str,
    payload: ExampleDecision,
    username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    try:
        return ok(
            await service.update(review_id, **payload.model_dump(), username=username)
        )
    except ValueError as exc:
        raise ApiError(str(exc), status_code=409) from exc
