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
