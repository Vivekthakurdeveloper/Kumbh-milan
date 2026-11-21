import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.context import request_id_var
from app.common.errors import AppError

logger = logging.getLogger("kumbh_setu.errors")

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    416: "range_not_satisfiable",
    422: "validation_failed",
    429: "too_many_requests",
}


def _body(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code, "message": message, "request_id": request_id_var.get(),
                "details": jsonable_encoder(details),
            }
        },
        headers=headers,
    )


def _registration_validation_message(errors: list[dict]) -> str:
    fields = {loc[-1] for err in errors if (loc := err.get("loc"))}
    messages = {str(err.get("msg", "")).lower() for err in errors}
    missing = sorted(
        str(err["loc"][-1])
        for err in errors
        if err.get("type") == "missing" and err.get("loc")
    )
    if missing:
        return f"Missing required field: {missing[0]}."
    if {"username", "phone_number"} & fields or any("same mobile number" in message for message in messages):
        return "Enter the same valid 10-digit mobile number for username and phone_number."
    if "email" in fields:
        return "Enter a valid email address."
    if "password" in fields:
        return "Password must be at least 8 characters."
    return "Request validation failed."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return _body(exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Submitted values are deliberately not echoed back: they may contain personal data.
        errors = exc.errors()
        details = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
            for err in errors
        ]
        if request.url.path == "/api/v1/auth/register":
            return _body(422, "validation_error", _registration_validation_message(errors), details)
        return _body(422, "validation_failed", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
        return _body(exc.status_code, code, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled error", exc_info=exc, extra={"request_id": request_id_var.get()}
        )
        return _body(500, "internal_error", "An unexpected error occurred")
