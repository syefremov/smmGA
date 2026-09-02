"""Bound request bodies before JSON/base64 parsing, including chunked MCP transports."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from smm_gpt.core.request_context import request_id


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int = 3 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def reject() -> None:
            response = JSONResponse(
                {
                    "detail": "request_too_large",
                    "error": {
                        "code": "request_too_large",
                        "correlation_id": str(request_id()),
                    },
                },
                status_code=413,
            )
            await response(scope, receive, send)

        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.limit + 1
                if declared < 0 or declared > self.limit:
                    await reject()
                    return
        buffer = bytearray()
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                await reject()
                return
            buffer.extend(body)
            if not message.get("more_body", False):
                break
        buffered = bytes(buffer)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
