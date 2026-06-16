"""Typed error responses returned to clients of the API.

Every response (success or error) carries a request_id — UUID v4 stamped
in main.py middleware. When a lead goes missing, request_id is the
debug anchor across logs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "CLIENT_NOT_FOUND",
    "META_SIGNATURE_INVALID",
    "GHL_UNAVAILABLE",
    "INTERNAL_ERROR",
]


class ApiError(BaseModel):
    code: ErrorCode
    request_id: str
    detail: dict[str, Any] | None = None


class ApiException(Exception):
    """Raise inside handlers to surface a typed JSON error.

    main.py installs an exception handler that converts this into a
    Response with the documented status code + ApiError body.
    """

    def __init__(
        self,
        code: ErrorCode,
        status_code: int,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.detail = detail
