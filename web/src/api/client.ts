import createClient from "openapi-fetch";

import type { paths } from "./schema";

export const client = createClient<paths>({
  baseUrl: "",
  fetch: (request) => globalThis.fetch(request),
});

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public correlation: string,
  ) {
    super(code);
  }
}

client.use({
  onRequest({ request }) {
    if (!["GET", "HEAD"].includes(request.method)) {
      const csrf = document.cookie
        .split("; ")
        .find((v) => v.startsWith("__Host-smm-csrf="))
        ?.split("=")[1];
      if (csrf) request.headers.set("X-CSRF-Token", csrf);
    }
    return request;
  },
  async onResponse({ request, response }) {
    if (response.ok) return response;
    const body = await response
      .clone()
      .json()
      .catch(() => ({}));
    if (
      [401, 403].includes(response.status) &&
      !request.url.endsWith("/session")
    ) {
      window.dispatchEvent(new Event("smm-access-changed"));
    }
    throw new ApiError(
      response.status,
      body.error?.code ?? body.detail ?? "request_failed",
      response.headers.get("X-Request-ID") ?? "",
    );
  },
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
