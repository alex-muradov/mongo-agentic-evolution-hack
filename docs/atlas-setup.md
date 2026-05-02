# Atlas Setup — One-Shot Provisioning

Manual steps for alexm. Code does the index creation; you do the cluster/user creation in Atlas UI.

## 1. Cluster (M0 free)

- https://cloud.mongodb.com → New Cluster → **M0 (free)**, region **EU (London)** if available
- Cluster name: `autoresearch-hackathon`
- Network access: temporarily `0.0.0.0/0` for development. Tighten before sharing with PowerSync (step 4).
- Admin database user: create one for our backend, password lands in `.env` (and shared with Gabik via `docs/credentials-for-gabik.md`)
- Connection string → put in `.env` as `MONGODB_URI`

## 2. Read-only user for Wild Coral

Atlas → Database Access → Add user
- Username: `wildcoral_rag_ro`
- Auth: password → `docs/credentials-for-gabik.md`
- Role: built-in `read` scoped to **DB `agentic_evolution`, collection `case_studies`** only
- Note: for hackathon scope alexm skipped this scoped user — Gabik uses the admin connection. Tighten before any post-demo extension.

## 3. Vector Search index

Auto-created by `ensure_search_indexes()` on first FastAPI startup. Verify:

```bash
uv sync
uv run uvicorn app.main:app --port 8000
# expect logs: ensure_indexes ok, ensure_search_indexes ok
```

Atlas → Search → Indexes → expect `case_studies_vec` with status moving `BUILDING → READY` (~30–90s).

If the auto-create fails for any reason, paste the JSON from `docs/rag-direct-access.md` § "Vector index definition" into Atlas UI → Search → Create Search Index → Vector Search.

## 4. IP allowlist for PowerSync

Only when integration with Wild Coral begins:
- Atlas → Network Access → IP Access List → add PowerSync Cloud egress IPs (from Gabik's PowerSync project Settings).
- Remove the temporary `0.0.0.0/0` once your laptop and PowerSync IPs are pinned.

## 5. Operational notes

- Search-index creation is **eventually consistent**. POSTs that arrive before `READY` still write the doc; `$vectorSearch` queries return empty until index is ready.
- M0 search-index limit is 3; we use one (`case_studies_vec`).
- `ensure_search_indexes()` is idempotent — safe to call on every startup; it short-circuits if the named index exists.
- On non-Atlas Mongo (local), `ensure_search_indexes()` returns `False` silently — vector search is unavailable but the rest works for smoke testing.
