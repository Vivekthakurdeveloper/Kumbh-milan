# Automatic matching

Kumbh Setu suggests possible links between a **missing person** and a **found person**, or a **lost item**
and a **found item**. Suggestions are leads for a police officer, nothing more.

- Rule-based and deterministic: the same inputs always give the same score. No machine learning or LLM.
- Every score comes with the per-feature reasons that produced it.
- Nothing is automatic beyond the suggestion. A suggestion never changes a case's status, never confirms
  identity, never reunifies anyone, and never reveals contact details.
- Confirming a suggestion uses the normal review endpoint, which requires written verification evidence
  and an officer who did not propose the match.

Code: `backend/app/matching/` (scoring, no database access) and `backend/app/services/matching.py`
(candidates, persistence, audit). Engine version: `ENGINE_VERSION` in `app/matching/rules.py`.

## When matching runs

| Trigger | How |
|---|---|
| Case registered | Automatically after the registration commits (if `MATCHING_AUTO_ENABLED=true`, the default) |
| Case edited (details or position) | Automatically after the edit commits; open automatic suggestions for the case are re-scored |
| On demand | `POST /api/v1/cases/{id}/matches/generate` (help desk, police, admin with access to the case) |
| Backfill | `python -m app.cli rematch --since-days 14` |

Automatic runs use their own transaction. A matching failure is logged, counted in
`kumbh_matching_failures_total`, and never fails the registration or edit.

## Candidates

For a case X, candidates are cases that:

1. are the counterpart type (missing ↔ found person, lost ↔ found item);
2. are in an active, unmatched status (`OPEN`, `UNDER_REVIEW`, `ASSIGNED`, `REOPENED`);
3. were registered within 14 days of X (`candidate_window_days`);
4. have never been paired with X in any status. Rejected and revoked pairs are never suggested again;
5. pass cheap SQL pre-filters: no contradicting known gender (persons), same item category (items).

At most 500 candidates are scored (`candidate_limit`), newest first. At most 10 suggestions are stored per
run (`max_suggestions_per_case`), best first. Matching is deliberately **not** limited to the case's own
station.

## Normalisation

`app/matching/normalize.py`:

- Unicode NFC normalisation and case folding; punctuation is removed.
- Hindi (Devanagari) and romanised Hindi spellings map to one canonical token for colours, garments, and
  common items. For example, `लाल` / `lal` → `red`, `कुर्ता` / `kurti` → `kurta`, `भगवा` / `kesariya` →
  `orange`, `पिट्ठू` → `bag`.
- Stop words, single characters, and numbers are dropped.

## Features and weights

A feature is scored **only when both cases record it**. Missing or `UNKNOWN` values add nothing to the score
or to the evidence total. Absence is never evidence.

### People

| Feature | Weight | Similarity |
|---|---|---|
| gender | 1.0 | 1 if equal. **Different known genders exclude the pair** |
| age | 2.0 | `1 − diff / (2 × tolerance)`. Tolerance grows with age (±1.5 y up to 5, ±2.5 up to 12, ±4 up to 18, ±8 up to 60, ±10 above). **A difference above 3 × tolerance excludes the pair** |
| age_group | 1.0 | Used only when both exact ages are not known. Same group 1, adjacent group 0.3. **Groups two steps apart exclude the pair** |
| language | 1.0 | 1 if the normalised languages share a token, else 0 |
| clothing_colours | 2.0 | Dice overlap of the canonical colours in the two clothing descriptions |
| clothing_items | 1.5 | Dice overlap of canonical garments |
| identifying_marks | 2.5 | Dice overlap of content tokens in identifying marks |
| location | 2.0 | Distance between positions: 1 within 300 m, falling linearly to 0 at 5 km. At least 0.5 if both places are in the same sector. With no positions on either case, identical free-text place descriptions score 0.7; differing text is not counted |
| time | 1.5 | Hours from last seen to found: 1 within 6 h, falling to 0 at 72 h. **Found more than 2 h before last seen excludes the pair** |
| disability | 0.5 | 1 if both record the same value |

### Items

| Feature | Weight | Similarity |
|---|---|---|
| category | 1.0 | Must be equal. **A different category excludes the pair** |
| name | 2.0 | Dice overlap of item-name tokens |
| colours | 2.0 | Dice overlap of canonical colours anywhere in the name or description |
| description | 2.0 | Dice overlap of description tokens, excluding colours and name tokens |
| location | 1.5 | As for people |
| time | 1.5 | As for people, including the found-before-lost exclusion |

A case's **position** is its own coordinates (device GPS or map pin) when recorded. Otherwise it is the
coordinates of the referenced location (ghat or sector). Positions are never derived from free text.

## Score and confidence bands

```
score = Σ (weight × similarity) over features present on both cases
        ─────────────────────────────────────────────────────────
        max( Σ weight of those features , 6.0 )
```

The 6.0 evidence floor (`min_evidence_weight`) stops a pair that agrees on one or two attributes from
scoring highly. Two perfect features of total weight 3 give at most 0.5. A pair also needs at least 3 scored
features (`min_features`) to be suggested at all.

| Band | Score |
|---|---|
| HIGH | ≥ 0.75 |
| MEDIUM | ≥ 0.55 |
| LOW | ≥ 0.40 |
| (not stored) | < 0.40, or excluded by a contradiction |

The bands describe how much recorded evidence agrees. They are not probabilities, and a HIGH suggestion
still needs physical verification.

## What is stored

Each suggestion is a `case_matches` row with:

- `source = AUTOMATIC`, `status = PROPOSED`, `proposed_by_user_id = NULL`;
- `score`, `confidence_band`, `engine_version`;
- `reasons`: a list of `{feature, weight, similarity, contribution, detail}`, highest contribution first.

Contradictions appear as `{feature, contradiction: true, detail}` when an open suggestion is re-scored after
an edit.

Two audit events are written, one per case, with action `MATCH_SUGGESTED` and actor `NULL` for automatic
runs, or the requesting user for `/generate`. Metric: `kumbh_match_suggestions_total{domain, band}`.

## Access control

- An automatic suggestion **does not** give the other station's officers access to the counterpart case.
  Only a confirmed match, or a proposal a person made deliberately, does that (`app/auth/scope.py`).
- Help-desk staff viewing a suggestion whose counterpart is at another station see the case number, type,
  and station, but no name, place, or time.
- Reviewing uses the existing workflow: `POST /cases/{id}/matches/{match_id}/review` with
  `decision = CONFIRMED | REJECTED` and evidence. Police only. The proposer cannot confirm, and optimistic
  version checks apply.

## Changing the rules

1. Edit `app/matching/rules.py`.
2. Bump `ENGINE_VERSION`.
3. Run `pytest tests/test_matching_engine.py tests/test_matching.py`.
4. Optionally run `python -m app.cli rematch` to suggest new pairs under the new rules. Existing suggestions
   keep their original version until their case is edited or re-generated.

## Known limitations

- There is no fuzzy or phonetic name matching. Found persons usually have no recorded name, and names are
  never compared.
- Free-text place descriptions are only compared for exact (normalised) equality. Use `location_id` or
  coordinates for useful location evidence.
- The synonym tables are small and should be extended from real help-desk data during rehearsal events.
- Weights are expert-set defaults. They have not been calibrated against labelled historical data, because
  none is available in this repository.
