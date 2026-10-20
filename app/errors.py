"""One error format for every HTTP error response.

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from booking_core import errors as domain

STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}

DOMAIN_STATUS = {
    domain.ValidationError: 422,
    domain.RoomOccupied: 409,
    domain.BookingNotFound: 404,
    domain.IdempotencyKeyReused: 422,
    domain.InjectedFault: 503,
}


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] = []


class ErrorResponse(BaseModel):
    error: ErrorBody


class RateLimitExceeded(Exception):
    def __init__(self, decision):
        self.decision = decision


def error_response(request, status, code, message, details=None, headers=None):
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=getattr(request.state, "request_id", None),
            details=details or [],
        )
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(),
        headers=headers,
    )


def error_responses(*statuses):
    """OpenAPI `responses=` entry documenting the error format."""
    return {status: {"model": ErrorResponse} for status in statuses}


def install_error_handlers(app: FastAPI):
    @app.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, exc: RequestValidationError):
        # Report the field and reason only; never echo the submitted input.
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or None,
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return error_response(
            request, 422, "validation_error", "Request is invalid", details
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return error_response(
            request,
            exc.status_code,
            STATUS_CODES.get(exc.status_code, "http_error"),
            str(exc.detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(domain.BookingError)
    async def booking_error(request: Request, exc: domain.BookingError):
        status = DOMAIN_STATUS.get(type(exc), 400)
        headers = {"Retry-After": "1"} if status == 503 else None
        return error_response(
            request, status, exc.code, exc.message, exc.details, headers
        )

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(request: Request, exc: RateLimitExceeded):
        decision = exc.decision
        return error_response(
            request,
            429,
            "rate_limited",
            f"Limit of {decision.limit} requests per "
            f"{decision.window_seconds:g} seconds reached; retry after "
            f"{decision.retry_after} seconds",
            headers={"Retry-After": str(decision.retry_after), **decision.headers()},
        )
