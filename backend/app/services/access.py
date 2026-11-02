import uuid

from sqlalchemy.orm import Session

from app.auth.scope import case_visibility_clause
from app.common.errors import NotFound
from app.models import Case, User
from app.repositories import cases as case_repo
from app.services.case_types import CaseTypeSpec

CASE_NOT_FOUND = "Case not found"


def load_case(
    db: Session,
    user: User,
    case_id: uuid.UUID,
    *,
    spec: CaseTypeSpec | None = None,
    for_update: bool = False,
) -> Case:
    """Returns a case the user may see. Out-of-scope and wrong-type ids are 404 so existence is not leaked."""
    case = case_repo.get_case(db, case_id, case_visibility_clause(user), for_update=for_update)
    if case is None or (spec is not None and case.case_type != spec.case_type):
        raise NotFound(CASE_NOT_FOUND, code="case_not_found")
    return case
