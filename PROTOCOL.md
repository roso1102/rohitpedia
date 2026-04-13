# PROTOCOL.md
## Rohitpedia — Role, Workflow, and Best Practices

**Read this before touching any file in this repository.**

---

## 1. Role Definition

You are working on **Rohitpedia** — a personal intelligence knowledge system. Your role is backend/full-stack engineer with a focus on correctness and simplicity. This system processes real user data. Mistakes cost either data integrity (wrong backlinks, leaked data across users) or money (runaway LLM calls).

**Core principles:**
1. Data isolation is never negotiable. Every DB query must be RLS-scoped.
2. Simpler is better. Add complexity only when a simpler approach demonstrably fails.
3. LLMs are expensive and unreliable. Use them sparingly, always with fallbacks.
4. Raw entries are immutable. Never modify or delete a raw_entry after creation.
5. The wiki is truth. The vector index is derived and can always be rebuilt.

---

## 2. System topology (memorise this)

```
Telegram / Web UI
      │
      ▼ (< 1s)
FastAPI webhook → write raw_entry → enqueue job → return ✓
      │
      ▼
pg-boss worker pool
      ├── ingest worker     (media extraction)
      ├── absorb worker     (Gemini Flash → wiki article)
      ├── embed worker      (nomic-embed → pgvector)
      └── intelligence cron (PPR + RNS + diff + resurface)
      │
      ▼
PostgreSQL + pgvector (single DB, RLS on all tables)
      │
      ▼
Next.js (wiki UI + intelligence dashboard)
```

---

## 3. Workflow — before writing any code

### Step 1: Identify the layer
Which layer does this change touch?
- **Database schema** → update Prisma schema + write migration + update SQLAlchemy models
- **Worker logic** → check idempotency, check RLS is set before first query
- **LLM call** → check if a cheaper alternative exists, add error handling, log to llm_usage
- **Next.js page** → check if data is available server-side, avoid unnecessary client fetches
- **Bot handler** → validate user binding before any DB access

### Step 2: Check the SRS
Every functional change should map to an FR-* or NFR-* requirement in `docs/SRS.md`. If it doesn't, it might be scope creep — discuss before implementing.

### Step 3: Write the test first
For every new function:
```python
# Test structure: arrange → act → assert → cleanup
async def test_my_function():
    # Arrange: create isolated test user
    user = await create_test_user()
    await set_rls(user.id)

    # Act: run the function
    result = await my_function(user.id, ...)

    # Assert: verify output
    assert result.something == expected

    # Cleanup: delete test user (CASCADE handles all child rows)
    await delete_test_user(user.id)
```

### Step 4: RLS check
If your function touches the database, verify:
- `SET LOCAL app.current_tenant = '{user_id}'` is called before the first query
- No raw `user_id` filtering in WHERE clauses (RLS handles it, but explicit is fine too)
- Cross-user joins are architecturally impossible

---

## 4. Workflow — Telegram message lifecycle

```
Message received
      │
      ▼
Validate webhook secret (X-Telegram-Bot-Api-Secret-Token)
      │
      ▼
Look up user by telegram_id → must exist or reject
      │
      ▼
Is this a query? (regex against QUERY_PATTERNS)
      ├── YES → embed → kNN → format → reply inline
      └── NO → detect source type
                    │
                    ▼
              text → write raw_entry → enqueue ingest
              URL  → write raw_entry → enqueue ingest (URL in metadata)
              voice → write raw_entry → enqueue ingest (media_path set)
              image → write raw_entry → enqueue ingest (media_path set)
              pdf   → write raw_entry → enqueue ingest (media_path set)
      │
      ▼
Reply ✓ to Telegram (must happen before any heavy processing)
```

---

## 5. Workflow — LLM call checklist

Before every LLM call, verify:

- [ ] Is there a deterministic alternative? (backlink parsing → AST, not LLM; conflict detection first pass → SQL, not LLM)
- [ ] Is this the right model tier? (facet extraction → Phi-3 mini, not Gemini Pro)
- [ ] Is the system prompt cached? (Gemini context caching for absorb system prompt)
- [ ] Is `max_tokens` set explicitly?
- [ ] Is output schema defined and validated?
- [ ] Is error handling in place with retry logic?
- [ ] Is the call logged to `llm_usage`?
- [ ] Does the prompt contain any other user's data? (must be NO)

LLM call template:
```python
async def llm_call_template(input_data: str, user_id: str) -> dict:
    start_time = time.time()
    try:
        result = await provider.complete(
            prompt=build_prompt(input_data),
            max_tokens=1000,
            response_schema=MY_SCHEMA
        )
        parsed = validate_schema(result, MY_SCHEMA)
        return parsed
    except Exception as e:
        logger.error(f"LLM call failed: {e} (user={user_id[:8]}...)")
        raise
    finally:
        await log_llm_usage(
            user_id=user_id,
            model=provider.model,
            tokens_in=result.usage.input_tokens if result else 0,
            tokens_out=result.usage.output_tokens if result else 0,
            duration_ms=int((time.time() - start_time) * 1000),
            task_type="my_task",
            success=parsed is not None
        )
```

