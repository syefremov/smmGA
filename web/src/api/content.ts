import { client } from "./client";
import type { components, paths } from "./schema";

export type Command =
  paths["/api/v1/workspaces/{workspace_id}/content/commands"]["post"]["requestBody"]["content"]["application/json"];
export type Input = Command extends infer C
  ? C extends Command
    ? Omit<C, "idempotency_key">
    : never
  : never;
export type Post = components["schemas"]["PostView"];
export type Revision = components["schemas"]["RevisionBody"];
export type RecordView = components["schemas"]["RecordView"];
export type RecordKind = components["schemas"]["RecordKind"];
export type PostState = components["schemas"]["PostState"];
export type Package = components["schemas"]["PackageSummary"];
export type Copy = components["schemas"]["WorkingCopyView"];
export type Artifact = components["schemas"]["Artifact"];
export const postStates: Record<PostState, string> = {
  draft: "Черновик",
  in_review: "На проверке",
  rejected: "На доработке",
  approved: "Одобрено",
  package_ready: "Пакет готов",
};
export const recordKinds: Record<RecordKind, string> = {
  source_item: "Материал источника",
  brand_profile: "Правила бренда",
  product_version: "Версия продукта",
  product_fact: "Факт продукта",
  claim_policy: "Правила утверждений",
  research: "Исследование",
  campaign: "Кампания",
  content_plan: "Контент-план",
  brief: "Бриф",
  idea: "Идея",
};

function required<T>(data: T | undefined): T {
  if (data === undefined) throw new Error("empty_response");
  return data;
}

export async function execute(workspace_id: string, body: Command) {
  return required(
    (
      await client.POST("/api/v1/workspaces/{workspace_id}/content/commands", {
        params: { path: { workspace_id } },
        body,
      })
    ).data,
  );
}
export async function posts(
  workspace_id: string,
  state?: PostState,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET("/api/v1/workspaces/{workspace_id}/content/posts", {
        params: { path: { workspace_id }, query: { state, cursor, limit: 25 } },
        signal,
      })
    ).data,
  );
}
export async function post(
  workspace_id: string,
  post_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/content/posts/{post_id}",
        { params: { path: { workspace_id, post_id } }, signal },
      )
    ).data,
  );
}
export async function workingCopy(
  workspace_id: string,
  post_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/content/posts/{post_id}/working-copy",
        { params: { path: { workspace_id, post_id } }, signal },
      )
    ).data,
  );
}
export async function preflight(
  workspace_id: string,
  post_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/content/posts/{post_id}/preflight",
        { params: { path: { workspace_id, post_id } }, signal },
      )
    ).data,
  );
}
export async function records(
  workspace_id: string,
  kind?: RecordKind,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET("/api/v1/workspaces/{workspace_id}/content/records", {
        params: { path: { workspace_id }, query: { kind, cursor, limit: 25 } },
        signal,
      })
    ).data,
  );
}
export async function packages(
  workspace_id: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET("/api/v1/workspaces/{workspace_id}/content/packages", {
        params: { path: { workspace_id }, query: { cursor, limit: 25 } },
        signal,
      })
    ).data,
  );
}
export async function packageRead(
  workspace_id: string,
  package_id: string,
  signal?: AbortSignal,
) {
  return required(
    (
      await client.GET(
        "/api/v1/workspaces/{workspace_id}/content/packages/{package_id}",
        { params: { path: { workspace_id, package_id } }, signal },
      )
    ).data,
  );
}
