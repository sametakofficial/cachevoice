"""CacheClaw — FastAPI server with TTS cache proxy."""
from __future__ import annotations
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response, FileResponse
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import asyncio
import hashlib
import tempfile
import subprocess
import os

from .config import Settings
from .cache.store import FuzzyCacheStorage
from .cache.metadata import CacheMetadataDB
from .cache.normalizer import normalize
from .cache.evictor import CacheEvictor
from .gateway.litellm_router import LiteLLMRouter
from .fillers.manager import FillerManager

logger = logging.getLogger("cachevoice")

_store: FuzzyCacheStorage | None = None
_db: CacheMetadataDB | None = None
_gateway: LiteLLMRouter | None = None
_filler_mgr: FillerManager | None = None
_settings: Settings | None = None
_evictor: CacheEvictor | None = None
_write_counter: int = 0
_eviction_task: asyncio.Task[None] | None = None


def _load_settings() -> Settings:
    for path in ["cachevoice.yaml", "cachevoice.example.yaml"]:
        if Path(path).exists():
            return Settings.from_yaml(path)
    return Settings()


async def _periodic_eviction():
    """Background task: run evictor every N hours."""
    global _evictor, _settings
    if not _evictor or not _settings:
        return
    
    interval_seconds = _settings.cache.eviction.cleanup_interval_hours * 3600
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            removed = _evictor.run()
            if removed > 0:
                logger.info("Periodic eviction removed %d entries", removed)
        except Exception as e:
            logger.error("Periodic eviction failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _db, _gateway, _filler_mgr, _settings, _evictor, _eviction_task, _write_counter
    _settings = _load_settings()
    logger.info("CacheClaw starting on port %s...", _settings.server.port)

    _db = CacheMetadataDB(_settings.cache.db_path)
    _store = FuzzyCacheStorage(
        audio_dir=_settings.cache.audio_dir,
        fuzzy_threshold=_settings.cache.fuzzy.threshold,
    )

    entries = _db.get_all_entries()
    _store.hot_cache.load_entries(entries)
    logger.info("Loaded %d cache entries into hot cache", len(entries))

    _gateway = LiteLLMRouter(_settings)

    _filler_mgr = FillerManager(_db, _store, _gateway)
    
    _evictor = CacheEvictor(
        _db,
        max_entries=_settings.cache.eviction.max_entries,
        max_size_mb=_settings.cache.eviction.max_size_mb,
        min_age_days=_settings.cache.eviction.min_age_days,
    )
    _write_counter = 0
    _eviction_task = asyncio.create_task(_periodic_eviction())
    logger.info("Cache evictor initialized (interval=%dh, max_entries=%d)", 
                _settings.cache.eviction.cleanup_interval_hours, _settings.cache.eviction.max_entries)

    yield
    
    if _eviction_task:
        _eviction_task.cancel()
        try:
            await _eviction_task
        except asyncio.CancelledError:
            pass
    logger.info("CacheClaw shutting down...")


app = FastAPI(title="CacheClaw", version="0.1.0", lifespan=lifespan)


def _convert_audio_format(audio_data: bytes, target_format: str) -> bytes | None:
    """Convert audio bytes to target format using ffmpeg.
    
    Args:
        audio_data: Input audio bytes (assumed mp3 from provider)
        target_format: Target format (opus, wav, ogg)
    
    Returns:
        Converted audio bytes or None if conversion fails
    """
    if target_format not in ("opus", "wav", "ogg"):
        return None
    
    input_fd, input_path = tempfile.mkstemp(suffix=".mp3")
    output_fd, output_path = tempfile.mkstemp(suffix=f".{target_format}")
    
    try:
        os.write(input_fd, audio_data)
        os.close(input_fd)
        os.close(output_fd)
        
        if target_format == "opus":
            # OGG Opus container for Telegram voice
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-c:a", "libopus", "-b:a", "64k",
                "-ar", "48000", "-ac", "1",
                "-application", "voip",
                "-f", "ogg", output_path
            ]
        elif target_format == "wav":
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-f", "wav", output_path
            ]
        elif target_format == "ogg":
            # OGG Vorbis
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-c:a", "libvorbis", "-q:a", "4",
                "-f", "ogg", output_path
            ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30
        )
        
        if result.returncode != 0:
            logger.warning(f"ffmpeg conversion to {target_format} failed (exit {result.returncode})")
            return None
        
        with open(output_path, "rb") as f:
            return f.read()
    
    except FileNotFoundError:
        logger.warning("ffmpeg not found, format conversion unavailable")
        return None
    except Exception as e:
        logger.error(f"Audio conversion error: {e}")
        return None
    finally:
        for path in [input_path, output_path]:
            try:
                os.unlink(path)
            except (OSError, UnboundLocalError):
                pass


@app.get("/health")
async def health():
    return {"status": "ok", "cache_size": _store.size if _store else 0}


