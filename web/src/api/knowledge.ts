import { client } from "./client";
import type { components } from "./schema";

export type Citation = components["schemas"]["Citation"];
function required<T>(data: T | undefined): T {
  if (data === undefined) throw new Error("empty_response");
  return data;
}
export async function documents(
  workspace_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge/documents",
        {
          params: { path: { workspace_id }, query: { cursor } },
          signal,
        },
      )
    ).data,
  );
}
export async function document(
  workspace_id: string,
  document_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge/documents/{document_id}",
        {
          params: { path: { workspace_id, document_id } },
          signal,
        },
      )
    ).data,
  );
}
export async function search(
  workspace_id: string,
  brand_id: string,
  query: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.POST("/api/v1/workspaces/{workspace_id}/knowledge/search", {
        params: { path: { workspace_id } },
        body: { brand_id, query, limit: 5 },
        signal,
      })
    ).data,
  );
}
export async function profiles(workspace_id: string, signal?: AbortSignal) {
  return required(
    (
      await client.GET("/api/v1/workspaces/{workspace_id}/knowledge/profiles", {
        params: { path: { workspace_id } },
        signal,
      })
    ).data,
  );
}
export async function runs(
  workspace_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET("/api/v1/workspaces/{workspace_id}/knowledge/runs", {
        params: { path: { workspace_id }, query: { cursor } },
        signal,
      })
    ).data,
  );
}
export async function run(
  workspace_id: string,
  run_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge/runs/{run_id}",
        {
          params: { path: { workspace_id, run_id } },
          signal,
        },
      )
    ).data,
  );
}
export async function notes(
  workspace_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET("/api/v1/workspaces/{workspace_id}/knowledge/notes", {
        params: { path: { workspace_id }, query: { cursor } },
        signal,
      })
    ).data,
  );
}
