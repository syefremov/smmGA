import createClient from "openapi-fetch";

import type { paths } from "./schema";

const client = createClient<paths>({
  baseUrl: "",
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchSystemStatus(signal?: AbortSignal) {
  const { data, error, response } = await client.GET("/api/v1/system/status", {
    signal,
  });

  if (!response.ok || error || !data) {
    throw new Error("Не удалось получить состояние системы");
  }

  return data;
}

export type SystemStatus = Awaited<ReturnType<typeof fetchSystemStatus>>;
