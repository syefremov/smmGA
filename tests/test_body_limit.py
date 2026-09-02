import pytest
from starlette.types import Message

from smm_gpt.api.body_limit import BodyLimitMiddleware


@pytest.mark.parametrize(
    "chunks,headers,expected",
    [
        ([b"small"], [], 200),
        ([b"ab", b"cdef"], [], 413),
        ([b"small"], [(b"content-length", b"6")], 413),
        ([b"small"], [(b"content-length", b"bad")], 413),
    ],
)
async def test_request_bound(
    chunks: list[bytes], headers: list[tuple[bytes, bytes]], expected: int
) -> None:
    messages: list[Message] = []
    pending = iter(
        [
            {"type": "http.request", "body": body, "more_body": i < len(chunks) - 1}
            for i, body in enumerate(chunks)
        ]
    )

    async def receive() -> Message:
        return next(pending)

    async def send(message: Message) -> None:
        messages.append(message)

    async def app(scope: object, receive: object, send: object) -> None:
        messages.append({"type": "http.response.start", "status": 200})

    await BodyLimitMiddleware(app, 5)({"type": "http", "headers": headers}, receive, send)
    assert messages[0]["status"] == expected
