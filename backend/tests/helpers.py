import uuid
from dataclasses import dataclass
from functools import lru_cache

from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.database import SessionLocal
from app.models import Station, User
from app.models.enums import Role, StationKind

PASSWORD = "Ganga#River2027!"
API = "/api/v1"


@lru_cache
def _password_hash() -> str:
    return hash_password(PASSWORD)


@dataclass
class Actor:
    id: uuid.UUID
    username: str
    station_id: uuid.UUID | None
    headers: dict[str, str]
    refresh_token: str


@dataclass
class Actors:
    station_a: uuid.UUID
    station_b: uuid.UUID
    admin: Actor
    police: Actor
    police2: Actor
    helpdesk: Actor
    police_b: Actor
    helpdesk_b: Actor


def login(client: TestClient, username: str, password: str = PASSWORD) -> tuple[dict[str, str], str]:
    response = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["refresh_token"]


def create_user(username: str, role: Role, station_id: uuid.UUID | None, badge: str | None = None) -> uuid.UUID:
    with SessionLocal() as db:
        user = User(
            id=uuid.uuid4(), full_name=username.title(), username=username, password_hash=_password_hash(),
            role=role, station_id=station_id, police_badge_number=badge,
        )
        db.add(user)
        db.commit()
        return user.id


def create_station(code: str, kind: StationKind = StationKind.POLICE_STATION) -> uuid.UUID:
    with SessionLocal() as db:
        station = Station(id=uuid.uuid4(), name=f"Station {code}", code=code, kind=kind)
        db.add(station)
        db.commit()
        return station.id


def configure_public_intake(helpdesk: Actor) -> None:
    """Point public portal intake at the test help-desk actor/station."""
    from app.config import get_settings
    from app.notifications import reset_provider_cache

    settings = get_settings()
    settings.public_intake_user_id = helpdesk.id
    settings.public_intake_station_id = helpdesk.station_id
    settings.public_security_secret = "test-public-security-secret-32chars"
    settings.notification_provider = "mock"
    reset_provider_cache()


def make_actors(client: TestClient) -> Actors:
    station_a = create_station("HKP-01")
    station_b = create_station("KNK-02", StationKind.HELP_DESK)
    specs = {
        "admin": (Role.ADMIN, None, None),
        "police": (Role.POLICE_OFFICER, station_a, "UK-1"),
        "police2": (Role.POLICE_OFFICER, station_a, "UK-2"),
        "helpdesk": (Role.HELP_DESK_OPERATOR, station_a, None),
        "police_b": (Role.POLICE_OFFICER, station_b, "UK-3"),
        "helpdesk_b": (Role.HELP_DESK_OPERATOR, station_b, None),
    }
    built = {}
    for name, (role, station, badge) in specs.items():
        user_id = create_user(name, role, station, badge)
        headers, refresh = login(client, name)
        built[name] = Actor(user_id, name, station, headers, refresh)
    actors = Actors(station_a=station_a, station_b=station_b, **built)
    configure_public_intake(actors.helpdesk)
    return actors


MISSING_PERSON = {
    "approximate_age": 7,
    "age_group": "CHILD",
    "gender": "MALE",
    "clothing_description": "red kurta and blue shorts",
    "identifying_marks": "mole on left cheek",
    "language": "Bhojpuri",
    "last_seen_location": "Har Ki Pauri ghat steps",
    "urgency_level": "CRITICAL",
    "contact_details": "Father Ramesh: +91 98765 43210",
}

FOUND_PERSON = {
    "approximate_age": 7,
    "age_group": "CHILD",
    "estimated_gender": "MALE",
    "clothing_description": "red kurta",
    "found_location": "Brahmakund",
    "communication_status": "LIMITED",
}

LOST_ITEM = {
    "item_category": "bag",
    "item_name": "Black backpack",
    "brand_or_color": "black Wildcraft",
    "identifying_features": "torn left strap",
    "last_seen_location": "Sector 4 bathing ghat",
    "contact_details": "Owner: +91 91234 56789",
}

FOUND_ITEM = {
    "item_category": "bag",
    "item_name": "Black backpack",
    "item_condition": "wet",
    "found_location": "Sector 4 lost property tent",
    "storage_location": "HKP-01 locker 3",
}

PATHS = {
    "MISSING_PERSON": "missing-persons",
    "FOUND_PERSON": "found-persons",
    "LOST_ITEM": "lost-items",
    "FOUND_ITEM": "found-items",
}


def create_case(client: TestClient, actor: Actor, kind: str, payload: dict | None = None, **headers) -> dict:
    defaults = {"missing-persons": MISSING_PERSON, "found-persons": FOUND_PERSON, "lost-items": LOST_ITEM, "found-items": FOUND_ITEM}
    response = client.post(f"{API}/cases/{kind}", json=payload or defaults[kind], headers={**actor.headers, **headers})
    assert response.status_code == 201, response.text
    return response.json()
