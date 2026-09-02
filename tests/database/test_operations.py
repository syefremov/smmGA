"""The two real HTTP transports exercise the same PostgreSQL transactions and policies."""

import asyncio
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update

from smm_gpt.application import create_app
from smm_gpt.domain import content as content_dto
from smm_gpt.domain import knowledge as knowledge_dto
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.operations import (
    CatalogKind,
    CreateWorkItem,
    OperationError,
    TransitionWorkItem,
    WorkState,
)
from smm_gpt.infrastructure.models import AuditEvent, Brand, Membership, WorkItem, utcnow
from smm_gpt.services.operations import Operations
from smm_gpt.services.sessions import SessionService

from ..identity_fakes import FakeIssuer
from .conftest import TenantFixture
from .test_content import pilot

pytestmark = pytest.mark.integration


async def test_commands_idempotency_concurrency_and_rls(tenants: TenantFixture) -> None:
    t = tenants
    core = Operations(t.access)
    cmd = CreateWorkItem(
        title="Synthetic task", brief="Never included in audit", idempotency_key="same-request"
    )
    results = await asyncio.gather(
        *(core.create_work(t.owner, t.workspace, cmd, uuid4()) for _ in range(4))
    )
    item = results[0]
    assert {r.id for r in results} == {item.id}
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await core.create_work(
            t.owner, t.workspace, cmd.model_copy(update={"title": "changed"}), uuid4()
        )
    with pytest.raises(AccessDenied):
        await core.create_work(t.viewer, t.workspace, cmd, uuid4())
    with pytest.raises(AccessDenied):
        await core.read_work(t.other, t.workspace, item.id, uuid4())
    with pytest.raises(OperationError, match="not_found"):
        await core.read_work(t.other, t.other_workspace, item.id, uuid4())
    async with t.runtime.transaction(t.other.user_id, t.other_workspace) as s:
        assert await s.get(WorkItem, item.id) is None
    async with t.runtime.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(WorkItem)) == 0
    transition = TransitionWorkItem(expected_version=1, state=WorkState.IN_PROGRESS)
    outcomes = await asyncio.gather(
        *(
            core.transition_work(t.owner, t.workspace, item.id, transition, uuid4())
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, OperationError) for r in outcomes) == 1
    current = await core.read_work(t.owner, t.workspace, item.id, uuid4())
    assert current.version == 2 and current.state == WorkState.IN_PROGRESS
    with pytest.raises(OperationError, match="invalid_transition"):
        await core.transition_work(
            t.owner,
            t.workspace,
            item.id,
            TransitionWorkItem(expected_version=2, state=WorkState.OPEN),
            uuid4(),
        )
    history = await core.audit_log(t.owner, t.workspace, uuid4(), target=item.id)
    assert {a.action for a in history.items} == {"work_item.created", "work_item.transitioned"}
    with pytest.raises(AccessDenied):
        await core.audit_log(t.viewer, t.workspace, uuid4())
    async with t.admin.transaction() as s:
        assert all(a.details == {} for a in (await s.scalars(select(AuditEvent))).all())


async def test_session_catalog_pagination_and_membership_revocation(tenants: TenantFixture) -> None:
    t = tenants
    core = Operations(t.access)
    session = await core.session(t.owner)
    assert [w.id for w in session.workspaces] == [t.workspace]
    async with t.admin.transaction() as s:
        s.add_all([Brand(workspace_id=t.workspace, name=f"Reference {n}") for n in range(3)])
        s.add(Brand(workspace_id=t.other_workspace, name="Invisible"))
    first = await core.catalog(t.owner, t.workspace, CatalogKind.BRANDS, uuid4(), limit=2)
    second = await core.catalog(
        t.owner, t.workspace, CatalogKind.BRANDS, uuid4(), limit=2, cursor=first.next_cursor
    )
    assert len(first.items) == 2 and len(second.items) == 1 and second.next_cursor is None
    assert len({i.id for i in first.items + second.items}) == 3
    assert "Invisible" not in first.model_dump_json() + second.model_dump_json()
    with pytest.raises(OperationError, match="invalid_request"):
        await core.catalog(t.owner, t.workspace, CatalogKind.BRANDS, uuid4(), limit=51)
    async with t.admin.transaction() as s:
        await s.execute(
            update(Membership).where(Membership.user_id == t.owner.user_id).values(active=False)
        )
    revoked = await core.session(t.owner)
    assert revoked.workspaces == [] and revoked.access_version != session.access_version
    with pytest.raises(AccessDenied):
        await core.catalog(t.owner, t.workspace, CatalogKind.BRANDS, uuid4())


