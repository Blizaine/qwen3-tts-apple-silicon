"""
Qwen3-TTS MLX Web UI
A Gradio-based web interface for Qwen3-TTS running on Apple MLX
"""
import os
import sys
import shutil
import time
import gc
import re
import warnings
from datetime import datetime

# Suppress warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

import gradio as gr

try:
    from mlx_audio.tts.utils import load_model
    from mlx_audio.tts.generate import generate_audio
except ImportError:
    print("Error: 'mlx_audio' library not found.")
    print("Please run the install script first.")
    sys.exit(1)

# Configuration
BASE_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
VOICES_DIR = os.path.join(os.path.dirname(__file__), "voices")
SAMPLE_RATE = 24000

# Model Definitions
MODELS = {
    # Pro (1.7B) - Best Quality
    "pro_custom": {"name": "Custom Voice (Pro 1.7B)", "folder": "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit", "mode": "custom"},
    "pro_design": {"name": "Voice Design (Pro 1.7B)", "folder": "Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit", "mode": "design"},
    "pro_clone": {"name": "Voice Clone (Pro 1.7B)", "folder": "Qwen3-TTS-12Hz-1.7B-Base-8bit", "mode": "clone"},
    # Lite (0.6B) - Faster
    "lite_custom": {"name": "Custom Voice (Lite 0.6B)", "folder": "Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", "mode": "custom"},
    "lite_design": {"name": "Voice Design (Lite 0.6B)", "folder": "Qwen3-TTS-12Hz-0.6B-VoiceDesign-8bit", "mode": "design"},
    "lite_clone": {"name": "Voice Clone (Lite 0.6B)", "folder": "Qwen3-TTS-12Hz-0.6B-Base-8bit", "mode": "clone"},
}

# Speaker options
SPEAKERS = {
    "English": ["Ryan", "Aiden", "Ethan", "Chelsie", "Serena", "Vivian"],
    "Chinese": ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric"],
    "Japanese": ["Ono_Anna"],
    "Korean": ["Sohee"]
}
ALL_SPEAKERS = [name for names in SPEAKERS.values() for name in names]

# Emotion presets
EMOTION_PRESETS = [
    "Normal tone",
    "Sad and crying, speaking slowly",
    "Excited and happy, speaking very fast",
    "Angry and shouting",
    "Whispering quietly",
    "Calm and soothing narrator voice",
    "Professional news anchor",
    "Friendly and warm customer service"
]

# Speed options
SPEED_OPTIONS = {
    "Slow (0.8x)": 0.8,
    "Normal (1.0x)": 1.0,
    "Fast (1.3x)": 1.3
}

# Global model cache
loaded_models = {}


def get_model_path(folder_name):
    """Get the actual model path, handling HuggingFace cache structure."""
    full_path = os.path.join(MODELS_DIR, folder_name)
    if not os.path.exists(full_path):
        return None

    snapshots_dir = os.path.join(full_path, "snapshots")
    if os.path.exists(snapshots_dir):
        subfolders = [f for f in os.listdir(snapshots_dir) if not f.startswith('.')]
        if subfolders:
            return os.path.join(snapshots_dir, subfolders[0])

    return full_path


def load_cached_model(model_key):
    """Load model with caching to avoid reloading."""
    global loaded_models

    if model_key in loaded_models:
        return loaded_models[model_key]

    # Clear other models to save memory
    loaded_models.clear()
    gc.collect()

    model_info = MODELS[model_key]
    model_path = get_model_path(model_info["folder"])

    if not model_path:
        raise ValueError(f"Model not found: {model_info['folder']}. Please run the install script.")

    model = load_model(model_path)
    loaded_models[model_key] = model
    return model


def generate_custom_voice(text, model_size, speaker, emotion, speed_choice):
    """Generate audio using Custom Voice model (preset speakers)."""
    if not text.strip():
        return None, "Please enter some text."

    model_key = f"{model_size}_custom"
    speed = SPEED_OPTIONS.get(speed_choice, 1.0)

    try:
        model = load_cached_model(model_key)

        # Create temp output directory
        temp_dir = f"temp_{int(time.time())}"
        os.makedirs(temp_dir, exist_ok=True)

        generate_audio(
            model=model,
            text=text,
            voice=speaker,
            instruct=emotion,
            speed=speed,
            output_path=temp_dir
        )

        # Get the output file
        output_file = os.path.join(temp_dir, "audio_000.wav")
        if os.path.exists(output_file):
            # Save to outputs folder
            os.makedirs(os.path.join(BASE_OUTPUT_DIR, "CustomVoice"), exist_ok=True)
            timestamp = datetime.now().strftime("%H-%M-%S")
            clean_text = re.sub(r'[^\w\s-]', '', text)[:20].strip().replace(' ', '_') or "audio"
            final_path = os.path.join(BASE_OUTPUT_DIR, "CustomVoice", f"{timestamp}_{clean_text}.wav")
            shutil.move(output_file, final_path)
            shutil.rmtree(temp_dir, ignore_errors=True)
            return final_path, f"Generated with {speaker} ({emotion})"

        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, "Generation failed - no output file created."

    except Exception as e:
        return None, f"Error: {str(e)}"


