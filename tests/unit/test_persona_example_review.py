import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlmodel import select

from astrbot.core.db.po import ConversationV2, Persona, PersonaExampleReview
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.persona_examples import ExampleCreate, router
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.services.persona_example_service import PersonaExampleService


@pytest_asyncio.fixture
async def service(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "reviews.db"))
    async with db.get_db() as session:
        session.add(
            Persona(
                persona_id="fish",
                system_prompt="original",
                begin_dialogs=["old user", "old reply"],
            )
        )
        await session.commit()
    manager = SimpleNamespace(
        db=db, get_all_personas=db.get_personas, get_v3_persona_data=MagicMock()
    )
    yield PersonaExampleService(SimpleNamespace(persona_mgr=manager), db)
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_approval_is_explicit_and_idempotent(service):
    row = await service.create("fish", ["hello", "hello back"])
    assert (await service.db.get_persona_by_id("fish")).begin_dialogs == [
        "old user",
        "old reply",
    ]
    approved = await service.update(
        row["review_id"], 1, row["begin_dialogs"], "approve", "admin"
    )
    await service.update(row["review_id"], 1, row["begin_dialogs"], "approve", "admin")
    persona = await service.db.get_persona_by_id("fish")
    assert approved["state"] == "approved"
    assert persona.begin_dialogs == ["old user", "old reply", "hello", "hello back"]
    assert persona.system_prompt == "original"
    assert service.persona_mgr.get_v3_persona_data.call_count == 2


@pytest.mark.asyncio
async def test_rejection_and_stale_edits_do_not_append(service):
    row = await service.create("fish", ["a", "b"])
    await service.update(row["review_id"], 1, ["a", "edited"], "save", "admin")
    with pytest.raises(ValueError, match="更新"):
        await service.update(row["review_id"], 1, ["a", "stale"], "approve", "admin")
    await service.update(row["review_id"], 2, ["a", "edited"], "reject", "admin")
    assert (await service.db.get_persona_by_id("fish")).begin_dialogs == [
        "old user",
        "old reply",
    ]
    assert (await service.list("fish", "rejected", 1))["total"] == 1


@pytest.mark.asyncio
async def test_concurrent_approvals_preserve_both_examples(service):
    first = await service.create("fish", ["a", "b"])
    second = await service.create("fish", ["c", "d"])
    await asyncio.gather(
        *(
            service.update(
                row["review_id"], 1, row["begin_dialogs"], "approve", "admin"
            )
            for row in (first, second)
        )
    )
    dialogs = (await service.db.get_persona_by_id("fish")).begin_dialogs
    assert dialogs[:2] == ["old user", "old reply"]
    assert len(dialogs) == 6
    assert {tuple(dialogs[2:4]), tuple(dialogs[4:6])} == {("a", "b"), ("c", "d")}


@pytest.mark.asyncio
async def test_existing_preset_is_not_duplicated(service):
    row = await service.create("fish", ["old user", "old reply"])
    await service.update(row["review_id"], 1, row["begin_dialogs"], "approve", "admin")
    assert (await service.db.get_persona_by_id("fish")).begin_dialogs == [
        "old user",
        "old reply",
    ]


@pytest.mark.asyncio
async def test_missing_persona_rolls_back_approval(service):
    row = await service.create("fish", ["a", "b"])
    await service.db.delete_persona("fish")
    with pytest.raises(ValueError, match="人格"):
        await service.update(
            row["review_id"], 1, row["begin_dialogs"], "approve", "admin"
        )
    assert (await service.list("fish", "pending", 1))["total"] == 1


@pytest.mark.asyncio
async def test_import_keeps_complete_context_and_skips_tool_and_system_text(service):
    async with service.db.get_db() as session:
        session.add(
            ConversationV2(
                conversation_id="conv",
                platform_id="qq",
                user_id="user",
                content=[
                    {"role": "system", "content": "system instructions"},
                    {"role": "assistant", "content": "orphan"},
                    {"role": "user", "content": "first"},
                    {"role": "user", "content": [{"type": "text", "text": "second"}]},
                    {
                        "role": "assistant",
                        "content": "tool plan",
                        "tool_calls": [{"id": "tool"}],
                    },
                    {"role": "tool", "content": "tool output"},
                    {"role": "assistant", "content": "reply"},
                    {"role": "user", "content": "next"},
                    {"role": "assistant", "content": "next reply"},
                    {"role": "user", "content": "unanswered"},
                ],
            )
        )
        await session.commit()
    assert await service.import_conversation("fish", "conv", 1) == {
        "added": 2,
        "skipped": 0,
    }
    assert await service.import_conversation("fish", "conv", 1) == {
        "added": 0,
        "skipped": 2,
    }
    items = (await service.list("fish", "pending", 1))["items"]
    assert sorted((row["begin_dialogs"] for row in items), key=len) == [
        ["first\n\nsecond", "reply"],
        ["first\n\nsecond", "reply", "next", "next reply"],
    ]
    assert (await service.db.get_persona_by_id("fish")).begin_dialogs == [
        "old user",
        "old reply",
    ]


@pytest.mark.parametrize("dialogs", [[], ["only one"], ["a", " "], ["a", "b", "c"]])
def test_incomplete_examples_are_rejected(dialogs):
    with pytest.raises(ValidationError):
        ExampleCreate(persona_id="fish", begin_dialogs=dialogs)


@pytest.mark.asyncio
async def test_review_api_requires_dashboard_login(service):
    app = FastAPI()
    app.state.jwt_secret = "test-secret-that-is-long-enough-for-hs256"
    app.state.services = SimpleNamespace(persona_examples=service)
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        return JSONResponse({"message": exc.message}, status_code=exc.status_code)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/persona-example-reviews")).status_code == 401
        token = jwt.encode(
            {"username": "admin"}, app.state.jwt_secret, algorithm="HS256"
        )
        client.headers["Authorization"] = f"Bearer {token}"
        created = await client.post(
            "/api/v1/persona-example-reviews",
            json={"persona_id": "fish", "begin_dialogs": ["question", "answer"]},
        )
        assert created.status_code == 200
        row = created.json()["data"]
        response = await client.put(
            f"/api/v1/persona-example-reviews/{row['review_id']}",
            json={
                "revision": 1,
                "action": "approve",
                "begin_dialogs": row["begin_dialogs"],
            },
        )
        assert response.status_code == 200
        async with service.db.get_db() as session:
            saved = (await session.execute(select(PersonaExampleReview))).scalar_one()
            assert saved.reviewed_by == "admin"
