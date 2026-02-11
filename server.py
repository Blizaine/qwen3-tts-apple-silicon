"""
Qwen3-TTS MLX API Server
FastAPI backend compatible with the beautiful Mac01 UI, using MLX for inference
"""
import os
import sys
import io
import gc
import base64
import logging
import warnings
from pathlib import Path
from typing import Optional, List
from contextlib import asynccontextmanager

# Suppress warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from fastapi import FastAPI, HTTPException, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import re
import json as json_module
from pydantic import BaseModel, Field
import numpy as np

try:
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model
    from mlx_audio.tts.generate import generate_audio
except ImportError:
    print("Error: 'mlx_audio' library not found.")
    print("Please run the install script first.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration
BASE_DIR = Path(__file__).parent
MODELS_DIR = BASE_DIR / "models"
VOICES_DIR = BASE_DIR / "voices"
OUTPUTS_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"
SAMPLE_RATE = 24000

# Model paths mapping
MODEL_PATHS = {
    "custom_voice_pro": "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
    "custom_voice_lite": "Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
    "voice_design_pro": "Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
    "voice_design_lite": "Qwen3-TTS-12Hz-0.6B-VoiceDesign-8bit",
    "base_pro": "Qwen3-TTS-12Hz-1.7B-Base-8bit",
    "base_lite": "Qwen3-TTS-12Hz-0.6B-Base-8bit",
}

# Global model cache
loaded_models = {}
current_model_type = None


def get_model_path(folder_name: str) -> Optional[Path]:
    """Get the actual model path, handling HuggingFace cache structure."""
    full_path = MODELS_DIR / folder_name
    if not full_path.exists():
        return None

    snapshots_dir = full_path / "snapshots"
    if snapshots_dir.exists():
        subfolders = [f for f in snapshots_dir.iterdir() if not f.name.startswith('.')]
        if subfolders:
            return subfolders[0]

    return full_path


def get_available_model(model_type: str):
    """Get the best available model (prefer lite for speed, fall back to pro)."""
    global loaded_models, current_model_type

    # Check if we need to switch models
    if current_model_type != model_type:
        # Clear previous models to save memory
        loaded_models.clear()
        gc.collect()
        current_model_type = model_type

    if model_type in loaded_models:
        return loaded_models[model_type]

    # Try lite first (faster), then pro
    for suffix in ["_lite", "_pro"]:
        key = f"{model_type}{suffix}"
        if key in MODEL_PATHS:
            model_path = get_model_path(MODEL_PATHS[key])
            if model_path:
                logger.info(f"Loading model from {model_path}")
                model = load_model(str(model_path))
                loaded_models[model_type] = model
                return model

    raise HTTPException(status_code=500, detail=f"No {model_type} model found. Please run the install script.")


# Pydantic models for API
class CustomVoiceRequest(BaseModel):
    text: str
    language: str = "Auto"
    speaker: str = "Vivian"
    instruct: str = ""
    speed: float = 1.0
    response_format: str = "base64"


class VoiceDesignRequest(BaseModel):
    text: str
    language: str = "Auto"
    instruct: str
    speed: float = 1.0
    response_format: str = "base64"


class VoiceCloneRequest(BaseModel):
    text: str
    language: str = "Auto"
    ref_audio_base64: Optional[str] = None
    ref_audio_url: Optional[str] = None
    ref_text: Optional[str] = None
    x_vector_only_mode: bool = False
    speed: float = 1.0
    response_format: str = "base64"


class AudioResponse(BaseModel):
    audio: str
    sample_rate: int = 24000
    format: str = "wav"


class SpeakerInfo(BaseModel):
    name: str
    description: str
    native_language: str


class SpeakersResponse(BaseModel):
    speakers: List[SpeakerInfo]


class LanguagesResponse(BaseModel):
    languages: List[str]


# Speaker information (matching Mac01)
SPEAKERS = [
    SpeakerInfo(name="Vivian", description="Bright, slightly edgy young female voice", native_language="Chinese"),
    SpeakerInfo(name="Serena", description="Warm, gentle young female voice", native_language="Chinese"),
    SpeakerInfo(name="Uncle_Fu", description="Seasoned male voice with a low, mellow timbre", native_language="Chinese"),
    SpeakerInfo(name="Dylan", description="Youthful Beijing male voice with a clear, natural timbre", native_language="Chinese"),
    SpeakerInfo(name="Eric", description="Lively Chengdu male voice with a slightly husky brightness", native_language="Chinese"),
    SpeakerInfo(name="Ryan", description="Dynamic male voice with strong rhythmic drive", native_language="English"),
    SpeakerInfo(name="Aiden", description="Sunny American male voice with a clear midrange", native_language="English"),
    SpeakerInfo(name="Ono_Anna", description="Playful Japanese female voice with a light, nimble timbre", native_language="Japanese"),
    SpeakerInfo(name="Sohee", description="Warm Korean female voice with rich emotion", native_language="Korean"),
]

SUPPORTED_LANGUAGES = [
    "Auto", "Chinese", "English", "Japanese", "Korean",
    "German", "French", "Russian", "Portuguese", "Spanish", "Italian"
]


def numpy_to_wav_bytes(audio_data: np.ndarray, sample_rate: int) -> bytes:
    """Convert numpy array to WAV bytes."""
    import wave

    # Normalize and convert to int16
    audio_data = np.clip(audio_data, -1.0, 1.0)
    audio_int16 = (audio_data * 32767).astype(np.int16)

    # Create WAV in memory
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())

    return buffer.getvalue()