def generate_voice_design(text, model_size, voice_description):
    """Generate audio using Voice Design model (natural language description)."""
    if not text.strip():
        return None, "Please enter some text."
    if not voice_description.strip():
        return None, "Please describe the voice you want."

    model_key = f"{model_size}_design"

    try:
        model = load_cached_model(model_key)

        temp_dir = f"temp_{int(time.time())}"
        os.makedirs(temp_dir, exist_ok=True)

        generate_audio(
            model=model,
            text=text,
            instruct=voice_description,
            output_path=temp_dir
        )

        output_file = os.path.join(temp_dir, "audio_000.wav")
        if os.path.exists(output_file):
            os.makedirs(os.path.join(BASE_OUTPUT_DIR, "VoiceDesign"), exist_ok=True)
            timestamp = datetime.now().strftime("%H-%M-%S")
            clean_text = re.sub(r'[^\w\s-]', '', text)[:20].strip().replace(' ', '_') or "audio"
            final_path = os.path.join(BASE_OUTPUT_DIR, "VoiceDesign", f"{timestamp}_{clean_text}.wav")
            shutil.move(output_file, final_path)
            shutil.rmtree(temp_dir, ignore_errors=True)
            return final_path, f"Generated with custom voice: {voice_description[:50]}..."

        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, "Generation failed - no output file created."

    except Exception as e:
        return None, f"Error: {str(e)}"


def generate_voice_clone(text, model_size, reference_audio, reference_text):
    """Generate audio using Voice Clone model (clone from reference)."""
    if not text.strip():
        return None, "Please enter some text."
    if reference_audio is None:
        return None, "Please upload a reference audio file."

    model_key = f"{model_size}_clone"
    ref_text = reference_text.strip() if reference_text else "."

    try:
        model = load_cached_model(model_key)

        temp_dir = f"temp_{int(time.time())}"
        os.makedirs(temp_dir, exist_ok=True)

        generate_audio(
            model=model,
            text=text,
            ref_audio=reference_audio,
            ref_text=ref_text,
            output_path=temp_dir
        )

        output_file = os.path.join(temp_dir, "audio_000.wav")
        if os.path.exists(output_file):
            os.makedirs(os.path.join(BASE_OUTPUT_DIR, "Clones"), exist_ok=True)
            timestamp = datetime.now().strftime("%H-%M-%S")
            clean_text = re.sub(r'[^\w\s-]', '', text)[:20].strip().replace(' ', '_') or "audio"
            final_path = os.path.join(BASE_OUTPUT_DIR, "Clones", f"{timestamp}_{clean_text}.wav")
            shutil.move(output_file, final_path)
            shutil.rmtree(temp_dir, ignore_errors=True)
            return final_path, "Generated with cloned voice"

        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, "Generation failed - no output file created."

    except Exception as e:
        return None, f"Error: {str(e)}"


def get_saved_voices():
    """Get list of saved voice profiles."""
    if not os.path.exists(VOICES_DIR):
        return []
    voices = [f.replace(".wav", "") for f in os.listdir(VOICES_DIR) if f.endswith(".wav")]
    return sorted(voices)


def load_saved_voice(voice_name):
    """Load a saved voice profile."""
    if not voice_name:
        return None, ""

    wav_path = os.path.join(VOICES_DIR, f"{voice_name}.wav")
    txt_path = os.path.join(VOICES_DIR, f"{voice_name}.txt")

    ref_text = ""
    if os.path.exists(txt_path):
        with open(txt_path, 'r', encoding='utf-8') as f:
            ref_text = f.read().strip()

    if os.path.exists(wav_path):
        return wav_path, ref_text
    return None, ""


def save_voice_profile(audio_file, voice_name, transcript):
    """Save a voice profile for later use."""
    if not audio_file or not voice_name.strip():
        return "Please provide both an audio file and a name."

    os.makedirs(VOICES_DIR, exist_ok=True)

    safe_name = re.sub(r'[^\w\s-]', '', voice_name).strip().replace(' ', '_')
    wav_path = os.path.join(VOICES_DIR, f"{safe_name}.wav")
    txt_path = os.path.join(VOICES_DIR, f"{safe_name}.txt")

    shutil.copy(audio_file, wav_path)
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(transcript or "")

    return f"Voice '{safe_name}' saved successfully!"


