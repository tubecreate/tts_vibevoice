"""
OmniVoice TTS Engine — Local Studio API integration wrapper for TubeCLI.
Queries the local OmniVoice Studio server (port 3900) to discover profiles and run TTS.
"""
import os
import re
import time
import httpx
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("TTS.OmniVoice")

# 127.0.0.1 chứ không `localhost`: OmniVoice Studio chỉ nghe IPv4, còn Windows hay thử ::1 trước.
API_BASE = "http://127.0.0.1:3900"
# Lượt đầu sau khi mở app phải nạp model (~17 s đo 24/9/2026); câu dài đọc lâu hơn 60 s cũ.
GENERATE_TIMEOUT_S = 300.0

_VI = re.compile("[ăâđêôơưĂÂĐÊÔƠƯ]|[\u1ea0-\u1ef9]")
_KANA = re.compile("[\u3040-\u30ff]")
_HANGUL = re.compile("[\uac00-\ud7af]")
_HAN = re.compile("[\u4e00-\u9fff]")


def language_of(text: str) -> Optional[str]:
    """Ngôn ngữ để báo cho OmniVoice, đoán từ CHỮ. Đo 24/9/2026: cùng giọng thuy trang 6, có «Vietnamese» thì đọc
    đúng 100% chữ; để model tự đoán thì giọng clone dễ đọc tiếng Việt bằng thanh điệu của tiếng Anh. Không chắc → None."""
    t = str(text or "")
    if _VI.search(t):
        return "Vietnamese"
    if _KANA.search(t):
        return "Japanese"
    if _HANGUL.search(t):
        return "Korean"
    if _HAN.search(t):
        return "Chinese"
    return None


# Nhịp đọc tiếng Việt: ~3,2 tiếng/giây + quãng nghỉ ở dấu câu. Để OmniVoice tự đặt độ dài thì nó đặt quá ngắn và
# phải đọc DỒN — đo 24/9/2026 (thuy trang 6): 30 tiếng trong 6,7 s ⇒ nuốt cả «Không gây mê toàn thân», «cổ tay,
# hoặc» thành «quặc». Cùng giọng, chỉ định 16 s cho 52 tiếng ⇒ Gemini chép lại đúng 0 lỗi, «tốc độ vừa phải».
VI_SYLLABLES_PER_S = 3.2


def vi_duration(text: str) -> float:
    t = str(text or "")
    syll = len(t.split())
    stops = len(re.findall(r"[.!?…]+", t))
    commas = len(re.findall(r"[,;:]", t))
    return round(max(1.0, syll / VI_SYLLABLES_PER_S + 0.25 * stops + 0.12 * commas), 2)


# MỖI CÂU một lượt đọc. Bản đầu gộp tới 24 tiếng một lượt: đoạn thử 30 tiếng thì đúng, nhưng bản dựng thật tập 540
# vẫn NUỐT câu ngắn nằm giữa lượt — «Nhiều người hỏi tôi:», «“Ngực có tức không?”» (Gemini nghe lại 25 s). Câu đơn
# lẻ đọc đúng 100%. Tách cả sau dấu hai chấm và sau dấu câu có ngoặc kép đóng; mảnh < 3 tiếng gộp vào câu sau
# (OmniVoice đọc 1–2 tiếng trơ trọi thì méo).
_SENT_END = re.compile(r"(?<=[.!?…:])\s+|(?<=[.!?…][”\"’»)])\s+")
MIN_CHUNK_SYLLABLES = 3
CHUNK_GAP_S = 0.22


def split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in _SENT_END.split(str(text or "").strip()) if p and p.strip()]
    out: List[str] = []
    carry = ""
    for p in parts:
        p = (carry + " " + p).strip() if carry else p
        if len(p.split()) < MIN_CHUNK_SYLLABLES:
            carry = p
            continue
        out.append(p)
        carry = ""
    if carry:
        if out:
            out[-1] = out[-1] + " " + carry
        else:
            out.append(carry)
    return out or [str(text or "")]