---

## 6. Workflow — database write checklist

Before any database write:

- [ ] Is the write inside a transaction that covers all related writes? (article + backlinks + facets = one transaction)
- [ ] Is there an `ON CONFLICT` clause? (every upsert needs one)
- [ ] Is the RLS session variable set?
- [ ] If deleting: is it truly appropriate? (raw_entries are never deleted)

Write template:
```python
async with db.begin():  # transaction boundary
    await db.execute(
        f"SET LOCAL app.current_tenant = '{user_id}'"
    )
    # All queries here execute within RLS policy and the same transaction
    await upsert_article(...)
    await write_backlinks(...)
    await write_facets(...)
    # Transaction commits or rolls back together
```

---

## 7. Code hygiene standards

### Naming
- Python: `snake_case` for all. Variables describe what they are, not how they're used.
- TypeScript: `camelCase` for variables/functions, `PascalCase` for components/types.
- DB tables: `snake_case` plural (`raw_entries`, `tunnel_suggestions`).
- DB columns: `snake_case` singular (`created_at`, `body_md`).
- API routes: REST, plural resources (`/api/articles`, `/api/tunnels`).
- Worker jobs: verb_noun (`ingest_entry`, `absorb_entry`, `embed_article`).

### File organisation
- One responsibility per file. `ingest.py` only handles extraction. `absorb.py` only handles LLM synthesis.
- No circular imports. Dependency direction: `api → workers → intelligence → db`.
- All constants in `config.py`. No magic strings in business logic.

### Error handling
- Never swallow exceptions silently.
- Always log with context (entry_id, user_id first 8 chars).
- Worker failures: mark job as failed, log, do not crash the worker process.
- LLM failures: retry 3× with backoff, then mark entry failed.

### Testing
- Every public function has at least one test.
- Tests use isolated test users with UUID-based cleanup.
- No test shares state with another test.
- Mock all external services (Gemini, Ollama, whisper) in unit tests.

### Logging
- Log levels: DEBUG for development detail, INFO for job lifecycle, WARNING for recoverable failures, ERROR for unrecoverable.
- Never log PII: no `entry.body`, no `user.telegram_id` in plain text.
- Use structured logging: `logger.info("absorb_complete", extra={"entry_id": id, "slug": slug, "tokens": n})`.

### Environment variables
- All secrets from env vars. No hardcoded API keys or passwords.
- `.env.example` always up to date.
- Validate all required env vars at startup. Fail fast if missing.

---

## 8. Security checklist

Before every PR / commit:

- [ ] No hardcoded secrets or credentials
- [ ] No SQL string concatenation (use parameterised queries)
- [ ] Telegram webhook secret validated on every request
- [ ] User input slugs validated against `^[a-zA-Z0-9._/-]+$`
- [ ] Media file paths checked for `..` (path traversal)
- [ ] RLS session variable set before all DB queries in workers
- [ ] LLM prompts don't include other users' data
- [ ] Error messages don't expose internal paths or stack traces to API responses

---

## 9. Dependency management

### Python
- `requirements.txt` for production dependencies
- `requirements-dev.txt` for development/testing only
- Pin major versions: `gemini==1.x`, not `gemini>=1.0`
- Audit monthly: `pip-audit`

### Node.js
- `package.json` with exact versions for production (`npm install --save-exact`)
- `devDependencies` correctly separated
- Audit monthly: `npm audit`

### Adding a dependency
Ask: is this in active maintenance? Does it add unique functionality or duplicate what Postgres/Python stdlib already does? If it's a single function, write it yourself.

---

## 10. Git workflow

### Branch naming
- `feature/phase1-telegram-webhook`
- `fix/rls-missing-on-chunks-table`
- `refactor/absorb-worker-idempotency`
- `docs/update-srs-phase2`

### Commit messages
```
<type>(<scope>): <short description>

<longer explanation if needed>

Refs: FR-ABS-04
```
Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

### Before pushing
- [ ] `pytest` passes (all tests green)
- [ ] `npm run lint` passes (no TS errors)
- [ ] No `console.log` left in Next.js code
- [ ] No `print()` left in Python workers (use logger)
- [ ] Migration file created if schema changed
- [ ] `.env.example` updated if new env vars added

---

## 11. Phase gate checklist

Before starting a new phase, all of the following must be true for the previous phase:

1. All tests in ROADMAP.md for that phase pass
2. RLS isolation test passes for all new tables
3. Idempotency verified: run each worker twice on same input, verify no duplicates
4. Performance target met (see NFR-PERF-* in SRS.md)
5. No open bugs tagged `critical` or `blocker`
6. ROADMAP.md updated with completion date
