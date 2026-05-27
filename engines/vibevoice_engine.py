"""
VibeVoice TTS Engine — Wrapper around VibeVoice-Realtime-0.5B for TubeCLI.
Supports batch synthesis (text → WAV) and streaming (WebSocket PCM chunks).
"""
import os
import sys
import copy
import glob
import time
import logging
import threading
from typing import Optional, Dict, Iterator, Tuple, Any

import numpy as np

logger = logging.getLogger("TTS.VibeVoice")

# ── Globals (lazy-loaded) ──
_engine_instance: Optional["VibeVoiceTTSEngine"] = None
_engine_lock = threading.Lock()

SAMPLE_RATE = 24_000
DEFAULT_MODEL = "microsoft/VibeVoice-Realtime-0.5B"


def get_engine() -> "VibeVoiceTTSEngine":
    """Get or create the singleton engine instance."""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = VibeVoiceTTSEngine()
    return _engine_instance


class VibeVoiceTTSEngine:
    """
    VibeVoice-Realtime-0.5B TTS Engine.
    Lazy-loads model on first synthesis request.
    """

    def __init__(self, model_path: str = DEFAULT_MODEL):
        self.model_path = model_path
        self.sample_rate = SAMPLE_RATE
        self.model = None
        self.processor = None
        self.voice_presets: Dict[str, str] = {}
        self._voice_cache: Dict[str, Any] = {}
        self.device = "cpu"
        self._torch_device = None
        self._loaded = False
        self._loading = False
        self._load_error: Optional[str] = None
        self._load_lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def get_status(self) -> dict:
        return {
            "loaded": self._loaded,
            "loading": self._loading,
            "error": self._load_error,
            "device": self.device,
            "model": self.model_path,
            "voices_count": len(self.voice_presets),
        }

    def _ensure_vibevoice_importable(self):
        """Ensure vibevoice package is importable, try local source first."""
        try:
            import vibevoice
            return True
        except ImportError:
            pass

        # Try local VibeVoice source paths
        local_paths = [
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "VibeVoice-main"),
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "VibeVoice"),
            r"C:\tubecreate-vue\VibeVoice-main",
        ]
        for p in local_paths:
            abs_p = os.path.abspath(p)
            if os.path.isdir(abs_p) and os.path.isfile(os.path.join(abs_p, "pyproject.toml")):
                if abs_p not in sys.path:
                    sys.path.insert(0, abs_p)
                    logger.info(f"Added VibeVoice source to sys.path: {abs_p}")
                try:
                    import vibevoice
                    return True
                except ImportError:
                    pass

        # Last resort: pip install
        logger.warning("vibevoice not found, attempting pip install from GitHub...")
        import subprocess
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install",
                "git+https://github.com/microsoft/VibeVoice.git"
            ])
            import vibevoice
            return True
        except Exception as e:
            logger.error(f"Failed to install vibevoice: {e}")
            return False

    def load_model(self):
        """Load VibeVoice model and processor. Thread-safe, idempotent."""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._loading = True
            self._load_error = None
            try:
                import torch
                self._ensure_vibevoice_importable()

                from vibevoice.modular.modeling_vibevoice_streaming_inference import (
                    VibeVoiceStreamingForConditionalGenerationInference,
                )
                from vibevoice.processor.vibevoice_streaming_processor import (
                    VibeVoiceStreamingProcessor,
                )

                # Auto-detect device
                if torch.cuda.is_available():
                    self.device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    self.device = "mps"
                else:
                    self.device = "cpu"

                self._torch_device = torch.device(self.device)
                logger.info(f"Using device: {self.device}")

                # Load processor
                logger.info(f"Loading processor from {self.model_path}...")
                self.processor = VibeVoiceStreamingProcessor.from_pretrained(self.model_path)

                # Device-specific dtype & attention
                if self.device == "cuda":
                    load_dtype = torch.bfloat16
                    device_map = "cuda"
                    attn_impl = "flash_attention_2"
                elif self.device == "mps":
                    load_dtype = torch.float32
                    device_map = None
                    attn_impl = "sdpa"
                else:
                    load_dtype = torch.float32
                    device_map = "cpu"
                    attn_impl = "sdpa"

                logger.info(f"Loading model: dtype={load_dtype}, attn={attn_impl}")

                try:
                    self.model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                        self.model_path,
                        torch_dtype=load_dtype,
                        device_map=device_map,
                        attn_implementation=attn_impl,
                    )
                    if self.device == "mps":
                        self.model.to("mps")
                except Exception as e:
                    if attn_impl == "flash_attention_2":
                        logger.warning(f"flash_attention_2 failed ({e}), falling back to SDPA")
                        self.model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                            self.model_path,
                            torch_dtype=load_dtype,
                            device_map=device_map if device_map != "cuda" else self.device,
                            attn_implementation="sdpa",
                        )
                    else:
                        raise

                self.model.eval()
                self.model.set_ddpm_inference_steps(num_steps=5)

                # Load voice presets
                self._scan_voice_presets()

                self._loaded = True
                logger.info(f"✅ VibeVoice model loaded! Device={self.device}, Voices={len(self.voice_presets)}")

            except Exception as e:
                self._load_error = str(e)
                logger.error(f"❌ Failed to load VibeVoice model: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self._loading = False

    def _scan_voice_presets(self):
        """Scan for .pt voice preset files from VibeVoice source."""
        search_dirs = [
            os.path.join(os.path.dirname(__file__), "voices"),
            r"C:\tubecreate-vue\VibeVoice-main\demo\voices\streaming_model",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "VibeVoice-main", "demo", "voices", "streaming_model"),
        ]

        self.voice_presets = {}
        for d in search_dirs:
            abs_d = os.path.abspath(d)
            if os.path.isdir(abs_d):
                for pt_file in glob.glob(os.path.join(abs_d, "**", "*.pt"), recursive=True):
                    name = os.path.splitext(os.path.basename(pt_file))[0]
                    if name not in self.voice_presets:
                        self.voice_presets[name] = os.path.abspath(pt_file)

        self.voice_presets = dict(sorted(self.voice_presets.items()))
        logger.info(f"Found {len(self.voice_presets)} voice presets: {list(self.voice_presets.keys())}")

    def list_voices(self) -> list:
        """Return list of available voice presets with metadata."""
        if not self.voice_presets:
            self._scan_voice_presets()

        voices = []
        for name, path in self.voice_presets.items():
            # Parse language and gender from name like "en-Carter_man"
            parts = name.split("-", 1)
            lang = parts[0] if len(parts) > 1 else "en"
            speaker = parts[1] if len(parts) > 1 else name
            gender = "male" if "_man" in name else ("female" if "_woman" in name else "unknown")

            lang_names = {
                "en": "English", "de": "German", "fr": "French",
                "it": "Italian", "jp": "Japanese", "kr": "Korean",
                "nl": "Dutch", "pl": "Polish", "pt": "Portuguese",
                "sp": "Spanish", "in": "Indian English",
            }

            voices.append({
                "id": name,
                "name": speaker.replace("_", " ").title(),
                "language": lang,
                "language_name": lang_names.get(lang, lang),
                "gender": gender,
                "path": path,
            })
        return voices

    def _get_voice_preset(self, voice_id: str) -> Any:
        """Load and cache a voice preset."""
        import torch

        if voice_id not in self.voice_presets:
            # Fallback to default
            voice_id = "en-Carter_man" if "en-Carter_man" in self.voice_presets else next(iter(self.voice_presets))
            logger.warning(f"Voice not found, using default: {voice_id}")

        if voice_id not in self._voice_cache:
            path = self.voice_presets[voice_id]
            logger.info(f"Loading voice preset: {voice_id} from {path}")
            self._voice_cache[voice_id] = torch.load(
                path,
                map_location=self._torch_device,
                weights_only=False,
            )

        return voice_id, self._voice_cache[voice_id]

    def synthesize(
        self,
        text: str,
        voice: str = "en-Carter_man",
        output_path: str = "output.wav",
        cfg_scale: float = 1.5,
    ) -> dict:
        """
        Batch TTS: Text → WAV file.
        Returns dict with status, output path, duration, etc.
        """
        if not self._loaded:
            self.load_model()
        if not self._loaded:
            return {"status": "error", "message": self._load_error or "Model not loaded"}

        import torch

        try:
            text = text.strip().replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
            if not text:
                return {"status": "error", "message": "Empty text"}

            voice_id, prefilled_outputs = self._get_voice_preset(voice)

            # Prepare inputs
            inputs = self.processor.process_input_with_cached_prompt(
                text=text,
                cached_prompt=prefilled_outputs,
                padding=True,
                return_tensors="pt",
                return_attention_mask=True,
            )
            for k, v in inputs.items():
                if torch.is_tensor(v):
                    inputs[k] = v.to(self._torch_device)

            # Generate
            start_time = time.time()
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=None,
                cfg_scale=cfg_scale,
                tokenizer=self.processor.tokenizer,
                generation_config={"do_sample": False},
                verbose=True,
                all_prefilled_outputs=copy.deepcopy(prefilled_outputs),
            )
            gen_time = time.time() - start_time

            # Save audio
            if outputs.speech_outputs and outputs.speech_outputs[0] is not None:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                self.processor.save_audio(outputs.speech_outputs[0], output_path=output_path)

                audio_samples = outputs.speech_outputs[0].shape[-1]
                audio_duration = audio_samples / self.sample_rate
                rtf = gen_time / audio_duration if audio_duration > 0 else 0

                return {
                    "status": "success",
                    "output": output_path,
                    "duration": round(audio_duration, 2),
                    "generation_time": round(gen_time, 2),
                    "rtf": round(rtf, 3),
                    "voice": voice_id,
                    "size": os.path.getsize(output_path),
                }
            else:
                return {"status": "error", "message": "No audio output generated"}

        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def stream(
        self,
        text: str,
        voice: str = "en-Carter_man",
        cfg_scale: float = 1.5,
        stop_event: Optional[threading.Event] = None,
    ) -> Iterator[np.ndarray]:
        """
        Streaming TTS: yields PCM float32 chunks as numpy arrays.
        """
        if not self._loaded:
            self.load_model()
        if not self._loaded:
            raise RuntimeError(self._load_error or "Model not loaded")

        import torch
        from vibevoice.modular.streamer import AudioStreamer

        text = text.strip().replace("\u2019", "'")
        if not text:
            return

        voice_id, prefilled_outputs = self._get_voice_preset(voice)

        inputs = self.processor.process_input_with_cached_prompt(
            text=text,
            cached_prompt=prefilled_outputs,
            padding=True,
            return_tensors="pt",
            return_attention_mask=True,
        )
        for k, v in inputs.items():
            if torch.is_tensor(v):
                inputs[k] = v.to(self._torch_device)

        audio_streamer = AudioStreamer(batch_size=1, stop_signal=None, timeout=None)
        errors = []
        stop_signal = stop_event or threading.Event()

        def _run_generation():
            try:
                self.model.generate(
                    **inputs,
                    max_new_tokens=None,
                    cfg_scale=cfg_scale,
                    tokenizer=self.processor.tokenizer,
                    generation_config={"do_sample": False},
                    audio_streamer=audio_streamer,
                    stop_check_fn=stop_signal.is_set,
                    verbose=False,
                    all_prefilled_outputs=copy.deepcopy(prefilled_outputs),
                )
            except Exception as exc:
                errors.append(exc)
                import traceback
                traceback.print_exc()
                audio_streamer.end()

        thread = threading.Thread(target=_run_generation, daemon=True)
        thread.start()

        try:
            stream = audio_streamer.get_stream(0)
            for audio_chunk in stream:
                if torch.is_tensor(audio_chunk):
                    audio_chunk = audio_chunk.detach().cpu().to(torch.float32).numpy()
                else:
                    audio_chunk = np.asarray(audio_chunk, dtype=np.float32)

                if audio_chunk.ndim > 1:
                    audio_chunk = audio_chunk.reshape(-1)

                peak = np.max(np.abs(audio_chunk)) if audio_chunk.size else 0.0
                if peak > 1.0:
                    audio_chunk = audio_chunk / peak

                yield audio_chunk.astype(np.float32, copy=False)
        finally:
            stop_signal.set()
            audio_streamer.end()
            thread.join(timeout=10)
            if errors:
                raise errors[0]

    @staticmethod
    def chunk_to_pcm16(chunk: np.ndarray) -> bytes:
        """Convert float32 numpy chunk to PCM16 bytes."""
        chunk = np.clip(chunk, -1.0, 1.0)
        pcm = (chunk * 32767.0).astype(np.int16)
        return pcm.tobytes()
