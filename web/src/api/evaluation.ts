import { client } from "./client";

function required<T>(data: T | undefined): T {
  if (data === undefined) throw new Error("empty_response");
  return data;
}

export async function datasets(
  workspace_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge/evaluations/datasets",
        {
          params: { path: { workspace_id }, query: { cursor } },
          signal,
        },
      )
    ).data,
  );
}

export async function runs(
  workspace_id: string,
  dataset_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/knowledge/evaluations/runs",
        {
          params: { path: { workspace_id }, query: { dataset_id, cursor } },
          signal,
        },
      )
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
        "/api/v1/workspaces/{workspace_id}/knowledge/evaluations/runs/{run_id}",
        {
          params: { path: { workspace_id, run_id } },
          signal,
        },
      )
    ).data,
  );
}
