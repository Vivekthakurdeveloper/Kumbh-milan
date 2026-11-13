# Frontend integration guide

## Assumptions

There is no frontend code in this repository. The examples assume **React + TypeScript built with Vite**, because the backend's example CORS origin is `http://localhost:5173` (Vite's default dev port, `.env.example`). The client code below uses only `fetch` and standard browser APIs, so it adapts to any framework or data-fetching library (TanStack Query, SWR, RTK Query).

Contracts referenced here are defined in [API_REQUEST_RESPONSE_REFERENCE.md](./API_REQUEST_RESPONSE_REFERENCE.md) and [DATABASE_AND_DATA_CONTRACTS.md](./DATABASE_AND_DATA_CONTRACTS.md).

Never put backend secrets in the frontend: `JWT_SECRET`, `CONTACT_ENCRYPTION_KEY`, S3 keys, `METRICS_TOKEN`, database URLs. The frontend needs only the API base URL.

## 1. Base URL and environments

| Environment | Backend `APP_ENV` | Suggested `VITE_API_BASE_URL` | Notes |
|---|---|---|---|
| Local | `local` | `/api/v1` with the Vite proxy below, or `http://localhost:8000/api/v1` | API from `docker compose up api` listens on `127.0.0.1:8000`. Swagger UI at `http://localhost:8000/docs` when `DOCS_ENABLED=true`. |
| Development / test | `test` or `local` | Team-specific | |
| Staging | `staging` | `https://<staging-host>/api/v1` | `DOCS_ENABLED` allowed; Redis rate limiter, ClamAV, and workers required. |
| Production | `production` | `https://<prod-host>/api/v1` | `/docs` and `/openapi.json` are **disabled**. Use the committed [openapi.json](./openapi.json). |

No staging or production hosts are defined anywhere in the repository (open question Q-9).

**Recommendation:** serve the frontend and the API from the **same origin** (reverse proxy routing `/api/` to the API). This avoids CORS entirely and removes the header-exposure limits listed in §14.

`vite.config.ts` for local development:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
    },
  },
});
```

`.env.development`:

```text
VITE_API_BASE_URL=/api/v1
```

## 2. Authentication and token handling

Facts from the backend:

- `POST /auth/login` returns `access_token` (JWT, 15 min) and `refresh_token` (opaque, 7-day session) in the JSON body. **No cookies are used**; CORS is configured with `allow_credentials=false`.
- Every refresh **rotates** the refresh token. Re-using the immediately previous refresh token **revokes the session** (treated as token theft).
- Logout, password change, admin password reset, deactivation, and role/station changes revoke sessions; the next call returns `401 session_revoked`.

Recommended handling:

1. Keep the access token **in memory only**.
2. Keep the refresh token in memory; persist it to `sessionStorage` only if the product accepts the XSS trade-off of surviving a page reload. Do not use `localStorage` (shared across tabs, see 4).
3. Refresh **single-flight**: all requests that see `401 token_expired` wait on one shared refresh promise.
4. **Multiple tabs:** two tabs sharing one refresh token will eventually refresh concurrently and log the user out. Either keep tokens per tab (each tab logs in), or coordinate with the Web Locks API (`navigator.locks.request("kumbh-refresh", …)`) plus a `BroadcastChannel` that shares the new pair.
5. Refresh proactively about 60 s before `expires_in` elapses, and reactively on `401 token_expired`.
6. On `401 session_revoked`, `invalid_refresh_token`, or `invalid_token`: clear tokens and redirect to login. Do not retry.

```ts
// src/api/auth.ts
import type { TokenResponse } from "./types";

let accessToken: string | null = null;
let refreshToken: string | null = sessionStorage.getItem("ks_refresh");
let refreshInFlight: Promise<boolean> | null = null;

export const getAccessToken = () => accessToken;

export function setTokens(t: TokenResponse | null): void {
  accessToken = t?.access_token ?? null;
  refreshToken = t?.refresh_token ?? null;
  if (refreshToken) sessionStorage.setItem("ks_refresh", refreshToken);
  else sessionStorage.removeItem("ks_refresh");
}

