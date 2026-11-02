# Workflow specification

Matching suggestions are not proof of identity. No API confirms a match, reunifies a person, or releases an item without an authenticated user and the recorded evidence required below.

## Case types

- Missing person: name may be absent. Age, age group, clothing, marks, last-seen place and time, disability, and medical needs are optional fields.
- Found person: care location, communication status, assistance needs, and `safeguarding_review_required`. The flag is an operator marker, not a legal finding. Automatic age-based legal rules are not implemented.
- Lost item: category, name, description, brand or colour, identifying features.
- Found item: the same descriptive fields plus custody. Custody starts at `IN_CUSTODY`.

## Status

`OPEN -> UNDER_REVIEW | ASSIGNED | CLOSED`

`UNDER_REVIEW -> ASSIGNED | MATCH_FOUND | CLOSED`

`ASSIGNED -> UNDER_REVIEW | MATCH_FOUND | RESOLVED | CLOSED`

`MATCH_FOUND -> ASSIGNED | RESOLVED | CLOSED`

`RESOLVED -> CLOSED | REOPENED`

`CLOSED -> REOPENED`

`REOPENED -> UNDER_REVIEW | ASSIGNED`

A confirmed human review may also move `OPEN`, `UNDER_REVIEW`, `ASSIGNED`, or `REOPENED` to `MATCH_FOUND`. That path is not the generic transition table.

`RESOLVED` requires a confirmed `match_id` or `verification_evidence`. A found-person case with `safeguarding_review_required` always requires `verification_evidence`. A found item must already be `RELEASED`. Anonymised cases cannot be reopened.

`expected_version` returns `409 case_changed` when stale. Create and selected updates accept an idempotency key.

## Custody

`IN_CUSTODY -> TRANSFERRED | RELEASED`

`TRANSFERRED -> IN_CUSTODY | TRANSFERRED | RELEASED`

`RELEASED` has no next state. Transfer requires `storage_location`. Release requires `recipient_acknowledgement`. The acknowledgement is stored on the found-item row and in the audit event.

## Matching

Person cases match only person cases. Item cases match only item cases. Scores use shared attributes, token similarity, distance, and time. A missing attribute is not a contradiction. Suggestions store `engine_version` and reasons. Review, rejection, and revocation are explicit. The proposer cannot confirm their own manual match. A second confirmed match that conflicts is rejected by the existing uniqueness rules.

Callers who cannot see the counterpart case do not receive the score, confidence band, reasons, or rationale.

## Search

List and search endpoints are station-scoped. Cross-station summary search requires `case:search_all_stations` and marks `in_scope`. Nearby and map queries use PostGIS. Hindi matching is dictionary normalisation in `app/matching/normalize.py`, not semantic search.
