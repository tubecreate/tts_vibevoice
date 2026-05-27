# 🔊 TTS VibeVoice — TubeCLI Extension

AI Text-to-Speech extension for [TubeCLI](https://github.com/tubecreate/tubecli) supporting multiple providers.

## ✨ Features

- **Edge-TTS** — Microsoft Edge online TTS, 100+ voices, 60+ languages (including Vietnamese 🇻🇳)
- **VibeVoice AI** — Offline AI TTS with voice cloning
- **Viterbox** — Vietnamese offline TTS
- **EverAI** — Cloud TTS with API key
- **Gemini TTS** — Google Gemini cloud TTS
- **OmniVoice** — Local OmniVoice Studio integration
- **SRT Mode** — Generate voice tracks from subtitle files
- **Voice Cloning** — Clone any voice from audio sample
- **Provider Settings** — Enable/disable providers individually, install guides

## 📦 Installation

Install via TubeCLI Marketplace or manually:

```bash
# Clone into extensions_external
git clone https://github.com/tubecreate/tts_vibevoice.git \
  ~/.tubecli/data/extensions_external/tts_vibevoice
```

## 🚀 Requirements

- TubeCLI >= 0.3.0
- Python 3.10+
- `edge-tts` (auto-installed)
- `python-multipart` (auto-installed)

### Optional (heavy) dependencies

For VibeVoice AI and Viterbox engines:

```bash
pip install -r requirements_vibevoice.txt
```

## 🎛️ Provider Settings

Click on any provider badge in the header to:
- **Enable/Disable** the provider
- View installation status
- Access install guides and GitHub links
- Manage API keys (cloud providers)

## 📁 Structure

```
tts_vibevoice/
├── extension.py          # Extension entry point
├── tts_routes.py         # FastAPI routes
├── tubecli-extension.json
├── engines/              # TTS engine implementations
├── static/
│   ├── tts.html
│   ├── tts.js
│   ├── tts.css
│   └── i18n/             # Translations (vi, en, zh)
└── outputs/              # Generated audio files
```

## 📜 License

MIT — © TubeCreate