export async function refreshSession(baseUrl: string): Promise<boolean> {
  if (!refreshToken) return false;
  refreshInFlight ??= (async () => {
    try {
      const res = await fetch(`${baseUrl}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        setTokens(null);
        return false;
      }
      setTokens((await res.json()) as TokenResponse);
      return true;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}
```

## 3. API client

```ts
// src/api/client.ts
import { getAccessToken, refreshSession } from "./auth";
import type { ErrorResponse } from "./types";

export const BASE_URL = import.meta.env.VITE_API_BASE_URL as string;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null,
    readonly details: unknown,
    readonly retryAfterSeconds: number | null,
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | string[] | undefined | null>;

export function toQuery(params: Query = {}): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => qs.append(key, v)); // status=OPEN&status=ASSIGNED
    else qs.append(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;            // JSON body (DELETE attachment needs one)
  form?: FormData;           // multipart upload
  query?: Query;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface ApiResult<T> {
  data: T;
  status: number;
  headers: Headers;
}

export async function api<T>(path: string, opts: RequestOptions = {}, retried = false): Promise<ApiResult<T>> {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  // For FormData, let the browser set the multipart boundary; never set Content-Type yourself.

  const res = await fetch(`${BASE_URL}${path}${toQuery(opts.query)}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
    signal: opts.signal,
  });

  if (res.status === 401 && !retried) {
    const err = (await res.clone().json().catch(() => null)) as ErrorResponse | null;
    if (err?.error.code === "token_expired" && (await refreshSession(BASE_URL))) {
      return api<T>(path, opts, true);
    }
  }

  if (!res.ok) {
    const err = (await res.json().catch(() => null)) as ErrorResponse | null;
    const retryAfter = res.headers.get("Retry-After");
    throw new ApiError(
      res.status,
      err?.error.code ?? "http_error",
      err?.error.message ?? res.statusText,
      err?.error.request_id ?? res.headers.get("X-Request-ID"),
      err?.error.details ?? null,
      retryAfter ? Number(retryAfter) : null,
    );
  }

  const isJson = res.headers.get("Content-Type")?.includes("application/json");
  const data = (res.status === 204 ? undefined : isJson ? await res.json() : await res.text()) as T;
  return { data, status: res.status, headers: res.headers };
}
```

The response `X-Request-ID` is exposed via CORS. Show it in error messages ("Reference: 4f1c…") so support can find the request in logs and audit events.

## 4. Interceptor behaviour

| Condition | Client action |
|---|---|
| `401 token_expired` | Single-flight refresh, retry once. |
| `401` any other code | Clear tokens, go to login. Preserve the intended route. |
| `403 permission_denied` / `station_scope` | Show "You don't have access to this action". Do not retry. Usually indicates a UI gating bug. |
| `404 case_not_found` | "Case not found or not available to you". Remove it from local caches. |
| `409 case_changed` | Reload the case, show what changed, let the user re-apply. `details.current_version` is the new version. |
| `409` other | Reload the resource; the action is no longer valid in its current state. |
| `422 validation_failed` | Map `details[]` to form fields (section 9). |
| `422` other codes | Show a field- or form-level message mapped from `code`. |
| `429` | Wait `Retry-After` seconds (if present) before allowing retry. `account_temporarily_locked` has no `Retry-After`; show the lockout message (default 15 min). |
| `503 rate_limiter_unavailable` / `storage_unavailable` | Retry with backoff (respect `Retry-After`). |
| `500 internal_error` | Generic error with `request_id`. Retry only safe requests. |
| Network error / timeout | Retry GETs. Retry POSTs **only** if sent with an `Idempotency-Key`, reusing the same key. |

## 5. Idempotent writes

Generate one key per user intent (per form submission), keep it until the request definitely succeeded or the user edits the form, and reuse it for retries:

```ts
const key = crypto.randomUUID(); // 36 chars, within 8–128
const { data, headers } = await api<MissingPersonCaseRead>("/cases/missing-persons", {
  method: "POST",
  body: form,
  idempotencyKey: key,
});
const replayed = headers.get("Idempotent-Replayed") === "true"; // original case returned
```

Supported on: case create, assign, status, custody, add note, match review, queue notification. If the body changes, generate a new key (same key + different body → `409 idempotency_key_reused`).

## 6. Typed models

Two options:

1. **Generate** types from the committed OpenAPI 3.1 document (recommended, stays exact):

   ```bash
   npx openapi-typescript docs/api/openapi.json -o src/api/schema.d.ts
   ```

   Regenerate whenever `docs/api/openapi.json` is regenerated (see [API_TESTING_GUIDE.md](./API_TESTING_GUIDE.md)).

2. **Hand-written** core types, matching the backend exactly:

```ts
// src/api/types.ts
export type UUID = string;
export type ISODateTime = string;

export type Role = "ADMIN" | "POLICE_OFFICER" | "HELP_DESK_OPERATOR";
export type CaseType = "MISSING_PERSON" | "FOUND_PERSON" | "LOST_ITEM" | "FOUND_ITEM";
export type CaseStatus = "OPEN" | "UNDER_REVIEW" | "ASSIGNED" | "MATCH_FOUND" | "RESOLVED" | "CLOSED" | "REOPENED";
export type UrgencyLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Gender = "MALE" | "FEMALE" | "OTHER" | "UNKNOWN";
export type AgeGroup = "INFANT" | "CHILD" | "ADULT" | "ELDERLY" | "UNKNOWN";
export type CommunicationStatus = "VERBAL" | "LIMITED" | "NON_VERBAL" | "UNCONSCIOUS" | "UNKNOWN";
export type CustodyStatus = "IN_CUSTODY" | "TRANSFERRED" | "RELEASED";
export type CoordinateSource = "DEVICE_GPS" | "MAP_PIN";
export type MediaType = "PHOTO" | "AUDIO";
export type ScanStatus = "QUARANTINED" | "SCANNING" | "CLEAN" | "REJECTED" | "SCAN_FAILED" | "SKIPPED_DEV";
export type NoteType = "OPERATIONAL" | "FOLLOW_UP" | "CORRECTION";
export type MatchStatus = "PROPOSED" | "CONFIRMED" | "REJECTED" | "REVOKED";
export type MatchSource = "MANUAL" | "AUTOMATIC";
export type ConfidenceBand = "HIGH" | "MEDIUM" | "LOW";
export type LocationKind = "GHAT" | "SECTOR" | "PARKING" | "CAMP" | "HELP_DESK" | "LANDMARK";
export type StationKind = "POLICE_STATION" | "HELP_DESK";
export type SortOrder = "-created_at" | "created_at" | "-updated_at" | "updated_at";
export type ContactRevealReason = "REUNIFICATION_REVIEW" | "FAMILY_ENQUIRY" | "INVESTIGATION" | "CARE_HANDOVER";

export interface ErrorResponse {
  error: { code: string; message: string; request_id: string | null; details: unknown };
}
export interface ValidationIssue { loc: (string | number)[]; msg: string; type: string }

export interface Page<T> { items: T[]; total: number; limit: number; offset: number }

export interface TokenResponse { access_token: string; refresh_token: string; token_type: "bearer"; expires_in: number }

export interface UserRead {
  id: UUID; full_name: string; username: string; email: string | null; phone_number: string | null;
  role: Role; police_badge_number: string | null; station_id: UUID | null; is_active: boolean;
  last_login_at: ISODateTime | null; created_at: ISODateTime; updated_at: ISODateTime;
}
export interface MeResponse extends UserRead { permissions: string[] }

export interface PositionInput {
  location_id?: UUID | null;
  latitude?: number | null;
  longitude?: number | null;
  coordinate_source?: CoordinateSource | null;
  coordinate_accuracy_m?: number | null;
}

interface CaseEnvelope {
  id: UUID; case_number: string; case_type: CaseType; status: CaseStatus; urgency_level: UrgencyLevel | null;
  station_id: UUID; reported_by_user_id: UUID; assigned_to_user_id: UUID | null;
  has_contact_details: boolean; version: number;
  location_id: UUID | null; latitude: number | null; longitude: number | null;
  coordinate_source: CoordinateSource | null; coordinate_accuracy_m: number | null;
  created_at: ISODateTime; updated_at: ISODateTime; closed_at: ISODateTime | null;
  archived_at: ISODateTime | null; anonymized_at: ISODateTime | null;
}

export interface MissingPersonDetails {
  person_name: string | null; approximate_age: number | null; age_group: AgeGroup | null; gender: Gender | null;
  height_description: string | null; clothing_description: string | null; identifying_marks: string | null;
  language: string | null; last_seen_location: string | null; last_seen_at: ISODateTime | null;
  has_disability: boolean | null; medical_needs: string | null; additional_description: string | null;
}
export interface FoundPersonDetails {
  approximate_age: number | null; age_group: AgeGroup | null; estimated_gender: Gender | null;
  physical_description: string | null; clothing_description: string | null; identifying_marks: string | null;
  found_location: string | null; found_at: ISODateTime | null; communication_status: CommunicationStatus | null;
  language: string | null; has_disability: boolean | null; care_or_assistance_required: string | null;
  current_care_location: string | null; safeguarding_review_required: boolean | null;
}
export interface LostItemDetails {
  item_category: string; item_name: string; item_description: string | null; brand_or_color: string | null;
  identifying_features: string | null; last_seen_location: string | null; last_seen_at: ISODateTime | null;
}
export interface FoundItemDetails {
  item_category: string; item_name: string; item_description: string | null; item_condition: string | null;
  found_location: string | null; found_at: ISODateTime | null; storage_location: string | null;
  custody_status: CustodyStatus; handover_acknowledgement: string | null; released_at: ISODateTime | null;
}

export type MissingPersonCaseRead = CaseEnvelope & { case_type: "MISSING_PERSON"; details: MissingPersonDetails };
export type FoundPersonCaseRead = CaseEnvelope & { case_type: "FOUND_PERSON"; details: FoundPersonDetails };
export type LostItemCaseRead = CaseEnvelope & { case_type: "LOST_ITEM"; details: LostItemDetails };
export type FoundItemCaseRead = CaseEnvelope & { case_type: "FOUND_ITEM"; details: FoundItemDetails };
export type AnyCaseRead = MissingPersonCaseRead | FoundPersonCaseRead | LostItemCaseRead | FoundItemCaseRead;

type Writable<T> = { [K in keyof T]?: T[K] | null };
interface CreateEnvelope extends PositionInput {
  urgency_level?: UrgencyLevel | null;
  station_id?: UUID | null;      // admins only
  contact_details?: string | null; // write-only
}
export type MissingPersonCreate = CreateEnvelope & Writable<MissingPersonDetails>;
export type FoundPersonCreate = CreateEnvelope & Writable<FoundPersonDetails>;
export type LostItemCreate = CreateEnvelope & Writable<LostItemDetails> & { item_category: string; item_name: string };
export type FoundItemCreate = CreateEnvelope &
  Writable<Omit<FoundItemDetails, "custody_status" | "handover_acknowledgement" | "released_at">> &
  { item_category: string; item_name: string };
// Updates: same fields minus station_id, plus expected_version.
export type CaseUpdate<T> = Omit<T, "station_id"> & { expected_version?: number };

export interface CaseSummary {
  id: UUID; case_number: string; case_type: CaseType; status: CaseStatus; urgency_level: UrgencyLevel | null;
  station_id: UUID; assigned_to_user_id: UUID | null; title: string | null; location: string | null;
  location_id: UUID | null; approximate_age: number | null; age_group: AgeGroup | null; gender: Gender | null;
  event_at: ISODateTime | null; version: number;
  created_at: ISODateTime; updated_at: ISODateTime; in_scope: boolean;
}

export interface MatchReason { feature: string; weight: number; similarity: number; contribution: number; detail: string }
export interface MatchRead {
  id: UUID; match_domain: "PERSON" | "ITEM"; status: MatchStatus; source: MatchSource;
  score: number | null; confidence_band: ConfidenceBand | null; reasons: MatchReason[] | null; engine_version: string | null;
  left_case_id: UUID; right_case_id: UUID; left_case_version: number; right_case_version: number;
  counterpart: CaseSummary; proposed_by_user_id: UUID | null; rationale: string | null;
  reviewed_by_user_id: UUID | null; reviewed_at: ISODateTime | null; review_evidence: string | null;
  revoked_by_user_id: UUID | null; revoked_at: ISODateTime | null; revocation_reason: string | null;
  created_at: ISODateTime;
}

/** Cross-case review queue row from GET /matches. */
export interface MatchQueueItem extends Omit<MatchRead, "counterpart"> {
  left_case: CaseSummary;
  right_case: CaseSummary;
}

export interface UserDirectoryEntry {
  id: UUID; full_name: string; role: Role; station_id: UUID | null;
}

export interface AttachmentRead {
  id: UUID; case_id: UUID; uploaded_by_user_id: UUID; media_type: MediaType; original_filename: string | null;
  content_type: string; file_size_bytes: number; checksum_sha256: string; scan_status: ScanStatus;
  scanned_at: ISODateTime | null; downloadable: boolean; created_at: ISODateTime;
}

export interface DashboardSummary {
  generated_at: ISODateTime;
  active_by_type: Partial<Record<CaseType, number>>; // zero counts are omitted
  by_status: Partial<Record<CaseStatus, number>>;    // zero counts are omitted
  active_total: number; urgent_active: number; unassigned_active: number; registered_today: number;
  pending_match_reviews: number; attachments_awaiting_scan: number; notifications_failed: number;
}
```

### Date handling

Event-time fields require an offset and must not be in the future. An `<input type="datetime-local">` value has no offset; convert it:

```ts
export function localInputToIso(value: string): string {
  // "2026-10-29T07:40" (device local time) -> "2026-10-29T02:10:00.000Z"
  return new Date(value).toISOString();
}
```

Display times in `Asia/Kolkata` explicitly so officers on devices with a wrong time zone see consistent values:

```ts
const fmt = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
```

## 7. Pagination and filtering

```ts
export interface CaseFilters {
  q?: string; case_number?: string; status?: CaseStatus[]; urgency_level?: UrgencyLevel;
  station_id?: UUID; assigned_to_user_id?: UUID; unassigned?: boolean; location?: string;
  created_from?: ISODateTime; created_to?: ISODateTime; event_from?: ISODateTime; event_to?: ISODateTime;
  item_category?: string; age_group?: AgeGroup; gender?: Gender; language?: string;
  include_archived?: boolean; sort?: SortOrder; limit?: number; offset?: number;
}

export const CASE_SLUG: Record<CaseType, string> = {
  MISSING_PERSON: "missing-persons", FOUND_PERSON: "found-persons", LOST_ITEM: "lost-items", FOUND_ITEM: "found-items",
};

export const listCases = <T extends AnyCaseRead>(type: CaseType, f: CaseFilters, signal?: AbortSignal) =>
  api<Page<T>>(`/cases/${CASE_SLUG[type]}`, { query: { ...f } as never, signal }).then((r) => r.data);

export const searchCases = (f: CaseFilters & { case_type?: CaseType; all_stations?: boolean }) =>
  api<Page<CaseSummary>>("/cases", { query: { ...f } as never }).then((r) => r.data);

export const listMatchQueue = (f: {
  status?: MatchStatus; confidence_band?: ConfidenceBand; match_domain?: "PERSON" | "ITEM";
  limit?: number; offset?: number;
} = {}) =>
  api<Page<MatchQueueItem>>("/matches", { query: { status: "PROPOSED", ...f } as never }).then((r) => r.data);

export const userDirectory = (ids: UUID[]) =>
  api<UserDirectoryEntry[]>("/users/directory", { query: { ids } as never }).then((r) => r.data);
```

- Page size: default 25, maximum 100. Compute pages as `Math.ceil(total / limit)`.
- Keep filters in the URL (`useSearchParams`) so lists are shareable and survive reload.
- Debounce `q` (≈300 ms) and cancel stale requests with `AbortController`; the `search` limit is 180/min per user.
- `status` is repeatable; `toQuery` above serialises arrays correctly.
- "My cases" = `assigned_to_user_id=<me.id>`. "Unassigned queue" = `unassigned=true&status=OPEN&status=UNDER_REVIEW`.
- Offset pagination can skip or repeat rows when cases are created during paging; with `sort=-created_at`, refreshing page 1 is the expected way to see new cases.

## 8. Image and file uploads

```ts
export async function uploadAttachment(caseId: UUID, file: File, mediaType: MediaType) {
  const form = new FormData();
  form.append("media_type", mediaType);
  form.append("file", file, file.name);
  const { data, status } = await api<AttachmentRead>(`/cases/${caseId}/attachments`, { method: "POST", form });
  return { attachment: data, pendingScan: status === 202 };
}
```

Client-side checks before upload (the server re-checks everything):

| Check | Photo | Audio |
|---|---|---|
| Max size | 10 MB | 25 MB |
| Accept attribute | `image/jpeg,image/png,image/webp` | `audio/wav,audio/mpeg,audio/mp4,audio/ogg,audio/webm` |
| Camera capture | `<input type="file" accept="image/*" capture="environment">`. iOS may deliver HEIC, which is rejected (`415`). Re-encode via `<canvas>.toBlob(cb, "image/jpeg", 0.85)`; this also strips EXIF location metadata and reduces size. | `MediaRecorder` produces `audio/webm` (Chrome/Android) or `audio/mp4` (Safari); both are accepted. |

Flow on a registration form: create the case first, then upload photos in parallel (≤ 30 uploads/min per user). Show per-file progress and allow retrying failed uploads; do not re-create the case.

Displaying images:

1. `GET /cases/{id}/attachments` → show items with `downloadable: true`; for `QUARANTINED`/`SCANNING`, show "Scanning…" and poll the list briefly; for `REJECTED`/`SCAN_FAILED`, show "File unavailable".
2. For each image, call `GET /cases/{id}/attachments/{attachment_id}` to get a URL valid for `expires_in` seconds (default 300) and set it as `<img src>`. Request URLs lazily (when the thumbnail enters the viewport); every call is audited.
3. Do not cache URLs beyond `expires_in`. On an `<img>` `error` event, fetch a new URL once.
4. The URL is always on a host the browser can reach: the object store's public host (`S3_PUBLIC_ENDPOINT_URL`) or this API (`/api/v1/media/{token}`, no `Authorization` header needed). Treat it as opaque. If the frontend sets a Content-Security-Policy, allow the API origin and any store host in `img-src` and `media-src`. The same applies to `GET /matches/{id}/photos/{side}`; that response has no similarity score.

Delete requires a JSON body: with `fetch` use `api(path, { method: "DELETE", body: { reason } })`; with axios use `axios.delete(url, { data: { reason } })`.

## 9. Form validation mapping

`422 validation_failed` returns `details: [{loc, msg, type}]`. For body fields, `loc` is `["body", "<field>"]`. Model-level rules (coordinate pairing) come back with `loc: ["body"]` and a `msg` starting with `Value error, `.

```ts
export function fieldErrors(err: ApiError): { fields: Record<string, string>; form: string[] } {
  const fields: Record<string, string> = {};
  const form: string[] = [];
  if (err.code === "validation_failed" && Array.isArray(err.details)) {
    for (const d of err.details as ValidationIssue[]) {
      const [where, field] = d.loc;
      if (where === "body" && typeof field === "string") fields[field] = d.msg;
      else form.push(d.msg.replace(/^Value error, /, ""));
    }
  } else {
    form.push(DOMAIN_MESSAGES[err.code] ?? err.message);
  }
  return { fields, form };
}

const DOMAIN_MESSAGES: Record<string, string> = {
  coordinates_out_of_area: "The location is outside the Mela area. Check that latitude and longitude are not swapped.",
  invalid_location: "The selected place is no longer available. Pick another.",
  verification_required: "Record how identity or ownership was verified, or select a confirmed match.",
  safeguarding_evidence_required: "This person is flagged for safeguarding review. Verification evidence is required.",
  custody_not_released: "Release the item from custody before resolving the case.",
  handover_required: "Record who received the item.",
  weak_password: "Password must be 12+ characters and mix at least three of: lowercase, uppercase, digits, symbols.",
};
```

Mirror these rules client-side for immediate feedback:

| Rule | Fields |
|---|---|
| Required on create | Lost/found items: `item_category` (2–64), `item_name` (1–200). Nothing else is required. |
| Length | Short text 1–200; location text 1–500; long text 1–5000; `reason` 3–2000 (match revoke `reason` 10–5000); evidence 10–5000; `recipient_acknowledgement` 10–500; `contact_details` 3–1000; `note_text` 1–10000; `rationale` 10–5000; **`language` ≤100** (validated; matches the database column). |
| Empty strings | Send `null` or omit the field. An empty string fails `min_length=1` for most fields. |
| Numbers | `approximate_age` 0–120 integer. |
| Times | Not in the future (5 min tolerance); offset required. |
| Coordinates | Both or neither, plus `coordinate_source`; inside `[29.70, 77.90]–[30.25, 78.45]`. |
| Unknown fields | Rejected. Strip UI-only fields before sending. |

## 10. Loading, empty, error, and retry states

| State | Guidance |
|---|---|
| Loading | Skeleton rows for lists; keep previous page visible while fetching the next (`keepPreviousData`). |
| Empty | Distinguish "no cases yet" (no filters) from "no results for these filters" (offer "Clear filters"). |
| Error | Show the mapped message and `request_id`. Offer "Retry" for GETs. |
| Retry | Automatic retry (max 2, exponential backoff) only for GETs and idempotency-keyed POSTs on network errors, `503`, and `500`. Never auto-retry `4xx` except `401 token_expired` (refresh) and `429` (after `Retry-After`). |
| Stale data | After any mutation, invalidate the case detail, the case's history, matches, and the relevant lists and dashboard. |
| Offline | Use `POST /api/v1/sync/push`, `GET /api/v1/sync/pull`, and `GET /api/v1/sync/status` with stable `device_id` + `client_record_id`. See [docs/MOBILE_SYNC.md](../MOBILE_SYNC.md). `Idempotency-Key` remains useful for online retries (24 h TTL). |

## 11. Role-based UI access

Use `GET /auth/me` → `permissions` instead of hard-coding roles:

```ts
export const can = (me: MeResponse | undefined, permission: string) => !!me?.permissions.includes(permission);

// Examples
can(me, "case:assign");          // show "Assign"
can(me, "case:resolve");         // show Resolve / Close / Reopen
can(me, "case:custody_update");  // show custody actions on found items
can(me, "case:contact_read");    // show "Reveal contact"
can(me, "note:sensitive");       // show "Sensitive" checkbox on notes
can(me, "match:review");         // show Confirm / Reject / Revoke
can(me, "attachment:delete");    // show Delete on attachments
can(me, "case:search_all_stations"); // show "All stations" toggle
can(me, "user:manage");          // Users admin menu
can(me, "audit:read");           // Audit log menu
can(me, "retention:manage");     // Retention menu
```

UI gating is a convenience only; the backend enforces every check. Additional rules the UI should reflect:

- A user who proposed a manual match cannot confirm it (hide Confirm when `match.proposed_by_user_id === me.id`).
- Non-admins register cases at their own station; hide the station picker. Admins must choose a station.
- Admins cannot deactivate or demote themselves.

**Resolving user names:** case responses contain user ids only. Resolve them with `GET /users/directory?ids=…` (all roles; returns `id`, `full_name`, `role`, `station_id` only). Admins can still use `GET /users/{id}` for full records; police use `GET /users/assignable-officers` for the assign dialog.

## 12. Status badges and workflow actions

| Status | Suggested label | Tone |
|---|---|---|
| `OPEN` | Open | blue |
| `UNDER_REVIEW` | Under review | amber |
| `ASSIGNED` | Assigned | indigo |
| `MATCH_FOUND` | Match found | purple |
| `RESOLVED` | Resolved | green |
| `CLOSED` | Closed | grey |
| `REOPENED` | Reopened | orange |

| Custody | Label |
|---|---|
| `IN_CUSTODY` | In custody |
| `TRANSFERRED` | Transferred |
| `RELEASED` | Released to owner |

Show case status and custody status as **two separate badges** on found items.

```ts
const TRANSITIONS: Record<CaseStatus, CaseStatus[]> = {
  OPEN: ["UNDER_REVIEW", "ASSIGNED", "CLOSED"],
  UNDER_REVIEW: ["ASSIGNED", "MATCH_FOUND", "CLOSED"],
  ASSIGNED: ["UNDER_REVIEW", "MATCH_FOUND", "RESOLVED", "CLOSED"],
  MATCH_FOUND: ["ASSIGNED", "RESOLVED", "CLOSED"],
  RESOLVED: ["CLOSED", "REOPENED"],
  CLOSED: ["REOPENED"],
  REOPENED: ["UNDER_REVIEW", "ASSIGNED"],
};
const TARGET_PERMISSION: Partial<Record<CaseStatus, string>> = {
  UNDER_REVIEW: "case:review", ASSIGNED: "case:assign", MATCH_FOUND: "case:resolve",
  RESOLVED: "case:resolve", CLOSED: "case:resolve", REOPENED: "case:resolve",
};

export function statusActions(c: AnyCaseRead, me: MeResponse): CaseStatus[] {
  return TRANSITIONS[c.status].filter((target) => {
    if (!can(me, TARGET_PERMISSION[target]!)) return false;
    if (target === "ASSIGNED") return false;           // use the Assign dialog instead
    if (target === "MATCH_FOUND") return false;        // reached by confirming a match
    if (target === "REOPENED" && c.anonymized_at) return false;
    return true;
  });
}

export const canResolveFoundItem = (c: FoundItemCaseRead) => c.details.custody_status === "RELEASED";

export function canEdit(c: AnyCaseRead): boolean {
  return c.status !== "RESOLVED" && c.status !== "CLOSED" && !c.archived_at;
}
```

Resolve dialog: require either a confirmed match (pick from `GET /cases/{id}/matches` where `status === "CONFIRMED"`) or verification evidence (≥10 chars); require evidence when `details.safeguarding_review_required` is true on a found person.

## 13. API integration testing

- **Contract types:** generate from `docs/api/openapi.json` in CI and fail the build on type errors when the spec changes.
- **Mocking:** use MSW (Mock Service Worker) handlers whose payloads are copied from the validated examples in [API_REQUEST_RESPONSE_REFERENCE.md](./API_REQUEST_RESPONSE_REFERENCE.md); include error envelopes (`409 case_changed`, `422 validation_failed`, `401 token_expired`).
- **Must-test client behaviours:** single-flight refresh under parallel 401s; logout on `session_revoked`; idempotency key reuse on retry; `202` upload path; dashboard with missing keys; `in_scope: false` rows; help-desk view with redacted match fields.
- **End-to-end:** run against a local stack (`docker compose up -d postgres redis objectstore`, migrate, `create-admin`). Never point E2E tests at shared or production data.

## 14. CORS and environment configuration

Backend CORS is configured by `CORS_ORIGINS` (JSON list, e.g. `["http://localhost:5173"]`). When empty, no CORS middleware is installed and cross-origin browser calls fail. Staging/production reject `*`.

| Setting | Value (`backend/app/main.py`) | Consequence |
|---|---|---|
| `allow_origins` | `CORS_ORIGINS` | Add every frontend origin explicitly. |
| `allow_credentials` | `false` | Use bearer headers, not cookies. |
| `allow_methods` | `GET, POST, PATCH, DELETE` | |
| `allow_headers` | `Authorization, Content-Type, Idempotency-Key, X-Request-ID` | `X-Step-Up-Code` is missing, so cross-origin step-up fails (gap G-17). |
| `expose_headers` | `X-Request-ID, Idempotent-Replayed, Retry-After` | `X-Export-Rows`, `X-Export-Truncated`, and `Content-Disposition` are **not** readable cross-origin (gap G-16). |

Same-origin deployment removes both limitations.

Request body limit: 30 MB (`MAX_REQUEST_BYTES`). The API sends `Cache-Control: no-store` on every `/api/` response, so HTTP caching does not apply; rely on the client cache (TanStack Query etc.).
