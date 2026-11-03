"""Exercise independent history excerpts, approval, editing, and model assembly."""

import copy
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

import astrbot.api  # noqa: F401 -- Initialize the public API before PersonaManager.
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message
from astrbot.core.db.po import ConversationV2, Persona
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.persona_presets import (
    PRESET_DIALOGUE_END_INSTRUCTION,
    PRESET_DIALOGUE_STYLE_INSTRUCTION,
    prepare_preset_messages,
)
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.persona_examples import ExampleCreate, router
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.services.persona_example_service import PersonaExampleService
from astrbot.dashboard.services.persona_preset_budget import preset_budget
from astrbot.dashboard.services.persona_service import PersonaService


@pytest_asyncio.fixture
async def service(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "segments.db"))
    async with db.get_db() as session:
        session.add(
            Persona(
                persona_id="example",
                system_prompt="Persona",
                begin_dialogs=["old", "old reply"],
            )
        )
        session.add(
            ConversationV2(
                conversation_id="source",
                platform_id="qq",
                user_id="owner",
                content=[
                    {"role": "system", "content": "not selectable"},
                    {"role": "assistant", "content": "opening"},
                    {"role": "assistant", "content": "second bubble"},
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                    {"role": "user", "content": "unselected"},
                    {"role": "assistant", "content": "unselected answer"},
                    {"role": "user", "content": "second question"},
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "second answer"}],
                    },
                    {"role": "user", "content": "unanswered"},
                ],
            )
        )
        await session.commit()
    manager = SimpleNamespace(
        db=db, get_all_personas=db.get_personas, get_v3_persona_data=MagicMock()
    )
    yield PersonaExampleService(SimpleNamespace(persona_mgr=manager), db)
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_selected_ranges_preserve_opening_and_become_independent_approved_segments(
    service,
):
    history = await service.history("source")
    assert [item["index"] for item in history["messages"]] == list(range(1, 10))
    result = await service.import_conversation(
        "example",
        "source",
        revision=history["revision"],
        ranges=[
            {"start": 1, "end": 4},
            {"start": 7, "end": 8},
        ],
    )
    assert result == {"added": 2, "skipped": 0}
    pending = (await service.list("example", "pending", 1))["items"]
    opening = next(item for item in pending if item["begin_dialogs"][0] == "")
    assert opening["begin_dialogs"] == [
        "",
        "opening\n\nsecond bubble",
        "first question",
        "first answer",
    ]
    assert ExampleCreate(persona_id="example", begin_dialogs=opening["begin_dialogs"])
    for item in pending:
        await service.update(
            item["review_id"],
            item["revision"],
            item["begin_dialogs"],
            "approve",
            "admin",
        )
    presets = await service.presets("example")
    assert len(presets["segments"]) == 3
    assert presets["segments"][0]["id"] == "legacy"
    assert {item["source_review_id"] for item in presets["segments"][1:]} == {
        item["review_id"] for item in pending
    }
    assert all(
        item["present"]
        for item in (await service.list("example", "approved", 1))["items"]
    )
    assert await service.import_conversation(
        "example",
        "source",
        revision=history["revision"],
        ranges=[
            {"start": 1, "end": 4},
            {"start": 7, "end": 8},
        ],
    ) == {"added": 0, "skipped": 2}


@pytest.mark.asyncio
async def test_invalid_range_or_stale_history_creates_nothing(service):
    snapshot = await service.history("source")
    for ranges in (
        [{"start": 1, "end": 4}, {"start": 7, "end": 9}],
        [{"start": 1, "end": 4}, {"start": 4, "end": 8}],
    ):
        with pytest.raises(ValueError):
            await service.import_conversation(
                "example", "source", revision=snapshot["revision"], ranges=ranges
            )
    async with service.db.get_db() as session:
        row = (await session.execute(select(ConversationV2))).scalar_one()
        row.content = row.content + [{"role": "assistant", "content": "new answer"}]
        session.add(row)
        await session.commit()
    with pytest.raises(ValueError, match="历史已更新"):
        await service.import_conversation(
            "example",
            "source",
            revision=snapshot["revision"],
            ranges=[{"start": 1, "end": 4}],
        )
    assert (await service.list("example", "pending", 1))["total"] == 0