def check_models_status():
    """Check which models are downloaded."""
    status = []
    for key, info in MODELS.items():
        path = get_model_path(info["folder"])
        icon = "✅" if path else "❌"
        status.append(f"{icon} {info['name']}")
    return "\n".join(status)


# Create Gradio interface
def create_ui():
    with gr.Blocks(
        title="Qwen3-TTS MLX",
        theme=gr.themes.Soft(primary_hue="blue"),
        css="""
        .gradio-container { max-width: 1200px !important; }
        .tab-content { padding: 20px; }
        """
    ) as app:
        gr.Markdown("""
        # 🎙️ Qwen3-TTS MLX Web UI
        **High-quality text-to-speech optimized for Apple Silicon**

        Choose a mode below to generate speech. Pro models (1.7B) offer better quality, Lite models (0.6B) are faster.
        """)

        with gr.Tabs():
            # Tab 1: Custom Voice
            with gr.TabItem("🎤 Custom Voice", id="custom"):
                gr.Markdown("Use preset speakers with emotion and speed control.")
                with gr.Row():
                    with gr.Column(scale=2):
                        custom_text = gr.Textbox(
                            label="Text to speak",
                            placeholder="Enter the text you want to convert to speech...",
                            lines=4
                        )
                        with gr.Row():
                            custom_model = gr.Radio(
                                choices=["pro", "lite"],
                                value="lite",
                                label="Model Size",
                                info="Pro: Better quality | Lite: Faster"
                            )
                            custom_speaker = gr.Dropdown(
                                choices=ALL_SPEAKERS,
                                value="Vivian",
                                label="Speaker"
                            )
                        with gr.Row():
                            custom_emotion = gr.Dropdown(
                                choices=EMOTION_PRESETS,
                                value="Normal tone",
                                label="Emotion/Style",
                                allow_custom_value=True
                            )
                            custom_speed = gr.Dropdown(
                                choices=list(SPEED_OPTIONS.keys()),
                                value="Normal (1.0x)",
                                label="Speed"
                            )
                        custom_btn = gr.Button("🔊 Generate", variant="primary", size="lg")

                    with gr.Column(scale=1):
                        custom_audio = gr.Audio(label="Generated Audio", type="filepath")
                        custom_status = gr.Textbox(label="Status", interactive=False)

                custom_btn.click(
                    fn=generate_custom_voice,
                    inputs=[custom_text, custom_model, custom_speaker, custom_emotion, custom_speed],
                    outputs=[custom_audio, custom_status]
                )

            # Tab 2: Voice Design
            with gr.TabItem("✨ Voice Design", id="design"):
                gr.Markdown("Create custom voices using natural language descriptions.")
                with gr.Row():
                    with gr.Column(scale=2):
                        design_text = gr.Textbox(
                            label="Text to speak",
                            placeholder="Enter the text you want to convert to speech...",
                            lines=4
                        )
                        design_description = gr.Textbox(
                            label="Voice Description",
                            placeholder="Describe the voice you want, e.g., 'A warm, friendly female voice with a slight British accent, speaking calmly'",
                            lines=2
                        )
                        design_model = gr.Radio(
                            choices=["pro", "lite"],
                            value="lite",
                            label="Model Size",
                            info="Pro: Better quality | Lite: Faster"
                        )
                        design_btn = gr.Button("🔊 Generate", variant="primary", size="lg")

                    with gr.Column(scale=1):
                        design_audio = gr.Audio(label="Generated Audio", type="filepath")
                        design_status = gr.Textbox(label="Status", interactive=False)

                design_btn.click(
                    fn=generate_voice_design,
                    inputs=[design_text, design_model, design_description],
                    outputs=[design_audio, design_status]
                )

            # Tab 3: Voice Clone
            with gr.TabItem("🎭 Voice Clone", id="clone"):
                gr.Markdown("Clone a voice from a reference audio sample.")
                with gr.Row():
                    with gr.Column(scale=2):
                        clone_text = gr.Textbox(
                            label="Text to speak",
                            placeholder="Enter the text you want the cloned voice to say...",
                            lines=4
                        )
                        clone_audio_input = gr.Audio(
                            label="Reference Audio",
                            type="filepath",
                            sources=["upload", "microphone"]
                        )
                        clone_ref_text = gr.Textbox(
                            label="Reference Transcript (optional but recommended)",
                            placeholder="Type exactly what is said in the reference audio for better quality...",
                            lines=2
                        )
                        clone_model = gr.Radio(
                            choices=["pro", "lite"],
                            value="lite",
                            label="Model Size",
                            info="Pro: Better quality | Lite: Faster"
                        )
                        clone_btn = gr.Button("🔊 Generate", variant="primary", size="lg")

                    with gr.Column(scale=1):
                        clone_audio = gr.Audio(label="Generated Audio", type="filepath")
                        clone_status = gr.Textbox(label="Status", interactive=False)

                        gr.Markdown("### 💾 Save Voice Profile")
                        save_name = gr.Textbox(label="Voice Name", placeholder="e.g., MyVoice")
                        save_btn = gr.Button("Save for Later", size="sm")
                        save_status = gr.Textbox(label="", interactive=False)

                clone_btn.click(
                    fn=generate_voice_clone,
                    inputs=[clone_text, clone_model, clone_audio_input, clone_ref_text],
                    outputs=[clone_audio, clone_status]
                )

                save_btn.click(
                    fn=save_voice_profile,
                    inputs=[clone_audio_input, save_name, clone_ref_text],
                    outputs=[save_status]
                )

            # Tab 4: Saved Voices
            with gr.TabItem("📚 Saved Voices", id="saved"):
                gr.Markdown("Use previously saved voice profiles for cloning.")
                with gr.Row():
                    with gr.Column(scale=2):
                        saved_voices_dropdown = gr.Dropdown(
                            choices=get_saved_voices(),
                            label="Select Saved Voice",
                            interactive=True
                        )
                        refresh_btn = gr.Button("🔄 Refresh List", size="sm")

                        saved_text = gr.Textbox(
                            label="Text to speak",
                            placeholder="Enter the text you want the saved voice to say...",
                            lines=4
                        )
                        saved_ref_text = gr.Textbox(
                            label="Reference Transcript (loaded from saved profile)",
                            lines=2,
                            interactive=False
                        )
                        saved_model = gr.Radio(
                            choices=["pro", "lite"],
                            value="lite",
                            label="Model Size"
                        )
                        saved_btn = gr.Button("🔊 Generate", variant="primary", size="lg")

                    with gr.Column(scale=1):
                        saved_audio_preview = gr.Audio(label="Reference Audio Preview", type="filepath")
                        saved_audio_output = gr.Audio(label="Generated Audio", type="filepath")
                        saved_status = gr.Textbox(label="Status", interactive=False)

                def refresh_voices():
                    return gr.update(choices=get_saved_voices())

                refresh_btn.click(fn=refresh_voices, outputs=[saved_voices_dropdown])

                saved_voices_dropdown.change(
                    fn=load_saved_voice,
                    inputs=[saved_voices_dropdown],
                    outputs=[saved_audio_preview, saved_ref_text]
                )

                saved_btn.click(
                    fn=generate_voice_clone,
                    inputs=[saved_text, saved_model, saved_audio_preview, saved_ref_text],
                    outputs=[saved_audio_output, saved_status]
                )

            # Tab 5: Settings
            with gr.TabItem("⚙️ Settings", id="settings"):
                gr.Markdown("### Model Status")
                model_status = gr.Textbox(
                    value=check_models_status(),
                    label="Downloaded Models",
                    lines=8,
                    interactive=False
                )
                refresh_models_btn = gr.Button("🔄 Refresh Status")
                refresh_models_btn.click(fn=check_models_status, outputs=[model_status])

                gr.Markdown("""
                ### About
                This web UI uses **MLX** (Apple's machine learning framework) for fast inference on Apple Silicon Macs.

                **Memory Usage:**
                - Lite models (0.6B): ~2-3 GB RAM
                - Pro models (1.7B): ~5-6 GB RAM

                **Output Location:**
                All generated audio files are saved to the `outputs/` folder.

                **Tips:**
                - Use Lite models for quick tests, Pro models for final output
                - For voice cloning, provide a clear reference audio (3-10 seconds works best)
                - Adding a transcript for the reference audio significantly improves clone quality
                """)

        gr.Markdown("""
        ---
        *Powered by [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) + [MLX Audio](https://github.com/Blaizzy/mlx-audio) | Optimized for Apple Silicon*
        """)

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=7860, help="Port to run on")
    parser.add_argument("--share", action="store_true", help="Create public link")
    args = parser.parse_args()

    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
    os.makedirs(VOICES_DIR, exist_ok=True)

    app = create_ui()
    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share
    )
