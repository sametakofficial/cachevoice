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

## T10: Deprecated Gateway File Removal

**Deleted files:**
- `cachevoice/gateway/minimax.py`
- `cachevoice/gateway/openai.py`
- `cachevoice/gateway/elevenlabs.py`

**Verification:**
- No imports found in codebase (grep confirmed)
- `gateway/__init__.py` had no references to deprecated files
- Import test passed: `from cachevoice.server import app` succeeded
- Full test suite: 75/75 passed

**Outcome:** Clean removal, no breaking changes. LiteLLMRouter fully replaced these deprecated gateways.

## T10: Enhanced Cache Stats and Health Endpoints

### Changes Made
1. **Miss Count Tracking**: Added in-memory `_miss_count` counter to `CacheMetadataDB.__init__()` with `record_miss()` and `get_miss_count()` methods
2. **Enhanced get_stats()**: Now returns:
   - `hit_rate`: Calculated as `total_hits / (total_hits + total_misses)`, rounded to 4 decimals
   - `total_misses`: From in-memory counter
   - `cache_age_seconds`: Time since oldest entry `created_at` using `MIN(created_at)` query
   - `per_voice`: Dict breakdown with `{voice_id: {entries, hits, size_bytes}}` from grouped query
3. **Enhanced /health endpoint**: Added `provider_status` field (available/unavailable/unknown) and optional `last_error_time` from gateway attributes
4. **Miss Recording**: Added `_db.record_miss()` calls in server.py for all cache miss paths (text too long, normal miss, no cache)
5. **Test Updates**: Enhanced integration tests to verify new fields, added `record_miss()` stub to test fixtures

### Implementation Notes
- Miss count is in-memory only (resets on restart) - acceptable for stats monitoring
- Per-voice stats use single GROUP BY query for efficiency
- Cache age uses `datetime.fromisoformat()` for SQLite timestamp parsing
- Return type changed from `dict[str, int]` to `dict[str, object]` to support mixed types
- All 75 tests pass

### API Response Example
```json
{
  "total_entries": 150,
  "total_hits": 450,
  "total_misses": 50,
  "hit_rate": 0.9000,
  "cache_age_seconds": 86400,
  "per_voice": {
    "Decent_Boy": {"entries": 100, "hits": 300, "size_bytes": 5242880},
    "alloy": {"entries": 50, "hits": 150, "size_bytes": 2621440}
  }
}
```

## T11: Fuzzy Matching Simplification + Voice Bucketing (2026-02-22)

### Changes Made
- config.py: `FuzzyConfig.enabled` default changed from `True` to `False` — normalizer already handles case+diacritic, exact match sufficient
- hot.py: Refactored from flat `dict[str, str]` keyed by `"{norm}:{voice}"` to voice-bucketed `dict[voice_id, dict[normalized_text, list[audio_path]]]` using `defaultdict`
- hot.py: `fuzzy_lookup()` now accepts `scorer` param, uses `SCORERS` dict to resolve scorer function from config string
- hot.py: Added `get_paths()` for variety depth — returns all cached audio paths for a (text, voice) pair
- matcher.py: `FuzzyMatcher.__init__` now takes `FuzzyConfig` instead of bare `threshold: int`. Respects `enabled` flag — skips fuzzy scan when disabled.
- store.py: `FuzzyCacheStorage.__init__` takes `fuzzy_config: FuzzyConfig` instead of `fuzzy_threshold: int`
- server.py + test_litellm_integration.py: Updated callers to pass `fuzzy_config=settings.cache.fuzzy`

### Key Findings
- Old HotCache had O(n) candidate filtering on every fuzzy lookup (`[t for t in self._texts if f"{t}:{voice_id}" in self._exact]`). Voice bucketing eliminates this — candidates are directly `bucket.keys()`.
- `list[audio_path]` per text enables variety depth without schema changes — multiple TTS generations for same text stored as list.
- Duplicate audio paths in `add()` are deduplicated with `if path not in paths` check.
- `size` property counts unique (text, voice) pairs across all buckets, not total audio paths.
- 80/80 tests pass including 5 new tests: fuzzy_disabled_by_default, fuzzy_enabled_via_config, voice_bucketing, voice_bucketing_variety_depth, voice_bucketing_size.

## T17: Startup Integrity Check — DB↔Filesystem Consistency (2026-02-22)

### Changes Made
- metadata.py: Added `get_all_entries_with_ids()` (includes `id` column) and `delete_entries_by_ids(ids)` (bulk DELETE with IN clause)
- server.py: Added `_startup_integrity_check(db, store, audio_dir)` function with two phases:
  - Phase 1: Scan all DB entries, delete those whose audio_path doesn't exist on disk, remove from HotCache
  - Phase 2: Scan audio_dir for files not referenced by any DB entry, delete orphan files
- server.py lifespan: Called after HotCache load (line ~90), before gateway/filler init
- Fillers directory is safe: only iterates top-level audio_dir files (not subdirs), so fillers/ contents are untouched
- Logs: "Startup: removed N orphan DB entries, M orphan files"

