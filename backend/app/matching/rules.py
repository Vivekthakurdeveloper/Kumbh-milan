"""
Matching configuration. Weights express how much a feature counts as evidence when BOTH cases record it.
Changing any value here must bump ENGINE_VERSION so stored suggestions remain explainable.
"""

from dataclasses import dataclass, field

ENGINE_VERSION = "rules-2026.10.1"


@dataclass(frozen=True)
class Bands:
    high: float = 0.75
    medium: float = 0.55
    low: float = 0.40  # below this nothing is stored


@dataclass(frozen=True)
class PersonRules:
    weights: dict[str, float] = field(default_factory=lambda: {
        "gender": 1.0,
        "age": 2.0,
        "age_group": 1.0,
        "language": 1.0,
        "clothing_colours": 2.0,
        "clothing_items": 1.5,
        "identifying_marks": 2.5,
        "location": 2.0,
        "time": 1.5,
        "disability": 0.5,
    })
    # Age tolerance in years, by the larger of the two ages: young children are estimated more precisely.
    age_tolerance: tuple[tuple[int, float], ...] = ((5, 1.5), (12, 2.5), (18, 4.0), (60, 8.0), (200, 10.0))
    age_exclusion_factor: float = 3.0  # difference beyond factor x tolerance rules the pair out
    found_before_seen_grace_hours: float = 2.0  # found this long before last seen is a contradiction


@dataclass(frozen=True)
class ItemRules:
    weights: dict[str, float] = field(default_factory=lambda: {
        "category": 1.0,
        "name": 2.0,
        "colours": 2.0,
        "description": 2.0,
        "location": 1.5,
        "time": 1.5,
    })
    found_before_seen_grace_hours: float = 2.0


@dataclass(frozen=True)
class SharedRules:
    # Location: full credit within near_m, none beyond far_m (linear in between).
    near_m: float = 300.0
    far_m: float = 5000.0
    same_sector_score: float = 0.5
    # Time: full credit within near_hours of the event, none beyond far_hours.
    near_hours: float = 6.0
    far_hours: float = 72.0
    # Denominator floor: a pair is never scored on less than this much evidence weight, so one matching
    # attribute cannot produce a high score on its own.
    min_evidence_weight: float = 6.0
    min_features: int = 3


@dataclass(frozen=True)
class MatchingRules:
    person: PersonRules = field(default_factory=PersonRules)
    item: ItemRules = field(default_factory=ItemRules)
    shared: SharedRules = field(default_factory=SharedRules)
    bands: Bands = field(default_factory=Bands)
    candidate_limit: int = 500
    candidate_window_days: int = 14
    max_suggestions_per_case: int = 10


DEFAULT_RULES = MatchingRules()
