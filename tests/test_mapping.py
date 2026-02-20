"""Tests for voice and model mapping."""

from cachevoice.gateway.mapping import VoiceMapper, ModelMapper


def test_voice_mapper_with_mapping():
    """Test VoiceMapper returns mapped voice when mapping exists."""
    config = {
        "voice_mapping": {
            "alloy": {
                "minimax": "Decent_Boy",
                "elevenlabs": "voice-id-123"
            },
            "echo": {
                "minimax": "Deep_Voice_Man"
            }
        }
    }
    mapper = VoiceMapper(config)
    
    assert mapper.map("alloy", "minimax") == "Decent_Boy"
    assert mapper.map("alloy", "elevenlabs") == "voice-id-123"
    assert mapper.map("echo", "minimax") == "Deep_Voice_Man"


def test_voice_mapper_passthrough_no_mapping():
    """Test VoiceMapper returns original voice when no mapping exists."""
    config = {
        "voice_mapping": {
            "alloy": {
                "minimax": "Decent_Boy"
            }
        }
    }
    mapper = VoiceMapper(config)
    
    # Voice not in config
    assert mapper.map("nova", "minimax") == "nova"
    
    # Provider not in voice mapping
    assert mapper.map("alloy", "openai") == "alloy"


def test_voice_mapper_empty_config():
    """Test VoiceMapper with empty config passes through all voices."""
    mapper = VoiceMapper({})
    
    assert mapper.map("alloy", "minimax") == "alloy"
    assert mapper.map("echo", "openai") == "echo"


def test_model_mapper_with_mapping():
    """Test ModelMapper returns mapped model when mapping exists."""
    config = {
        "model_mapping": {
            "tts-1": {
                "minimax": "speech-01-turbo",
                "openai": "tts-1"
            },
            "tts-1-hd": {
                "minimax": "speech-01-hd"
            }
        }
    }
    mapper = ModelMapper(config)
    
    assert mapper.map("tts-1", "minimax") == "speech-01-turbo"
    assert mapper.map("tts-1", "openai") == "tts-1"
    assert mapper.map("tts-1-hd", "minimax") == "speech-01-hd"


def test_model_mapper_passthrough_no_mapping():
    """Test ModelMapper returns original model when no mapping exists."""
    config = {
        "model_mapping": {
            "tts-1": {
                "minimax": "speech-01-turbo"
            }
        }
    }
    mapper = ModelMapper(config)
    
    # Model not in config
    assert mapper.map("tts-2", "minimax") == "tts-2"
    
    # Provider not in model mapping
    assert mapper.map("tts-1", "elevenlabs") == "tts-1"


def test_model_mapper_empty_config():
    """Test ModelMapper with empty config passes through all models."""
    mapper = ModelMapper({})
    
    assert mapper.map("tts-1", "minimax") == "tts-1"
    assert mapper.map("tts-1-hd", "openai") == "tts-1-hd"


def test_multiple_providers():
    """Test mapping works correctly with multiple providers."""
    config = {
        "voice_mapping": {
            "alloy": {
                "minimax": "Decent_Boy",
                "elevenlabs": "ElevenLabs_Voice",
                "openai": "alloy"
            }
        },
        "model_mapping": {
            "tts-1": {
                "minimax": "speech-01-turbo",
                "elevenlabs": "eleven_multilingual_v2",
                "openai": "tts-1"
            }
        }
    }
    
    voice_mapper = VoiceMapper(config)
    model_mapper = ModelMapper(config)
    
    # Test all providers for voice
    assert voice_mapper.map("alloy", "minimax") == "Decent_Boy"
    assert voice_mapper.map("alloy", "elevenlabs") == "ElevenLabs_Voice"
    assert voice_mapper.map("alloy", "openai") == "alloy"
    
    # Test all providers for model
    assert model_mapper.map("tts-1", "minimax") == "speech-01-turbo"
    assert model_mapper.map("tts-1", "elevenlabs") == "eleven_multilingual_v2"
    assert model_mapper.map("tts-1", "openai") == "tts-1"
