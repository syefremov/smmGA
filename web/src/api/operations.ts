import { client } from "./client";
import type { components } from "./schema";

export type Session = components["schemas"]["SessionView"];
export type Workspace = components["schemas"]["WorkspaceView"];
export type WorkItem = components["schemas"]["WorkItemView"];
export type WorkState = components["schemas"]["WorkState"];
export type CatalogKind = components["schemas"]["CatalogKind"];
export const states: Record<WorkState, string> = {
  open: "Открыта",
  in_progress: "В работе",
  done: "Завершена",
  cancelled: "Отменена",
};

export async function fetchSession(signal?: AbortSignal) {
  const { data } = await client.GET("/api/v1/session", { signal });
  if (!data) throw new Error("empty_response");
  return data;
}

export async function listWork(
  workspace_id: string,
  state?: WorkState,
  cursor?: string,
  signal?: AbortSignal,
) {
  const { data } = await client.GET(
    "/api/v1/workspaces/{workspace_id}/work-items",
    {
      params: { path: { workspace_id }, query: { state, cursor, limit: 25 } },
      signal,
    },
  );
  if (!data) throw new Error("empty_response");
  return data;
}

export async function createWork(
  workspace_id: string,
  body: components["schemas"]["CreateWorkItem"],
) {
  const { data } = await client.POST(
    "/api/v1/workspaces/{workspace_id}/work-items",
    { params: { path: { workspace_id } }, body },
  );
  if (!data) throw new Error("empty_response");
  return data;
}

export async function transitionWork(
  workspace_id: string,
  item: WorkItem,
  state: WorkState,
) {
  const { data } = await client.POST(
    "/api/v1/workspaces/{workspace_id}/work-items/{item_id}/transition",
    {
      params: { path: { workspace_id, item_id: item.id } },
      body: { expected_version: item.version, state },
    },
  );
  if (!data) throw new Error("empty_response");
  return data;
}

export async function listCatalog(
  workspace_id: string,
  kind: CatalogKind,
  cursor?: string,
  signal?: AbortSignal,
) {
  const { data } = await client.GET(
    "/api/v1/workspaces/{workspace_id}/catalog/{kind}",
    {
      params: { path: { workspace_id, kind }, query: { cursor, limit: 25 } },
      signal,
    },
  );
  if (!data) throw new Error("empty_response");
  return data;
}

export async function listAudit(
  workspace_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  const { data } = await client.GET("/api/v1/workspaces/{workspace_id}/audit", {
    params: { path: { workspace_id }, query: { cursor, limit: 25 } },
    signal,
  });
  if (!data) throw new Error("empty_response");
  return data;
}
