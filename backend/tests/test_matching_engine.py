"""Pure scoring tests: no database."""

import uuid
from datetime import UTC, datetime, timedelta

from app.matching import ItemProfile, PersonProfile, Place, score_items, score_persons
from app.matching.normalize import colours, tokens
from app.models.enums import AgeGroup, ConfidenceBand, Gender

T0 = datetime(2027, 4, 14, 6, 0, tzinfo=UTC)
HKP = Place(29.9563, 78.1716, text="Har Ki Pauri")


def person(**kw) -> PersonProfile:
    base = dict(
        case_id=uuid.uuid4(), gender=Gender.MALE, age=7, age_group=AgeGroup.CHILD, language="Bhojpuri",
        clothing="red kurta and blue shorts", marks="mole on left cheek", place=HKP, event_at=T0,
    )
    return PersonProfile(**{**base, **kw})


def features(result) -> set[str]:
    return {r["feature"] for r in result.reasons if not r.get("contradiction")}


def test_strong_pair_is_high_and_explained():
    found = person(age=8, clothing="लाल कुर्ता, नीली निक्कर", marks="mole left cheek",
                   place=Place(29.9580, 78.1730), event_at=T0 + timedelta(hours=3))
    result = score_persons(person(), found)
    assert result.band is ConfidenceBand.HIGH and result.score >= 0.75
    assert {"gender", "age", "language", "clothing_colours", "clothing_items", "identifying_marks", "location", "time"} <= features(result)
    colour = next(r for r in result.reasons if r["feature"] == "clothing_colours")
    assert colour["similarity"] == 1.0 and "red" in colour["detail"] and "blue" in colour["detail"]
    for reason in result.reasons:
        assert set(reason) == {"feature", "weight", "similarity", "contribution", "detail"}


def test_scoring_is_deterministic():
    a, b = person(), person(age=9)
    first, second = score_persons(a, b), score_persons(a, b)
    assert first.score == second.score and first.reasons == second.reasons


def test_missing_fields_are_not_evidence():
    sparse_missing = PersonProfile(uuid.uuid4(), gender=Gender.MALE, age=7)
    sparse_found = PersonProfile(uuid.uuid4(), gender=Gender.MALE, age=7)
    result = score_persons(sparse_missing, sparse_found)
    # Two perfect features (weight 3) are divided by the evidence floor (6), and too few features to suggest.
    assert result.score == 0.5 and result.band is None and result.features == 2
    unknown = score_persons(PersonProfile(uuid.uuid4(), gender=Gender.UNKNOWN), PersonProfile(uuid.uuid4(), gender=Gender.MALE))
    assert result.excluded_by is None and unknown.features == 0 and unknown.band is None


def test_differing_free_text_location_is_not_counted_against():
    a = person(place=Place(text="near the main gate"))
    b = person(place=Place(text="by the river"))
    assert "location" not in features(score_persons(a, b))


def test_contradictions_exclude():
    assert score_persons(person(), person(gender=Gender.FEMALE)).excluded_by == "gender"
    assert score_persons(person(), person(age=40)).excluded_by == "age"
    early = score_persons(person(), person(event_at=T0 - timedelta(hours=5)))
    assert early.excluded_by == "time" and early.band is None
    groups = score_persons(person(age=None, age_group=AgeGroup.INFANT), person(age=None, age_group=AgeGroup.ADULT))
    assert groups.excluded_by == "age_group"


def test_distance_and_time_decay():
    near = score_persons(person(), person(place=Place(29.9580, 78.1730)))
    far = score_persons(person(), person(place=Place(30.1022, 78.2988)))
    loc = lambda r: next(x for x in r.reasons if x["feature"] == "location")["similarity"]  # noqa: E731
    assert loc(near) == 1.0 and loc(far) == 0.0
    later = score_persons(person(), person(event_at=T0 + timedelta(hours=39)))
    assert next(x for x in later.reasons if x["feature"] == "time")["similarity"] == 0.5


def test_weak_pair_not_suggested():
    result = score_persons(person(), person(age=10, clothing="green saree", marks="scar on chin", language="Tamil",
                                            place=Place(30.1022, 78.2988), event_at=T0 + timedelta(hours=60)))
    assert result.excluded_by is None and result.band is None


def test_normalisation_synonyms():
    assert colours("लाल कुर्ता, सफ़ेद पजामा") == {"red", "white"}
    assert tokens("Kesariya KURTI with chappal") == {"orange", "kurta", "slippers"}
    assert tokens("the and of") == set()


def item(**kw) -> ItemProfile:
    base = dict(case_id=uuid.uuid4(), category="bag", name="Black backpack",
                description="black Wildcraft torn left strap", place=HKP, event_at=T0)
    return ItemProfile(**{**base, **kw})


def test_items():
    found = item(name="black pitthu bag", description="Wildcraft, strap torn", event_at=T0 + timedelta(hours=2))
    result = score_items(item(), found)
    assert result.band in (ConfidenceBand.HIGH, ConfidenceBand.MEDIUM)
    assert {"category", "name", "colours", "description", "location", "time"} <= features(result)
    assert score_items(item(), item(category="phone")).excluded_by == "category"
    assert score_items(item(), item(event_at=T0 - timedelta(days=1))).excluded_by == "time"
