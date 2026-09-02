"""Reusable dependency probes that never touch real infrastructure."""


class FakeProbe:
    def __init__(self, name: str, available: bool = True) -> None:
        self.name = name
        self.available = available
        self.closed = False

    async def ping(self) -> None:
        if not self.available:
            raise ConnectionError("simulated dependency failure")

    async def close(self) -> None:
        self.closed = True
