import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import SessionLocal
from app.models import (
    AuditEvent,
    Case,
    CaseAssignment,
    CaseMatch,
    CaseNote,
    CaseNumberCounter,
    CaseStatusHistory,
    MissingPersonDetails,
    User,
)
from app.common.time import utcnow
from app.models.enums import CaseStatus, CaseType, MatchDomain, MatchSource, MatchStatus, NoteType, Role
from app.schemas.cases import MissingPersonCreate
from app.services import cases as case_service
from app.services.case_types import MISSING_PERSON
from tests.helpers import create_station, create_user


def _base_case(db, station_id, user_id, case_type=CaseType.MISSING_PERSON, number=None) -> Case:
    case = Case(
        id=uuid.uuid4(), case_number=number or f"T-{uuid.uuid4().hex[:10]}", case_type=case_type,
        station_id=station_id, reported_by_user_id=user_id, status=CaseStatus.OPEN,
    )
    db.add(case)
    db.flush()
    return case


@pytest.fixture
def seed():
    station = create_station("DB-01")
    user = create_user("dbofficer", Role.POLICE_OFFICER, station, "B-1")
    return station, user


def test_case_requires_existing_station_and_reporter(db, seed):
    _, user = seed
    db.add(Case(id=uuid.uuid4(), case_number="X-1", case_type=CaseType.LOST_ITEM, station_id=uuid.uuid4(),
                reported_by_user_id=user, status=CaseStatus.OPEN))
    with pytest.raises(IntegrityError, match="fk_cases_station_id_stations"):
        db.flush()


def test_subtype_must_match_case_type(db, seed):
    station, user = seed
    case = _base_case(db, station, user, CaseType.FOUND_PERSON)
    db.add(MissingPersonDetails(case_id=case.id))
    with pytest.raises(IntegrityError, match="fk_missing_person_details_case"):
        db.flush()


def test_case_number_is_unique(db, seed):
    station, user = seed
    _base_case(db, station, user, number="KSLF-MP-2027-000001")
    with pytest.raises(IntegrityError, match="uq_cases_case_number"):
        _base_case(db, station, user, number="KSLF-MP-2027-000001")


def test_closed_at_must_match_status(db, seed):
    station, user = seed
    case = _base_case(db, station, user)
    case.status = CaseStatus.CLOSED
    with pytest.raises(IntegrityError, match="closed_at_matches_status"):
        db.flush()


def test_match_rejects_incompatible_case_types(db, seed):
    station, user = seed
    a = _base_case(db, station, user, CaseType.MISSING_PERSON)
    b = _base_case(db, station, user, CaseType.FOUND_ITEM)
    db.add(CaseMatch(match_domain=MatchDomain.PERSON, left_case_id=a.id, left_case_type=a.case_type,
                     right_case_id=b.id, right_case_type=b.case_type, proposed_by_user_id=user))
    with pytest.raises(IntegrityError, match="domain_matches_case_types"):
        db.flush()


def test_found_case_can_have_only_one_confirmed_match(db, seed):
    station, user = seed
    found = _base_case(db, station, user, CaseType.FOUND_PERSON)
    for _ in range(2):
        missing = _base_case(db, station, user, CaseType.MISSING_PERSON)
        db.add(CaseMatch(match_domain=MatchDomain.PERSON, left_case_id=missing.id, left_case_type=missing.case_type,
                         right_case_id=found.id, right_case_type=found.case_type, proposed_by_user_id=user,
                         status=MatchStatus.CONFIRMED, reviewed_by_user_id=user, reviewed_at=utcnow(),
                         review_evidence="verified in person"))
    with pytest.raises(IntegrityError, match="uq_case_matches_confirmed_right"):
        db.flush()


def test_automatic_match_requires_score_and_no_proposer(db, seed):
    station, user = seed
    a = _base_case(db, station, user, CaseType.MISSING_PERSON)
    b = _base_case(db, station, user, CaseType.FOUND_PERSON)
    db.add(CaseMatch(match_domain=MatchDomain.PERSON, left_case_id=a.id, left_case_type=a.case_type,
                     right_case_id=b.id, right_case_type=b.case_type, source=MatchSource.AUTOMATIC))
    with pytest.raises(IntegrityError, match="automatic_matches_are_scored"):
        db.flush()


