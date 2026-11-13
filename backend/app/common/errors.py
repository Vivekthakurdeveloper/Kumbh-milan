from typing import Any


class AppError(Exception):
    """Domain error mapped to a stable JSON body: {"error": {"code", "message", "request_id", "details"}}."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class InvalidTransition(Conflict):
    code = "invalid_status_transition"


class Gone(AppError):
    status_code = 410
    code = "gone"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMedia(AppError):
    status_code = 415
    code = "unsupported_media_type"


class RangeNotSatisfiable(AppError):
    status_code = 416
    code = "range_not_satisfiable"


class TooManyRequests(AppError):
    status_code = 429
    code = "too_many_requests"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