def join_wavs(blobs: List[bytes], gap_s: float = CHUNK_GAP_S) -> bytes:
    """Ghép các WAV cùng khuôn (OmniVoice trả 24 kHz, 16-bit, mono), chèn khoảng lặng giữa các cụm."""
    import io
    import wave
    params = None
    frames = []
    for b in blobs:
        with wave.open(io.BytesIO(b)) as w:
            p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if params is None:
                params = p
            elif p != params:
                raise ValueError(f"OmniVoice returned WAV chunks in different formats: {params} vs {p}")
            frames.append(w.readframes(w.getnframes()))
    ch, sw, sr = params
    gap = bytes(int(sr * gap_s) * sw * ch)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        w.writeframes(gap.join(frames))
    return out.getvalue()


# ── NẮN NHỊP ──────────────────────────────────────────────────────────────────
# User 24/9/2026: «voice nghe được mà nó giật giật». Đo tập 540: mỗi lượt OmniVoice có ~0,2 s lặng đầu + 0,1–0,2 s lặng
# cuối, cộng khoảng chèn giữa lượt ⇒ giữa hai câu TRONG một nhịp lặng 0,6–0,9 s, giữa hai NHỊP chỉ ~0,35 s, còn tiếng
# thì đọc nhanh — nghe thành «nói dồn, dừng lâu, nói dồn». Và tiếng vào đột ngột ở đầu câu.
# Nắn: mọi khoảng lặng ≥ 0,25 s giữa tiếng → 0,30 s; đầu file 0,10 s, cuối 0,25 s (giữa hai nhịp ≈ 0,35 s, bằng giữa
# hai câu); ngắt ngắn (dấu phẩy) GIỮ nguyên; vuốt 12 ms ở mọi chỗ cắt. Chạy được cả trên file đã đọc (tighten_file).
SIL_RMS = 0.01          # dưới mức này (≈ −40 dBFS) một khung 10 ms là lặng
LONG_GAP_S = 0.25
PAUSE_S = 0.30
EDGE_LEAD_S = 0.10
EDGE_TAIL_S = 0.25
FADE_S = 0.012


def tighten_pcm(samples, sr: int):
    """array('h') mono → array('h') đã nắn nhịp. Không thấy tiếng nào thì trả nguyên."""
    from array import array
    fr = max(1, int(sr * 0.01))
    n = len(samples) // fr
    lim = (SIL_RMS * 32768) ** 2 * fr
    voiced = []
    for i in range(n):
        seg = samples[i * fr:(i + 1) * fr]
        voiced.append(sum(v * v for v in seg) > lim)
    if not any(voiced):
        return samples
    # các đoạn tiếng [đầu, cuối) theo khung; khoảng lặng ngắn (< LONG_GAP_S) nằm TRONG đoạn
    gap_frames = int(LONG_GAP_S / 0.01)
    runs, i = [], 0
    while i < n:
        if not voiced[i]:
            i += 1
            continue
        j = i
        while j < n:
            if voiced[j]:
                j += 1
                continue
            k = j
            while k < n and not voiced[k]:
                k += 1
            if k - j >= gap_frames or k >= n:
                break
            j = k
        runs.append((i, j))
        i = j
    fade = max(1, int(sr * FADE_S))
    margin = fr * 2                                     # giữ 20 ms quanh tiếng — không xén phụ âm cuối

    def cut(a: int, b: int):
        piece = array("h", samples[max(0, a):min(len(samples), b)])
        m = len(piece)
        for t in range(min(fade, m)):                    # vuốt đầu / cuối
            piece[t] = int(piece[t] * t / fade)
            piece[m - 1 - t] = int(piece[m - 1 - t] * t / fade)
        return piece

    out = array("h", bytes(2 * int(sr * EDGE_LEAD_S)))
    for r, (a, b) in enumerate(runs):
        if r:
            out.extend(array("h", bytes(2 * int(sr * PAUSE_S))))
        out.extend(cut(a * fr - margin, b * fr + margin))
    out.extend(array("h", bytes(2 * int(sr * EDGE_TAIL_S))))
    return out


