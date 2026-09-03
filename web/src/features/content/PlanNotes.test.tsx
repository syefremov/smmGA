import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { components } from "../../api/schema";
import { PlanNotes, PlanNotesResult } from "./PlanNotes";
import { Library } from "./Library";

const api = vi.hoisted(() => ({
  planNotes: vi.fn(),
  record: vi.fn(),
  records: vi.fn(),
}));
vi.mock("../../api/content", async (original) => ({
  ...(await original<typeof import("../../api/content")>()),
  ...api,
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  window.history.replaceState({}, "", "/");
});
const notes: components["schemas"]["PlanNotesView"] = {
  id: "notes",
  plan_id: "plan",
  plan_hash: "a".repeat(64),
  content_hash: "b".repeat(64),
  actor_id: "owner",
  created_at: "2026-09-03T12:00:00Z",
  requested_plan_id: "plan",
  exact_version: true,
  historical_only: true,
  warning: "История, не одобрение",
  body: {
    fact_ids: ["fact"],
    evidence_record_ids: ["source", "fact"],
    warnings: ["Human review"],
    knowledge_gaps: ["Missing price"],
    slots: [
      {
        slot_index: 0,
        planned_at: "2026-09-05T12:00:00Z",
        destination: "vk:group:123",
        owner_id: "owner",
        topic: "<img src=x>",
        rationale: "Explain",
        evidence: [
          { fact_id: "fact", quote: "<img", source_quote: "Synthetic source" },
        ],
      },
    ],
  },
};

it("shows scoped disclosed notes as inert text and labels ancestor versions explicitly", () => {
  const view = render(
    <PlanNotesResult notes={notes} timezone="Europe/Moscow" />,
  );
  expect(screen.getByText(/этой точной версии/)).toBeInTheDocument();
  expect(screen.getByText("Missing price")).toBeInTheDocument();
  expect(screen.getByText(/В факте: «Synthetic source»/)).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: /Слот 1.*15:00/ }),
  ).toBeInTheDocument();
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  view.rerender(
    <PlanNotesResult
      notes={{ ...notes, exact_version: false, requested_plan_id: "new-plan" }}
      timezone="UTC"
    />,
  );
  expect(
    screen.getByText(/Исторические заметки предыдущей версии/),
  ).toBeInTheDocument();
  expect(screen.getByText("Missing price")).toBeInTheDocument();
});

it("does not retain notes across workspace/record changes or denied refetch", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  api.planNotes.mockResolvedValue(notes);
  const panel = (workspaceId: string, planId = "plan") => (
    <QueryClientProvider client={cache}>
      <PlanNotes workspaceId={workspaceId} planId={planId} timezone="UTC" />
    </QueryClientProvider>
  );
  const view = render(panel("first"));
  expect(await screen.findByText("Missing price")).toBeInTheDocument();
  api.planNotes.mockResolvedValue(null);
  view.rerender(panel("second"));
  expect(screen.queryByText("Missing price")).not.toBeInTheDocument();
  expect(
    await screen.findByText(/нет сохранённых AI-оснований/),
  ).toBeInTheDocument();
  api.planNotes.mockResolvedValue(notes);
  view.rerender(panel("second", "different-plan"));
  expect(await screen.findByText("Missing price")).toBeInTheDocument();
  api.planNotes.mockRejectedValue(
    new ApiError(403, "access_denied", "synthetic"),
  );
  await act(async () => {
    await cache.invalidateQueries({
      queryKey: ["second", "plan-notes", "different-plan"],
    });
  });
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(screen.queryByText("Missing price")).not.toBeInTheDocument();
  view.unmount();
  cache.clear();
});

it("opens the exact record from a receipt link with read-only viewer notes", async () => {
  window.history.replaceState(
    {},
    "",
    "/app/materials?record=plan&kind=content_plan",
  );
  api.records.mockResolvedValue({ items: [], next_cursor: null });
  api.planNotes.mockResolvedValue(notes);
  api.record.mockResolvedValue({
    id: "plan",
    number: 2,
    content_hash: notes.plan_hash,
    expires_at: "2026-09-10T00:00:00Z",
    confirmed_by: null,
    body: {
      kind: "content_plan",
      name: "Saved plan",
      brand_id: "brand",
      campaign_id: "campaign",
      slots: [],
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <Library
        workspace={{
          id: "workspace",
          name: "Test",
          timezone: "UTC",
          permissions: ["workspace.read"],
        }}
        offline={false}
      />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("heading", { name: "Saved plan" }),
  ).toBeInTheDocument();
  expect(await screen.findByText("Missing price")).toBeInTheDocument();
  expect(api.record).toHaveBeenCalledWith(
    "workspace",
    "plan",
    expect.anything(),
  );
  expect(screen.getByLabelText("Тип материала")).toHaveValue("content_plan");
  expect(
    screen.queryByRole("button", { name: "Подтвердить новую версию" }),
  ).not.toBeInTheDocument();
  api.record.mockRejectedValue(new ApiError(403, "access_denied", "synthetic"));
  await act(async () => {
    await cache.invalidateQueries({
      queryKey: ["workspace", "record", "plan"],
    });
  });
  await waitFor(() =>
    expect(screen.queryByText("Missing price")).not.toBeInTheDocument(),
  );
  expect(
    screen.queryByRole("heading", { name: "Saved plan" }),
  ).not.toBeInTheDocument();
  view.unmount();
  cache.clear();
});