async def test_rest_mcp_parity_resources_and_secret_redaction(tenants: TenantFixture) -> None:
    t = tenants
    issuer = FakeIssuer()
    sessions = SessionService(issuer.settings, t.access, issuer.client())
    app = create_app(settings=issuer.settings, sessions=sessions)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=issuer.settings.web_origin
        ) as browser,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url=issuer.settings.web_origin,
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer " + issuer.token(),
            },
        ) as chat,
    ):
        login = await browser.get("/api/v1/auth/login")
        params = parse_qs(urlsplit(login.headers["location"]).query)
        issuer.nonce = params["nonce"][0]
        assert (
            await browser.get(
                "/api/v1/auth/callback", params={"code": "synthetic", "state": params["state"][0]}
            )
        ).status_code == 303
        browser.headers.update(
            {
                "origin": issuer.settings.web_origin,
                "x-csrf-token": browser.cookies["__Host-smm-csrf"],
            }
        )

        async def call(name: str, args: dict[str, object]) -> dict[str, object]:
            response = await chat.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                },
            )
            assert response.status_code == 200
            result: dict[str, object] = response.json()["result"]
            return result

        session = await browser.get("/api/v1/session")
        assert (await call("session_read", {}))["structuredContent"] == session.json()
        wid = str(t.workspace)
        cmd = {"title": "From chat", "idempotency_key": "cross-transport"}
        chat_result = await call("work_item_create", {"workspace_id": wid, "command": cmd})
        rest = await browser.post(f"/api/v1/workspaces/{wid}/work-items", json=cmd)
        assert rest.status_code == 201
        assert chat_result["structuredContent"] == rest.json()
        item_id = rest.json()["id"]
        item = await browser.get(f"/api/v1/workspaces/{wid}/work-items/{item_id}")
        assert item.headers["etag"] == '"1"'
        assert (await call("work_item_read", {"workspace_id": wid, "item_id": item_id}))[
            "structuredContent"
        ] == item.json()
        changed = await browser.post(
            f"/api/v1/workspaces/{wid}/work-items/{item_id}/transition",
            json={"state": "in_progress", "expected_version": 1},
        )
        assert changed.status_code == 200 and changed.json()["version"] == 2
        stale = await call(
            "work_item_transition",
            {
                "workspace_id": wid,
                "item_id": item_id,
                "command": {"state": "in_progress", "expected_version": 1},
            },
        )
        assert stale["isError"] is True and "version_conflict" in str(stale)
        malformed: tuple[dict[str, object], ...] = (
            {"workspace_id": "never-echo-this-value"},
            {"workspace_id": wid, "limit": 100000},
        )
        for bad in malformed:
            result = await call("work_item_list", bad)
            assert result["isError"] is True and "invalid_request" in str(result)
            assert "never-echo-this-value" not in str(result)
        denied = await call("work_item_list", {"workspace_id": str(t.other_workspace)})
        assert denied["isError"] is True and "access_denied" in str(denied)
        assert (
            await browser.get(f"/api/v1/workspaces/{t.other_workspace}/work-items")
        ).status_code == 403
        resource = await chat.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/read",
                "params": {"uri": f"smm://workspaces/{wid}/catalog/brands"},
            },
        )
        assert "contents" in resource.json()["result"]
        content = await pilot(t)
        knowledge_command = knowledge_dto.SubmitDocument(
            idempotency_key=uuid4().hex,
            brand_id=content.brand,
            title="Transport evidence",
            text="Synthetic knowledge evidence",
            source_date=utcnow(),
            effective_from=utcnow() - timedelta(days=1),
            effective_to=utcnow() + timedelta(days=1),
        ).model_dump(mode="json")
        knowledge_mcp = await call(
            "knowledge_execute", {"workspace_id": wid, "command": knowledge_command}
        )
        assert not knowledge_mcp.get("isError"), knowledge_mcp
        knowledge_rest = await browser.post(
            f"/api/v1/workspaces/{wid}/knowledge/commands", json=knowledge_command
        )
        assert knowledge_rest.status_code == 200, knowledge_rest.text
        assert knowledge_mcp["structuredContent"] == knowledge_rest.json()
        read_knowledge = await browser.get(f"/api/v1/workspaces/{wid}/knowledge/documents")
        assert (await call("knowledge_documents", {"workspace_id": wid}))[
            "structuredContent"
        ] == read_knowledge.json()
        profiles_rest = await browser.get(f"/api/v1/workspaces/{wid}/knowledge/profiles")
        assert profiles_rest.status_code == 200 and len(profiles_rest.json()) == 8
        invalid_knowledge = await browser.post(
            f"/api/v1/workspaces/{wid}/knowledge/commands",
            json={**knowledge_command, "format": "pdf", "text": "never-echo-knowledge-input"},
        )
        assert invalid_knowledge.status_code == 422 and "never-echo" not in invalid_knowledge.text
        eval_command = {
            "action": "dataset_submit",
            "idempotency_key": uuid4().hex,
            "brand_id": str(content.brand),
            "definition": {
                "title": "Synthetic transport benchmark",
                "origin": "synthetic",
                "cases": [
                    {
                        "key": "absent",
                        "category": "no_answer",
                        "audience": "workspace",
                        "query": "No approved source",
                        "expected_document_ids": [],
                    }
                ],
            },
        }
        eval_prefix = f"/api/v1/workspaces/{wid}/knowledge/evaluations"
        eval_mcp = await call(
            "knowledge_eval_execute", {"workspace_id": wid, "command": eval_command}
        )
        eval_rest = await browser.post(eval_prefix + "/commands", json=eval_command)
        assert eval_rest.status_code == 200, eval_rest.text
        assert eval_mcp["structuredContent"] == eval_rest.json()
        dataset_id = eval_rest.json()["entity_id"]
        assert (await call("knowledge_eval_datasets", {"workspace_id": wid}))[
            "structuredContent"
        ] == (await browser.get(eval_prefix + "/datasets")).json()
        assert (
            await call(
                "knowledge_eval_dataset_read", {"workspace_id": wid, "dataset_id": dataset_id}
            )
        )["structuredContent"] == (
            await browser.get(eval_prefix + f"/datasets/{dataset_id}")
        ).json()
        run_command = {
            "action": "evaluation_run",
            "idempotency_key": uuid4().hex,
            "dataset_id": dataset_id,
            "dataset_hash": eval_rest.json()["content_hash"],
        }
        run_mcp = await call(
            "knowledge_eval_execute", {"workspace_id": wid, "command": run_command}
        )
        run_rest = await browser.post(eval_prefix + "/commands", json=run_command)
        assert run_rest.status_code == 200, run_rest.text
        assert run_mcp["structuredContent"] == run_rest.json()
        rid = run_rest.json()["entity_id"]
        report_mcp = await call("knowledge_eval_run_read", {"workspace_id": wid, "run_id": rid})
        report_rest = await browser.get(eval_prefix + f"/runs/{rid}")
        assert report_mcp["structuredContent"] == report_rest.json()
        assert not report_rest.json()["baseline_current"]
        assert (await call("knowledge_eval_runs", {"workspace_id": wid}))["structuredContent"] == (
            await browser.get(eval_prefix + "/runs")
        ).json()
        malformed_eval = await browser.post(
            eval_prefix + "/commands",
            json={
                **eval_command,
                "definition": {"title": "never-echo-eval-input"},
            },
        )
        assert malformed_eval.status_code == 422 and "never-echo" not in malformed_eval.text
        foreign_eval = await call(
            "knowledge_eval_run_read", {"workspace_id": str(t.other_workspace), "run_id": rid}
        )
        assert foreign_eval["isError"] and "access_denied" in str(foreign_eval)
        post = await content.post()
        revision = post.revisions[0]
        commands: list[content_dto.ContentCommand] = [
            content_dto.SaveRevision(
                post_id=post.id,
                expected_version=2,
                body=content.body("Cross transport"),
                idempotency_key=uuid4().hex,
            ),
        ]

        # Exact same command through both transports must execute once.
        async def both(command: content_dto.ContentCommand) -> dict[str, object]:
            payload = command.model_dump(mode="json")
            mcp = await call("content_execute", {"workspace_id": wid, "command": payload})
            assert not mcp.get("isError"), mcp
            response = await browser.post(
                f"/api/v1/workspaces/{wid}/content/commands", json=payload
            )
            assert response.status_code == 200, response.text
            result: dict[str, object] = response.json()
            assert mcp["structuredContent"] == result
            return result

        for c in commands:
            await both(c)
        await both(
            content_dto.RequestReview(
                post_id=post.id, expected_version=3, idempotency_key=uuid4().hex
            )
        )
        post = await content.post()
        revision = post.revisions[0]
        await both(
            content_dto.DecidePost(
                post_id=post.id,
                expected_version=4,
                revision_id=revision.id,
                content_hash=revision.content_hash,
                decision="approve",
                human_confirmed=True,
                claims_reviewed=True,
                reason="Synthetic human",
                idempotency_key=uuid4().hex,
            )
        )
        package = await both(
            content_dto.PreparePackage(
                post_id=post.id,
                expected_version=5,
                revision_id=revision.id,
                content_hash=revision.content_hash,
                scheduled_at=utcnow() + timedelta(days=1),
                human_confirmed=True,
                idempotency_key=uuid4().hex,
            )
        )
        content_rest = await browser.get(f"/api/v1/workspaces/{wid}/content/posts/{post.id}")
        assert (await call("content_post_read", {"workspace_id": wid, "post_id": str(post.id)}))[
            "structuredContent"
        ] == content_rest.json()
        package_rest = await browser.get(
            f"/api/v1/workspaces/{wid}/content/packages/{package['entity_id']}"
        )
        assert (
            await call(
                "content_package_read", {"workspace_id": wid, "package_id": package["entity_id"]}
            )
        )["structuredContent"] == package_rest.json()
        assert package_rest.json()["manifest"]["external_dispatch"] is False
        assert (
            await browser.post(
                f"/api/v1/workspaces/{wid}/content/commands",
                json={"action": "post_decide", "reason": "do-not-echo"},
            )
        ).status_code == 422
        async with t.admin.transaction() as s:
            await s.execute(
                update(Membership)
                .where(Membership.user_id == t.owner.user_id)
                .values(role="viewer")
            )
        assert (
            await browser.post(f"/api/v1/workspaces/{wid}/work-items", json=cmd)
        ).status_code == 403
        denied_write = await call("work_item_create", {"workspace_id": wid, "command": cmd})
        assert denied_write["isError"] is True
