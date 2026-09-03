import { client } from "./client";
import type { components } from "./schema";

export type SubmitFile = components["schemas"]["SubmitFile"];
export type CancelFile = components["schemas"]["CancelIngestion"];

function required<T>(data: T | undefined): T {
  if (data === undefined) throw new Error("empty_response");
  return data;
}

export async function files(
  workspace_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET("/api/v1/workspaces/{workspace_id}/knowledge/files", {
        params: { path: { workspace_id }, query: { cursor } },
        signal,
      })
    ).data,
  );
}

export async function file(
  workspace_id: string,
  file_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge/files/{file_id}",
        {
          params: { path: { workspace_id, file_id } },
          signal,
        },
      )
    ).data,
  );
}

export async function submit(
  workspace_id: string,
  body: SubmitFile,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.POST("/api/v1/workspaces/{workspace_id}/knowledge/files", {
        params: { path: { workspace_id } },
        body,
        signal,
      })
    ).data,
  );
}

export async function cancel(
  workspace_id: string,
  body: CancelFile,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.POST(
        "/api/v1/workspaces/{workspace_id}/knowledge/jobs/cancel",
        {
          params: { path: { workspace_id } },
          body,
          signal,
        },
      )
    ).data,
  );
}

export async function history(
  workspace_id: string,
  job_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge/jobs/{kind}/{job_id}/history",
        {
          params: { path: { workspace_id, kind: "file", job_id } },
          signal,
        },
      )
    ).data,
  );
}
