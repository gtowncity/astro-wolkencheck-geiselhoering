"""Source-runner contract used by the runtime coordinator."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from nowcast_service.runtime.models import SourceSnapshot


class SourceRunError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class SourceRunner(Protocol):
    source_id: str

    async def run(self, *, evaluated_at: datetime) -> SourceSnapshot: ...
