import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { CopyDraftResult, CopywriterResult } from "./CopyDraftResult";
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

const adoption: components["schemas"]["CopyAdoptionView"] = {
  id: "receipt",
  run_id: "run",
  artifact_id: "artifact",
  artifact_hash: "a".repeat(64),
  input_id: "input",
  input_hash: "b".repeat(64),
  post_id: "post&unsafe",
  source_revision_id: "source",
  source_content_hash: "c".repeat(64),
  revision_id: "new-revision",
  content_hash: "d".repeat(64),
  post_version: 6,
  preview_hash: "e".repeat(64),
  actor_id: "human-owner",
  created_at: "2026-09-03T12:00:00Z",
  reason: '<img src=x onerror="alert(1)">',
  historical_only: true,
  warning: "Историческая запись; перечитайте пост.",
  preflight: {
    revision_id: "new-revision",
    content_hash: "d".repeat(64),
    checked_at: "2026-09-03T12:00:00Z",
    passed: true,
    findings: [],
    checked_record_ids: [],
    ai_review: "not_run",
  },
};

it("keeps a private historical receipt readable without a stale draft or approval controls", () => {
  const { container } = render(
    <CopywriterResult
      adoption={adoption}
      workspaceId="workspace&other"
      timezone="Europe/Moscow"
    />,
  );
  expect(
    screen.getByRole("region", { name: "История сохранения AI-текста" }),
  ).toBeInTheDocument();
  expect(screen.getByText("new-revision")).toBeInTheDocument();
  expect(screen.getByText(adoption.content_hash)).toBeInTheDocument();
  expect(screen.getByText(/human-owner.*15:00/)).toBeInTheDocument();
  expect(screen.getByText(/Историческая запись/)).toBeInTheDocument();
  expect(
    screen.getByText(/без детерминированных блокеров, не одобрение/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Рабочие копии сохранены/)).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Открыть пост для актуальной проверки" }),
  ).toHaveAttribute(
    "href",
    "/app/content?workspace=workspace%26other&post=post%26unsafe",
  );
  expect(
    screen.queryByRole("region", { name: "Предложение копирайтера" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByText(`Основание: ${adoption.reason}`)).toBeInTheDocument();
});

it("reports blockers at save time without implying current approval", () => {
  render(
    <CopywriterResult
      adoption={{
        ...adoption,
        preflight: {
          ...adoption.preflight,
          passed: false,
          findings: [
            { code: "knowledge_gap", severity: "blocker", location: "body" },
          ],
        },
      }}
      workspaceId="workspace"
      timezone="UTC"
    />,
  );
  expect(screen.getByText(/есть блокирующие замечания/)).toBeInTheDocument();
  expect(screen.getByText(/Замечания при сохранении: 1/)).toBeInTheDocument();
  expect(screen.getByText(/blocker.*knowledge_gap/)).toBeInTheDocument();
  expect(screen.getByText(/human-owner.*12:00/)).toBeInTheDocument();
});