@pytest.mark.asyncio
async def test_partial_edit_delete_keeps_ids_and_audit_presence(service):
    candidate = await service.create("example", ["a", "b", "c", "d"])
    await service.update(
        candidate["review_id"], 1, candidate["begin_dialogs"], "approve", "admin"
    )
    current = await service.presets("example")
    segments = copy.deepcopy(current["segments"])
    segments[1]["pair_count"] = 1
    changed = await service.update_presets(
        "example", current["revision"], ["old", "old reply", "c", "edited"], segments
    )
    assert changed["segments"][1]["id"] == current["segments"][1]["id"]
    assert (await service.list("example", "approved", 1))["items"][0]["present"]
    await service.update_presets(
        "example", changed["revision"], ["old", "old reply"], segments[:1]
    )
    archived = (await service.list("example", "approved", 1))["items"][0]
    assert not archived["present"] and archived["live_segment_id"] is None
    assert archived["begin_dialogs"] == candidate["begin_dialogs"]


@pytest.mark.asyncio
async def test_metadata_revision_and_native_update_preserve_concurrent_approval(
    service,
):
    current = await service.presets("example")
    renamed = copy.deepcopy(current["segments"])
    renamed[0]["source_title"] = "renamed excerpt"
    updated = await service.update_presets(
        "example", current["revision"], current["begin_dialogs"], renamed
    )
    assert updated["revision"] != current["revision"]
    with pytest.raises(ValueError, match="预设对话已更新"):
        await service.db.update_persona(
            "example",
            begin_dialogs=["old", "stale"],
            expected_preset_revision=current["revision"],
        )
    await service.db.update_persona("example", system_prompt="New persona")
    assert (await service.presets("example"))["segments"] == renamed
    assert (
        await service.db.get_persona_by_id("example")
    ).system_prompt == "New persona"


@pytest.mark.asyncio
async def test_all_examples_and_boundaries_share_native_budget(service):
    dialogs = ["one", "answer", "two", "answer two"]
    segments = [
        {"id": "a", "start_pair": 0, "pair_count": 1},
        {"id": "b", "start_pair": 1, "pair_count": 1},
    ]
    budget = await preset_budget(service.core_lifecycle, dialogs, segments=segments)
    processed = prepare_preset_messages(dialogs, segments)
    assert budget["estimated_tokens"] == EstimateTokenCounter().count_tokens(
        [Message(role=item["role"], content=item["content"]) for item in processed]
    )
    manager = PersonaManager(Mock(), SimpleNamespace(default_conf={}))
    manager.personas = [
        Persona(
            persona_id="example",
            system_prompt="Persona",
            begin_dialogs=dialogs,
            begin_dialog_segments=segments,
        )
    ]
    for _ in range(2):
        _, personas, _ = manager.get_v3_persona_data()
        assert personas[0]["_begin_dialogs_processed"] == processed
    assert [item["content"] for item in processed].count(
        PRESET_DIALOGUE_STYLE_INSTRUCTION
    ) == 1
    assert processed[-1]["content"] == PRESET_DIALOGUE_END_INSTRUCTION
    assert [
        item["content"] for item in processed if item["role"] != "system"
    ] == dialogs
    assert all(item["_no_save"] for item in processed)
    assert (await preset_budget(service.core_lifecycle, []))["estimated_tokens"] == 0


