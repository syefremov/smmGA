import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { PlanDraftResult } from "./PlanDraftResult";
import type { components } from "../../api/schema";

afterEach(cleanup);
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