@app.post("/v1/audio/speech")
async def audio_speech(request: Request):
    body = await request.json()
    text = body.get("input", "")
    voice = body.get("voice", "Decent_Boy")
    model = body.get("model", "tts-1")
    response_format = body.get("response_format", "mp3")

    if not text:
        return Response(content=b"", status_code=400)

    # Cache lookup with format-specific key
    if _store and _settings and _settings.cache.enabled:
        result = _store.lookup(text, voice)
        if result:
            audio_path = result["audio_path"]
            cached_format = Path(audio_path).suffix[1:]  # Extract format from file extension
            
            try:
                audio_data = Path(audio_path).read_bytes()
                
                # Convert if cached format doesn't match requested format
                if cached_format != response_format and response_format != "mp3":
                    converted = _convert_audio_format(audio_data, response_format)
                    if converted:
                        audio_data = converted
                        logger.info("Cache HIT + converted %s->%s: %s", cached_format, response_format, text[:50])
                    else:
                        logger.warning("Cache HIT but conversion failed, using cached format")
                        response_format = cached_format
                
                if _db:
                    normalized = result.get("normalized", normalize(text))
                    await _db.record_hit_async(normalized, voice)
                logger.info("Cache %s (score=%s): %s", result["match_type"], result["score"], text[:50])
                
                content_type = {"mp3": "audio/mpeg", "opus": "audio/ogg", "ogg": "audio/ogg", "wav": "audio/wav"}.get(response_format, "audio/mpeg")
                return Response(content=audio_data, media_type=content_type)
            except FileNotFoundError:
                pass

    if not _gateway or not getattr(_gateway, "available", True):
        return Response(content=b"No TTS gateway configured", status_code=503)

    # Gateway returns mp3 by default
    try:
        audio_data = await _gateway.synthesize(text, voice, model, "mp3")
    except Exception as e:
        logger.error("TTS API error: %s", e)
        return Response(content=str(e).encode(), status_code=502)

    # Convert if non-mp3 format requested
    provider_format = "mp3"
    if response_format != "mp3":
        converted = _convert_audio_format(audio_data, response_format)
        if converted:
            audio_data = converted
            provider_format = response_format
            logger.info("Converted gateway output mp3->%s", response_format)
        else:
            logger.warning("Format conversion failed, returning mp3")
            response_format = "mp3"

    # Cache the audio in the format we're returning
    if _store and _db and _settings:
        if len(text) > _settings.cache.eviction.max_text_length:
            logger.info("Text too long (%d chars), skipping cache", len(text))
        else:
            normalized = normalize(text)
            audio_path = _store.store(text, voice, audio_data, provider_format)
            _db.add_entry(
                text_original=text, text_normalized=normalized, voice_id=voice,
                audio_path=audio_path, model=model, audio_format=provider_format,
                file_size=len(audio_data),
            )
            logger.info("Cache MISS -> stored: %s", text[:50])
            
            global _write_counter, _evictor
            _write_counter += 1
            if _write_counter >= 100 and _evictor:
                _write_counter = 0
                try:
                    removed = _evictor.run()
                    if removed > 0:
                        logger.info("Write-triggered eviction removed %d entries", removed)
                except Exception as e:
                    logger.error("Write-triggered eviction failed: %s", e)

    content_type = {"mp3": "audio/mpeg", "opus": "audio/ogg", "ogg": "audio/ogg", "wav": "audio/wav"}.get(response_format, "audio/mpeg")
    return Response(content=audio_data, media_type=content_type)


@app.get("/v1/cache/stats")
async def cache_stats():
    if not _db:
        return {"error": "not initialized"}
    stats = _db.get_stats()
    stats["hot_cache_size"] = _store.size if _store else 0
    return stats


@app.delete("/v1/cache")
async def cache_clear():
    if not _db or not _store:
        return {"error": "not initialized"}
    paths = _db.delete_all()
    _store.clear()
    removed_files = 0
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
            removed_files += 1
        except Exception:
            pass
    return {"cleared_entries": len(paths), "removed_files": removed_files}


@app.get("/v1/cache/fillers")
async def list_fillers(voice_id: str = "Decent_Boy"):
    if not _filler_mgr:
        return {"error": "not initialized"}
    return {"fillers": _filler_mgr.list_fillers(voice_id)}


@app.post("/v1/cache/fillers/generate")
async def generate_fillers(request: Request):
    body = await request.json()
    voice_id = body.get("voice_id", "Decent_Boy")
    if not _filler_mgr:
        return {"error": "not initialized"}
    results = await _filler_mgr.generate_fillers(voice_id)
    return {"results": results}


@app.get("/v1/fillers")
async def get_fillers():
    """List all available filler audio files."""
    if not _settings:
        raise HTTPException(status_code=503, detail="Server not initialized")
    
    fillers_dir = Path(_settings.cache.audio_dir) / "fillers"
    if not fillers_dir.exists():
        return {"fillers": []}
    
    filler_names = []
    for audio_file in fillers_dir.iterdir():
        if audio_file.is_file() and audio_file.suffix in [".mp3", ".ogg"]:
            filler_names.append(audio_file.stem)
    
    return {"fillers": sorted(filler_names)}


@app.get("/v1/fillers/{name}")
async def get_filler_audio(name: str, request: Request):
    """Download a specific filler audio file with ETag caching support."""
    if not _settings:
        raise HTTPException(status_code=503, detail="Server not initialized")
    
    fillers_dir = Path(_settings.cache.audio_dir) / "fillers"
    
    # Try .mp3 first, then .ogg
    audio_path = None
    content_type = None
    for ext, mime in [(".mp3", "audio/mpeg"), (".ogg", "audio/ogg")]:
        candidate = fillers_dir / f"{name}{ext}"
        if candidate.exists():
            audio_path = candidate
            content_type = mime
            break
    
    if not audio_path:
        raise HTTPException(status_code=404, detail=f"Filler '{name}' not found")
    
    # Generate ETag from file mtime and size
    stat = audio_path.stat()
    etag = hashlib.md5(f"{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()
    
    # Check If-None-Match header
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status_code=304)
    
    # Return audio file with ETag
    audio_data = audio_path.read_bytes()
    return Response(
        content=audio_data,
        media_type=content_type,
        headers={"ETag": f'"{etag}"'}
    )
