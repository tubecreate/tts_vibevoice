"""
TTS API Routes — FastAPI router for text-to-speech operations.
Supports: VibeVoice (offline AI) + Edge-TTS (online, Vietnamese support).
"""
import os
import json
import asyncio
import logging
import threading
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, File, UploadFile, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = logging.getLogger("TTS.API")

# ── Extension directory (auto-detected) ──
_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_EXT_DIR, "static")

# ── Single Router (serves both pages and API) ──
router = APIRouter(tags=["TTS VibeVoice"])

# ── Page Routes (no prefix) ──

@router.get("/tts-vibevoice", response_class=HTMLResponse)
async def tts_vibevoice_page():
    """Serve the TTS VibeVoice HTML page."""
    html_path = os.path.join(_STATIC_DIR, "tts.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(404, "TTS page not found")


@router.get("/tts-vibevoice-static/{filename:path}")
async def tts_vibevoice_static(filename: str):
    """Serve static files (CSS, JS, i18n) for the TTS UI."""
    filepath = os.path.join(_STATIC_DIR, filename)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return FileResponse(filepath)
    raise HTTPException(404, f"Static file not found: {filename}")


# ── In-memory task tracking ──
_tasks = {}


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = "vi-VN-HoaiMyNeural"
    engine: str = "edge"  # "edge" or "vibevoice"
    cfg_scale: float = 2.0
    output_path: Optional[str] = None
    browser_profile: Optional[str] = None


# ── Helper: get output dir ──

def _get_output_dir():
    try:
        from tubecli.config import DATA_DIR
        out_dir = os.path.join(str(DATA_DIR), "tts_vibevoice", "outputs")
    except Exception:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


# ── Helper: load VibeVoice engine ──

def _get_vibevoice_engine():
    import importlib.util
    engine_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines", "vibevoice_engine.py")
    spec = importlib.util.spec_from_file_location("vv_engine", engine_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_engine()

def _get_viterbox_engine():
    import importlib.util
    engine_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines", "viterbox_engine.py")
    spec = importlib.util.spec_from_file_location("vb_engine", engine_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_engine()

def _get_everai_engine():
    import importlib.util
    engine_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines", "everai_engine.py")
    spec = importlib.util.spec_from_file_location("everai_engine", engine_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_engine()

def _get_omnivoice_engine():
    import importlib.util
    engine_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines", "omnivoice_engine.py")
    spec = importlib.util.spec_from_file_location("omnivoice_engine", engine_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_engine()

# ═══════════════════════════════════════════════════
# ── Engine Status & On-Demand Install
# ═══════════════════════════════════════════════════

_tts_ext_module = None

def _get_tts_extension():
    """Load TTSVibeVoiceExtension from the local extension.py using importlib (cached)."""
    global _tts_ext_module
    if _tts_ext_module is None:
        import importlib.util
        ext_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extension.py")
        spec = importlib.util.spec_from_file_location("tts_vibevoice_ext", ext_file)
        _tts_ext_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tts_ext_module)
    return _tts_ext_module.TTSVibeVoiceExtension()

@router.get("/api/v1/tts/engine-status")
async def engine_status():
    """Check which TTS engines have their dependencies installed."""
    try:
        ext = _get_tts_extension()
    except Exception:
        ext = None

    engines = {}
    for eng_name in ["edge", "everai", "gemini", "vibevoice", "viterbox", "omnivoice"]:
        if ext:
            info = ext.check_provider_available(eng_name)
            engines[eng_name] = info
        else:
            engines[eng_name] = {"available": eng_name in ("edge", "everai", "gemini", "omnivoice"), "engine": eng_name, "details": ""}

    return {"success": True, "engines": engines}


@router.post("/api/v1/tts/install-engine")
async def install_engine_deps(body: dict, background_tasks: BackgroundTasks):
    """Install dependencies for a specific engine on-demand.
    Called by frontend before first use of a heavy engine."""
    engine_name = body.get("engine", "")
    if not engine_name:
        raise HTTPException(400, "Missing 'engine' parameter")

    # Quick check if already available
    try:
        ext = _get_tts_extension()
        check = ext.check_provider_available(engine_name)
        if check.get("available"):
            return {"success": True, "message": f"{engine_name} is already installed.", "already_installed": True}
    except Exception:
        pass

    # Install in background
    install_id = f"install_{engine_name}"
    _tasks[install_id] = {"status": "installing", "progress": 0, "result": None}

    def _do_install():
        try:
            ext = _get_tts_extension()
            result = ext.ensure_provider_deps(engine_name)
            _tasks[install_id]["status"] = "success" if result["success"] else "error"
            _tasks[install_id]["progress"] = 100
            _tasks[install_id]["result"] = result
        except Exception as e:
            _tasks[install_id]["status"] = "error"
            _tasks[install_id]["result"] = {"success": False, "message": str(e)}

    background_tasks.add_task(_do_install)
    return {"success": True, "message": f"Installing {engine_name} dependencies...", "task_id": install_id, "already_installed": False}


# ═══════════════════════════════════════════════════
# ── Model Status (MUST be before /status/{task_id})
# ═══════════════════════════════════════════════════

@router.get("/api/v1/tts/status/model")
async def get_model_status():
    """Get VibeVoice model loading status."""
    try:
        engine = _get_vibevoice_engine()
        return {"success": True, **engine.get_status()}
    except Exception as e:
        return {
            "success": True,
            "loaded": False,
            "loading": False,
            "error": str(e),
            "device": None,
        }


# ── Task Status ──

@router.get("/api/v1/tts/status/{task_id}")
async def get_task_status(task_id: str):
    """Get TTS task status and progress."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return {"success": True, "task_id": task_id, **task}


# ═══════════════════════════════════════════════════
# ── Edge-TTS: Synthesize ──
# ═══════════════════════════════════════════════════

async def _edge_tts_synthesize(text: str, voice: str, output_path: str) -> dict:
    """Generate speech using Edge-TTS (online, free)."""
    import time
    try:
        import edge_tts
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "edge-tts", "-q"])
        import edge_tts

    start_time = time.time()

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

    gen_time = time.time() - start_time
    file_size = os.path.getsize(output_path)

    # Estimate duration from file size (MP3 ~16kbps for speech)
    duration = file_size / 16000  # rough estimate

    return {
        "status": "success",
        "engine": "edge-tts",
        "voice": voice,
        "output": output_path,
        "duration": round(duration, 2),
        "generation_time": round(gen_time, 2),
        "rtf": round(gen_time / max(duration, 0.1), 2),
        "size": file_size,
    }


# ═══════════════════════════════════════════════════
# ── Synthesize (Batch) — supports both engines ──
# ═══════════════════════════════════════════════════

@router.post("/api/v1/tts/synthesize")
async def synthesize(body: SynthesizeRequest, background_tasks: BackgroundTasks):
    """Convert text to speech. engine='edge' (online) or 'vibevoice' (offline AI)."""
    if not body.text.strip():
        raise HTTPException(400, "Text is empty")

    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {"status": "processing", "progress": 0, "result": None}

    if body.engine == "edge":
        # Edge-TTS (online, fast)
        async def _run_edge():
            try:
                import re, tempfile
                import subprocess
                
                output_path = body.output_path or os.path.join(
                    _get_output_dir(), f"tts_{task_id}.mp3"
                )
                
                # Split text into sentences for reliable progress tracking
                text = body.text.replace('\n', ' ')
                chunks = [s.strip() for s in re.split(r'(?<=[.!?]) +(?=[A-Z0-9À-Ỹa-z])', text) if s.strip()]
                if not chunks: chunks = [text]
                
                temp_dir = tempfile.mkdtemp()
                temp_files = []
                
                for i, chunk in enumerate(chunks):
                    _tasks[task_id]["progress"] = int((i / len(chunks)) * 90)
                    tmp_out = os.path.join(temp_dir, f"chunk_{i}.mp3")
                    
                    # Synthesize chunk
                    res = await _edge_tts_synthesize(chunk, body.voice, tmp_out)
                    if os.path.exists(tmp_out):
                        temp_files.append(tmp_out)
                
                if not temp_files:
                    raise Exception("All Edge-TTS chunks failed.")
                
                _tasks[task_id]["status"] = "stitching"
                _tasks[task_id]["progress"] = 95
                
                # Concat using ffmpeg concat demuxer
                list_path = os.path.join(temp_dir, "list.txt")
                with open(list_path, "w", encoding="utf-8") as f:
                    for fn in temp_files:
                        f.write(f"file '{fn}'\n")
                
                subprocess.check_call(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Cleanup
                try:
                    for fn in temp_files:
                        os.remove(fn)
                    os.remove(list_path)
                    os.rmdir(temp_dir)
                except: pass
                
                # Build dummy result dict like _edge_tts_synthesize to satisfy frontend check
                result = {
                    "status": "success",
                    "engine": "edge-tts",
                    "voice": body.voice,
                    "output": output_path,
                    "duration": 0,
                    "size": os.path.getsize(output_path)
                }
                
                _tasks[task_id]["progress"] = 100
                _tasks[task_id]["status"] = "success"
                _tasks[task_id]["result"] = result
            except Exception as e:
                logger.error(f"Edge-TTS error: {e}")
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = {"status": "error", "message": str(e)}

        background_tasks.add_task(_run_edge)
    elif body.engine == "gemini":
        # Gemini AI Studio Speech (Playwright automation)
        async def _run_gemini():
            try:
                import subprocess, tempfile
                output_path = body.output_path or os.path.join(
                    _get_output_dir(), f"tts_{task_id}.wav"
                )
                
                # Write text to temp file
                text_file = os.path.join(_get_output_dir(), f"temp_text_{task_id}.txt")
                with open(text_file, "w", encoding="utf-8") as f:
                    f.write(body.text)
                
                _tasks[task_id]["status"] = "processing"
                _tasks[task_id]["progress"] = 10
                
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines", "gemini_tts.js")
                # Use browser_profile from request or fallback to first available
                profile = getattr(body, 'browser_profile', None)
                if not profile:
                    profile = "default"
                    try:
                        from tubecli.config import DATA_DIR
                        profiles_dir = os.path.join(DATA_DIR, "browser_profiles")
                        if os.path.exists(profiles_dir):
                            profiles = [d for d in os.listdir(profiles_dir) if os.path.isdir(os.path.join(profiles_dir, d))]
                            if profiles:
                                profile = sorted(profiles)[0]
                    except Exception as e:
                        logger.error(f"Failed to get browser profiles: {e}")
                
                cmd = ["node", script_path, "--text-file", text_file, "--voice", body.voice, "--output", output_path, "--profile", profile]
                
                from pathlib import Path
                tubecli_root = Path(__file__).resolve().parent.parent.parent.parent
                browser_ext_dir = tubecli_root / "tubecli" / "extensions" / "browser"
                
                env = os.environ.copy()
                env["NODE_PATH"] = str(browser_ext_dir / "node_modules")
                
                _tasks[task_id]["progress"] = 30
                # We use subprocess.Popen to capture output and wait
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(browser_ext_dir))
                
                # Poll progress (mock progress for now)
                for _ in range(60):
                    if proc.poll() is not None:
                        break
                    _tasks[task_id]["progress"] = min(90, _tasks[task_id]["progress"] + 1)
                    await asyncio.sleep(2)
                
                stdout, stderr = proc.communicate()
                
                # Cleanup text file
                try: os.remove(text_file)
                except: pass
                
                if proc.returncode != 0:
                    raise Exception(f"Gemini TTS failed: {stderr}")
                
                # Parse output json if possible
                try:
                    out_data = json.loads(stdout.strip())
                    if out_data.get("status") == "error":
                        raise Exception(out_data.get("message", "Unknown error in JS script"))
                except json.JSONDecodeError:
                    pass
                
                if not os.path.exists(output_path):
                    raise Exception("Output file was not created by Gemini script")
                
                _tasks[task_id]["progress"] = 100
                _tasks[task_id]["status"] = "success"
                _tasks[task_id]["result"] = {
                    "status": "success",
                    "engine": "gemini",
                    "voice": body.voice,
                    "output": output_path,
                    "duration": 0,
                    "size": os.path.getsize(output_path)
                }
            except Exception as e:
                logger.error(f"Gemini TTS error: {e}")
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = {"status": "error", "message": str(e)}

        background_tasks.add_task(_run_gemini)
    elif body.engine == "viterbox":
        # Viterbox (offline AI, Vietnamese)
        async def _run_viterbox():
            try:
                engine = _get_viterbox_engine()
                output_path = body.output_path or os.path.join(
                    _get_output_dir(), f"tts_{task_id}.wav"
                )
                _tasks[task_id]["status"] = "loading_model"
                engine.load_model()
                
                if not engine.is_loaded:
                    raise Exception(f"Model failed to load: {engine.load_error or 'Unknown Error'}")
                
                # Split text into chunks primarily by paragraphs to preserve natural prosody.
                # Only split by sentences if a paragraph is extremely long to prevent GPU OOM.
                import re, wave, tempfile
                chunks = []
                for p in re.split(r'\n+', body.text):
                    p = p.strip()
                    if not p: continue
                    # Safe threshold for VITS Viterbox is ~600 chars
                    if len(p) < 600:
                        chunks.append(p)
                    else:
                        sub_chunks = [s.strip() for s in re.split(r'(?<=[.!?]) +(?=[A-Z0-9À-Ỹa-z])', p) if s.strip()]
                        chunks.extend(sub_chunks)
                if not chunks: chunks = [body.text]
                
                _tasks[task_id]["status"] = "processing"
                temp_dir = tempfile.mkdtemp()
                temp_files = []
                total_duration = 0
                
                try:
                    for i, chunk in enumerate(chunks):
                        _tasks[task_id]["progress"] = int((i / len(chunks)) * 90)
                        tmp_out = os.path.join(temp_dir, f"chunk_{i}.wav")
                        res = engine.synthesize(text=chunk, voice=body.voice, output_path=tmp_out, cfg_scale=body.cfg_scale)
                        if res.get("status") == "success" and os.path.exists(tmp_out):
                            temp_files.append(tmp_out)
                            total_duration += res.get("duration", 0)
                    
                    if not temp_files:
                        raise Exception("All Viterbox chunks failed to generate.")
                        
                    _tasks[task_id]["status"] = "stitching"
                    _tasks[task_id]["progress"] = 95
                    
                    # Concat wavs
                    data = []
                    params = None
                    for f in temp_files:
                        with wave.open(f, 'rb') as w:
                            if not params: params = w.getparams()
                            data.append(w.readframes(w.getnframes()))
                    
                    if params and len(data) > 1:
                        # 400ms silence block between paragraphs
                        silence_frames = int(params.framerate * 0.4)
                        silence_bytes = bytes([0] * (silence_frames * params.sampwidth * params.nchannels))
                        final_data = []
                        for i, d in enumerate(data):
                            final_data.append(d)
                            if i < len(data) - 1:
                                final_data.append(silence_bytes)
                        data = final_data

                    with wave.open(output_path, 'wb') as w:
                        w.setparams(params)
                        for d in data: w.writeframes(d)
                        
                    result = {
                        "status": "success",
                        "output": output_path,
                        "duration": round(total_duration, 2),
                        "size": os.path.getsize(output_path),
                        "voice": body.voice,
                        "engine": "viterbox"
                    }
                finally:
                    import shutil as _sh
                    _sh.rmtree(temp_dir, ignore_errors=True)
                _tasks[task_id]["status"] = result.get("status", "error")
                _tasks[task_id]["result"] = result
            except Exception as e:
                logger.error(f"Viterbox synthesis error: {e}")
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = {"status": "error", "message": str(e)}

        background_tasks.add_task(_run_viterbox)
    elif body.engine == "everai":
        # EverAI (Cloud API)
        async def _run_everai():
            try:
                engine = _get_everai_engine()
                output_path = body.output_path or os.path.join(
                    _get_output_dir(), f"tts_{task_id}.mp3"
                )
                
                # Split text into chunks primarily by paragraphs
                import re, tempfile
                chunks = []
                for p in re.split(r'\n+', body.text):
                    p = p.strip()
                    if not p: continue
                    if len(p) < 1000:
                        chunks.append(p)
                    else:
                        sub_chunks = [s.strip() for s in re.split(r'(?<=[.!?]) +(?=[A-Z0-9À-Ỹa-z])', p) if s.strip()]
                        chunks.extend(sub_chunks)
                if not chunks: chunks = [body.text]
                
                _tasks[task_id]["status"] = "processing"
                temp_dir = tempfile.mkdtemp()
                temp_files = []
                total_duration = 0
                
                try:
                    for i, chunk in enumerate(chunks):
                        _tasks[task_id]["progress"] = int((i / len(chunks)) * 90)
                        tmp_out = os.path.join(temp_dir, f"chunk_{i}.mp3")
                        res = await engine.synthesize_async(text=chunk, voice=body.voice, output_path=tmp_out)
                        if res.get("status") == "success" and os.path.exists(tmp_out):
                            temp_files.append(tmp_out)
                            # Estimate duration from mp3 size (128kbps = 16KB/s)
                            total_duration += os.path.getsize(tmp_out) / 16000.0
                    
                    if not temp_files:
                        raise Exception("All EverAI chunks failed to generate.")
                        
                    _tasks[task_id]["status"] = "stitching"
                    _tasks[task_id]["progress"] = 95
                    
                    # Concat using ffmpeg concat demuxer
                    import subprocess
                    list_path = os.path.join(temp_dir, "list.txt")
                    with open(list_path, "w", encoding="utf-8") as f:
                        for fn in temp_files:
                            f.write(f"file '{fn}'\n")
                    
                    subprocess.check_call(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
                    result = {
                        "status": "success",
                        "output": output_path,
                        "duration": round(total_duration, 2),
                        "size": os.path.getsize(output_path),
                        "voice": body.voice,
                        "engine": "everai"
                    }
                finally:
                    import shutil as _sh
                    _sh.rmtree(temp_dir, ignore_errors=True)
                    
                _tasks[task_id]["status"] = result.get("status", "error")
                _tasks[task_id]["result"] = result
            except Exception as e:
                logger.error(f"EverAI synthesis error: {e}")
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = {"status": "error", "message": str(e)}

        background_tasks.add_task(_run_everai)
    elif body.engine == "omnivoice":
        # OmniVoice (local API)
        async def _run_omnivoice():
            try:
                engine = _get_omnivoice_engine()
                output_path = body.output_path or os.path.join(
                    _get_output_dir(), f"tts_{task_id}.wav"
                )
                _tasks[task_id]["status"] = "processing"
                _tasks[task_id]["progress"] = 30
                
                res = await engine.synthesize_async(
                    text=body.text,
                    voice=body.voice,
                    output_path=output_path,
                    cfg_scale=body.cfg_scale
                )
                if res.get("status") == "success" and os.path.exists(output_path):
                    _tasks[task_id]["progress"] = 100
                    _tasks[task_id]["status"] = "success"
                    _tasks[task_id]["result"] = res
                else:
                    raise Exception(res.get("message") or "OmniVoice synthesis failed")
            except Exception as e:
                logger.error(f"OmniVoice synthesis error: {e}")
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = {"status": "error", "message": str(e)}

        background_tasks.add_task(_run_omnivoice)
    else:
        # VibeVoice (offline AI)
        async def _run_vibevoice():
            try:
                engine = _get_vibevoice_engine()
                output_path = body.output_path or os.path.join(
                    _get_output_dir(), f"tts_{task_id}.wav"
                )
                _tasks[task_id]["status"] = "loading_model"
                engine.load_model()
                
                # Split text into chunks primarily by paragraphs to preserve natural prosody.
                # Only split by sentences if a paragraph is extremely long to prevent GPU OOM.
                import re, wave, tempfile
                chunks = []
                for p in re.split(r'\n+', body.text):
                    p = p.strip()
                    if not p: continue
                    if len(p) < 600:
                        chunks.append(p)
                    else:
                        sub_chunks = [s.strip() for s in re.split(r'(?<=[.!?]) +(?=[A-Z0-9À-Ỹa-z])', p) if s.strip()]
                        chunks.extend(sub_chunks)
                if not chunks: chunks = [body.text]
                
                _tasks[task_id]["status"] = "processing"
                temp_dir = tempfile.mkdtemp()
                temp_files = []
                total_duration = 0
                
                try:
                    for i, chunk in enumerate(chunks):
                        _tasks[task_id]["progress"] = int((i / len(chunks)) * 90)
                        tmp_out = os.path.join(temp_dir, f"chunk_{i}.wav")
                        res = engine.synthesize(text=chunk, voice=body.voice, output_path=tmp_out, cfg_scale=body.cfg_scale)
                        if res.get("status") == "success" and os.path.exists(tmp_out):
                            temp_files.append(tmp_out)
                            total_duration += res.get("duration", 0)
                    
                    if not temp_files:
                        raise Exception("All chunks failed to generate.")
                        
                    _tasks[task_id]["status"] = "stitching"
                    _tasks[task_id]["progress"] = 95
                    
                    # Concat wavs
                    data = []
                    params = None
                    for f in temp_files:
                        with wave.open(f, 'rb') as w:
                            if not params: params = w.getparams()
                            data.append(w.readframes(w.getnframes()))
                    
                    if params and len(data) > 1:
                        # 400ms silence block between paragraphs
                        silence_frames = int(params.framerate * 0.4)
                        silence_bytes = bytes([0] * (silence_frames * params.sampwidth * params.nchannels))
                        final_data = []
                        for i, d in enumerate(data):
                            final_data.append(d)
                            if i < len(data) - 1:
                                final_data.append(silence_bytes)
                        data = final_data

                    with wave.open(output_path, 'wb') as w:
                        w.setparams(params)
                        for d in data: w.writeframes(d)
                        
                    result = {
                        "status": "success",
                        "output": output_path,
                        "duration": round(total_duration, 2),
                        "size": os.path.getsize(output_path),
                        "voice": body.voice
                    }
                finally:
                    import shutil as _sh
                    _sh.rmtree(temp_dir, ignore_errors=True)
                _tasks[task_id]["status"] = result.get("status", "error")
                _tasks[task_id]["result"] = result
            except Exception as e:
                logger.error(f"VibeVoice synthesis error: {e}")
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = {"status": "error", "message": str(e)}

        background_tasks.add_task(_run_vibevoice)

    return {"success": True, "task_id": task_id, "engine": body.engine, "message": "TTS synthesis started"}


# ── Serve audio file ──

@router.get("/api/v1/tts/audio/{filename}")
async def serve_audio(filename: str):
    """Serve generated audio file."""
    filepath = os.path.join(_get_output_dir(), filename)
    if os.path.exists(filepath):
        return FileResponse(filepath)
    raise HTTPException(404, "Audio file not found")


# ═══════════════════════════════════════════════════
# ── SRT → Voice Track (timed synthesis) ──
# ═══════════════════════════════════════════════════

class SRTSynthesizeRequest(BaseModel):
    srt_content: str
    voice: str = "vi-VN-HoaiMyNeural"
    engine: str = "edge"
    cfg_scale: float = 2.0
    speed_adjust: bool = True  # speed up/slow down to fit SRT timing


def _parse_srt(srt_text: str) -> list:
    """Parse SRT content into list of {index, start_ms, end_ms, text}."""
    import re
    blocks = re.split(r'\n\s*\n', srt_text.strip())
    segments = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        # Parse timestamp line
        ts_match = re.match(
            r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})',
            lines[1].strip()
        )
        if not ts_match:
            continue
        g = ts_match.groups()
        start_ms = int(g[0]) * 3600000 + int(g[1]) * 60000 + int(g[2]) * 1000 + int(g[3])
        end_ms = int(g[4]) * 3600000 + int(g[5]) * 60000 + int(g[6]) * 1000 + int(g[7])
        text = ' '.join(lines[2:]).strip()
        # Clean HTML tags from SRT
        text = re.sub(r'<[^>]+>', '', text)
        if text:
            segments.append({
                "index": lines[0].strip(),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
            })
    return segments


@router.post("/api/v1/tts/synthesize-srt")
async def synthesize_srt(body: SRTSynthesizeRequest, background_tasks: BackgroundTasks):
    """Generate a voice track from SRT content — audio follows SRT timing with silence gaps."""
    segments = _parse_srt(body.srt_content)
    if not segments:
        raise HTTPException(400, "No valid SRT segments found")

    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "total_segments": len(segments),
        "current_segment": 0,
        "result": None,
    }

    async def _run_srt():
        import struct
        import tempfile
        import time

        start_time = time.time()
        out_dir = _get_output_dir()
        temp_dir = os.path.join(out_dir, f"srt_temp_{task_id}")
        os.makedirs(temp_dir, exist_ok=True)

        SAMPLE_RATE = 24000
        CHANNELS = 1
        BITS = 16

        try:
            # Phase 1: Generate individual segment audio files
            segment_audio = []  # list of (start_ms, end_ms, audio_data_bytes)

            for idx, seg in enumerate(segments):
                _tasks[task_id]["current_segment"] = idx + 1
                _tasks[task_id]["progress"] = int((idx / len(segments)) * 80)

                seg_path = os.path.join(temp_dir, f"seg_{idx:04d}.mp3")

                if body.engine == "edge":
                    try:
                        import edge_tts
                        communicate = edge_tts.Communicate(seg["text"], body.voice)
                        await communicate.save(seg_path)
                    except Exception as e:
                        logger.warning(f"Edge-TTS segment {idx} failed: {e}")
                        continue
                elif body.engine == "viterbox":
                    # Viterbox
                    try:
                        engine_obj = _get_viterbox_engine()
                        seg_path_wav = seg_path.replace('.mp3', '.wav')
                        engine_obj.synthesize(
                            text=seg["text"],
                            voice=body.voice,
                            output_path=seg_path_wav,
                            cfg_scale=body.cfg_scale,
                        )
                        seg_path = seg_path_wav
                    except Exception as e:
                        logger.warning(f"Viterbox segment {idx} failed: {e}")
                        continue
                elif body.engine == "everai":
                    # EverAI
                    try:
                        engine_obj = _get_everai_engine()
                        await engine_obj.synthesize_async(
                            text=seg["text"],
                            voice=body.voice,
                            output_path=seg_path,
                        )
                    except Exception as e:
                        logger.warning(f"EverAI segment {idx} failed: {e}")
                        continue
                elif body.engine == "omnivoice":
                    # OmniVoice
                    try:
                        engine_obj = _get_omnivoice_engine()
                        seg_path_wav = seg_path.replace('.mp3', '.wav')
                        await engine_obj.synthesize_async(
                            text=seg["text"],
                            voice=body.voice,
                            output_path=seg_path_wav,
                            cfg_scale=body.cfg_scale,
                        )
                        seg_path = seg_path_wav
                    except Exception as e:
                        logger.warning(f"OmniVoice segment {idx} failed: {e}")
                        continue
                else:
                    # VibeVoice
                    try:
                        engine_obj = _get_vibevoice_engine()
                        seg_path_wav = seg_path.replace('.mp3', '.wav')
                        engine_obj.synthesize(
                            text=seg["text"],
                            voice=body.voice,
                            output_path=seg_path_wav,
                            cfg_scale=body.cfg_scale,
                        )
                        seg_path = seg_path_wav
                    except Exception as e:
                        logger.warning(f"VibeVoice segment {idx} failed: {e}")
                        continue

                if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                    segment_audio.append({
                        "start_ms": seg["start_ms"],
                        "end_ms": seg["end_ms"],
                        "text": seg["text"],
                        "file": seg_path,
                    })

            if not segment_audio:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = {"status": "error", "message": "All segments failed to generate"}
                return

            # Phase 2: Stitch together with silence gaps using ffmpeg
            _tasks[task_id]["progress"] = 85
            _tasks[task_id]["status"] = "stitching"

            total_duration_ms = max(s["end_ms"] for s in segment_audio)
            output_path = os.path.join(out_dir, f"srt_voice_{task_id}.mp3")

            # Build ffmpeg filter complex
            import subprocess
            import shutil

            ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"

            # Create a silence base track
            silence_path = os.path.join(temp_dir, "silence.mp3")
            silence_duration = total_duration_ms / 1000.0

            # Generate silence track
            subprocess.run([
                ffmpeg_path, "-y",
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
                "-t", str(silence_duration),
                "-q:a", "9",
                silence_path,
            ], capture_output=True, timeout=30)

            # Build overlay command: start with silence, overlay each segment at its timestamp
            inputs = ["-i", silence_path]
            filter_parts = []
            for i, seg in enumerate(segment_audio):
                inputs.extend(["-i", seg["file"]])

            # Filter: adelay each segment to its start time, then amix all
            filter_str = ""
            mix_inputs = ["[0:a]"]  # silence base

            for i, seg in enumerate(segment_audio):
                delay_ms = seg["start_ms"]
                segment_dur_ms = seg["end_ms"] - seg["start_ms"]
                # Use atempo to speed up/slow down if needed
                filter_str += f"[{i+1}:a]adelay={delay_ms}|{delay_ms}[d{i}]; "
                mix_inputs.append(f"[d{i}]")

            n = len(mix_inputs)
            filter_str += "".join(mix_inputs) + f"amix=inputs={n}:duration=first:dropout_transition=0[out]"

            cmd = [
                ffmpeg_path, "-y",
                *inputs,
                "-filter_complex", filter_str,
                "-map", "[out]",
                "-ac", "1",
                "-ar", "44100",
                "-b:a", "128k",
                output_path,
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                logger.error(f"FFmpeg stitch error: {proc.stderr[:500]}")
                # Fallback: simple concatenation
                output_path = await _simple_concat_srt(segment_audio, out_dir, task_id)

            _tasks[task_id]["progress"] = 95

            # Cleanup temp files
            try:
                import shutil as _sh
                _sh.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

            gen_time = time.time() - start_time
            file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

            _tasks[task_id]["status"] = "success"
            _tasks[task_id]["progress"] = 100
            _tasks[task_id]["result"] = {
                "status": "success",
                "engine": body.engine,
                "voice": body.voice,
                "output": output_path,
                "total_duration": round(total_duration_ms / 1000, 2),
                "segments_count": len(segment_audio),
                "generation_time": round(gen_time, 2),
                "size": file_size,
                "duration": round(total_duration_ms / 1000, 2),
                "rtf": round(gen_time / max(total_duration_ms / 1000, 0.1), 2),
            }

        except Exception as e:
            logger.error(f"SRT synthesis error: {e}", exc_info=True)
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["result"] = {"status": "error", "message": str(e)}

    background_tasks.add_task(_run_srt)
    return {
        "success": True,
        "task_id": task_id,
        "segments_count": len(segments),
        "message": f"SRT voice track generation started ({len(segments)} segments)",
    }


async def _simple_concat_srt(segment_audio, out_dir, task_id):
    """Fallback: generate WAV with silence + concat using wave module."""
    import wave
    import struct
    import subprocess
    import shutil

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    RATE = 44100

    # Convert each segment mp3 to wav
    wav_segments = []
    for i, seg in enumerate(segment_audio):
        wav_path = seg["file"].rsplit(".", 1)[0] + "_conv.wav"
        subprocess.run([ffmpeg, "-y", "-i", seg["file"], "-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16", wav_path],
                       capture_output=True, timeout=30)
        if os.path.exists(wav_path):
            wav_segments.append({**seg, "wav_file": wav_path})

    if not wav_segments:
        return ""

    total_ms = max(s["end_ms"] for s in wav_segments)
    total_samples = int(total_ms * RATE / 1000)
    final_audio = bytearray(total_samples * 2)  # 16-bit mono

    for seg in wav_segments:
        try:
            with wave.open(seg["wav_file"], "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            start_sample = int(seg["start_ms"] * RATE / 1000) * 2
            end_sample = min(start_sample + len(raw), len(final_audio))
            available = end_sample - start_sample
            final_audio[start_sample:end_sample] = raw[:available]
        except Exception as e:
            logger.warning(f"Concat segment error: {e}")

    output_path = os.path.join(out_dir, f"srt_voice_{task_id}.wav")
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(bytes(final_audio))

    return output_path


# ═══════════════════════════════════════════════════
# ── List Voices (both engines) ──
# ═══════════════════════════════════════════════════

@router.get("/api/v1/tts/voices")
async def list_voices():
    """List available voice presets from all engines."""
    voices = []

    # 1. Edge-TTS voices (online)
    try:
        import edge_tts
        edge_voices = await edge_tts.list_voices()
        # Group popular languages
        priority_locales = [
            "vi-VN", "en-US", "en-GB", "zh-CN", "zh-TW",
            "ja-JP", "ko-KR", "fr-FR", "de-DE", "es-ES",
            "it-IT", "pt-BR", "th-TH", "id-ID", "ru-RU",
        ]
        for v in edge_voices:
            locale = v.get("Locale", "")
            short_name = v.get("ShortName", "")
            gender = v.get("Gender", "").lower()
            lang_name = v.get("FriendlyName", "").split(" ")[1] if " " in v.get("FriendlyName", "") else locale

            # Only include priority locales to keep the list manageable
            if any(locale.startswith(pl) for pl in priority_locales):
                voices.append({
                    "id": short_name,
                    "name": v.get("FriendlyName", short_name).replace("Microsoft Server Speech Text to Speech Voice ", ""),
                    "language": locale[:2],
                    "locale": locale,
                    "language_name": _locale_to_name(locale),
                    "gender": gender,
                    "engine": "edge",
                })
    except Exception as e:
        logger.warning(f"Could not load Edge-TTS voices: {e}")

    # 2. VibeVoice voices (offline)
    try:
        engine = _get_vibevoice_engine()
        vv_voices = engine.list_voices()
        for v in vv_voices:
            v["engine"] = "vibevoice"
        voices.extend(vv_voices)
    except Exception as e:
        logger.warning(f"Could not load VibeVoice voices: {e}")

    # 3. Viterbox voices (offline)
    try:
        engine = _get_viterbox_engine()
        vb_voices = engine.list_voices()
        for v in vb_voices:
            v["engine"] = "viterbox"
        voices.extend(vb_voices)
    except Exception as e:
        logger.warning(f"Could not load Viterbox voices: {e}")

    # 4. EverAI voices (cloud)
    try:
        engine = _get_everai_engine()
        everai_voices = engine.list_voices()
        for v in everai_voices:
            v["engine"] = "everai"
        voices.extend(everai_voices)
    except Exception as e:
        logger.warning(f"Could not load EverAI voices: {e}")

    # 5. Gemini voices (online via automation)
    gemini_voice_names = ["Aoede", "Charon", "Fenrir", "Kore", "Puck", "Achernar"]
    for vname in gemini_voice_names:
        voices.append({
            "id": vname,
            "name": vname,
            "language": "en", # AI studio usually handles multilang but maybe default en
            "locale": "en-US",
            "language_name": "Multi-language",
            "gender": "neutral",
            "engine": "gemini",
        })

    # 6. OmniVoice voices (local FastAPI server)
    try:
        engine = _get_omnivoice_engine()
        ov_voices = engine.list_voices()
        for v in ov_voices:
            v["engine"] = "omnivoice"
        voices.extend(ov_voices)
    except Exception as e:
        logger.warning(f"Could not load OmniVoice voices: {e}")

    return {"success": True, "voices": voices, "count": len(voices)}


def _locale_to_name(locale: str) -> str:
    """Convert locale code to language name."""
    mapping = {
        "vi": "Tiếng Việt", "en": "English", "zh": "中文",
        "ja": "日本語", "ko": "한국어", "fr": "Français",
        "de": "Deutsch", "es": "Español", "it": "Italiano",
        "pt": "Português", "th": "ภาษาไทย", "id": "Bahasa Indonesia",
        "ru": "Русский",
    }
    lang = locale[:2]
    return mapping.get(lang, locale)


# ═══════════════════════════════════════════════════
# ── Edge-TTS: List all available voices ──
# ═══════════════════════════════════════════════════

@router.get("/api/v1/tts/voices/edge/all")
async def list_all_edge_voices():
    """List ALL Edge-TTS voices (all languages)."""
    try:
        import edge_tts
        edge_voices = await edge_tts.list_voices()
        voices = []
        for v in edge_voices:
            voices.append({
                "id": v.get("ShortName", ""),
                "name": v.get("FriendlyName", ""),
                "locale": v.get("Locale", ""),
                "gender": v.get("Gender", "").lower(),
            })
        return {"success": True, "voices": voices, "count": len(voices)}
    except Exception as e:
        return {"success": False, "error": str(e), "voices": [], "count": 0}


# ── Load Model ──

@router.post("/api/v1/tts/load")
async def load_model(body: dict, background_tasks: BackgroundTasks):
    """Trigger model loading (preload before first synthesis). Supports engine parameter."""
    try:
        engine_name = body.get("engine", "vibevoice")
        
        if engine_name == "viterbox":
            engine = _get_viterbox_engine()
        else:
            engine = _get_vibevoice_engine()
            
        if engine.is_loaded:
            return {"success": True, "message": "Model already loaded"}
        if engine.is_loading:
            return {"success": True, "message": "Model is currently loading"}

        background_tasks.add_task(engine.load_model)
        return {"success": True, "message": f"{engine_name} model loading started in background"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════
# ── Upload Voice (Cloning) ──

@router.post("/api/v1/tts/upload-voice")
async def upload_voice(
    voice_name: str = Form("Custom Voice"),
    gender: str = Form("unknown"),
    file: UploadFile = File(...)
):
    """Upload a custom voice (.wav) for Viterbox cloning."""
    try:
        if not file.filename.lower().endswith('.wav'):
            return JSONResponse(status_code=400, content={"success": False, "message": "Only .wav files are supported for voice cloning."})

        # Save to viterbox wavs directory
        save_dir = r"C:\tubecreate-vue\viterbox-tts-main\wavs"
        os.makedirs(save_dir, exist_ok=True)
        
        # generate a unique filename incorporating gender and name
        import uuid, re
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '', voice_name.replace(' ', '_'))
        unique_id = f"clone_{gender}_{safe_name}_{uuid.uuid4().hex[:6]}"
        file_path = os.path.join(save_dir, f"{unique_id}.wav")

        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Force viterbox engine to rescan presets
        try:
            engine = _get_viterbox_engine()
            engine._scan_voice_presets()
        except:
            pass # Ignore if engine not loaded yet

        return {"success": True, "message": "Voice uploaded successfully", "voice_id": unique_id}
    except Exception as e:
        logger.error(f"Voice upload failed: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@router.delete("/api/v1/tts/voice/{voice_id}")
async def delete_voice(voice_id: str):
    """Delete a cloned voice by ID."""
    if not voice_id.startswith("clone_"):
        return JSONResponse(status_code=400, content={"success": False, "message": "Cannot delete default generic voices."})
    
    save_dir = r"C:\tubecreate-vue\viterbox-tts-main\wavs"
    file_path = os.path.join(save_dir, f"{voice_id}.wav")
    
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            engine = _get_viterbox_engine()
            engine._scan_voice_presets()
            return {"success": True, "message": "Voice deleted."}
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    return JSONResponse(status_code=404, content={"success": False, "message": "Voice not found."})


class UpdateVoiceRequest(BaseModel):
    new_name: str
    gender: str

@router.put("/api/v1/tts/voice/{voice_id}")
async def update_voice(voice_id: str, req: UpdateVoiceRequest):
    """Rename a cloned voice by updating the attributes packed in filename."""
    if not voice_id.startswith("clone_"):
        return JSONResponse(status_code=400, content={"success": False, "message": "Cannot edit default generic voices."})
        
    save_dir = r"C:\tubecreate-vue\viterbox-tts-main\wavs"
    old_file_path = os.path.join(save_dir, f"{voice_id}.wav")
    
    if not os.path.exists(old_file_path):
        return JSONResponse(status_code=404, content={"success": False, "message": "Voice not found."})
        
    parts = voice_id.split('_')
    if len(parts) >= 4:
        uuid_part = parts[-1]
    else:
        import uuid
        uuid_part = uuid.uuid4().hex[:6]
        
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '', req.new_name.replace(' ', '_'))
    new_id = f"clone_{req.gender}_{safe_name}_{uuid_part}"
    new_file_path = os.path.join(save_dir, f"{new_id}.wav")
    
    try:
        os.rename(old_file_path, new_file_path)
        engine = _get_viterbox_engine()
        engine._scan_voice_presets()
        return {"success": True, "message": "Voice updated.", "voice_id": new_id}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

# ── Static Streaming HTML Route (Fallback) ──
# ═══════════════════════════════════════════════════

_ws_lock = asyncio.Lock()


@router.websocket("/api/v1/tts/stream")
async def websocket_stream(ws: WebSocket):
    """WebSocket endpoint for streaming TTS audio (VibeVoice engine)."""
    await ws.accept()

    text = ws.query_params.get("text", "")
    voice = ws.query_params.get("voice", "en-Carter_man")
    cfg_param = ws.query_params.get("cfg", "1.5")

    try:
        cfg_scale = float(cfg_param)
        if cfg_scale <= 0:
            cfg_scale = 1.5
    except ValueError:
        cfg_scale = 1.5

    if not text.strip():
        await ws.send_text(json.dumps({"type": "error", "message": "Empty text"}))
        await ws.close()
        return

    if _ws_lock.locked():
        await ws.send_text(json.dumps({
            "type": "error",
            "message": "Server busy, please wait for current generation to complete."
        }))
        await ws.close(code=1013, reason="Service busy")
        return

    try:
        await _ws_lock.acquire()
        engine = _get_vibevoice_engine()

        if not engine.is_loaded:
            await ws.send_text(json.dumps({"type": "status", "message": "Loading model..."}))
            engine.load_model()
            if not engine.is_loaded:
                await ws.send_text(json.dumps({"type": "error", "message": engine.load_error or "Model failed to load"}))
                await ws.close()
                return

        await ws.send_text(json.dumps({"type": "status", "message": "Generating..."}))

        stop_event = threading.Event()
        iterator = engine.stream(text=text, voice=voice, cfg_scale=cfg_scale, stop_event=stop_event)

        generated_samples = 0
        sentinel = object()

        try:
            while ws.client_state == WebSocketState.CONNECTED:
                chunk = await asyncio.to_thread(next, iterator, sentinel)
                if chunk is sentinel:
                    break

                payload = engine.chunk_to_pcm16(chunk)
                await ws.send_bytes(payload)
                generated_samples += chunk.size

                duration = generated_samples / engine.sample_rate
                await ws.send_text(json.dumps({
                    "type": "progress",
                    "generated_sec": round(duration, 2),
                }))
        except WebSocketDisconnect:
            logger.info("Client disconnected")
            stop_event.set()
        except Exception as e:
            logger.error(f"Stream error: {e}")
            stop_event.set()
        finally:
            stop_event.set()
            try:
                close_fn = getattr(iterator, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:
                pass

        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.send_text(json.dumps({
                    "type": "complete",
                    "total_sec": round(generated_samples / engine.sample_rate, 2),
                }))
                await ws.close()
        except Exception:
            pass

    finally:
        _ws_lock.release()
