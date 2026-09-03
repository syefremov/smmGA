"""The two real HTTP transports exercise the same PostgreSQL transactions and policies."""

import asyncio
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from smm_gpt.application import create_app
from smm_gpt.domain import content as content_dto
from smm_gpt.domain import editor_triage as triage_dto
from smm_gpt.domain import knowledge as knowledge_dto
from smm_gpt.domain import profiles as profile_dto
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.editor import RunEditorialReview
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
from smm_gpt.workers.ai import process as process_ai

from ..identity_fakes import FakeIssuer
from .conftest import TenantFixture
from .test_ai_queue import Gateway
from .test_ai_queue import config as ai_test_config
from .test_content import pilot
from .test_editor import EditorGateway
from .test_editor_triage import decision as finding_decision
from .test_knowledge import activate as activate_knowledge
from .test_knowledge_files import command as file_command
from .test_memory_curation import proposal as memory_proposal

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


async def test_rest_mcp_parity_resources_and_secret_redaction(
    tenants: TenantFixture, tmp_path: Path
) -> None:
    t = tenants
    issuer = FakeIssuer()
    issuer.settings.media_root = str(tmp_path)
    issuer.settings.knowledge_files_enabled = True
    # Queue-only transport fixture: no worker/provider is called by these HTTP clients.
    issuer.settings.ai_provider = "openai"
    issuer.settings.ai_model = "synthetic-model"
    issuer.settings.ai_api_key = SecretStr("test-only")
    issuer.settings.ai_allowed_workspaces = (t.workspace,)
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
        upload = file_command(content.brand).model_dump(mode="json")
        file_prefix = f"/api/v1/workspaces/{wid}/knowledge/files"
        uploaded_mcp = await call("knowledge_file_submit", {"workspace_id": wid, "command": upload})
        uploaded_rest = await browser.post(file_prefix, json=upload)
        assert uploaded_rest.status_code == 200, uploaded_rest.text
        assert uploaded_mcp["structuredContent"] == uploaded_rest.json(), uploaded_mcp
        file_id = uploaded_rest.json()["file_id"]
        assert (await call("knowledge_files", {"workspace_id": wid}))["structuredContent"] == (
            await browser.get(file_prefix)
        ).json()
        assert (await call("knowledge_file_read", {"workspace_id": wid, "file_id": file_id}))[
            "structuredContent"
        ] == (await browser.get(file_prefix + "/" + file_id)).json()
        assert (await browser.get(file_prefix + "/" + file_id + "/original")).status_code == 409
        jobs_prefix = f"/api/v1/workspaces/{wid}/knowledge/jobs"
        jobs_rest = await browser.get(jobs_prefix, params={"kind": "file"})
        assert jobs_rest.status_code == 200
        assert (await call("knowledge_jobs", {"workspace_id": wid, "kind": "file"}))[
            "structuredContent"
        ] == jobs_rest.json()
        cancel_job = {
            "idempotency_key": uuid4().hex,
            "kind": "file",
            "job_id": file_id,
            "expected_version": 1,
        }
        cancel_job_mcp = await call(
            "knowledge_job_cancel", {"workspace_id": wid, "command": cancel_job}
        )
        cancel_job_rest = await browser.post(jobs_prefix + "/cancel", json=cancel_job)
        assert cancel_job_rest.status_code == 200 and cancel_job_rest.json()["state"] == "cancelled"
        assert cancel_job_mcp["structuredContent"] == cancel_job_rest.json()
        history_rest = await browser.get(jobs_prefix + "/file/" + file_id + "/history")
        assert history_rest.status_code == 200
        assert (
            await call(
                "knowledge_job_history", {"workspace_id": wid, "kind": "file", "job_id": file_id}
            )
        )["structuredContent"] == history_rest.json()
        malformed_file = await browser.post(
            file_prefix, json={**upload, "content_base64": "never-echo-file-input"}
        )
        assert malformed_file.status_code == 422 and "never-echo" not in malformed_file.text
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
        await activate_knowledge(
            t, knowledge_dto.KnowledgeResult.model_validate(knowledge_rest.json()), "Synthetic"
        )
        _, memory = await memory_proposal(t)
        memory_prefix = f"/api/v1/workspaces/{wid}/knowledge"
        note_rest = await browser.get(memory_prefix + f"/notes/{memory.note_id}")
        assert note_rest.status_code == 200
        assert (
            await call(
                "knowledge_note_read",
                {
                    "workspace_id": wid,
                    "note_id": str(memory.note_id),
                },
            )
        )["structuredContent"] == note_rest.json()
        memory_args = memory.model_dump(mode="json")
        memory_mcp = await call("knowledge_execute", {"workspace_id": wid, "command": memory_args})
        memory_rest = await browser.post(memory_prefix + "/commands", json=memory_args)
        assert memory_rest.status_code == 200, memory_rest.text
        assert memory_mcp["structuredContent"] == memory_rest.json()
        memory_did = memory_rest.json()["entity_id"]
        origin_rest = await browser.get(memory_prefix + f"/documents/{memory_did}/memory-origin")
        assert origin_rest.status_code == 200
        assert (
            await call(
                "knowledge_memory_origin",
                {
                    "workspace_id": wid,
                    "document_id": memory_did,
                },
            )
        )["structuredContent"] == origin_rest.json()
        memory_doc = await browser.get(memory_prefix + f"/documents/{memory_did}")
        assert memory_doc.json()["active_index_id"] is None
        denied_memory = await browser.post(
            memory_prefix + "/commands",
            json={
                **memory_args,
                "human_confirmed": False,
                "text": "never-echo-proposal-body",
            },
        )
        assert denied_memory.status_code == 422 and "never-echo" not in denied_memory.text
        registry_prefix = f"/api/v1/workspaces/{wid}/knowledge/profile-registry"
        assert (await browser.get(registry_prefix)).json() == []
        profile_draft = profile_dto.DraftProfile(
            idempotency_key=uuid4().hex,
            profile="product_expert",
            expected_revision=0,
            purpose="Synthetic transport-specific purpose",
            model="synthetic-model",
            reason="Synthetic registry transport test",
        ).model_dump(mode="json")
        draft_mcp = await call(
            "ai_profile_execute", {"workspace_id": wid, "command": profile_draft}
        )
        draft_rest = await browser.post(registry_prefix + "/commands", json=profile_draft)
        assert draft_rest.status_code == 200, draft_rest.text
        assert draft_mcp["structuredContent"] == draft_rest.json()
        profile_version_id = draft_rest.json()["version_id"]
        profile_version = await browser.get(registry_prefix + f"/versions/{profile_version_id}")
        assert profile_version.status_code == 200
        assert (
            await call(
                "ai_profile_version_read",
                {
                    "workspace_id": wid,
                    "version_id": profile_version_id,
                },
            )
        )["structuredContent"] == profile_version.json()
        select_profile = {
            "action": "profile_select_testing",
            "idempotency_key": uuid4().hex,
            "profile": "product_expert",
            "expected_revision": 1,
            "version_id": profile_version_id,
            "content_hash": profile_version.json()["content_hash"],
            "reason": "Exact synthetic test selection",
            "human_confirmed": True,
        }
        selected_rest = await browser.post(registry_prefix + "/commands", json=select_profile)
        assert selected_rest.status_code == 200, selected_rest.text
        assert (
            await call(
                "ai_profile_execute",
                {
                    "workspace_id": wid,
                    "command": select_profile,
                },
            )
        )["structuredContent"] == selected_rest.json()
        registry_mcp = await call("ai_profile_registry", {"workspace_id": wid})
        # MCP list results are wrapped by the SDK under result.
        assert registry_mcp["structuredContent"] == {
            "result": (await browser.get(registry_prefix)).json()
        }
        assert (await call("ai_profile_read", {"workspace_id": wid, "profile": "product_expert"}))[
            "structuredContent"
        ] == (await browser.get(registry_prefix + "/product_expert")).json()
        malformed_profile = await browser.post(
            registry_prefix + "/commands",
            json={
                **select_profile,
                "human_confirmed": False,
                "reason": "never-echo-profile-input",
            },
        )
        assert malformed_profile.status_code == 422 and "never-echo" not in malformed_profile.text
        ai_command = {
            "idempotency_key": uuid4().hex,
            "profile": "product_expert",
            "brand_id": str(content.brand),
            "question": "Synthetic",
            "testing_only": True,
            "profile_version_id": profile_version_id,
            "profile_selection_id": selected_rest.json()["decision_id"],
        }
        ai_prefix = f"/api/v1/workspaces/{wid}/knowledge/runs"
        ai_mcp = await call("ai_assess", {"workspace_id": wid, "command": ai_command})
        ai_rest = await browser.post(ai_prefix, json=ai_command)
        assert ai_rest.status_code == 200 and ai_rest.json()["state"] == "queued", ai_rest.text
        assert ai_mcp["structuredContent"] == ai_rest.json(), ai_mcp
        ai_id = ai_rest.json()["id"]
        assert (await call("ai_run_inputs", {"workspace_id": wid, "run_id": ai_id}))[
            "structuredContent"
        ] == (await browser.get(ai_prefix + f"/{ai_id}/inputs")).json()
        cancel_ai = {"idempotency_key": uuid4().hex, "expected_version": 1}
        cancel_mcp = await call(
            "ai_run_cancel", {"workspace_id": wid, "run_id": ai_id, "command": cancel_ai}
        )
        cancel_rest = await browser.post(ai_prefix + f"/{ai_id}/cancel", json=cancel_ai)
        assert cancel_rest.status_code == 200 and cancel_rest.json()["state"] == "cancelled"
        assert cancel_mcp["structuredContent"] == cancel_rest.json()
        assert (await call("ai_run_read", {"workspace_id": wid, "run_id": ai_id}))[
            "structuredContent"
        ] == (await browser.get(ai_prefix + f"/{ai_id}")).json()
        from .profile_fixtures import select_profile as register_editor

        editor_selection = await register_editor(t, "editor")
        revision = (await content.post()).revisions[0]
        editor_command = RunEditorialReview(
            idempotency_key=uuid4().hex,
            brand_id=content.brand,
            post_id=content.post_id,
            revision_id=revision.id,
            content_hash=revision.content_hash,
            profile_version_id=editor_selection.version_id,
            profile_selection_id=editor_selection.decision_id,
            testing_only=True,
        ).model_dump(mode="json")
        editorial_mcp = await call(
            "ai_review_revision", {"workspace_id": wid, "command": editor_command}
        )
        editorial_rest = await browser.post(
            f"/api/v1/workspaces/{wid}/knowledge/editor-runs", json=editor_command
        )
        assert editorial_rest.status_code == 200 and editorial_rest.json()["state"] == "queued", (
            editorial_rest.text
        )
        assert editorial_mcp["structuredContent"] == editorial_rest.json()
        editorial_id = editorial_rest.json()["id"]
        assert (await call("ai_run_inputs", {"workspace_id": wid, "run_id": editorial_id}))[
            "structuredContent"
        ] == (await browser.get(ai_prefix + f"/{editorial_id}/inputs")).json()
        denied_editorial = await browser.post(
            f"/api/v1/workspaces/{wid}/knowledge/editor-runs",
            json={**editor_command, "approved": True, "text": "never-echo-editor-input"},
        )
        assert denied_editorial.status_code == 422 and "never-echo" not in denied_editorial.text
        await process_ai(
            t.worker,
            ai_test_config(t.workspace),
            Gateway(),
            t.workspace,
            UUID(editorial_id),
            t.owner.user_id,
            editorial_gateway=EditorGateway(),
        )
        triage_path = ai_prefix + f"/{editorial_id}/editor-triage"
        triage_rest = await browser.get(triage_path)
        assert triage_rest.status_code == 200, triage_rest.text
        assert (await call("ai_editor_triage_read", {"workspace_id": wid, "run_id": editorial_id}))[
            "structuredContent"
        ] == triage_rest.json()
        triage_command = finding_decision(
            triage_dto.EditorialTriageView.model_validate(triage_rest.json())
        ).model_dump(mode="json")
        decided = await call(
            "ai_editor_finding_decide",
            {"workspace_id": wid, "run_id": editorial_id, "command": triage_command},
        )
        decided_rest = await browser.post(triage_path, json=triage_command)
        assert decided_rest.status_code == 200, decided_rest.text
        assert decided["structuredContent"] == decided_rest.json()
        assert decided_rest.json()["historical_only"]
        assert (
            await call("ai_editor_triage_history", {"workspace_id": wid, "run_id": editorial_id})
        )["structuredContent"] == (await browser.get(triage_path + "/history")).json()
        malformed_triage = await browser.post(
            triage_path,
            json={**triage_command, "status": "approved", "reason": "never-echo-triage"},
        )
        assert malformed_triage.status_code == 422 and "never-echo" not in malformed_triage.text
        assert (await browser.get(triage_path + "/history?before=0")).status_code == 422
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
