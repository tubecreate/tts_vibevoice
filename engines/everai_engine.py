"""
EverAI TTS Engine — Cloud API wrapper for TubeCLI.
Supports high-quality Vietnamese and International voices.
"""
import os
import time
import httpx
import logging
from typing import Optional, Dict

logger = logging.getLogger("TTS.EverAI")

# List of voices provided by the user
EVERAI_VOICES = [
    {"id": "vi_male_lehoang_mb", "name": "Lê Hoàng (EverAI)", "language": "vi", "gender": "male", "desc": "Miền Bắc"},
    {"id": "vi_female_thuytrang_mb", "name": "Thùy Trang (EverAI)", "language": "vi", "gender": "female", "desc": "Miền Bắc"},
    {"id": "vi_male_minhtriet_mb", "name": "Minh Triết (EverAI)", "language": "vi", "gender": "male", "desc": "Miền Bắc"},
    {"id": "vi_male_echo_default", "name": "Echo (EverAI)", "language": "vi", "gender": "male", "desc": "Giọng Mỹ (Tiếng Việt)"},
    {"id": "vi_female_nova_default", "name": "Nova (EverAI)", "language": "vi", "gender": "female", "desc": "Giọng Mỹ (Tiếng Việt)"},
    {"id": "vi_male_onyx_default", "name": "Onyx (EverAI)", "language": "vi", "gender": "male", "desc": "Giọng Mỹ (Tiếng Việt)"},
    {"id": "vi_female_hacuc_mb", "name": "Hạ Cúc (EverAI)", "language": "vi", "gender": "female", "desc": "Miền Bắc"},
    {"id": "vi_male_ductrong_mb", "name": "Đức Trọng (EverAI)", "language": "vi", "gender": "male", "desc": "Miền Bắc"},
    {"id": "vi_female_kieunhi_mn", "name": "Kiều Nhi (EverAI)", "language": "vi", "gender": "female", "desc": "Miền Nam"},
    {"id": "vi_female_huyenanh_mb", "name": "Huyền Anh (EverAI)", "language": "vi", "gender": "female", "desc": "Miền Bắc"},
    {"id": "vi_female_halinh_mb", "name": "Hà Linh (EverAI)", "language": "vi", "gender": "female", "desc": "Miền Bắc"},
    {"id": "vi_female_hoaian_mb", "name": "Hoài An (EverAI)", "language": "vi", "gender": "female", "desc": "Miền Bắc"},
    {"id": "vi_female_khanhhuyentvc_mb", "name": "Khánh Huyền (EverAI)", "language": "vi", "gender": "female", "desc": "Miền Bắc"},
    
    # English
    {"id": "en_male_echo_default", "name": "Echo (EverAI EN)", "language": "en", "gender": "male", "desc": "American"},
    {"id": "en_female_nova_default", "name": "Nova (EverAI EN)", "language": "en", "gender": "female", "desc": "American"},
    {"id": "en_male_onyx_default", "name": "Onyx (EverAI EN)", "language": "en", "gender": "male", "desc": "American"},
    {"id": "en_female_emily_us", "name": "Emily (EverAI EN)", "language": "en", "gender": "female", "desc": "American"},
    {"id": "en_female_alice_default", "name": "Alice (EverAI EN)", "language": "en", "gender": "female", "desc": "American"},
    {"id": "en_female_jessica_au", "name": "Jessica (EverAI EN)", "language": "en", "gender": "female", "desc": "Australian"},
    {"id": "en_female_elara_br", "name": "Elara (EverAI EN)", "language": "en", "gender": "female", "desc": "British"},
    {"id": "en_female_meera_indian", "name": "Meera (EverAI EN)", "language": "en", "gender": "female", "desc": "Indian"},
    
    # Other languages
    {"id": "zh_female_liu-ying_default", "name": "Liu Ying (EverAI ZH)", "language": "zh", "gender": "female", "desc": "Chinese"},
    {"id": "jp_female_yuki_default", "name": "Yuki (EverAI JP)", "language": "ja", "gender": "female", "desc": "Japanese"},
    {"id": "kr_female_seo-yeon_default", "name": "Seo Yeon (EverAI KR)", "language": "ko", "gender": "female", "desc": "Korean"},
    {"id": "fr_female_camille_default", "name": "Camille (EverAI FR)", "language": "fr", "gender": "female", "desc": "French"},
    {"id": "es_female_elana_default", "name": "Elena (EverAI ES)", "language": "es", "gender": "female", "desc": "Spanish"},
]

def get_engine():
    return EverAITTSEngine()

class EverAITTSEngine:
    def __init__(self):
        self.api_base = "https://everai.vn/api/v1/tts"
        
    def _get_api_key(self) -> str:
        """Fetch API Key dynamically from KeyManager."""
        try:
            from tubecli.extensions.cloud_api.extension import key_manager
            api_key = key_manager.get_active_key("everai")
            if not api_key:
                raise ValueError("No API Key configured for EverAI")
            return api_key
        except ImportError:
            raise ValueError("KeyManager not found")

    def list_voices(self) -> list:
        return EVERAI_VOICES

    def get_status(self) -> dict:
        return {"loaded": True, "loading": False, "error": None, "device": "cloud", "voices_count": len(EVERAI_VOICES)}

    async def synthesize_async(self, text: str, voice: str, output_path: str, speed_rate: float = 1.0) -> dict:
        api_key = self._get_api_key()
        start_time = time.time()
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        payload = {
            "response_type": "indirect",
            "callback_url": "https://tubecli.local/callback", # Dummy callback, we will poll
            "input_text": text,
            "voice_code": voice,
            "audio_type": "mp3",
            "bitrate": 128,
            "speed_rate": speed_rate,
            "pitch_rate": 1.0
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Start generation
            resp = await client.post(self.api_base, headers=headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"EverAI API Error {resp.status_code}: {resp.text}")
                
            data = resp.json()
            if str(data.get("status")) != "1" or not data.get("result", {}).get("request_id"):
                raise Exception(f"EverAI Generation Failed: {data}")
                
            request_id = data["result"]["request_id"]
            logger.info(f"[EverAI] Task started: {request_id}")
            
            # 2. Poll for completion
            audio_link = None
            max_attempts = 150 # 5 minutes timeout
            
            for attempt in range(max_attempts):
                import asyncio
                await asyncio.sleep(2.0)
                
                poll_resp = await client.get(f"{self.api_base}/{request_id}", headers=headers)
                if poll_resp.status_code == 200:
                    poll_data = poll_resp.json()
                    status = poll_data.get("result", {}).get("status")
                    if status == "done":
                        audio_link = poll_data["result"]["audio_link"]
                        break
                    elif status == "error" or status == "failed":
                        raise Exception(f"EverAI task failed: {poll_data}")
            else:
                raise Exception(f"EverAI Task timed out after {max_attempts * 2}s")
                
            if not audio_link:
                raise Exception("EverAI returned success but no audio_link")
                
            # 3. Download audio
            logger.info(f"[EverAI] Downloading audio from {audio_link}")
            async with client.stream("GET", audio_link) as stream_resp:
                if stream_resp.status_code != 200:
                    raise Exception(f"Failed to download audio: {stream_resp.status_code}")
                
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                with open(output_path, "wb") as f:
                    async for chunk in stream_resp.aiter_bytes():
                        f.write(chunk)
                        
        gen_time = time.time() - start_time
        file_size = os.path.getsize(output_path)
        
        return {
            "status": "success",
            "output": output_path,
            "generation_time": round(gen_time, 2),
            "voice": voice,
            "engine": "everai",
            "size": file_size
        }
