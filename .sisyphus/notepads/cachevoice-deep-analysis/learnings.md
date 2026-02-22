# Learnings — CacheVoice Deep Analysis

## Conventions & Patterns

(To be populated as we discover patterns during implementation)

## Structured Logging Implementation (2026-02-22)

### Changes Made
- Added `_setup_logging()` function to configure StreamHandler with INFO level
- Called from lifespan startup using `_settings.server.log_level` config
- Added structured log fields to all cache operations:
  - `reason_code`: exact_hit, fuzzy_hit, miss, miss_no_cache, miss_text_too_long, error_file_not_found
  - `text_preview`: First 50 chars of input text
  - `voice_id`: Voice identifier
  - `score`: Fuzzy match score (for hits)
  - `format`: Audio format info (for conversions)

### Key Findings
- Logger had no handler configured, causing silent log drops
- `logging.getLogger("cachevoice")` at line 22 needed explicit handler setup
- Log level from config.py ServerConfig.log_level (default "info")
- Structured format enables easy parsing for monitoring/alerting

### Test Results
- 46/47 tests pass
- 1 pre-existing test failure (test_speech_no_gateway) unrelated to logging changes
- Logs now visible in pytest stderr output with reason codes
- Example log: `Cache HIT | reason_code=exact_hit text_preview='test' voice_id=Decent_Boy score=1.0`

## DB Schema Hardening — version_num + Migration (2026-02-22)

### Changes Made
- Added `version_num INTEGER DEFAULT 1` column to cache_entries
- Added `UNIQUE INDEX idx_normalized_voice_version(text_normalized, voice_id, version_num)` — composite key enforces uniqueness per version
- Added `schema_version` table for migration tracking (CURRENT_SCHEMA_VERSION = 2)
- `_init_db()` now detects schema version and runs migrations automatically
- `_migrate_to_v2()`: adds column, deduplicates (keeps highest hit_count via ROW_NUMBER OVER PARTITION), creates unique index
- `_create_tables_v2()`: fresh installs get v2 schema directly
- `add_entry()` accepts `version_num` param (default=1, backward compatible)
- `record_hit()` accepts optional `version_num` for targeted version hits
- `get_version_count(text_normalized, voice_id)` returns count of versions for a pair
- `get_schema_version()` exposes current DB schema version
- `get_all_entries()` now includes `version_num` in SELECT

### Key Findings
- SQLite `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)` works well for deduplication in migration
- Partial migration recovery: checks if `version_num` column exists before ALTER TABLE (idempotent)
- `cursor.lastrowid` returns `int | None` — pre-existing basedpyright warning, not introduced by this change
- `test_speech_no_gateway` failure is pre-existing (55/56 pass)

### Test Coverage (9 new tests)
- `test_schema_version_tracked`: verifies schema_version table populated
- `test_unique_constraint_same_version_rejects_duplicate`: IntegrityError on duplicate (text, voice, version)
- `test_unique_constraint_different_version_allowed`: same text+voice, different version_num OK
- `test_unique_constraint_different_voice_allowed`: same text+version, different voice OK
- `test_get_version_count`: 0 → 1 → 3 progression
- `test_record_hit_specific_version`: only targeted version incremented
- `test_version_num_defaults_to_1`: backward compat
- `test_migration_adds_version_num`: v1 DB migrated, version_num=1 assigned
- `test_migration_deduplicates_keeps_highest_hit_count`: 3 dupes → 1 survivor with highest hits

