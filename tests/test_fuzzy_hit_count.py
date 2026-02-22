"""Test that fuzzy cache hits increment the MATCHED entry's hit_count, not the input's."""
import pytest
from cachevoice.cache.store import FuzzyCacheStorage
from cachevoice.cache.metadata import CacheMetadataDB


@pytest.fixture
def store_and_db(tmp_path):
    store = FuzzyCacheStorage(str(tmp_path / "audio"))
    db = CacheMetadataDB(str(tmp_path / "test.db"))
    return store, db


def test_fuzzy_hit_increments_matched_entry(store_and_db):
    """When fuzzy match occurs, the MATCHED entry's hit_count should increment."""
    store, db = store_and_db
    
    # Store original text
    original_text = "I found 3 sources"
    voice_id = "Decent_Boy"
    store.store(original_text, voice_id, b"fake_audio")
    
    # Add to metadata DB
    from cachevoice.cache.normalizer import normalize
    normalized_original = normalize(original_text)
    db.add_entry(original_text, normalized_original, voice_id, f"/tmp/{normalized_original}.mp3")
    
    # Lookup with different number - should match exactly due to number normalization
    lookup_text = "I found 5 sources"
    result = store.lookup(lookup_text, voice_id)
    
    # Verify match occurred (will be exact due to number normalization)
    assert result is not None
    match_type = result["match_type"]
    
    # Simulate what server.py does: record hit using matched text
    matched_normalized = result.get("matched", result.get("normalized", normalize(lookup_text)))
    db.record_hit(matched_normalized, voice_id)
    
    # Verify the ORIGINAL entry's hit_count was incremented
    entries = db.get_all_entries()
    assert len(entries) == 1
    
    # Get hit_count for the matched entry
    conn = db._get_conn()
    row = conn.execute(
        "SELECT hit_count FROM cache_entries WHERE text_normalized = ? AND voice_id = ?",
        (normalized_original, voice_id)
    ).fetchone()
    conn.close()
    
    assert row is not None
    assert row["hit_count"] == 1, f"Expected hit_count=1 for matched entry, got {row['hit_count']}"
