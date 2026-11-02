"""
Deterministic, explainable scoring of a missing/lost case against a found case.

score = sum(weight * similarity for features recorded on BOTH cases)
        / max(sum(weight for those features), min_evidence_weight)

A feature missing on either side contributes nothing to the numerator or the denominator: absence is never
evidence. Hard contradictions (different gender, implausible age gap, found long before last seen,
different item category) exclude the pair entirely. Every contribution is returned as a reason.
"""

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.matching import normalize as n
from app.matching.rules import DEFAULT_RULES, MatchingRules, SharedRules
from app.models.enums import AgeGroup, ConfidenceBand, Gender

_AGE_ORDER = [AgeGroup.INFANT, AgeGroup.CHILD, AgeGroup.ADULT, AgeGroup.ELDERLY]
_KNOWN_GENDERS = {Gender.MALE, Gender.FEMALE, Gender.OTHER}


@dataclass(frozen=True)
class Place:
    latitude: float | None = None
    longitude: float | None = None
    location_id: uuid.UUID | None = None
    sector_id: uuid.UUID | None = None
    text: str | None = None


@dataclass(frozen=True)
class PersonProfile:
    case_id: uuid.UUID
    gender: Gender | None = None
    age: int | None = None
    age_group: AgeGroup | None = None
    language: str | None = None
    clothing: str | None = None
    marks: str | None = None
    place: Place = field(default_factory=Place)
    event_at: datetime | None = None
    disability: bool | None = None


@dataclass(frozen=True)
class ItemProfile:
    case_id: uuid.UUID
    category: str
    name: str
    description: str | None = None  # free text excluding the item name
    place: Place = field(default_factory=Place)
    event_at: datetime | None = None


@dataclass
class Assessment:
    score: float = 0.0
    band: ConfidenceBand | None = None
    reasons: list[dict] = field(default_factory=list)
    excluded_by: str | None = None
    evidence_weight: float = 0.0
    features: int = 0

    @property
    def suggest(self) -> bool:
        return self.band is not None


class _Ledger:
    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights
        self.result = Assessment()
        self._numerator = 0.0

    def add(self, feature: str, similarity: float, detail: str) -> None:
        weight = self.weights[feature]
        similarity = max(0.0, min(1.0, similarity))
        self._numerator += weight * similarity
        self.result.evidence_weight += weight
        self.result.features += 1
        self.result.reasons.append(
            {"feature": feature, "weight": weight, "similarity": round(similarity, 3),
             "contribution": round(weight * similarity, 3), "detail": detail}
        )

    def exclude(self, feature: str, detail: str) -> Assessment:
        self.result.excluded_by = feature
        self.result.reasons.append({"feature": feature, "contradiction": True, "detail": detail})
        self.result.score = 0.0
        return self.result

    def finish(self, rules: MatchingRules) -> Assessment:
        shared = rules.shared
        denominator = max(self.result.evidence_weight, shared.min_evidence_weight)
        self.result.score = round(self._numerator / denominator, 4) if denominator else 0.0
        self.result.reasons.sort(key=lambda r: -r.get("contribution", 0))
        if self.result.features >= shared.min_features:
            bands = rules.bands
            if self.result.score >= bands.high:
                self.result.band = ConfidenceBand.HIGH
            elif self.result.score >= bands.medium:
                self.result.band = ConfidenceBand.MEDIUM
            elif self.result.score >= bands.low:
                self.result.band = ConfidenceBand.LOW
        return self.result


def _linear(value: float, full: float, zero: float) -> float:
    if value <= full:
        return 1.0
    if value >= zero:
        return 0.0
    return 1.0 - (value - full) / (zero - full)