def tighten_wav_bytes(blob: bytes) -> bytes:
    """WAV 16-bit mono → WAV đã nắn nhịp. Khuôn khác (stereo, 8/24-bit) thì trả nguyên — không đoán."""
    import io
    import wave
    from array import array
    with wave.open(io.BytesIO(blob)) as w:
        ch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if ch != 1 or sw != 2:
        return blob
    samples = array("h")
    samples.frombytes(raw)
    fixed = tighten_pcm(samples, sr)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(fixed.tobytes())
    return out.getvalue()


def tighten_file(path: str) -> bool:
    """Nắn nhịp một file .wav tại chỗ (ghi tạm rồi thay). Trả True nếu đã ghi."""
    try:
        with open(path, "rb") as f:
            blob = f.read()
        fixed = tighten_wav_bytes(blob)
        if fixed is blob:
            return False
        tmp = path + ".tight"
        with open(tmp, "wb") as f:
            f.write(fixed)
        os.replace(tmp, path)
        return True
    except Exception as e:      # noqa: BLE001 — nắn hỏng thì giữ file gốc
        logger.warning(f"OmniVoice tighten {path}: {e}")
        return False


def get_engine():
    return OmniVoiceTTSEngine()

class OmniVoiceTTSEngine:
    def __init__(self):
        self.api_base = API_BASE

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

    def _payload(self, text: str, voice: str, cfg_scale: float, language: Optional[str]) -> dict:
        payload = {
            "text": text,
            "profile_id": voice,
            "instruct": self._resolve_clean_instruct(voice),
            "guidance_scale": cfg_scale,
            "num_step": 16,
            "effect_preset": "broadcast"
        }
        lang = language or language_of(text)
        if lang:
            payload["language"] = lang
        if lang == "Vietnamese":
            payload["duration"] = vi_duration(text)
        return payload

    def synthesize(self, text: str, voice: str, output_path: str, cfg_scale: float = 2.0,
                   language: Optional[str] = None) -> dict:
        """Synchronously query the local OmniVoice generate endpoint to synthesize speech.

        Tiếng Việt đọc theo CỤM CÂU ngắn rồi ghép: một lượt 52 tiếng ra 10–14 s dù xin 18 s và rơi mất nguyên câu
        (đo 24/9/2026, thuy trang 6); lượt 30 tiếng giữ đúng thời lượng xin và đúng từng chữ."""
        start_time = time.time()
        lang = language or language_of(text)
        chunks = split_sentences(text) if lang == "Vietnamese" else [text]

        try:
            blobs, duration = [], 0.0
            for chunk in chunks:
                resp = httpx.post(f"{self.api_base}/generate", data=self._payload(chunk, voice, cfg_scale, lang),
                                  timeout=GENERATE_TIMEOUT_S)
                if resp.status_code != 200:
                    raise Exception(f"OmniVoice synthesis error {resp.status_code}: {resp.text}")
                blobs.append(resp.content)
                duration += float(resp.headers.get("X-Audio-Duration", 0.0) or 0.0)

            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            data = blobs[0] if len(blobs) == 1 else join_wavs(blobs)
            try:
                data = tighten_wav_bytes(data)      # nhịp ngắt đều: 0,3 s giữa câu, đầu/cuối cố định
            except Exception as e:      # noqa: BLE001 — nắn hỏng thì giữ bản ghép thô
                logger.warning(f"OmniVoice tighten: {e}")
            with open(output_path, "wb") as f:
                f.write(data)

            gen_time = time.time() - start_time
            file_size = os.path.getsize(output_path)
            duration = max(0.0, (file_size - 44) / 48000.0)     # 24 kHz 16-bit mono sau khi nắn
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

    async def synthesize_async(self, text: str, voice: str, output_path: str, cfg_scale: float = 2.0,
                               language: Optional[str] = None) -> dict:
        """Asynchronously query the local OmniVoice generate endpoint to synthesize speech.
        Cùng đường với bản đồng bộ (tách cụm câu + ghép), chạy trong luồng riêng để không chặn vòng sự kiện."""
        import asyncio
        return await asyncio.to_thread(self.synthesize, text, voice, output_path, cfg_scale, language)
