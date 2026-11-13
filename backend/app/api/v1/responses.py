from app.schemas.common import ErrorResponse

_DESCRIPTIONS = {
    401: "Missing, expired, or revoked credentials",
    403: "Authenticated but not permitted",
    404: "Not found or outside your authorised scope",
    409: "Conflicts with the current state",
    410: "The link has expired",
    413: "Payload too large",
    415: "Unsupported media type",
    416: "Requested range is not satisfiable",
    422: "Validation failed",
    429: "Rate limited or temporarily locked",
    503: "Temporarily unavailable or not configured",
}


def errors(*codes: int) -> dict:
    return {code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in codes}


AUTHED = errors(401, 403, 422, 429)
