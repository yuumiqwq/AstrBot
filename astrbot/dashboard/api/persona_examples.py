from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.persona_example_service import PersonaExampleService
from astrbot.dashboard.services.persona_preset_budget import preset_budget

from .auth import require_dashboard_user

router = APIRouter(prefix="/persona-example-reviews", tags=["Persona example reviews"])


class ExampleDialogs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    begin_dialogs: list[str] = Field(min_length=2)

    @field_validator("begin_dialogs")
    @classmethod
    def validate_dialogs(cls, value: list[str]) -> list[str]:
        if len(value) % 2 or any(not item.strip() for item in value[1:]):
            raise ValueError("预设对话须包含完整且非空的用户和助手发言")
        if sum(map(len, value)) > 200000:
            raise ValueError("候选对话过长")
        return value


class ExampleCreate(ExampleDialogs):
    persona_id: str = Field(min_length=1)


class ExampleDecision(ExampleDialogs):
    revision: int = Field(ge=1)
    action: Literal["save", "approve", "reject"]


class HistoryRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class ExampleImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    context_rounds: int = Field(default=2, ge=0, le=10)
    revision: str | None = None
    ranges: list[HistoryRange] | None = None
    append: bool = False


class PresetSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    start_pair: int = Field(ge=0)
    pair_count: int = Field(ge=1)
    source_review_id: str | None = None
    source_title: str = ""
    source_conversation_id: str | None = None
    source_messages: list[list[str]] | None = None


class PresetBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona_id: str = Field(min_length=1)
    begin_dialogs: list[str]
    segments: list[PresetSegment] | None = None


class PresetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona_id: str = Field(min_length=1)
    revision: str = Field(min_length=64, max_length=64)
    begin_dialogs: list[str]
    segments: list[PresetSegment] | None = None

    @field_validator("begin_dialogs")
    @classmethod
    def validate_pairs(cls, value: list[str]) -> list[str]:
        if len(value) % 2:
            raise ValueError("预设对话数量必须为偶数")
        return value


def get_service(request: Request) -> PersonaExampleService:
    return request.app.state.services.persona_examples


@router.get("/sources")
async def list_sources(
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    return ok(await service.sources())


@router.get("/history")
async def get_source_history(
    conversation_id: str = Query(min_length=1),
    persona_id: str = "",
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    try:
        return ok(await service.history(conversation_id, persona_id))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


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


@router.get("/presets")
async def get_presets(
    persona_id: str = Query(min_length=1),
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    try:
        return ok(await service.presets(persona_id))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


@router.put("/presets")
async def edit_presets(
    payload: PresetUpdate,
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    try:
        return ok(await service.update_presets(**payload.model_dump()))
    except ValueError as exc:
        raise ApiError(str(exc), status_code=409) from exc


@router.post("/presets/budget")
async def estimate_presets(
    payload: PresetBudgetRequest,
    _username: str = Depends(require_dashboard_user),
    service: PersonaExampleService = Depends(get_service),
):
    try:
        data = payload.model_dump()
        persona = await service.db.get_persona_by_id(data.pop("persona_id"))
        if persona is None:
            raise ValueError("人格不存在")
        return ok(
            await preset_budget(
                service.core_lifecycle, data["begin_dialogs"], segments=data["segments"]
            )
        )
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
