# Load harness

`cases.js` is a k6 script for an already-authenticated staging API. It is not part of CI and it must not be pointed at the operational database.

No traffic target has been approved, so capacity is `NOT_VERIFIED`. Do not treat a local run as a capacity result.

Example, only against an isolated environment:

```bash
k6 run -e BASE_URL=http://127.0.0.1:8000 -e TOKEN=... load/cases.js
```
