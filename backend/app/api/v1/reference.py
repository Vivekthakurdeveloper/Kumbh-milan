"""Reference data for clients and explainable duplicate candidates."""

import uuid

from fastapi import APIRouter, Path

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.schemas.reference import DuplicateCandidate, EnumLabel, ItemCategoryRead, ItemCategoryUpsert
from app.services import reference as reference_service

router = APIRouter(tags=["Reference data"])

CategoryCode = Path(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.get("/public/reference/enums", response_model=dict[str, list[EnumLabel]],
            summary="Stable codes with English and Hindi labels (no authentication)")
def enums() -> dict[str, list[EnumLabel]]:
    return reference_service.enum_labels()


@router.get("/public/reference/item-categories", response_model=list[ItemCategoryRead],
            summary="Active lost/found item categories (no authentication)")
def item_categories(db: DbSession, include_inactive: bool = False):
    return reference_service.list_item_categories(db, include_inactive=include_inactive)


@router.put("/reference/item-categories/{code}", response_model=ItemCategoryRead, responses=AUTHED,
            summary="Create or update an item category (administrators)")
def upsert_item_category(
    payload: ItemCategoryUpsert, principal: CurrentPrincipal, db: DbSession, code: str = CategoryCode
):
    return reference_service.upsert_item_category(db, principal.user, code, **payload.model_dump())


@router.get("/cases/{case_id}/duplicates", response_model=list[DuplicateCandidate], responses={**AUTHED, **errors(404)},
            summary="Likely duplicate registrations of the same type, with reasons. Never merges automatically.")
def duplicates(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return reference_service.find_duplicates(db, principal.user, case_id)
