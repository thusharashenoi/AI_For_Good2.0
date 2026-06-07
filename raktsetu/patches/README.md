# RaktSetu patches

## `raktsetu-dev-db-and-booking.patch`

Apply from the **git repo root** (parent of `raktsetu/`):

```bash
cd "/path/to/AI For Good 2.0"
git apply --check raktsetu/patches/raktsetu-dev-db-and-booking.patch
git apply raktsetu/patches/raktsetu-dev-db-and-booking.patch
```

Or from inside `raktsetu/` if that directory is the repo root:

```bash
cd raktsetu
git apply --check patches/raktsetu-dev-db-and-booking.patch
git apply patches/raktsetu-dev-db-and-booking.patch
```

### What this patch includes

- Context-aware voice slot questions; no default date/time until donor confirms
- Spoken time normalization (`2 PM`, `2pm` → stored/displayed correctly)
- AWS dev DynamoDB support via `legacy_schema.py` (Bridge → Tara field mapping)
- Matching requires donor `phone`; legacy coord/score fields honored
- `.env.example` dev table names; `run_server.sh` no longer forces `LOCAL_MODE=1`
- Tests for legacy schema, slot confirmation, and time parsing

### Not in the patch (manual step)

Your `.env` is gitignored. Merge `env-dev-dynamodb.snippet` into `.env` to use AWS dev tables.

Base commit when generated: `268c13a` on branch `automation`.