def test_match_case_type_columns_cannot_lie(db, seed):
    station, user = seed
    a = _base_case(db, station, user, CaseType.MISSING_PERSON)
    b = _base_case(db, station, user, CaseType.FOUND_ITEM)
    db.add(CaseMatch(match_domain=MatchDomain.PERSON, left_case_id=a.id, left_case_type=CaseType.MISSING_PERSON,
                     right_case_id=b.id, right_case_type=CaseType.FOUND_PERSON, proposed_by_user_id=user))
    with pytest.raises(IntegrityError, match="fk_case_matches_right_case"):
        db.flush()


def test_only_one_active_assignment_per_case(db, seed):
    station, user = seed
    case = _base_case(db, station, user)
    for _ in range(2):
        db.add(CaseAssignment(case_id=case.id, assigned_to_user_id=user, assigned_by_user_id=user, is_active=True))
    with pytest.raises(IntegrityError, match="uq_case_assignments_one_active_per_case"):
        db.flush()


def test_note_correction_must_target_same_case(db, seed):
    station, user = seed
    a, b = _base_case(db, station, user), _base_case(db, station, user)
    note = CaseNote(id=uuid.uuid4(), case_id=a.id, author_user_id=user, note_text="x", note_type=NoteType.OPERATIONAL)
    db.add(note)
    db.flush()
    db.add(CaseNote(case_id=b.id, author_user_id=user, note_text="fix", note_type=NoteType.CORRECTION,
                    corrects_note_id=note.id))
    with pytest.raises(IntegrityError, match="fk_case_notes_correction_same_case"):
        db.flush()


@pytest.mark.parametrize("model", [AuditEvent, CaseNote, CaseStatusHistory])
def test_append_only_tables_reject_update_and_delete(db, seed, model):
    station, user = seed
    case = _base_case(db, station, user)
    if model is AuditEvent:
        row = AuditEvent(action="X", entity_type="case", actor_user_id=user, case_id=case.id)
    elif model is CaseNote:
        row = CaseNote(case_id=case.id, author_user_id=user, note_text="x", note_type=NoteType.OPERATIONAL)
    else:
        row = CaseStatusHistory(case_id=case.id, to_status=CaseStatus.OPEN, changed_by_user_id=user)
    db.add(row)
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(update(model).where(model.id == row.id).values(created_at=text("now()")))
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(delete(model).where(model.id == row.id))
    db.rollback()


def test_users_with_history_cannot_be_deleted(db, seed):
    station, user = seed
    _base_case(db, station, user)
    db.commit()
    with pytest.raises(IntegrityError, match="fk_cases_reported_by_user_id_users"):
        db.execute(delete(User).where(User.id == user))


def test_non_admin_requires_station(db):
    db.add(User(full_name="No Station", username="nostation", password_hash="x", role=Role.POLICE_OFFICER))
    with pytest.raises(IntegrityError, match="station_required_for_non_admin"):
        db.flush()


def test_failed_create_rolls_back_everything(seed, monkeypatch):
    station, user_id = seed
    with SessionLocal() as db:
        user = db.get(User, user_id)

        def boom(*args, **kwargs):
            raise RuntimeError("audit store down")

        monkeypatch.setattr(case_service, "record_audit", boom)
        with pytest.raises(RuntimeError):
            case_service.create_case(db, user, MISSING_PERSON, MissingPersonCreate(approximate_age=4))
        db.rollback()
    with SessionLocal() as db:
        assert db.scalar(select(Case.id)) is None
        assert db.scalar(select(CaseNumberCounter.last_value)) is None


def test_case_numbers_are_unique_under_concurrency(seed):
    station, user_id = seed

    def create(_):
        with SessionLocal() as db:
            user = db.get(User, user_id)
            case, _ = case_service.create_case(db, user, MISSING_PERSON, MissingPersonCreate(approximate_age=5))
            return case.case_number

    with ThreadPoolExecutor(max_workers=12) as pool:
        numbers = list(pool.map(create, range(40)))
    assert len(set(numbers)) == 40
    sequence = sorted(int(n.rsplit("-", 1)[1]) for n in numbers)
    assert sequence == list(range(1, 41))
    assert all(n.startswith("KSLF-MP-") for n in numbers)
