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


## T5: Fixed _has_api_key inverted logic

**Issue**: `_has_api_key("")` returned `True` (incorrect), treating empty strings as "no auth required" instead of "no key provided".

**Root cause**: Line 228 had inverted logic - empty/whitespace strings returned `True` when they should return `False`.

**Fix**: Changed `return True` to `return False` for empty/whitespace strings. Updated type signature to `str | None` to match actual usage.

**Impact**: 
- Empty API keys now correctly skip provider initialization
- Test `test_speech_no_gateway` now expects 503 (all providers skipped) instead of 502
- Added 5 test cases covering all edge cases: empty string, whitespace, real key, unresolved env var, None

**Test results**: 60/62 tests pass (2 pre-existing failures unrelated to this fix)

## T5: Fixed fuzzy cache hit bug (server.py line 232)

**Problem**: When fuzzy match occurred, `record_hit` was called with INPUT's normalized text instead of MATCHED entry's normalized text, causing wrong row to be updated in DB.

**Root Cause**: Line 232 used `result.get("normalized", normalize(text))` which always returns the input's normalized form, not the matched cache entry's text.

**Solution**: Changed to `result.get("matched", result.get("normalized", normalize(text)))` to prioritize the "matched" field from fuzzy lookup results.

**Verification**: 
- matcher.py line 23 confirms "matched" field exists in fuzzy results
- Added test_fuzzy_hit_count.py to verify correct hit_count increment
- All 63 tests pass

**Key Insight**: The "matched" field from matcher.py contains the actual cached entry's normalized text, which is what should be used for record_hit to ensure the correct DB row is updated.

## Race Condition Fix - Concurrent Cache Inserts (2026-02-22)

### Changes Made
- add_entry() now uses INSERT OR IGNORE to avoid duplicate-row insertion races on (text_normalized, voice_id, version_num) unique key.
- When insert is ignored, add_entry() resolves and returns the existing row id to preserve caller contract (int id always returned).
- Server cache-store path now catches sqlite3.IntegrityError and treats it as race-resolved cache hit by recording hit metadata instead of failing request flow.
- Added concurrent regression test with 10 parallel inserts of identical key asserting exactly one DB row remains and all calls return the same id.

### Key Findings
- INSERT OR IGNORE plus post-select by unique key is enough to make cache writes idempotent under concurrent misses without file locks.
- Returning existing id keeps backward compatibility for all existing add_entry() callers.

## T7: Evictor HotCache Sync Bug Fix (2026-02-22)

### Problem
evictor.run() deleted entries from DB + filesystem but never updated HotCache. Stale HotCache entries caused lookup to return paths to deleted files → FileNotFoundError at server.py line 214.

### Changes Made
- metadata.py: get_eviction_candidates() now SELECTs text_normalized + voice_id alongside id + audio_path (both queries)
- evictor.py: Constructor accepts optional hot_cache param. run() calls hot_cache.remove(text_normalized, voice_id) for each evicted entry.
- server.py: CacheEvictor init passes hot_cache=_store.hot_cache
- test_cache.py: Added test_eviction_syncs_hot_cache — stores entry in all 3 layers, evicts, asserts lookup returns None (not FileNotFoundError)

### Key Findings
- HotCache.remove() already existed but was never called from evictor
- get_eviction_candidates returned only id + audio_path, insufficient for HotCache removal which needs text_normalized + voice_id
- Evictor overflow path (max_entries=0) used in test to force eviction without needing min_age_days wait
- basedpyright strict mode flags dict[str, object] values from sqlite3.Row — used pyright: ignore for pre-existing pattern
- 64/64 tests pass, 0 errors in diagnostics

## T9: Config-Driven Normalizer + MiniMax TTS Stripping (2026-02-22)

### Changes Made
- config.py: Added `strip_minimax: bool = True` to NormalizeConfig
- normalizer.py: Refactored `normalize()` to accept optional `NormalizeConfig` param. Each transform (lowercase, strip_punctuation, collapse_whitespace, replace_numbers, strip_minimax) is now independently toggleable. Default config = all True (backward compatible).
- MiniMax regex: `<#[\d.]+#>` for pause markers, `\([a-z_]+\)` for interjection tags. Stripped first in pipeline so markers don't leak into later steps.
- store.py: FuzzyCacheStorage accepts optional `normalize_config` and passes it to `normalize()` calls.
- All other callers (matcher.py, server.py, fillers/manager.py) use `normalize()` with no args → default config → backward compatible.

### Key Findings
- Diacritic folding is coupled with lowercase (both use `turkish_lower` + `DIACRITIC_MAP`). Kept them under the `lowercase` flag since diacritics only make sense after lowercasing.
- MiniMax stripping runs before all other transforms to prevent pause markers like `<#2.4#>` from being partially consumed by punctuation stripping.
- `TYPE_CHECKING` guard used for `NormalizeConfig` import to avoid circular imports (normalizer.py → config.py).

### Test Coverage (11 new tests)
- test_minimax_pause_markers_stripped: `<#N.N#>` removal
- test_minimax_interjection_tags_stripped: `(gasps)`, `(laughs)` etc.
- test_minimax_all_interjections: all 19 supported tags
- test_minimax_combined_with_other_transforms: full pipeline with MiniMax
- test_minimax_disabled: strip_minimax=False preserves markers
- test_config_lowercase_disabled: case preserved
- test_config_strip_punctuation_disabled: punctuation preserved
- test_config_collapse_whitespace_disabled: whitespace preserved
- test_config_replace_numbers_disabled: digits preserved
- test_config_all_disabled: no transforms applied
- test_default_config_backward_compatible: explicit default == implicit default
- 75/75 tests pass