@pytest.mark.asyncio
async def test_existing_database_migration_is_additive_and_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE personas (id INTEGER PRIMARY KEY, persona_id VARCHAR(255), system_prompt TEXT, begin_dialogs JSON, tools JSON, created_at DATETIME, updated_at DATETIME)"
    )
    connection.execute(
        "INSERT INTO personas VALUES (1, 'example', 'unchanged', '[\"q\", \"a\"]', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    connection.commit()
    connection.close()
    db = SQLiteDatabase(str(path))
    await db.initialize()
    await db.initialize()
    row = await db.get_persona_by_id("example")
    assert row.system_prompt == "unchanged" and row.begin_dialogs == ["q", "a"]
    assert row.begin_dialog_segments is None
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_range_and_budget_api_contract_roundtrip(service):
    app = FastAPI()
    app.state.services = SimpleNamespace(persona_examples=service)
    app.dependency_overrides[require_dashboard_user] = lambda: "admin"
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        return JSONResponse({"message": exc.message}, status_code=exc.status_code)

    endpoint = "/api/v1/persona-example-reviews"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        source = (
            await client.get(
                f"{endpoint}/history", params={"conversation_id": "source"}
            )
        ).json()["data"]
        response = await client.post(
            f"{endpoint}/import",
            json={
                "persona_id": "example",
                "conversation_id": "source",
                "revision": source["revision"],
                "ranges": [{"start": 1, "end": 4}],
            },
        )
        assert response.status_code == 200 and response.json()["data"]["added"] == 1
        presets = (
            await client.get(f"{endpoint}/presets", params={"persona_id": "example"})
        ).json()["data"]
        budget = await client.post(
            f"{endpoint}/presets/budget",
            json={
                "persona_id": "example",
                "begin_dialogs": presets["begin_dialogs"],
                "segments": presets["segments"],
            },
        )
        assert budget.status_code == 200 and budget.json()["data"] == presets["budget"]
        presets["segments"][0]["source_title"] = "Named excerpt"
        saved = await client.put(
            f"{endpoint}/presets",
            json={
                "persona_id": "example",
                "begin_dialogs": presets["begin_dialogs"],
                "segments": presets["segments"],
                "revision": presets["revision"],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["segments"][0]["source_title"] == "Named excerpt"


@pytest.mark.asyncio
async def test_persona_copy_and_prompt_edit_preserve_excerpt_metadata(service):
    candidate = await service.create("example", ["a", "b"])
    await service.update(
        candidate["review_id"], 1, candidate["begin_dialogs"], "approve", "admin"
    )
    persona = await service.db.get_persona_by_id("example")
    exported = PersonaService.serialize_persona(persona)
    manager = PersonaManager(service.db, SimpleNamespace(default_conf={}))
    manager.personas = [persona]
    native_service = PersonaService(SimpleNamespace(persona_mgr=manager))
    result = await native_service.create_persona({**exported, "persona_id": "copy"})
    assert (
        result["persona"]["begin_dialog_segments"] == exported["begin_dialog_segments"]
    )
    await native_service.update_persona(
        {"persona_id": "example", "system_prompt": "Changed persona"}
    )
    assert (
        await service.db.get_persona_by_id("example")
    ).begin_dialog_segments == exported["begin_dialog_segments"]


async def replace_source(service, messages):
    """Replace a synthetic source without touching a live conversation.

    Args:
        service: Persona example service using a temporary test database.
        messages: Complete synthetic native history to persist.
    """
    async with service.db.get_db() as session:
        row = (
            await session.execute(
                select(ConversationV2).where(ConversationV2.conversation_id == "source")
            )
        ).scalar_one()
        row.content = messages
        session.add(row)
        await session.commit()


@pytest.mark.asyncio
async def test_direct_append_needs_no_review_and_is_idempotent(service):
    source = await service.history("source", "example")
    arguments = {
        "persona_id": "example",
        "conversation_id": "source",
        "revision": source["revision"],
        "append": True,
        "ranges": [{"start": 1, "end": 4}, {"start": 7, "end": 8}],
    }
    assert await service.import_conversation(**arguments) == {
        "added": 3,
        "skipped": 0,
        "written": True,
    }
    assert (await service.list("example", "pending", 1))["total"] == 0
    presets = await service.presets("example")
    assert len(presets["segments"]) == 3
    assert presets["begin_dialogs"][2:6] == [
        "",
        "opening\n\nsecond bubble",
        "first question",
        "first answer",
    ]
    assert presets["segments"][1]["source_conversation_id"] == "source"
    assert len(presets["segments"][1]["source_messages"]) == 2
    assert await service.import_conversation(**arguments) == {
        "added": 0,
        "skipped": 3,
        "written": True,
    }
    assert (await service.presets("example"))["revision"] == presets["revision"]
    tagged = await service.history("source", "example")
    assert {item["index"] for item in tagged["messages"] if item["in_presets"]} == {
        1,
        2,
        3,
        4,
        7,
        8,
    }


@pytest.mark.asyncio
async def test_direct_append_budget_failure_leaves_no_partial_write(service):
    await replace_source(
        service,
        [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "a" * 65000},
        ],
    )
    source = await service.history("source")
    before = await service.presets("example")
    with pytest.raises(ValueError, match="15%"):
        await service.import_conversation(
            "example",
            "source",
            revision=source["revision"],
            ranges=[{"start": 0, "end": 1}],
            append=True,
        )
    assert (await service.presets("example"))["revision"] == before["revision"]
    assert (await service.list("example", "pending", 1))["total"] == 0


@pytest.mark.asyncio
async def test_direct_source_tags_follow_partial_deletion_and_stable_qq_ids(service):
    raw = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": text,
            "_qq_sequence": 100 + i,
            "_qq_time": 1793664000 + i,
        }
        for i, text in enumerate(["q1", "a1", "q2", "a2", "q3", "a3"])
    ]
    await replace_source(service, raw)
    source = await service.history("source")
    await service.import_conversation(
        "example",
        "source",
        revision=source["revision"],
        ranges=[{"start": 0, "end": 5}],
        append=True,
    )
    presets = await service.presets("example")
    assert presets["segments"][1]["source_messages"] == [
        ["qq:100", "qq:101"],
        ["qq:102", "qq:103"],
        ["qq:104", "qq:105"],
    ]
    segment = dict(
        presets["segments"][1],
        pair_count=2,
        source_messages=[
            presets["segments"][1]["source_messages"][0],
            presets["segments"][1]["source_messages"][2],
        ],
    )
    await service.update_presets(
        "example",
        presets["revision"],
        presets["begin_dialogs"][:4] + presets["begin_dialogs"][6:],
        [presets["segments"][0], segment],
    )
    await replace_source(
        service, [{"role": "system", "content": "shift indexes"}, *raw]
    )
    tagged = await service.history("source", "example")
    assert {m["source_id"] for m in tagged["messages"] if m["in_presets"]} == {
        "qq:100",
        "qq:101",
        "qq:104",
        "qq:105",
    }
    assert tagged["messages"][0]["index"] == 1
    current = await service.presets("example")
    await service.update_presets("example", current["revision"], [], [])
    assert not any(
        m["in_presets"]
        for m in (await service.history("source", "example"))["messages"]
    )


