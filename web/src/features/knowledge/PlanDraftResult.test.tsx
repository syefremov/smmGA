import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { PlanDraftResult, PlannerResult } from "./PlanDraftResult";
import type { components } from "../../api/schema";

afterEach(cleanup);
it("shows immutable private plan history without reviving a stale draft", () => {
  const adoption: components["schemas"]["PlanAdoptionView"] = {
    id: "receipt",
    run_id: "run",
    artifact_id: "artifact",
    artifact_hash: "a".repeat(64),
    input_id: "input",
    input_hash: "b".repeat(64),
    source_plan_id: "old-plan",
    source_content_hash: "c".repeat(64),
    plan_id: "new&plan",
    content_hash: "d".repeat(64),
    plan_number: 2,
    notes_id: "notes",
    notes_hash: "e".repeat(64),
    preview_hash: "f".repeat(64),
    actor_id: "owner",
    created_at: "2026-09-03T12:00:00Z",
    reason: "<img src=x>",
    historical_only: true,
    warning: "Historical receipt only",
  };
  const { container } = render(
    <PlannerResult
      adoption={adoption}
      workspaceId="w&other"
      timezone="Europe/Moscow"
    />,
  );
  expect(
    screen.getByRole("region", { name: "История сохранения AI-плана" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/owner.*15:00/)).toBeInTheDocument();
  expect(
    screen.getByText(/Личное основание решения: <img src=x>/),
  ).toBeInTheDocument();
  expect(screen.getByText(adoption.notes_hash)).toBeInTheDocument();
  expect(
    screen.getByRole("link", {
      name: "Открыть сохранённый план и ограничения",
    }),
  ).toHaveAttribute(
    "href",
    "/app/materials?workspace=w%26other&record=new%26plan&kind=content_plan",
  );
  expect(
    screen.queryByRole("region", { name: "Предложение контент-плана" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
});
const draft: components["schemas"]["PlanDraft"] = {
  plan_id: "synthetic-plan",
  content_hash: "a".repeat(64),
  context_hash: "b".repeat(64),
  outcome: "draft",
  slots: [
    {
      slot_index: 0,
      planned_at: "2026-09-05T12:00:00Z",
      destination: "vk:group:123",
      owner_id: "synthetic-owner",
      topic: '<img src=x onerror="alert(1)">',
      rationale: "Explain the fact",
      evidence: [
        {
          fact_id: "synthetic-fact",
          quote: "src=x",
          source_quote: "Synthetic source",
        },
      ],
    },
  ],
  warnings: ["Synthetic warning"],
  knowledge_gaps: ["Missing promotion dates"],
};

it("shows inert proposal with exact slot, owner, timezone, evidence and no mutation controls", () => {
  const { container } = render(
    <PlanDraftResult draft={draft} timezone="Europe/Moscow" />,
  );
  expect(screen.getByText(draft.slots[0].topic)).toBeInTheDocument();
  expect(screen.getByText("synthetic-plan")).toBeInTheDocument();
  expect(screen.getByText(/synthetic-fact/)).toBeInTheDocument();
  expect(screen.getByText(/Synthetic source/)).toBeInTheDocument();
  expect(screen.getByText(/vk:group:123.*synthetic-owner/)).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: /Слот 1.*15:00/ }),
  ).toBeInTheDocument();
  expect(screen.getByText(/Часовой пояс: Europe\/Moscow/)).toBeInTheDocument();
  expect(screen.getByText("Missing promotion dates")).toBeInTheDocument();
  expect(
    screen.getByText(/расписание отправок не созданы/),
  ).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("renders abstention without slots or a successful-check implication", () => {
  render(
    <PlanDraftResult
      draft={{
        ...draft,
        outcome: "insufficient_evidence",
        slots: [],
        warnings: [],
      }}
      timezone="UTC"
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Недостаточно оснований" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/это не результат проверки/)).toBeInTheDocument();
  expect(screen.queryByText(draft.slots[0].topic)).not.toBeInTheDocument();
});

it("uses explicit workspace timezone on re-render", () => {
  const { rerender } = render(
    <PlanDraftResult draft={draft} timezone="Europe/Moscow" />,
  );
  rerender(<PlanDraftResult draft={draft} timezone="UTC" />);
  expect(
    screen.getByRole("heading", { name: /Слот 1.*12:00/ }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: /15:00/ }),
  ).not.toBeInTheDocument();
});