def numpy_to_base64(audio_data: np.ndarray, sample_rate: int) -> str:
    """Convert numpy array to base64 encoded WAV."""
    wav_bytes = numpy_to_wav_bytes(audio_data, sample_rate)
    return base64.b64encode(wav_bytes).decode('utf-8')


def generate_with_temp_dir(model, **kwargs):
    """Generate audio using MLX and return the audio data."""
    import shutil
    import time

    temp_dir = f"temp_{int(time.time() * 1000)}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        generate_audio(model=model, output_path=temp_dir, **kwargs)

        # Read the generated audio
        output_file = os.path.join(temp_dir, "audio_000.wav")
        if os.path.exists(output_file):
            import wave
            with wave.open(output_file, 'rb') as wav_file:
                frames = wav_file.readframes(wav_file.getnframes())
                sample_rate = wav_file.getframerate()
                audio_data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
                return audio_data, sample_rate

        raise Exception("Audio generation failed - no output file")
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def chunk_text(text: str, max_chunk_size: int = 500) -> List[str]:
    """
    Split text into chunks by sentences, keeping chunks under max_chunk_size.
    Tries to maintain natural sentence boundaries for better TTS quality.
    """
    # Split by sentence-ending punctuation
    sentence_pattern = r'(?<=[.!?])\s+'
    sentences = re.split(sentence_pattern, text.strip())

    chunks = []
    current_chunk = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # If adding this sentence would exceed max size, save current chunk
        if current_chunk and len(current_chunk) + len(sentence) + 1 > max_chunk_size:
            chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = sentence

    # Don't forget the last chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # If no chunks (text had no sentence boundaries), split by max size
    if not chunks and text.strip():
        text = text.strip()
        while text:
            # Try to split at a space near max_chunk_size
            if len(text) <= max_chunk_size:
                chunks.append(text)
                break
            split_pos = text.rfind(' ', 0, max_chunk_size)
            if split_pos == -1:
                split_pos = max_chunk_size
            chunks.append(text[:split_pos].strip())
            text = text[split_pos:].strip()

    return chunks


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Qwen3-TTS MLX API Server")

    # Create directories
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    VOICES_DIR.mkdir(parents=True, exist_ok=True)

    yield

    logger.info("Shutting down Qwen3-TTS MLX API Server")