@pytest.mark.asyncio
async def test_legacy_tags_allow_excerpt_edges_inside_consecutive_bubbles(service):
    messages = [
        {"role": "user", "content": "before"},
        {"role": "user", "content": "target question"},
        {"role": "assistant", "content": "target answer"},
        {"role": "assistant", "content": "after"},
    ]
    await replace_source(service, messages)
    preset = await service.presets("example")
    await service.update_presets(
        "example", preset["revision"], ["target question", "target answer"]
    )
    marked = await service.history("source", "example")
    assert [m["in_presets"] for m in marked["messages"]] == [False, True, True, False]
    await replace_source(service, [*messages, *messages[1:3]])
    ambiguous = await service.history("source", "example")
    assert not any(m["in_presets"] for m in ambiguous["messages"])


@pytest.mark.asyncio
async def test_history_uses_only_real_saved_send_times(service):
    times = [1793664000, None, "1793664001", True, 0, -1]
    await replace_source(
        service,
        [
            {"role": "assistant", "content": str(index), "_qq_time": value}
            for index, value in enumerate(times)
        ],
    )
    history = await service.history("source")
    assert [message["sent_at"] for message in history["messages"]] == [
        1793664000,
        None,
        None,
        None,
        None,
        None,
    ]


@pytest.mark.asyncio
async def test_direct_import_api_accepts_provenance_and_persona_history_query(service):
    app = FastAPI()
    app.state.services = SimpleNamespace(persona_examples=service)
    app.dependency_overrides[require_dashboard_user] = lambda: "admin"
    app.include_router(router, prefix="/api/v1")
    endpoint = "/api/v1/persona-example-reviews"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        snapshot = (
            await client.get(
                endpoint + "/history",
                params={"conversation_id": "source", "persona_id": "example"},
            )
        ).json()["data"]
        written = await client.post(
            endpoint + "/import",
            json={
                "persona_id": "example",
                "conversation_id": "source",
                "append": True,
                "revision": snapshot["revision"],
                "ranges": [{"start": 3, "end": 4}],
            },
        )
        assert written.status_code == 200 and written.json()["data"]["written"] is True
        presets = (
            await client.get(endpoint + "/presets", params={"persona_id": "example"})
        ).json()["data"]
        saved = await client.put(
            endpoint + "/presets",
            json={
                "persona_id": "example",
                **{
                    key: presets[key]
                    for key in ("revision", "begin_dialogs", "segments")
                },
            },
        )
        assert saved.status_code == 200
        after = (
            await client.get(
                endpoint + "/history",
                params={"conversation_id": "source", "persona_id": "example"},
            )
        ).json()["data"]
        assert {m["index"] for m in after["messages"] if m["in_presets"]} == {3, 4}


