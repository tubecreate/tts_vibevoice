---
name: Text-to-Speech (TTS)
description: Chuyển văn bản thành giọng nói tự nhiên bằng VibeVoice AI, hỗ trợ 25 giọng, streaming realtime
---

# Text-to-Speech (TTS) Skill

## Khả năng
- **VibeVoice Realtime**: AI TTS chất lượng cao, model 0.5B, streaming realtime
- **25 Voice Presets**: 6 English + 19 multilingual (DE, FR, IT, JP, KR, NL, PL, PT, ES)
- **Streaming**: Phát âm thanh ngay khi đang generate, latency ~200ms
- **Batch Mode**: Generate toàn bộ rồi save WAV file
- **Long-form**: Hỗ trợ tạo audio lên đến ~10 phút

## Trigger Keywords
- "đọc text", "đọc văn bản", "text to speech", "tts"
- "tạo giọng nói", "tạo audio", "generate voice", "generate speech"
- "chuyển text thành giọng", "convert to speech"
- "narrate", "voiceover", "lồng tiếng"

## Cách sử dụng

### TTS cơ bản
```
Đọc text: "Xin chào, đây là TubeCreate"
```

### Chọn giọng
```
Đọc text bằng giọng Emma: "Hello, welcome to TubeCreate"
```

### TTS từ file
```
Đọc file script.txt bằng giọng Carter
```

## API Endpoints
- `POST /api/v1/tts/synthesize` — Text → WAV (batch mode)
- `GET /api/v1/tts/voices` — Danh sách voice presets
- `GET /api/v1/tts/status/{task_id}` — Task progress
- `GET /api/v1/tts/status/model` — Model loading status
- `WS /api/v1/tts/stream` — WebSocket streaming TTS

## Yêu cầu
- GPU NVIDIA + CUDA (khuyến nghị) hoặc CPU (chậm hơn)
- ~1GB VRAM cho model
- ~1GB disk cho model download (tự động từ HuggingFace)
