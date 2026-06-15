"""
TTS VibeVoice Extension — AI Text-to-Speech for TubeCLI.
Uses VibeVoice-Realtime-0.5B (Microsoft) for high-quality speech synthesis.
"""
import os
import sys
import logging
from tubecli.core.extension_manager import Extension

logger = logging.getLogger("TTSVibeVoice")


class TTSVibeVoiceExtension(Extension):
    name = "tts_vibevoice"
    version = "2026.05.28.162800"
    description = "AI Text-to-Speech — VibeVoice Realtime, 25 voices, streaming"
    author = "TubeCreate"
    extension_type = "external"

    def on_enable(self):
        """Enable extension — install ONLY core (lightweight) deps."""
        logger.info("TTS VibeVoice extension enabled")
        self._ensure_edge_tts()
        # NOTE: Heavy provider deps (torch, vibevoice, viterbox) are installed
        # on-demand when user first uses that engine, NOT at startup.
        self._register_skill()

    def _ensure_edge_tts(self):
        """Ensure edge-tts is installed (lightweight, <100KB)."""
        try:
            import edge_tts
        except ImportError:
            logger.info("Installing edge-tts...")
            import subprocess
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "edge-tts", "-q"])
                logger.info("edge-tts installed successfully.")
            except Exception as e:
                logger.warning(f"Failed to install edge-tts: {e}")

    # ── On-Demand Provider Installation ──────────────────────────────

    def ensure_provider_deps(self, engine: str) -> dict:
        """Install dependencies for a specific engine on-demand.
        Returns {"success": bool, "message": str, "already_installed": bool}
        """
        if engine in ("edge", "everai", "gemini", "omnivoice"):
            return {"success": True, "message": "No heavy dependencies needed.", "already_installed": True}
        elif engine == "vibevoice":
            return self._ensure_vibevoice_deps()
        elif engine == "viterbox":
            return self._ensure_viterbox_deps()
        else:
            return {"success": False, "message": f"Unknown engine: {engine}", "already_installed": False}

    def check_provider_available(self, engine: str) -> dict:
        """Check if an engine's dependencies are already available (no install).
        Returns {"available": bool, "engine": str, "details": str}
        """
        if engine == "edge":
            try:
                import edge_tts
                return {"available": True, "engine": "edge", "details": "edge-tts ready"}
            except ImportError:
                return {"available": False, "engine": "edge", "details": "edge-tts not installed"}

        elif engine == "everai":
            try:
                import httpx
                return {"available": True, "engine": "everai", "details": "httpx ready (cloud API)"}
            except ImportError:
                return {"available": False, "engine": "everai", "details": "httpx not installed"}

        elif engine == "gemini":
            return {"available": True, "engine": "gemini", "details": "Browser automation (Node.js)"}

        elif engine == "omnivoice":
            try:
                import httpx
                resp = httpx.get("http://localhost:3900/profiles", timeout=1.0)
                if resp.status_code == 200:
                    return {"available": True, "engine": "omnivoice", "details": "OmniVoice Studio is running on port 3900"}
                return {"available": False, "engine": "omnivoice", "details": f"OmniVoice Studio offline (status: {resp.status_code})"}
            except Exception:
                return {"available": False, "engine": "omnivoice", "details": "OmniVoice Studio offline or port 3900 unreachable"}

        elif engine == "vibevoice":
            try:
                import torch
                import transformers
                try:
                    import vibevoice
                    return {"available": True, "engine": "vibevoice", "details": f"torch + vibevoice ready"}
                except ImportError:
                    # Check local VibeVoice source
                    for p in self._get_vibevoice_paths():
                        if os.path.isdir(p):
                            return {"available": True, "engine": "vibevoice", "details": "torch ready, vibevoice from local source"}
                    return {"available": False, "engine": "vibevoice", "details": "torch ready but vibevoice package missing"}
            except ImportError:
                return {"available": False, "engine": "vibevoice", "details": "torch/transformers not installed (~3GB)"}

        elif engine == "viterbox":
            try:
                import torch
                try:
                    from viterbox.tts import Viterbox
                    return {"available": True, "engine": "viterbox", "details": "viterbox ready"}
                except ImportError:
                    # Check local viterbox source
                    for p in self._get_viterbox_paths():
                        if os.path.isdir(p):
                            return {"available": True, "engine": "viterbox", "details": "torch ready, viterbox from local source"}
                    return {"available": False, "engine": "viterbox", "details": "torch ready but viterbox package missing"}
            except ImportError:
                return {"available": False, "engine": "viterbox", "details": "torch not installed (~2GB)"}

        return {"available": False, "engine": engine, "details": "Unknown engine"}

    def _get_vibevoice_paths(self):
        return [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "VibeVoice-main")),
            r"C:\tubecreate-vue\VibeVoice-main",
        ]

    def _get_viterbox_paths(self):
        return [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "viterbox-tts-main")),
            r"C:\tubecreate-vue\viterbox-tts-main",
        ]

    def _ensure_vibevoice_deps(self):
        """Install heavy VibeVoice deps on-demand (torch ~2GB, transformers ~400MB).
        Only called when user explicitly uses VibeVoice engine."""
        already_available = True
        try:
            import torch
            import transformers
        except ImportError:
            already_available = False
            logger.info("Installing VibeVoice dependencies (torch, transformers)...")
            import subprocess
            req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements_vibevoice.txt")
            if os.path.exists(req_file):
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
                    logger.info("Successfully installed VibeVoice requirements.")
                except Exception as e:
                    logger.error(f"Failed to install requirements: {e}")
                    return {"success": False, "message": f"Failed to install VibeVoice deps: {e}", "already_installed": False}
            else:
                return {"success": False, "message": "requirements_vibevoice.txt not found", "already_installed": False}

        # Ensure vibevoice is importable
        try:
            import vibevoice
        except ImportError:
            for p in self._get_vibevoice_paths():
                if os.path.isdir(p) and p not in sys.path:
                    sys.path.insert(0, p)
                    logger.info(f"Added VibeVoice source: {p}")
                    break

        return {"success": True, "message": "VibeVoice dependencies ready.", "already_installed": already_available}

    def _ensure_viterbox_deps(self):
        """Install Viterbox package and its dependencies on-demand."""
        already_available = True
        try:
            import einops
            import soe_vinorm
        except ImportError:
            already_available = False
            logger.info("Installing Viterbox dependencies...")
            import subprocess
            req_file_1 = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "viterbox-tts-main", "requirements.txt")
            req_file_2 = r"C:\tubecreate-vue\viterbox-tts-main\requirements.txt"

            req_file = req_file_2 if os.path.exists(req_file_2) else (req_file_1 if os.path.exists(req_file_1) else None)

            if req_file:
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file, "-q"])
                    logger.info("Successfully installed Viterbox requirements.")
                except Exception as e:
                    logger.error(f"Failed to install Viterbox requirements: {e}")
                    return {"success": False, "message": f"Failed to install Viterbox deps: {e}", "already_installed": False}
            else:
                return {"success": False, "message": "Viterbox requirements.txt not found", "already_installed": False}

        # Ensure viterbox is importable
        for p in self._get_viterbox_paths():
            if os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)
                logger.info(f"Added Viterbox source: {p}")
                break

        return {"success": True, "message": "Viterbox dependencies ready.", "already_installed": already_available}

    def _register_skill(self):
        """Register TTS skill for chatbot routing."""
        try:
            from tubecli.core.skill import skill_manager
            from tubecli.config import get_language

            existing = skill_manager.find_by_name("Text-to-Speech (TTS)")
            lang = get_language()

            if lang == "vi":
                desc = (
                    "Chuyển văn bản thành giọng nói tự nhiên bằng VibeVoice AI và Viterbox (Tiếng Việt). "
                    "Hỗ trợ 25+ giọng nói (Vietnamese, English, German, French, etc.), "
                    "streaming realtime, tạo audio WAV. "
                    "Có thể dùng để tạo voiceover, narrate script, đọc text."
                )
                cmds = [
                    "đọc text", "đọc văn bản", "text to speech", "tts",
                    "tạo giọng nói", "tạo audio", "generate voice", "generate speech",
                    "chuyển text thành giọng", "convert to speech",
                    "narrate", "voiceover", "lồng tiếng", "đọc bài",
                ]
                sop = (
                    "1. Nhận text cần chuyển thành giọng nói\n"
                    "2. Chọn voice preset (mặc định: en-Carter_man)\n"
                    "3. Gọi API POST /api/v1/tts/synthesize\n"
                    "   Body: {\"text\": \"...\", \"voice\": \"en-Carter_man\", \"engine\": \"vibevoice\"} (engine có thể là edge, vibevoice, viterbox)\n"
                    "4. Poll status tại GET /api/v1/tts/status/{task_id}\n"
                    "5. Khi hoàn thành, trả về file WAV"
                )
            else:
                desc = (
                    "Convert text to natural speech using VibeVoice AI and Viterbox (Vietnamese). "
                    "Supports 25+ voices (Vietnamese, English, German, French, etc.), "
                    "real-time streaming, WAV audio creation. "
                    "Can be used for voiceover, narration, reading text."
                )
                cmds = [
                    "read text", "text to speech", "tts",
                    "generate voice", "generate audio", "generate speech",
                    "convert to speech", "narrate", "voiceover",
                ]
                sop = (
                    "1. Get the text to convert to speech\n"
                    "2. Choose a voice preset (default: en-Carter_man)\n"
                    "3. Call API POST /api/v1/tts/synthesize\n"
                    "   Body: {\"text\": \"...\", \"voice\": \"en-Carter_man\", \"engine\": \"vibevoice\"} (engine can be edge, vibevoice, viterbox)\n"
                    "4. Poll status at GET /api/v1/tts/status/{task_id}\n"
                    "5. On completion, return the WAV file"
                )

            if not existing:
                skill_manager.create(
                    name="Text-to-Speech (TTS)",
                    description=desc,
                    skill_type="Extension Skill",
                    commands=cmds,
                    workflow_data={
                        "extension": "tts_vibevoice",
                        "action": "generate_tts",
                        "sop": sop,
                    },
                )
                logger.info("✅ TTS skill registered successfully.")
            else:
                skill_manager.update(
                    existing.id,
                    description=desc,
                    commands=cmds,
                    workflow_data={
                        "extension": "tts_vibevoice",
                        "action": "generate_tts",
                        "sop": sop,
                    },
                )
                logger.info("⚡ TTS skill updated/synced.")
        except Exception as e:
            logger.warning(f"Could not register TTS skill: {e}")

    def get_routes(self):
        """Load and return FastAPI router."""
        try:
            import importlib.util
            routes_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_routes.py")
            spec = importlib.util.spec_from_file_location("tts_ext_routes", routes_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            router = getattr(mod, "router", None)
            logger.info(f"TTS VibeVoice: loaded router, {len(router.routes) if router else 0} routes")
            return router
        except Exception as e:
            logger.error(f"Failed to load TTS routes: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_nodes(self):
        """Load pipeline nodes."""
        try:
            import importlib.util
            nodes_init = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nodes", "__init__.py")
            if os.path.exists(nodes_init):
                spec = importlib.util.spec_from_file_location("tts_nodes", nodes_init)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return getattr(mod, "ALL_NODES", {})
        except Exception as e:
            logger.warning(f"Could not load TTS nodes: {e}")
        return {}

    def get_telegram_actions(self):
        return {
            "generate_tts": self._action_generate_tts,
        }

    async def _action_generate_tts(self, action_data: dict, context: dict) -> str:
        """Telegram action: generate speech from text."""
        text = action_data.get("text", "")
        voice = action_data.get("voice", "en-Carter_man")
        engine_type = action_data.get("engine", "vibevoice")
        file_path = action_data.get("file_path", "")

        # If file_path given, read text from file
        if file_path and os.path.isfile(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read().strip()
            except Exception as e:
                return f"❌ Không thể đọc file: {e}"

        if not text:
            return "❌ Thiếu text. Hãy cung cấp văn bản cần chuyển thành giọng nói."

        # Ensure provider deps are installed before using
        dep_result = self.ensure_provider_deps(engine_type)
        if not dep_result["success"]:
            return f"❌ {dep_result['message']}"

        try:
            import importlib.util
            if engine_type == "viterbox":
                engine_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines", "viterbox_engine.py")
            else:
                engine_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines", "vibevoice_engine.py")
                
            spec = importlib.util.spec_from_file_location("tts_action_engine", engine_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            engine = mod.get_engine()

            # Generate output path
            from tubecli.config import DATA_DIR
            import uuid
            out_dir = os.path.join(str(DATA_DIR), "tts_vibevoice", "outputs")
            os.makedirs(out_dir, exist_ok=True)
            output_path = os.path.join(out_dir, f"tts_{uuid.uuid4().hex[:8]}.wav")

            result = engine.synthesize(text=text, voice=voice, output_path=output_path)

            if result.get("status") == "success":
                duration = result.get("duration", 0)
                gen_time = result.get("generation_time", 0)
                rtf = result.get("rtf", 0)
                size_mb = result.get("size", 0) / (1024 * 1024)

                return (
                    f"✅ **TTS thành công!**\n\n"
                    f"🎙️ Voice: {result.get('voice', voice)}\n"
                    f"⏱️ Duration: {duration:.1f}s\n"
                    f"⚡ Gen time: {gen_time:.1f}s (RTF: {rtf:.2f}x)\n"
                    f"📁 File: `{os.path.basename(output_path)}` ({size_mb:.1f}MB)\n"
                    f"📝 Text: {text[:100]}{'...' if len(text) > 100 else ''}"
                )
            else:
                return f"❌ TTS thất bại: {result.get('message', 'Unknown error')}"

        except Exception as e:
            logger.error(f"TTS action error: {e}")
            return f"❌ Lỗi TTS: {str(e)[:200]}"