def _dice(a: set[str], b: set[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _place(ledger: _Ledger, a: Place, b: Place, shared: SharedRules) -> None:
    if None not in (a.latitude, b.latitude):
        metres = haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
        similarity = _linear(metres, shared.near_m, shared.far_m)
        detail = f"{metres:.0f} m apart"
        if similarity < shared.same_sector_score and a.sector_id and a.sector_id == b.sector_id:
            similarity, detail = shared.same_sector_score, f"{detail}; same sector"
        ledger.add("location", similarity, detail)
    elif n.same_text(a.text, b.text):
        # Identical free text is weak evidence; differing free text is not counted against the pair.
        ledger.add("location", 0.7, "same place description")


def _time(ledger: _Ledger, lost_at: datetime | None, found_at: datetime | None, grace: float,
          shared: SharedRules) -> str | None:
    if lost_at is None or found_at is None:
        return None
    hours = (found_at - lost_at).total_seconds() / 3600
    if hours < -grace:
        return f"found {abs(hours):.1f} h before last seen"
    gap = max(0.0, hours)
    ledger.add("time", _linear(gap, shared.near_hours, shared.far_hours), f"found {gap:.1f} h after last seen")
    return None


def _age_tolerance(age: int, table: tuple[tuple[int, float], ...]) -> float:
    for upper, tolerance in table:
        if age <= upper:
            return tolerance
    return table[-1][1]


def score_persons(missing: PersonProfile, found: PersonProfile, rules: MatchingRules = DEFAULT_RULES) -> Assessment:
    pr, shared = rules.person, rules.shared
    ledger = _Ledger(pr.weights)

    if missing.gender in _KNOWN_GENDERS and found.gender in _KNOWN_GENDERS:
        if missing.gender != found.gender:
            return ledger.exclude("gender", f"{missing.gender} vs {found.gender}")
        ledger.add("gender", 1.0, f"both {missing.gender}")

    if missing.age is not None and found.age is not None:
        tolerance = _age_tolerance(max(missing.age, found.age), pr.age_tolerance)
        diff = abs(missing.age - found.age)
        if diff > pr.age_exclusion_factor * tolerance:
            return ledger.exclude("age", f"{missing.age} vs {found.age} years")
        ledger.add("age", 1.0 - diff / (2 * tolerance), f"{missing.age} vs {found.age} years (tolerance ±{tolerance:g})")
    else:
        groups = (missing.age_group, found.age_group)
        if all(g in _AGE_ORDER for g in groups):
            distance = abs(_AGE_ORDER.index(groups[0]) - _AGE_ORDER.index(groups[1]))
            if distance > 1:
                return ledger.exclude("age_group", f"{groups[0]} vs {groups[1]}")
            ledger.add("age_group", 1.0 if distance == 0 else 0.3, f"{groups[0]} vs {groups[1]}")

    contradiction = _time(ledger, missing.event_at, found.event_at, pr.found_before_seen_grace_hours, shared)
    if contradiction:
        return ledger.exclude("time", contradiction)

    if n.fold(missing.language) and n.fold(found.language):
        same = bool(n.tokens(missing.language) & n.tokens(found.language))
        ledger.add("language", 1.0 if same else 0.0, "same language" if same else "different language")

    colours_a, colours_b = n.colours(missing.clothing), n.colours(found.clothing)
    if colours_a and colours_b:
        shared_colours = sorted(colours_a & colours_b)
        ledger.add("clothing_colours", _dice(colours_a, colours_b),
                   f"shared colours: {', '.join(shared_colours) or 'none'}")
    garments = set(n.GARMENTS)
    items_a, items_b = n.tokens(missing.clothing) & garments, n.tokens(found.clothing) & garments
    if items_a and items_b:
        shared_items = sorted(items_a & items_b)
        ledger.add("clothing_items", _dice(items_a, items_b), f"shared garments: {', '.join(shared_items) or 'none'}")

    marks_a, marks_b = n.tokens(missing.marks), n.tokens(found.marks)
    if marks_a and marks_b:
        common = sorted(marks_a & marks_b)
        ledger.add("identifying_marks", _dice(marks_a, marks_b), f"shared terms: {', '.join(common[:6]) or 'none'}")

    _place(ledger, missing.place, found.place, shared)

    if missing.disability is not None and found.disability is not None:
        same = missing.disability == found.disability
        ledger.add("disability", 1.0 if same else 0.0, "disability recorded the same" if same else "disability differs")

    return ledger.finish(rules)


def score_items(lost: ItemProfile, found: ItemProfile, rules: MatchingRules = DEFAULT_RULES) -> Assessment:
    ir, shared = rules.item, rules.shared
    ledger = _Ledger(ir.weights)

    if n.fold(lost.category) != n.fold(found.category):
        return ledger.exclude("category", f"{lost.category} vs {found.category}")
    ledger.add("category", 1.0, f"both {n.fold(lost.category)}")

    contradiction = _time(ledger, lost.event_at, found.event_at, ir.found_before_seen_grace_hours, shared)
    if contradiction:
        return ledger.exclude("time", contradiction)

    name_a, name_b = n.tokens(lost.name), n.tokens(found.name)
    if name_a and name_b:
        ledger.add("name", _dice(name_a, name_b), f"shared terms: {', '.join(sorted(name_a & name_b)) or 'none'}")

    text_a = f"{lost.name} {lost.description or ''}"
    text_b = f"{found.name} {found.description or ''}"
    colours_a, colours_b = n.colours(text_a), n.colours(text_b)
    if colours_a and colours_b:
        ledger.add("colours", _dice(colours_a, colours_b), f"shared colours: {', '.join(sorted(colours_a & colours_b)) or 'none'}")

    desc_a = n.tokens(lost.description) - n.COLOUR_NAMES - name_a
    desc_b = n.tokens(found.description) - n.COLOUR_NAMES - name_b
    if desc_a and desc_b:
        ledger.add("description", _dice(desc_a, desc_b), f"shared terms: {', '.join(sorted(desc_a & desc_b)[:6]) or 'none'}")

    _place(ledger, lost.place, found.place, shared)
    return ledger.finish(rules)
