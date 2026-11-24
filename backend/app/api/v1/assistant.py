"""Staff help assistant: questions about using the application, answered from the reviewed help content."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.api.v1.responses import errors
from app.auth.dependencies import CurrentPrincipal, DbSession, requires
from app.auth.permissions import Permission
from app.schemas.assistant import AssistantAnswerResponse, AssistantAskRequest
from app.services import assistant

router = APIRouter(
    prefix="/assistant", tags=["Help assistant"], dependencies=[Depends(requires(Permission.ASSISTANT_USE))]
)
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Optional client key (8-128 chars). A retry with the same key and question replays the answer",
    ),
]


@router.post(
    "/ask",
    response_model=AssistantAnswerResponse,
    responses=errors(401, 403, 409, 422, 429, 503),
    summary="Ask the help assistant how to use Kumbh Milan",
)
def ask(
    payload: AssistantAskRequest, principal: CurrentPrincipal, db: DbSession, response: Response,
    idempotency_key: IdempotencyKey = None,
) -> AssistantAnswerResponse:
    """
    Staff only (`assistant:use`: help desk operators, police officers, administrators); PUBLIC accounts get 403.
    Answers how-to questions about the application in English (`en`, default) or Hindi (`hi`).

    The assistant answers only from reviewed help content. It cannot look up cases, people, or contact details,
    and when the content does not cover a question it says it cannot confirm and points to an administrator or
    help desk supervisor (`sources: []`).

    - `422 validation_failed`: missing, empty, or whitespace-only `question`; more than 1000 characters;
      `locale` other than `en`/`hi`; malformed `Idempotency-Key`.
    - `409 idempotency_key_reused`: the key was already used for a different question.
    - `429 too_many_requests`: per-user limit (`RATE_LIMIT_ASSISTANT_PER_MINUTE`, default 20), with `Retry-After`.
    - `503 assistant_unavailable`: the assistant is switched off or its language model is unreachable.

    A replay of an earlier answer has the `Idempotent-Replayed: true` header.
    """
    result, replayed = assistant.ask(
        db, principal.user, question=payload.question, locale=payload.locale, idempotency_key=idempotency_key
    )
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return AssistantAnswerResponse(answer=result.text, locale=result.locale, sources=list(result.sources))
