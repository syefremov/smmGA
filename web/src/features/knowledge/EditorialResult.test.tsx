import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { EditorialResult } from "./EditorialResult";
import type { components } from "../../api/schema";

afterEach(cleanup);
const review: components["schemas"]["EditorialReview"] = {
  revision_id: "synthetic-revision",
  content_hash: "a".repeat(64),
  context_hash: "b".repeat(64),
  recommendation: "needs_changes",
  summary: "Synthetic summary",
  findings: [
    {
      category: "claims",
      severity: "blocking",
      location: "variant",
      variant_index: 0,
      quote: '<img src=x onerror="alert(1)">',
      description: "Requires human check",
      suggestion: "Check source",
      record_ids: ["synthetic-evidence"],
    },
  ],
};

it("shows exact binding, evidence and inert source text without approval controls", () => {
  const { container } = render(<EditorialResult review={review} />);
  expect(
    screen.getByRole("heading", { name: "Нужны изменения" }),
  ).toBeInTheDocument();
  expect(screen.getByText(review.findings[0].quote)).toBeInTheDocument();
  expect(screen.getByText("synthetic-revision")).toBeInTheDocument();
  expect(screen.getByText(/synthetic-evidence/)).toBeInTheDocument();
  expect(screen.getByText(/не одобрение публикации/)).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("does not turn pass or empty findings into human approval", () => {
  render(
    <EditorialResult
      review={{ ...review, recommendation: "pass", findings: [] }}
    />,
  );
  expect(
    screen.getByText(/Проверка человеком всё равно обязательна/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Изображения не проверялись/)).toBeInTheDocument();
});

it("shows human triage and immutable rationale as inert text, not approval or a fix", () => {
  const decision: components["schemas"]["EditorialDecisionView"] = {
    id: "decision-one",
    run_id: "run-one",
    artifact_id: "artifact-one",
    artifact_hash: "c".repeat(64),
    revision_id: review.revision_id,
    content_hash: review.content_hash,
    finding_index: 0,
    finding_hash: "d".repeat(64),
    sequence: 1,
    status: "dismissed",
    reason: "<script>synthetic()</script>",
    actor_id: "owner-one",
    created_at: "2026-09-03T10:00:00Z",
  };
  const { container, rerender } = render(
    <EditorialResult
      review={review}
      timezone="Europe/Moscow"
      triage={{
        run_id: decision.run_id,
        artifact_id: decision.artifact_id,
        artifact_hash: decision.artifact_hash,
        revision_id: review.revision_id,
        content_hash: review.content_hash,
        version: 1,
        findings: [
          {
            finding_index: 0,
            finding_hash: decision.finding_hash,
            status: "dismissed",
            latest_decision: decision,
          },
        ],
        recent_history: [decision],
        next_before: 2,
        warning: "Решения не исправляют и не одобряют пост.",
      }}
    />,
  );
  expect(
    screen.getByText(/Решение человека: Отклонено человеком/),
  ).toBeInTheDocument();
  expect(screen.getByText("История решений по замечаниям")).toBeInTheDocument();
  expect(
    screen.getByText(/Более ранняя история доступна через чат/),
  ).toBeInTheDocument();
  expect(container.querySelector("script")).toBeNull();
  expect(screen.getByText(/13:00:00/)).toBeInTheDocument();
  expect(screen.getByText(/Часовой пояс: Europe\/Moscow/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  // Parent removes the complete report when its evidence/authorization becomes stale.
  rerender(<p>Отчёт недоступен</p>);
  expect(screen.queryByText(/Отклонено человеком/)).not.toBeInTheDocument();
  expect(container.querySelector("details")).toBeNull();
});
