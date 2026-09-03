import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { CopyDraftResult } from "./CopyDraftResult";
import type { components } from "../../api/schema";

afterEach(cleanup);
const draft: components["schemas"]["CopyDraft"] = {
  revision_id: "synthetic-revision",
  content_hash: "a".repeat(64),
  context_hash: "b".repeat(64),
  outcome: "draft",
  variants: [
    {
      variant_index: 0,
      text: '<img src=x onerror="alert(1)">',
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
  knowledge_gaps: ["Missing expiry"],
};

it("shows inert draft, exact provenance and gaps without apply/approval controls", () => {
  const { container } = render(<CopyDraftResult draft={draft} />);
  expect(screen.getByText(draft.variants[0].text)).toBeInTheDocument();
  expect(screen.getByText("synthetic-revision")).toBeInTheDocument();
  expect(screen.getByText(/synthetic-fact/)).toBeInTheDocument();
  expect(screen.getByText(/Synthetic source/)).toBeInTheDocument();
  expect(screen.getByText("Missing expiry")).toBeInTheDocument();
  expect(screen.getByText(/Текст не сохранён/)).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("displays abstention and does not turn empty warnings into a passed check", () => {
  render(
    <CopyDraftResult
      draft={{
        ...draft,
        outcome: "insufficient_evidence",
        variants: [],
        warnings: [],
      }}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Недостаточно оснований" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/это не результат проверки/)).toBeInTheDocument();
  expect(screen.queryByText(draft.variants[0].text)).not.toBeInTheDocument();
});
