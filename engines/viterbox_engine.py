"""
Viterbox TTS Engine — Wrapper around Viterbox for TubeCLI.
Supports batch synthesis (text → WAV).
"""
import os
import sys
import time
import glob
import logging
import threading
from typing import Optional, Dict

logger = logging.getLogger("TTS.Viterbox")

# ── Globals (lazy-loaded) ──
_engine_instance: Optional["ViterboxTTSEngine"] = None
_engine_lock = threading.Lock()

def get_engine() -> "ViterboxTTSEngine":
    """Get or create the singleton engine instance."""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = ViterboxTTSEngine()
    return _engine_instance

class ViterboxTTSEngine:
    """
    Viterbox TTS Engine.
    Lazy-loads model on first synthesis request.
    """

    def __init__(self):
        self.model = None
        self.voice_presets: Dict[str, str] = {}
        self.device = "cpu"
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
            "voices_count": len(self.voice_presets),
        }

    def load_model(self):
        """Load Viterbox model. Thread-safe, idempotent."""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._loading = True
            self._load_error = None
            try:
                import torch
                
                # Auto-detect device
                if torch.cuda.is_available():
                    self.device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    self.device = "mps"
                else:
                    self.device = "cpu"

                logger.info(f"Using device for Viterbox: {self.device}")

                from viterbox.tts import Viterbox
                logger.info("Loading Viterbox model from pretrained...")
                self.model = Viterbox.from_pretrained(self.device)

                # Load voice presets
                self._scan_voice_presets()

                self._loaded = True
                logger.info(f"✅ Viterbox model loaded! Device={self.device}, Voices={len(self.voice_presets)}")

            except Exception as e:
                self._load_error = str(e)
                logger.error(f"❌ Failed to load Viterbox model: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self._loading = False

    def _scan_voice_presets(self):
        """Scan for .wav voice preset files from viterbox source."""
        search_dirs = [
            r"C:\tubecreate-vue\viterbox-tts-main\wavs",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "viterbox-tts-main", "wavs"),
        ]

        self.voice_presets = {}
        for d in search_dirs:
            abs_d = os.path.abspath(d)
            if os.path.isdir(abs_d):
                for wav_file in glob.glob(os.path.join(abs_d, "*.wav")):
                    name = os.path.splitext(os.path.basename(wav_file))[0]
                    if name not in self.voice_presets:
                        self.voice_presets[name] = os.path.abspath(wav_file)

        logger.info(f"Found {len(self.voice_presets)} Viterbox voice presets: {list(self.voice_presets.keys())}")

    def list_voices(self) -> list:
        """Return list of available voice presets with metadata."""
        if not self.voice_presets:
            self._scan_voice_presets()

        voices = []
        for name, path in self.voice_presets.items():
            # parse gender from name if present (e.g. clone_male_...)
            gender = "unknown"
            if "_male_" in name.lower():
                gender = "male"
            elif "_female_" in name.lower():
                gender = "female"
                
            display_name = f"Vietnamese Voice ({name[:6]})"
            if name.startswith("clone_"):
                parts = name.split('_')
                if len(parts) >= 3:
                     display_name = f"Clone: {parts[2]}"
                else:
                     display_name = f"Clone Voice ({name[:6]})"

            voices.append({
                "id": name,
                "name": display_name,
                "language": "vi",
                "language_name": "Tiếng Việt",
                "gender": gender,
                "path": path,
            })
        return voices

    def synthesize(
        self,
        text: str,
        voice: str = None,
        output_path: str = "output.wav",
        cfg_scale: float = 0.5,
    ) -> dict:
        """
        Batch TTS: Text → WAV file via Viterbox.
        Returns dict with status, output path, duration, etc.
        """
        if not self._loaded:
            self.load_model()
        if not self._loaded:
            return {"status": "error", "message": self._load_error or "Model not loaded"}

        try:
            text = text.strip()
            if not text:
                return {"status": "error", "message": "Empty text"}

            # Preprocess text to enforce better pausing for Viterbox clones
            # Viterbox fails to pause if sentences start with capital letters.
            text = text.lower()
            # User noted the native period pause is too long. Replace with comma for a shorter, natural beat.
            text = text.replace('. ', ', ').replace('.\n', ',\n')

            audio_prompt = self.voice_presets.get(voice)
            if not audio_prompt and self.voice_presets:
                audio_prompt = next(iter(self.voice_presets.values()))
                
            start_time = time.time()
            
            # Generate speech
            audio_tensor = self.model.generate(
                text=text,
                language="vi",
                audio_prompt=audio_prompt,
                exaggeration=0.5,
                cfg_weight=cfg_scale,
                temperature=0.8,
            )
            
            gen_time = time.time() - start_time

            # Save audio
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            self.model.save_audio(audio_tensor, output_path)

            audio_samples = audio_tensor.shape[-1]
            audio_duration = audio_samples / self.model.sr
            rtf = gen_time / audio_duration if audio_duration > 0 else 0

            return {
                "status": "success",
                "output": output_path,
                "duration": round(audio_duration, 2),
                "generation_time": round(gen_time, 2),
                "rtf": round(rtf, 3),
                "voice": voice or "random",
                "size": os.path.getsize(output_path),
            }

        except Exception as e:
            logger.error(f"Viterbox Synthesis error: {e}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

