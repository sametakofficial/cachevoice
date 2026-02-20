"""Tests for Edge TTS provider."""
import pytest
from cachevoice.gateway.edge import EdgeTTSProvider


@pytest.mark.anyio
async def test_edge_tts_synthesize():
    """Test Edge TTS synthesis returns valid MP3 bytes."""
    provider = EdgeTTSProvider(default_voice="tr-TR-AhmetNeural")
    
    # Synthesize test text
    audio_bytes = await provider.synthesize("test", "tr-TR-AhmetNeural")
    
    # Verify we got bytes back
    assert isinstance(audio_bytes, bytes)
    assert len(audio_bytes) > 0
    
    # Verify it looks like MP3 (starts with ID3 or FF FB/FF F3)
    assert audio_bytes[:3] == b'ID3' or audio_bytes[0:2] in (b'\xff\xfb', b'\xff\xf3')


@pytest.mark.anyio
async def test_edge_tts_default_voice():
    """Test Edge TTS uses default voice when none specified."""
    provider = EdgeTTSProvider(default_voice="tr-TR-AhmetNeural")
    
    # Synthesize without specifying voice
    audio_bytes = await provider.synthesize("merhaba")
    
    assert isinstance(audio_bytes, bytes)
    assert len(audio_bytes) > 0


@pytest.mark.anyio
async def test_edge_tts_custom_voice():
    """Test Edge TTS with custom voice."""
    provider = EdgeTTSProvider()
    
    # Use English voice
    audio_bytes = await provider.synthesize("hello", voice="en-US-GuyNeural")
    
    assert isinstance(audio_bytes, bytes)
    assert len(audio_bytes) > 0


@pytest.mark.anyio
async def test_edge_tts_property():
    """Test default_voice property."""
    provider = EdgeTTSProvider(default_voice="tr-TR-AhmetNeural")
    assert provider.default_voice == "tr-TR-AhmetNeural"