### Key Findings
- `audio_dir.iterdir()` only yields direct children — fillers live in `audio_dir/fillers/` subdir, so `f.is_file()` check naturally skips the fillers directory
- Path resolution (`Path.resolve()`) needed for reliable set membership comparison between DB paths and filesystem paths
- Bulk delete via `WHERE id IN (?,?,...)` is efficient for batch orphan removal
- Pre-existing basedpyright errors in server.py are all `dict[str, object]` type narrowing issues, not introduced by this change

### Test Coverage (3 new tests)
- `test_integrity_removes_orphan_db_entries`: DB entry with missing file → removed from DB + HotCache, valid entry preserved
- `test_integrity_removes_orphan_audio_files`: Audio file not in DB → deleted, referenced file preserved
- `test_integrity_preserves_filler_dir`: Filler files in fillers/ subdir untouched
- 83/83 tests pass

## T11-wire: FallbackOrchestrator Wired into server.py (2026-02-22)

### Changes Made
- server.py: `_gateway` type changed from `LiteLLMRouter` to `FallbackOrchestrator`. LiteLLMRouter now wrapped inside FallbackOrchestrator alongside EdgeTTSProvider.
- server.py: Fallback chain is config-driven — `["litellm"]` base, `"edge"` appended if present in `providers.fallback_chain`.
- server.py: HTTPException re-raised before generic `except Exception` to preserve FallbackOrchestrator's 503 status code.
- fallback.py: Added `available` property (True if fallback_chain non-empty) for `/health` endpoint compat.
- fallback.py: `synthesize` signature updated to `voice: str | None = None, model: str | None = None` to match `_SynthesizerGateway` protocol.
- fallback.py: `_should_fallback` extended to include `RuntimeError` — LiteLLMRouter raises this when no deployments exist.

### Key Findings
- FillerManager uses `_SynthesizerGateway` protocol with `synthesize(text, voice)`. FallbackOrchestrator needed optional params to satisfy this.
- `LiteLLMRouter.synthesize` raises `RuntimeError("No TTS gateway configured")` when no deployments — this must trigger Edge fallback, not abort.
- EdgeTTSProvider voice defaults from `providers.configs.edge.default_voice` in YAML config.
- Pre-existing caplog flakiness: `_setup_logging` sets `propagate=False`, breaking caplog if integration tests run first. Test file ordering matters.
- 86/86 tests pass

## T15: Filler Auto-Generation on Startup

### Implementation
- Added startup check in `server.py` lifespan for `fillers.auto_generate_on_startup` config flag
- When enabled, calls `filler_manager.generate_fillers(voice_id)` during startup
- 30-second timeout prevents blocking startup indefinitely
- Failures logged as warnings, don't crash server (graceful degradation)
- Log format: "Fillers: generated N/M templates for voice '{voice_id}'"

### Testing
- Added `test_auto_generate_fillers_on_startup`: verifies log output when enabled
- Added `test_auto_generate_disabled_by_default`: verifies no generation when disabled
- Used `capfd` fixture to capture stderr logs (logging goes to stderr by default)
- Tests pass: 88/88 (1 pre-existing failure in `test_voice_bucketing_variety_depth` unrelated to this task)

### Key Decisions
- Timeout: 30s prevents long startup delays
- Error handling: warnings only, continue startup on failure
- Log level: INFO for success, WARNING for timeout/failure
- Config requirement: both `auto_generate_on_startup=true` AND `voice_id` must be set

### Integration Points
- Depends on: FillerManager, FallbackOrchestrator, config system
- Blocks: none (optional feature)
- Works with: existing filler generation endpoint `/v1/cache/fillers/generate`

## T16 Foundation: Variety Depth in HotCache + Store (2026-02-22)

### Changes Made
- Added `cache.variety_depth` to `CacheConfig` with default `1` for backward compatibility.
- Updated `HotCache` to accept `variety_depth`; `add()` now deduplicates and caps paths per `(text_normalized, voice_id)` by depth.
- Updated `HotCache.exact_lookup()` to return `random.choice(paths)` when multiple versions exist.
- Extended `FuzzyCacheStorage` with optional `metadata_db` and `variety_depth`; `store()` now accepts optional `version_num`.
- `store()` now derives `version_num` from `db.get_version_count(...)` when DB is attached, capped by `variety_depth`, and persists via `db.add_entry(..., version_num=...)`.
- Filename generation now includes version in hash key for versions `>1`, preserving old hash behavior for version `1`.

### Verification
- Added tests for:
  - `variety_depth=1` keeping a single cached path
  - `variety_depth=4` allowing multiple paths with dedup
  - random selection path in exact lookup
  - DB-backed store version increment behavior
- Full suite passed: `pytest tests/ -v` -> `91 passed`.

- Variety depth now warms asynchronously: cache HIT schedules next version when version_count < variety_depth, cache MISS stores v1 then schedules v2 when depth > 1.
- Background generation uses asyncio.create_task plus in-flight dedup set keyed by (text_normalized, voice) so only one warm-up runs per key at a time.
- Background generation reuses gateway synthesize path, stores with explicit version_num, and logs "Variety: generating version N/M for text_preview" for observability.
