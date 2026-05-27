"""
OmniVoice TTS Engine — Local Studio API integration wrapper for TubeCLI.
Queries the local OmniVoice Studio server (port 3900) to discover profiles and run TTS.
"""
import os
import time
import httpx
import logging
from typing import List, Dict

logger = logging.getLogger("TTS.OmniVoice")

def get_engine():
    return OmniVoiceTTSEngine()

class OmniVoiceTTSEngine:
    def __init__(self):
        self.api_base = "http://localhost:3900"

    def list_voices(self) -> list:
        """Fetch custom voice profiles dynamically from the local OmniVoice Studio backend."""
        try:
            resp = httpx.get(f"{self.api_base}/profiles", timeout=2.0)
            if resp.status_code == 200:
                profiles = resp.json()
                voices = []
                for p in profiles:
                    # Map OmniVoice voice profiles to VibeVoice format
                    personality = (p.get("personality") or "").lower()
                    gender = "female" if "female" in personality else ("male" if "male" in personality else "neutral")
                    
                    # Because OmniVoice uses zero-shot cross-lingual cloning models, 
                    # every voice profile can synthesize speech in multiple languages.
                    # We map them to both 'vi' (Tiếng Việt) and 'en' (English) tabs for seamless access.
                    for lang_code, lang_name, locale in [("vi", "Tiếng Việt", "vi-VN"), ("en", "English", "en-US")]:
                        voices.append({
                            "id": p["id"],
                            "name": f"{p['name']} (OmniVoice)",
                            "language": lang_code,
                            "locale": locale,
                            "language_name": lang_name,
                            "gender": gender,
                            "engine": "omnivoice"
                        })
                return voices
        except Exception as e:
            logger.warning(f"Could not load OmniVoice profiles (is OmniVoice Studio running on port 3900?): {e}")
        return []

    def get_status(self) -> dict:
        """Check if the local OmniVoice Studio server is alive on port 3900."""
        try:
            resp = httpx.get(f"{self.api_base}/profiles", timeout=1.0)
            available = (resp.status_code == 200)
            details = "OmniVoice Studio active on port 3900" if available else f"OmniVoice Studio offline (status: {resp.status_code})"
        except Exception:
            available = False
            details = "OmniVoice Studio offline or port 3900 unreachable"

        return {
            "loaded": available,
            "loading": False,
            "error": None if available else "OmniVoice Studio not running on port 3900",
            "device": "local-api",
            "voices_count": len(self.list_voices()) if available else 0
        }

    def _sanitize_instruct(self, raw_instruct: str) -> str:
        if not raw_instruct:
            return ""
        
        valid_items = {
            "male", "female", "child", "teenager", "young adult", "middle-aged", "elderly",
            "very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch", "whisper",
            "american accent", "british accent", "australian accent", "canadian accent", 
            "indian accent", "japanese accent", "korean accent", "portuguese accent", "russian accent", "chinese accent",
            "东北话", "中年", "中音调", "云南话", "低音调", "儿童", "四川话", "女", "宁夏话", "少年", "极低音调", 
            "极高音调", "桂林话", "河南话", "济南话", "甘肃话", "男", "石家庄话", "老年", "耳语", "贵州话", "陕西话", 
            "青岛话", "青年", "高音调"
        }
        
        import re
        items = re.split(r"\s*[,，]\s*", raw_instruct.strip())
        filtered = [x.strip() for x in items if x.strip().lower() in valid_items or x.strip() in valid_items]
        return ", ".join(filtered)

    def _resolve_clean_instruct(self, voice_id: str) -> str:
        """Resolve database instruct for this voice, sanitizing it to only keep valid ones.
        Returns a single space " " as a fallback to bypass default database instruct lookup
        if the database instruct is invalid or empty, allowing pure cloning style.
        """
        try:
            resp = httpx.get(f"{self.api_base}/profiles/{voice_id}", timeout=1.0)
            if resp.status_code == 200:
                p = resp.json()
                raw_instruct = p.get("instruct") or ""
                sanitized = self._sanitize_instruct(raw_instruct)
                if sanitized:
                    return sanitized
        except Exception:
            pass
        return " " # Space acts as truthy string to avoid DB fallback, but resolves to None in model preprocessing!

    def synthesize(self, text: str, voice: str, output_path: str, cfg_scale: float = 2.0) -> dict:
        """Synchronously query the local OmniVoice generate endpoint to synthesize speech."""
        start_time = time.time()
        
        payload = {
            "text": text,
            "profile_id": voice,
            "instruct": self._resolve_clean_instruct(voice),
            "guidance_scale": cfg_scale,
            "num_step": 16,
            "effect_preset": "broadcast"
        }
        
        try:
            resp = httpx.post(f"{self.api_base}/generate", data=payload, timeout=60.0)
            if resp.status_code != 200:
                raise Exception(f"OmniVoice synthesis error {resp.status_code}: {resp.text}")
            
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(resp.content)
                
            gen_time = time.time() - start_time
            file_size = os.path.getsize(output_path)
            
            # Retrieve generated audio duration from headers if provided
            duration = float(resp.headers.get("X-Audio-Duration", 0.0))
            if duration == 0.0:
                # WAV PCM fallback (assuming 24kHz 16-bit mono -> 48KB/sec)
                duration = file_size / 48000.0
                
            return {
                "status": "success",
                "output": output_path,
                "duration": round(duration, 2),
                "generation_time": round(gen_time, 2),
                "rtf": round(gen_time / max(duration, 0.1), 2),
                "voice": voice,
                "engine": "omnivoice",
                "size": file_size
            }
        except Exception as e:
            logger.error(f"OmniVoice synthesis error: {e}")
            return {"status": "error", "message": str(e)}

    async def synthesize_async(self, text: str, voice: str, output_path: str, cfg_scale: float = 2.0) -> dict:
        """Asynchronously query the local OmniVoice generate endpoint to synthesize speech."""
        start_time = time.time()
        
        payload = {
            "text": text,
            "profile_id": voice,
            "instruct": self._resolve_clean_instruct(voice),
            "guidance_scale": cfg_scale,
            "num_step": 16,
            "effect_preset": "broadcast"
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{self.api_base}/generate", data=payload)
                if resp.status_code != 200:
                    raise Exception(f"OmniVoice synthesis error {resp.status_code}: {resp.text}")
                
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                # Write file in threadpool to avoid blocking event loop
                import asyncio
                def _write_file():
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                await asyncio.to_thread(_write_file)
                
            gen_time = time.time() - start_time
            file_size = os.path.getsize(output_path)
            
            duration = float(resp.headers.get("X-Audio-Duration", 0.0))
            if duration == 0.0:
                duration = file_size / 48000.0
                
            return {
                "status": "success",
                "output": output_path,
                "duration": round(duration, 2),
                "generation_time": round(gen_time, 2),
                "rtf": round(gen_time / max(duration, 0.1), 2),
                "voice": voice,
                "engine": "omnivoice",
                "size": file_size
            }
        except Exception as e:
            logger.error(f"OmniVoice synthesis error: {e}")
            return {"status": "error", "message": str(e)}