# Create FastAPI application
app = FastAPI(
    title="Qwen3-TTS MLX API Server",
    description="API server for Qwen3-TTS models using Apple MLX",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    logger.info(f"Static files mounted from {STATIC_DIR}")


# ============= CustomVoice Endpoints =============

@app.post("/api/v1/custom-voice/generate")
async def generate_custom_voice(request: CustomVoiceRequest):
    """Generate speech using CustomVoice model with preset speakers."""
    try:
        logger.info(f"Generating custom voice for speaker: {request.speaker}")

        model = get_available_model("custom_voice")

        audio_data, sr = generate_with_temp_dir(
            model,
            text=request.text,
            voice=request.speaker,
            instruct=request.instruct or "Normal tone",
            speed=request.speed,
        )

        if request.response_format == "base64":
            return AudioResponse(
                audio=numpy_to_base64(audio_data, sr),
                sample_rate=sr,
                format="wav"
            )
        else:
            wav_bytes = numpy_to_wav_bytes(audio_data, sr)
            return Response(
                content=wav_bytes,
                media_type="audio/wav",
                headers={"Content-Disposition": f"attachment; filename=custom_voice_{request.speaker}.wav"}
            )

    except Exception as e:
        logger.error(f"Error generating custom voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/custom-voice/speakers", response_model=SpeakersResponse)
async def list_speakers():
    """List available speakers for CustomVoice model."""
    return SpeakersResponse(speakers=SPEAKERS)


@app.get("/api/v1/custom-voice/languages", response_model=LanguagesResponse)
async def list_languages():
    """List supported languages for CustomVoice model."""
    return LanguagesResponse(languages=SUPPORTED_LANGUAGES)


# ============= VoiceDesign Endpoints =============

@app.post("/api/v1/voice-design/generate")
async def generate_voice_design(request: VoiceDesignRequest):
    """Generate speech using VoiceDesign model with natural language voice description."""
    try:
        logger.info(f"Generating voice design with instruct: {request.instruct[:50]}...")

        model = get_available_model("voice_design")

        audio_data, sr = generate_with_temp_dir(
            model,
            text=request.text,
            instruct=request.instruct,
        )

        if request.response_format == "base64":
            return AudioResponse(
                audio=numpy_to_base64(audio_data, sr),
                sample_rate=sr,
                format="wav"
            )
        else:
            wav_bytes = numpy_to_wav_bytes(audio_data, sr)
            return Response(
                content=wav_bytes,
                media_type="audio/wav",
                headers={"Content-Disposition": "attachment; filename=voice_design.wav"}
            )

    except Exception as e:
        logger.error(f"Error generating voice design: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============= Voice Clone (Base) Endpoints =============

@app.post("/api/v1/base/clone")
async def clone_voice(request: VoiceCloneRequest):
    """Generate speech using Base model with voice cloning from reference audio."""
    try:
        logger.info("Generating voice clone")

        if not request.ref_audio_base64 and not request.ref_audio_url:
            raise HTTPException(status_code=400, detail="Either ref_audio_url or ref_audio_base64 must be provided")

        model = get_available_model("base")

        # Decode reference audio
        ref_audio_path = None
        temp_ref_file = None

        if request.ref_audio_base64:
            import tempfile
            audio_bytes = base64.b64decode(request.ref_audio_base64)
            temp_ref_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            temp_ref_file.write(audio_bytes)
            temp_ref_file.close()
            ref_audio_path = temp_ref_file.name

        try:
            audio_data, sr = generate_with_temp_dir(
                model,
                text=request.text,
                ref_audio=ref_audio_path,
                ref_text=request.ref_text or ".",
            )

            if request.response_format == "base64":
                return AudioResponse(
                    audio=numpy_to_base64(audio_data, sr),
                    sample_rate=sr,
                    format="wav"
                )
            else:
                wav_bytes = numpy_to_wav_bytes(audio_data, sr)
                return Response(
                    content=wav_bytes,
                    media_type="audio/wav",
                    headers={"Content-Disposition": "attachment; filename=voice_clone.wav"}
                )
        finally:
            if temp_ref_file and os.path.exists(temp_ref_file.name):
                os.unlink(temp_ref_file.name)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating voice clone: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class StreamingVoiceCloneRequest(BaseModel):
    ref_audio_base64: Optional[str] = None
    ref_audio_url: Optional[str] = None
    ref_text: Optional[str] = None
    text: str
    language: str = "Auto"
    speed: float = 1.0
    chunk_size: int = 500  # Max characters per chunk
    seed: Optional[int] = None  # Random seed for consistent voice across chunks


@app.post("/api/v1/base/clone/stream")
async def clone_voice_stream(request: StreamingVoiceCloneRequest):
    """Stream voice clone audio in chunks for real-time playback."""

    logger.info(f"Clone stream request received, text length: {len(request.text)}")

    def generate_chunks():
        try:
            logger.info("Clone stream generator started")

            if not request.ref_audio_base64 and not request.ref_audio_url:
                yield f"data: {json_module.dumps({'error': 'Either ref_audio_url or ref_audio_base64 must be provided'})}\n\n"
                return

            model = get_available_model("base")
            logger.info("Model loaded for clone stream")

            # Prepare reference audio
            import tempfile
            temp_ref_file = None
            if request.ref_audio_base64:
                audio_bytes = base64.b64decode(request.ref_audio_base64)
                temp_ref_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                temp_ref_file.write(audio_bytes)
                temp_ref_file.close()
                ref_audio_path = temp_ref_file.name
                logger.info(f"Reference audio prepared: {temp_ref_file.name}")

            try:
                # Chunk the text
                chunks = chunk_text(request.text, request.chunk_size)
                total_chunks = len(chunks)

                logger.info(f"Streaming voice clone: {total_chunks} chunks from {len(request.text)} chars")

                # Send initial metadata
                yield f"data: {json_module.dumps({'type': 'start', 'total_chunks': total_chunks, 'total_chars': len(request.text)})}\n\n"

                for i, chunk_text_content in enumerate(chunks):
                    logger.info(f"Generating chunk {i+1}/{total_chunks}: {len(chunk_text_content)} chars")

                    # Set seed for consistent voice across chunks
                    if request.seed is not None:
                        mx.random.seed(request.seed)
                        logger.info(f"Set random seed to {request.seed} for chunk {i+1}")

                    # Generate audio for this chunk
                    audio_data, sr = generate_with_temp_dir(
                        model,
                        text=chunk_text_content,
                        ref_audio=ref_audio_path,
                        ref_text=request.ref_text or ".",
                    )

                    # Convert to base64
                    audio_base64 = numpy_to_base64(audio_data, sr)

                    # Send chunk
                    chunk_data = {
                        'type': 'chunk',
                        'chunk_index': i,
                        'total_chunks': total_chunks,
                        'audio': audio_base64,
                        'sample_rate': sr,
                        'text': chunk_text_content,
                    }
                    logger.info(f"Sending chunk {i+1}/{total_chunks}")
                    yield f"data: {json_module.dumps(chunk_data)}\n\n"

                # Send completion
                logger.info("Sending done message")
                yield f"data: {json_module.dumps({'type': 'done', 'total_chunks': total_chunks})}\n\n"

            finally:
                if temp_ref_file and os.path.exists(temp_ref_file.name):
                    os.unlink(temp_ref_file.name)

        except Exception as e:
            import traceback
            logger.error(f"Error in streaming voice clone: {e}")
            logger.error(traceback.format_exc())
            yield f"data: {json_module.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate_chunks(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/api/v1/base/upload-ref-audio")
async def upload_reference_audio(file: UploadFile = File(...)):
    """Upload reference audio file and get base64 encoded data."""
    try:
        logger.info(f"Uploading reference audio: {file.filename}")

        content = await file.read()
        audio_base64 = base64.b64encode(content).decode("utf-8")

        return {
            "filename": file.filename,
            "content_type": file.content_type,
            "audio_base64": audio_base64,
            "message": "File uploaded and encoded successfully"
        }

    except Exception as e:
        logger.error(f"Error uploading reference audio: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Storage for saved voice prompts (with file-based persistence)
saved_voice_prompts = {}
SAVED_VOICES_DIR = VOICES_DIR / "saved"


def load_saved_voices():
    """Load all saved voice prompts from disk on startup."""
    global saved_voice_prompts
    SAVED_VOICES_DIR.mkdir(parents=True, exist_ok=True)

    count = 0
    for voice_dir in SAVED_VOICES_DIR.iterdir():
        if voice_dir.is_dir():
            metadata_file = voice_dir / "metadata.json"
            audio_file = voice_dir / "audio.wav"

            if metadata_file.exists() and audio_file.exists():
                try:
                    import json
                    with open(metadata_file, "r") as f:
                        metadata = json.load(f)

                    # Load audio as base64
                    with open(audio_file, "rb") as f:
                        audio_base64 = base64.b64encode(f.read()).decode("utf-8")

                    prompt_id = voice_dir.name
                    saved_voice_prompts[prompt_id] = {
                        "ref_audio_base64": audio_base64,
                        "ref_text": metadata.get("ref_text"),
                        "name": metadata.get("name", f"Voice_{prompt_id[:8]}"),
                        "x_vector_only_mode": metadata.get("x_vector_only_mode", False),
                    }
                    count += 1
                except Exception as e:
                    logger.error(f"Error loading voice {voice_dir.name}: {e}")

    logger.info(f"Loaded {count} saved voice prompts from disk")


def save_voice_to_disk(prompt_id: str, data: dict):
    """Save a voice prompt to disk."""
    voice_dir = SAVED_VOICES_DIR / prompt_id
    voice_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata (without audio)
    import json
    metadata = {
        "name": data.get("name"),
        "ref_text": data.get("ref_text"),
        "x_vector_only_mode": data.get("x_vector_only_mode", False),
    }
    with open(voice_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # Save audio as WAV file
    audio_bytes = base64.b64decode(data["ref_audio_base64"])
    with open(voice_dir / "audio.wav", "wb") as f:
        f.write(audio_bytes)

    logger.info(f"Saved voice {prompt_id} to disk")


def delete_voice_from_disk(prompt_id: str):
    """Delete a voice prompt from disk."""
    import shutil
    voice_dir = SAVED_VOICES_DIR / prompt_id
    if voice_dir.exists():
        shutil.rmtree(voice_dir)
        logger.info(f"Deleted voice {prompt_id} from disk")


# Load saved voices on module import
load_saved_voices()


class CreatePromptRequest(BaseModel):
    ref_audio_base64: Optional[str] = None
    ref_audio_url: Optional[str] = None
    ref_text: Optional[str] = None
    name: Optional[str] = None  # Optional name for the saved voice
    x_vector_only_mode: bool = False


class CreatePromptResponse(BaseModel):
    prompt_id: str
    message: str


@app.post("/api/v1/base/create-prompt", response_model=CreatePromptResponse)
async def create_voice_clone_prompt(request: CreatePromptRequest):
    """Create a reusable voice clone prompt from reference audio."""
    try:
        logger.info("Creating voice clone prompt")

        if not request.ref_audio_base64 and not request.ref_audio_url:
            raise HTTPException(status_code=400, detail="Either ref_audio_url or ref_audio_base64 must be provided")

        import uuid

        # Generate unique prompt ID
        prompt_id = str(uuid.uuid4())

        # Store the prompt data (reference audio and text)
        prompt_data = {
            "ref_audio_base64": request.ref_audio_base64,
            "ref_text": request.ref_text,
            "name": request.name or f"Voice_{prompt_id[:8]}",
            "x_vector_only_mode": request.x_vector_only_mode,
        }
        saved_voice_prompts[prompt_id] = prompt_data

        # Persist to disk
        save_voice_to_disk(prompt_id, prompt_data)

        logger.info(f"Created voice clone prompt with ID: {prompt_id}")

        return CreatePromptResponse(
            prompt_id=prompt_id,
            message="Prompt created successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating voice clone prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class GenerateWithPromptRequest(BaseModel):
    prompt_id: str
    text: str
    language: str = "Auto"
    speed: float = 1.0
    response_format: str = "base64"


@app.post("/api/v1/base/generate-with-prompt")
async def generate_with_voice_clone_prompt(request: GenerateWithPromptRequest):
    """Generate speech using a saved voice clone prompt."""
    try:
        logger.info(f"Generating with voice clone prompt: {request.prompt_id}")

        # Get stored prompt
        if request.prompt_id not in saved_voice_prompts:
            raise HTTPException(status_code=404, detail=f"Prompt ID not found: {request.prompt_id}")

        prompt_data = saved_voice_prompts[request.prompt_id]

        model = get_available_model("base")

        # Decode reference audio
        import tempfile
        audio_bytes = base64.b64decode(prompt_data["ref_audio_base64"])
        temp_ref_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_ref_file.write(audio_bytes)
        temp_ref_file.close()

        try:
            audio_data, sr = generate_with_temp_dir(
                model,
                text=request.text,
                ref_audio=temp_ref_file.name,
                ref_text=prompt_data["ref_text"] or ".",
            )

            if request.response_format == "base64":
                return AudioResponse(
                    audio=numpy_to_base64(audio_data, sr),
                    sample_rate=sr,
                    format="wav"
                )
            else:
                wav_bytes = numpy_to_wav_bytes(audio_data, sr)
                return Response(
                    content=wav_bytes,
                    media_type="audio/wav",
                    headers={"Content-Disposition": "attachment; filename=voice_clone_prompt.wav"}
                )
        finally:
            if os.path.exists(temp_ref_file.name):
                os.unlink(temp_ref_file.name)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating with voice clone prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class StreamingGenerateWithPromptRequest(BaseModel):
    prompt_id: str
    text: str
    language: str = "Auto"
    speed: float = 1.0
    chunk_size: int = 500
    seed: Optional[int] = None  # Random seed for consistent voice across chunks


@app.post("/api/v1/base/generate-with-prompt/stream")
async def stream_generate_with_voice_clone_prompt(request: StreamingGenerateWithPromptRequest):
    """Stream speech generation using a saved voice clone prompt."""

    logger.info(f"Stream request received for prompt_id: {request.prompt_id}, text length: {len(request.text)}")
    logger.info(f"Available prompts: {list(saved_voice_prompts.keys())}")

    def generate_chunks():
        try:
            logger.info("Generator started")

            # Get stored prompt
            if request.prompt_id not in saved_voice_prompts:
                logger.error(f"Prompt ID not found: {request.prompt_id}")
                yield f"data: {json_module.dumps({'type': 'error', 'error': f'Prompt ID not found: {request.prompt_id}'})}\n\n"
                return

            prompt_data = saved_voice_prompts[request.prompt_id]
            logger.info(f"Found prompt data for: {prompt_data.get('name', 'unnamed')}")

            model = get_available_model("base")
            logger.info("Model loaded")

            # Prepare reference audio
            import tempfile
            audio_bytes = base64.b64decode(prompt_data["ref_audio_base64"])
            temp_ref_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            temp_ref_file.write(audio_bytes)
            temp_ref_file.close()
            logger.info(f"Temp reference audio created: {temp_ref_file.name}")

            try:
                # Chunk the text
                chunks = chunk_text(request.text, request.chunk_size)
                total_chunks = len(chunks)

                logger.info(f"Streaming with saved prompt: {total_chunks} chunks from {len(request.text)} chars")

                # Send initial metadata
                start_msg = f"data: {json_module.dumps({'type': 'start', 'total_chunks': total_chunks, 'total_chars': len(request.text), 'voice_name': prompt_data.get('name', '')})}\n\n"
                logger.info(f"Sending start message")
                yield start_msg

                for i, chunk_text_content in enumerate(chunks):
                    logger.info(f"Generating chunk {i+1}/{total_chunks}: {len(chunk_text_content)} chars")

                    # Set seed for consistent voice across chunks
                    if request.seed is not None:
                        mx.random.seed(request.seed)
                        logger.info(f"Set random seed to {request.seed} for chunk {i+1}")

                    # Generate audio for this chunk
                    audio_data, sr = generate_with_temp_dir(
                        model,
                        text=chunk_text_content,
                        ref_audio=temp_ref_file.name,
                        ref_text=prompt_data["ref_text"] or ".",
                    )
                    logger.info(f"Chunk {i+1} generated, audio shape: {audio_data.shape if hasattr(audio_data, 'shape') else len(audio_data)}")

                    # Convert to base64
                    audio_base64 = numpy_to_base64(audio_data, sr)

                    # Send chunk
                    chunk_data = {
                        'type': 'chunk',
                        'chunk_index': i,
                        'total_chunks': total_chunks,
                        'audio': audio_base64,
                        'sample_rate': sr,
                        'text': chunk_text_content,
                    }
                    logger.info(f"Sending chunk {i+1}/{total_chunks}")
                    yield f"data: {json_module.dumps(chunk_data)}\n\n"

                # Send completion
                logger.info("Sending done message")
                yield f"data: {json_module.dumps({'type': 'done', 'total_chunks': total_chunks})}\n\n"

            finally:
                if os.path.exists(temp_ref_file.name):
                    os.unlink(temp_ref_file.name)

        except Exception as e:
            import traceback
            logger.error(f"Error in streaming with saved prompt: {e}")
            logger.error(traceback.format_exc())
            yield f"data: {json_module.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate_chunks(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/v1/base/prompts")
async def list_saved_prompts():
    """List all saved voice clone prompts."""
    prompts = []
    for prompt_id, data in saved_voice_prompts.items():
        prompts.append({
            "prompt_id": prompt_id,
            "ref_text": data.get("ref_text", ""),
            "name": data.get("name", ""),
            "x_vector_only_mode": data.get("x_vector_only_mode", False),
        })
    return {"prompts": prompts, "count": len(prompts)}


@app.get("/api/v1/base/prompts/{prompt_id}")
async def get_saved_prompt(prompt_id: str):
    """Get a specific saved voice clone prompt."""
    if prompt_id not in saved_voice_prompts:
        raise HTTPException(status_code=404, detail=f"Prompt ID not found: {prompt_id}")

    data = saved_voice_prompts[prompt_id]
    return {
        "prompt_id": prompt_id,
        "ref_text": data.get("ref_text", ""),
        "name": data.get("name", ""),
        "x_vector_only_mode": data.get("x_vector_only_mode", False),
        "has_audio": bool(data.get("ref_audio_base64")),
    }


@app.delete("/api/v1/base/prompts/{prompt_id}")
async def delete_saved_prompt(prompt_id: str):
    """Delete a saved voice clone prompt."""
    if prompt_id not in saved_voice_prompts:
        raise HTTPException(status_code=404, detail=f"Prompt ID not found: {prompt_id}")

    del saved_voice_prompts[prompt_id]

    # Delete from disk
    delete_voice_from_disk(prompt_id)

    return {"message": f"Prompt {prompt_id} deleted successfully"}


@app.get("/api/v1/base/cache/stats")
async def get_cache_stats():
    """Get voice prompt cache statistics (stub - caching not implemented in MLX version)."""
    return {
        "enabled": False,
        "message": "Voice prompt caching not implemented in MLX version"
    }


# ============= Save Voice & Transcribe Endpoints =============

class SaveGeneratedVoiceRequest(BaseModel):
    name: str
    ref_audio_base64: str
    ref_text: str


@app.post("/api/v1/base/save-voice")
async def save_generated_voice(request: SaveGeneratedVoiceRequest):
    """Save generated audio as a reusable voice clone prompt."""
    try:
        import uuid

        prompt_id = str(uuid.uuid4())
        prompt_data = {
            "ref_audio_base64": request.ref_audio_base64,
            "ref_text": request.ref_text,
            "name": request.name,
            "x_vector_only_mode": False,
        }
        saved_voice_prompts[prompt_id] = prompt_data
        save_voice_to_disk(prompt_id, prompt_data)

        logger.info(f"Saved generated voice '{request.name}' with ID: {prompt_id}")

        return CreatePromptResponse(
            prompt_id=prompt_id,
            message=f"Voice '{request.name}' saved successfully"
        )
    except Exception as e:
        logger.error(f"Error saving voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class TranscribeRequest(BaseModel):
    ref_audio_base64: str


@app.post("/api/v1/base/transcribe")
async def transcribe_reference_audio(request: TranscribeRequest):
    """Transcribe reference audio using Whisper (MLX)."""
    try:
        logger.info("Transcribing reference audio with mlx-whisper")

        import tempfile
        import mlx_whisper

        # Decode base64 audio to temp file
        audio_bytes = base64.b64decode(request.ref_audio_base64)
        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_file.write(audio_bytes)
        temp_file.close()

        try:
            result = mlx_whisper.transcribe(
                temp_file.name,
                path_or_hf_repo="mlx-community/whisper-tiny",
            )
            text = result.get("text", "").strip()
            logger.info(f"Transcription result: {text[:100]}...")
            return {"text": text}
        finally:
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)

    except Exception as e:
        logger.error(f"Error transcribing audio: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============= Health & Info Endpoints =============

@app.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "healthy", "backend": "mlx"}


@app.get("/health/models")
async def models_status():
    """Check which models are available."""
    status = {}
    for key, folder in MODEL_PATHS.items():
        path = get_model_path(folder)
        status[key] = "available" if path else "not_found"
    return {"models": status}


@app.get("/demo")
async def demo_page():
    """Serve the interactive demo page."""
    demo_file = STATIC_DIR / "index.html"
    if demo_file.exists():
        return FileResponse(demo_file, media_type="text/html")
    return {"error": "Demo page not found", "hint": "Static files may not be installed"}


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Qwen3-TTS MLX API Server",
        "version": "1.0.0",
        "backend": "Apple MLX",
        "demo": "/demo",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=7860,
        reload=False,
    )
