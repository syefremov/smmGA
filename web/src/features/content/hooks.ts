import { useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "../../api/content";
import { ApiError } from "../../api/client";

export function useCommand(wid: string) {
  const cache = useQueryClient();
  const pending = useRef<{ body: string; key: string } | null>(null);
  return useMutation({
    mutationFn: (input: api.Input) => {
      const body = JSON.stringify(input);
      if (pending.current && pending.current.body !== body)
        throw new ApiError(409, "pending_request_changed", "");
      if (!pending.current)
        pending.current = { body, key: crypto.randomUUID() };
      return api.execute(wid, {
        ...input,
        idempotency_key: pending.current.key,
      });
    },
    onSuccess: async () => {
      pending.current = null;
      await cache.invalidateQueries({ queryKey: [wid] });
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.status < 500 &&
        error.code !== "pending_request_changed"
      )
        pending.current = null;
    },
  });
}

export function time(value: string, zone: string) {
  return new Intl.DateTimeFormat("ru", {
    timeZone: zone,
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