@pytest.mark.asyncio
async def test_equal_text_at_distinct_qq_positions_remains_distinct_provenance(service):
    raw = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": text,
            "_qq_sequence": 200 + index,
        }
        for index, text in enumerate(
            ["same question", "same answer", "same question", "same answer"]
        )
    ]
    await replace_source(service, raw)
    source = await service.history("source")
    first = await service.import_conversation(
        "example",
        "source",
        revision=source["revision"],
        ranges=[{"start": 0, "end": 1}],
        append=True,
    )
    assert first["added"] == 1
    assert [
        m["in_presets"]
        for m in (await service.history("source", "example"))["messages"]
    ] == [True, True, False, False]
    second = await service.import_conversation(
        "example",
        "source",
        revision=source["revision"],
        ranges=[{"start": 2, "end": 3}],
        append=True,
    )
    assert second["added"] == 1 and second["skipped"] == 0
    assert all(
        m["in_presets"]
        for m in (await service.history("source", "example"))["messages"]
    )


@pytest.mark.asyncio
async def test_partial_overlap_does_not_commit_earlier_staged_pairs(service):
    raw = [
        {"role": role, "content": value, "_qq_sequence": 300 + index}
        for index, (role, value) in enumerate(
            [
                ("user", "new question"),
                ("assistant", "new answer"),
                ("user", "old question"),
                ("assistant", "old answer"),
                ("assistant", "extra bubble"),
            ]
        )
    ]
    await replace_source(service, raw)
    source = await service.history("source")
    await service.import_conversation(
        "example",
        "source",
        revision=source["revision"],
        ranges=[{"start": 2, "end": 3}],
        append=True,
    )
    before = await service.presets("example")
    with pytest.raises(ValueError, match="部分重叠"):
        await service.import_conversation(
            "example",
            "source",
            revision=source["revision"],
            ranges=[{"start": 0, "end": 4}],
            append=True,
        )
    assert (await service.presets("example"))["revision"] == before["revision"]
