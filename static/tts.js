/**
 * TTS VibeVoice — Frontend JavaScript
 * Supports: Edge-TTS (online, Vietnamese) + VibeVoice (offline AI)
 */

// ── State ──
let voices = [];
let filteredVoices = [];
let engineStatus = {};
let currentTaskId = null;
let pollInterval = null;
let wsConnection = null;
let streamAudioContext = null;
let streamBuffers = [];
let streamTotalSamples = 0;
let isGenerating = false;
let i18n = {};
let currentInputMode = 'text';
const SAMPLE_RATE = 24000;
const API_BASE = '/api/v1/tts';

const ENGINE_LABELS = {
    edge: '⚡ Edge-TTS',
    vibevoice: '🧠 VibeVoice',
    viterbox: '🇻🇳 Viterbox',
    everai: '☁️ EverAI',
    gemini: '✨ Gemini',
    omnivoice: '🎙️ OmniVoice',
};

// ── Init ──
document.addEventListener('DOMContentLoaded', async () => {
    let savedLang = localStorage.getItem('tts_lang');
    try {
        const resp = await fetch('/api/v1/settings/language');
        if (resp.ok) {
            const data = await resp.json();
            if (data && data.language) {
                savedLang = data.language;
                localStorage.setItem('tts_lang', savedLang);
            }
        }
    } catch(e) {}
    savedLang = savedLang || 'en';
    
    document.getElementById('langSelect').value = savedLang;
    await loadI18n(savedLang);

    document.getElementById('textInput').addEventListener('input', updateCharCount);
    document.getElementById('srtInput').addEventListener('input', parseSRTPreview);
    document.getElementById('cfgSlider').addEventListener('input', (e) => {
        document.getElementById('cfgValue').textContent = e.target.value;
    });
    document.getElementById('voiceSelect').addEventListener('change', () => {
        updateVoicePreview();
        // Save last selected voice
        const sel = document.getElementById('voiceSelect');
        if (sel.value) localStorage.setItem('tts_last_voice', sel.value);
    });
    document.getElementById('langSelect').addEventListener('change', async (e) => {
        const newLang = e.target.value;
        localStorage.setItem('tts_lang', newLang);
        await loadI18n(newLang);
        if (voices.length > 0) {
            applyVoiceFilters();
        }
    });

    // SRT drag-and-drop
    const dropZone = document.getElementById('srtDropZone');
    if (dropZone) {
        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
        dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault(); dropZone.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file) readSRTFile(file);
        });
    }

    await loadVoices();
    checkModelStatus();
    setInterval(checkModelStatus, 15000);
});

// ── i18n ──
async function loadI18n(lang) {
    try {
        const resp = await fetch(`/tts-vibevoice-static/i18n/${lang}.json`);
        if (resp.ok) {
            i18n = await resp.json();
            applyI18n();
        }
    } catch (e) {
        console.warn('i18n load failed:', e);
    }
}

function t(key) { return i18n[key] || key; }

function applyI18n() {
    const mappings = {
        'page-title': 'title', 'header-subtitle': 'subtitle',
        'label-input': 'input_label', 'label-voice': 'voice_label',
        'label-cfg': 'cfg_label', 'btn-generate-text': 'btn_generate',
        'btn-stream-text': 'btn_stream', 'btn-stop-text': 'btn_stop',
        'btn-load-text': 'btn_load_model', 'btn-download-text': 'btn_download',
        'label-output': 'output_label', 'label-log': 'log_label',
    };
    for (const [elemId, key] of Object.entries(mappings)) {
        const el = document.getElementById(elemId);
        if (el && i18n[key]) el.textContent = i18n[key];
    }
    const ta = document.getElementById('textInput');
    if (ta && i18n.placeholder) ta.placeholder = i18n.placeholder;
}

// ── Voices ──
async function loadVoices() {
    try {
        // Load voices and engine status in parallel
        const [voicesResp, statusResp] = await Promise.all([
            fetch(`${API_BASE}/voices`),
            fetch(`${API_BASE}/engine-status`).catch(() => null),
        ]);
        const data = await voicesResp.json();
        if (data.success && data.voices) {
            voices = data.voices;
            // Rebuild using only enabled providers
            const enabledVoices = voices.filter(v => getProviderEnabled(v.engine || 'vibevoice'));
            buildFilterOptions(enabledVoices);
            // Restore saved filter selections
            const savedProv = localStorage.getItem('tts_filter_provider');
            const savedLangF = localStorage.getItem('tts_filter_language');
            if (savedProv) document.getElementById('filterProvider').value = savedProv;
            if (savedLangF) document.getElementById('filterLanguage').value = savedLangF;
            applyVoiceFiltersFiltered(enabledVoices);
            log('info', `Loaded ${voices.length} voice presets (${enabledVoices.length} from enabled providers)`);
        }
        if (statusResp && statusResp.ok) {
            const statusData = await statusResp.json();
            if (statusData.success) {
                engineStatus = statusData.engines || {};
                renderProviderStatus();
            }
        }
    } catch (e) {
        log('error', `Failed to load voices: ${e.message}`);
    }
}

function buildFilterOptions(voiceList) {
    const providerSet = new Map();
    const langSet = new Map();

    for (const v of voiceList) {
        const eng = v.engine || 'vibevoice';
        if (!providerSet.has(eng)) providerSet.set(eng, ENGINE_LABELS[eng] || eng);
        const langKey = v.language || v.locale?.substring(0, 2) || '?';
        const langName = v.language_name || langKey;
        if (!langSet.has(langKey)) langSet.set(langKey, langName);
    }

    // Provider filter
    const provSel = document.getElementById('filterProvider');
    provSel.innerHTML = '<option value="all">All Providers</option>';
    for (const [key, label] of providerSet) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = label;
        provSel.appendChild(opt);
    }

    // Language filter — sort Vietnamese first
    const langSel = document.getElementById('filterLanguage');
    langSel.innerHTML = '<option value="all">All Languages</option>';
    const sortedLangs = [...langSet.entries()].sort((a, b) => {
        if (a[0] === 'vi') return -1;
        if (b[0] === 'vi') return 1;
        return a[1].localeCompare(b[1]);
    });
    for (const [key, name] of sortedLangs) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = name;
        langSel.appendChild(opt);
    }
}

function applyVoiceFilters() {
    const provFilter = document.getElementById('filterProvider').value;
    const langFilter = document.getElementById('filterLanguage').value;

    // Persist filter choices
    localStorage.setItem('tts_filter_provider', provFilter);
    localStorage.setItem('tts_filter_language', langFilter);

    filteredVoices = voices.filter(v => {
        const eng = v.engine || 'vibevoice';
        const lang = v.language || v.locale?.substring(0, 2) || '?';
        if (provFilter !== 'all' && eng !== provFilter) return false;
        if (langFilter !== 'all' && lang !== langFilter) return false;
        return true;
    });

    populateVoiceSelect(filteredVoices);
}

function populateVoiceSelect(voiceList) {
    const select = document.getElementById('voiceSelect');
    const prevValue = select.value;
    select.innerHTML = '';

    if (voiceList.length === 0) {
        const opt = document.createElement('option');
        opt.textContent = 'No voices match filters';
        opt.disabled = true;
        opt.selected = true;
        select.appendChild(opt);
        return;
    }

    // Group by engine then language
    const engineGroups = {};
    for (const v of voiceList) {
        const engine = v.engine || 'vibevoice';
        const langName = v.language_name || v.language || v.locale || '?';
        if (!engineGroups[engine]) engineGroups[engine] = {};
        if (!engineGroups[engine][langName]) engineGroups[engine][langName] = [];
        engineGroups[engine][langName].push(v);
    }

    // Engine display order
    const engineOrder = ['edge', 'everai', 'gemini', 'vibevoice', 'viterbox', 'omnivoice'];
    const sortedEngines = Object.keys(engineGroups).sort((a, b) => {
        return (engineOrder.indexOf(a) === -1 ? 99 : engineOrder.indexOf(a)) -
               (engineOrder.indexOf(b) === -1 ? 99 : engineOrder.indexOf(b));
    });

    for (const engineKey of sortedEngines) {
        const langs = engineGroups[engineKey];
        const label = ENGINE_LABELS[engineKey] || engineKey;
        const available = engineStatus[engineKey]?.available !== false;
        const statusTag = available ? '' : ' ⚠️ Not Installed';

        // Sort languages: Vietnamese first
        const sortedLangs = Object.keys(langs).sort((a, b) => {
            if (a.includes('Việt')) return -1;
            if (b.includes('Việt')) return 1;
            return a.localeCompare(b);
        });

        for (const lang of sortedLangs) {
            const items = langs[lang];
            const optgroup = document.createElement('optgroup');
            optgroup.label = `${label} — ${lang}${statusTag}`;
            for (const v of items) {
                const opt = document.createElement('option');
                opt.value = v.id;
                opt.dataset.engine = engineKey;
                const genderIcon = v.gender === 'male' ? '♂️' : v.gender === 'female' ? '♀️' : '🎤';
                opt.textContent = `${genderIcon} ${v.name}`;
                optgroup.appendChild(opt);
            }
            select.appendChild(optgroup);
        }
    }

    // Priority: 1) localStorage saved voice, 2) previous in-session value, 3) auto by language
    const savedVoice = localStorage.getItem('tts_last_voice');
    if (savedVoice && voiceList.find(v => v.id === savedVoice)) {
        select.value = savedVoice;
    } else if (prevValue && voiceList.find(v => v.id === prevValue)) {
        select.value = prevValue;
    } else {
        const currentLang = document.getElementById('langSelect').value || 'en';
        let prefix = 'en-US';
        if (currentLang === 'vi') prefix = 'vi-VN';
        else if (currentLang === 'zh') prefix = 'zh-CN';
        const defaultVoice = voiceList.find(v => v.locale && v.locale.startsWith(prefix));
        if (defaultVoice) select.value = defaultVoice.id;
        else if (voiceList.length > 0) select.value = voiceList[0].id;
    }

    updateVoicePreview();
    updateEngineControls();
}

function getSelectedEngine() {
    const select = document.getElementById('voiceSelect');
    const opt = select.selectedOptions[0];
    if (!opt) return 'edge';
    if (opt.dataset.engine) return opt.dataset.engine;
    const voice = voices.find(v => v.id === select.value);
    return voice?.engine || 'edge';
}

function updateEngineControls() {
    const engine = getSelectedEngine();
    const cfgGroup = document.getElementById('cfgGroup');
    const btnStream = document.getElementById('btnStream');
    const btnLoadModel = document.getElementById('btnLoadModel');

    if (engine === 'vibevoice') {
        if (cfgGroup) cfgGroup.style.display = '';
        if (btnStream) btnStream.style.display = 'inline-flex';
        if (btnLoadModel) btnLoadModel.style.display = 'inline-flex';
    } else if (engine === 'viterbox') {
        if (cfgGroup) cfgGroup.style.display = '';
        if (btnStream) btnStream.style.display = 'none';
        if (btnLoadModel) btnLoadModel.style.display = 'inline-flex';
    } else if (engine === 'omnivoice') {
        if (cfgGroup) cfgGroup.style.display = '';
        if (btnStream) btnStream.style.display = 'none';
        if (btnLoadModel) btnLoadModel.style.display = 'none';
    } else {
        if (cfgGroup) cfgGroup.style.display = 'none';
        if (btnStream) btnStream.style.display = 'none';
        if (btnLoadModel) btnLoadModel.style.display = 'none';
    }

    // Show install banner for unavailable engines
    checkEngineAvailability(engine);
}

function checkEngineAvailability(engine) {
    const banner = document.getElementById('engineInstallBanner');
    if (!banner) return;
    const info = engineStatus[engine];
    if (info && info.available === false && (engine === 'vibevoice' || engine === 'viterbox' || engine === 'omnivoice')) {
        banner.style.display = 'flex';
        const installBtn = document.getElementById('btnInstallEngine');
        if (engine === 'omnivoice') {
            document.getElementById('installBannerText').textContent =
                `OmniVoice: OmniVoice Studio server is offline. Please make sure the OmniVoice server is running locally on port 3900.`;
            if (installBtn) installBtn.style.display = 'none';
        } else {
            document.getElementById('installBannerText').textContent =
                `${ENGINE_LABELS[engine] || engine}: ${info.details || 'Dependencies not installed'}`;
            if (installBtn) {
                installBtn.style.display = 'inline-flex';
                installBtn.dataset.engine = engine;
            }
        }
    } else {
        banner.style.display = 'none';
        const installBtn = document.getElementById('btnInstallEngine');
        if (installBtn) installBtn.style.display = 'inline-flex';
    }
}

async function installCurrentEngine() {
    const btn = document.getElementById('btnInstallEngine');
    const engine = btn.dataset.engine;
    if (!engine) return;

    document.getElementById('installBtnText').innerHTML = '<span class="spinner"></span> Installing...';
    btn.disabled = true;
    log('info', `📦 Installing ${ENGINE_LABELS[engine] || engine} dependencies...`);

    try {
        const resp = await fetch(`${API_BASE}/install-engine`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ engine }),
        });
        const data = await resp.json();

        if (data.already_installed) {
            log('success', `✅ ${engine} is already installed!`);
            engineStatus[engine] = { available: true, engine };
            document.getElementById('engineInstallBanner').style.display = 'none';
        } else if (data.task_id) {
            log('info', `⏳ ${data.message}`);
            // Poll install status
            const pollId = setInterval(async () => {
                try {
                    const sr = await fetch(`${API_BASE}/status/${data.task_id}`);
                    const sd = await sr.json();
                    if (sd.status === 'success') {
                        clearInterval(pollId);
                        log('success', `✅ ${engine} installed successfully!`);
                        engineStatus[engine] = { available: true, engine };
                        document.getElementById('engineInstallBanner').style.display = 'none';
                        btn.disabled = false;
                        document.getElementById('installBtnText').textContent = 'Install';
                    } else if (sd.status === 'error') {
                        clearInterval(pollId);
                        log('error', `❌ Install failed: ${sd.result?.message || 'Unknown error'}`);
                        btn.disabled = false;
                        document.getElementById('installBtnText').textContent = 'Retry';
                    }
                } catch (e) { /* continue polling */ }
            }, 3000);
        }
    } catch (e) {
        log('error', `Install error: ${e.message}`);
        btn.disabled = false;
        document.getElementById('installBtnText').textContent = 'Retry';
    }
}

function updateVoicePreview() {
    const voiceId = document.getElementById('voiceSelect').value;
    const voice = voices.find(v => v.id === voiceId);
    if (!voice) return;

    const avatar = voice.gender === 'female' ? '👩' : '👨';
    const engineBadge = ENGINE_LABELS[voice.engine] || voice.engine || '🎤';
    document.getElementById('previewAvatar').textContent = avatar;
    document.getElementById('previewName').textContent = voice.name || voice.id;
    document.getElementById('previewLang').textContent = `🌐 ${voice.language_name || voice.locale || voice.language}`;
    document.getElementById('previewGender').textContent = `${engineBadge} • ${voice.gender === 'male' ? '♂️ Male' : voice.gender === 'female' ? '♀️ Female' : '🎤'}`;

    updateEngineControls();
}

// ── Provider Status Badges ──
const CLOUD_PROVIDERS = ['everai', 'gemini'];
let _currentApiKeysProvider = '';

// Provider definitions — single source of truth
const ALL_PROVIDERS = [
    { key: 'edge',      icon: '⚡',  name: 'Edge',      type: 'online',  githubUrl: 'https://github.com/rany2/edge-tts', docs: 'https://github.com/rany2/edge-tts#readme' },
    { key: 'everai',    icon: '☁️',  name: 'EverAI',    type: 'cloud',   githubUrl: null, docs: 'https://everai.ai' },
    { key: 'gemini',    icon: '✨',  name: 'Gemini',    type: 'cloud',   githubUrl: null, docs: 'https://ai.google.dev' },
    { key: 'vibevoice', icon: '🧠',  name: 'VibeVoice', type: 'local',   githubUrl: 'https://github.com/huylenq/vibevoice', docs: null },
    { key: 'viterbox',  icon: '🇻🇳', name: 'Viterbox',  type: 'local',   githubUrl: 'https://github.com/trungle/viterbox', docs: null },
    { key: 'omnivoice', icon: '🎙️', name: 'OmniVoice', type: 'local',   githubUrl: null, docs: 'http://localhost:3900' },
];

// Persist enabled/disabled state per provider
function getProviderEnabled(key) {
    const stored = localStorage.getItem(`tts_prov_enabled_${key}`);
    return stored === null ? true : stored === '1'; // default: enabled
}
function setProviderEnabled(key, val) {
    localStorage.setItem(`tts_prov_enabled_${key}`, val ? '1' : '0');
}

function renderProviderStatus() {
    const bar = document.getElementById('providerStatusBar');
    if (!bar) return;

    bar.innerHTML = '';
    for (const eng of ALL_PROVIDERS) {
        const info = engineStatus[eng.key];
        const enabled = getProviderEnabled(eng.key);
        const isCloud = CLOUD_PROVIDERS.includes(eng.key);
        const available = enabled && (info ? info.available : (eng.key === 'edge' || isCloud));

        const badge = document.createElement('div');
        badge.className = `provider-badge clickable ${available ? 'available' : 'unavailable'}${!enabled ? ' disabled-provider' : ''}`;
        badge.title = !enabled
            ? `${eng.name} — Disabled (click to enable)`
            : `${eng.name} — Click to settings`;

        const keyIcon = isCloud ? '<span class="pbadge-key">🔑</span>' : '';
        badge.innerHTML = `<span class="pbadge-dot"></span><span class="pbadge-icon">${eng.icon}</span><span class="pbadge-name">${eng.name}</span>${keyIcon}`;

        badge.addEventListener('click', () => openProviderSettings(eng.key));
        bar.appendChild(badge);
    }
}

async function checkModelStatus() {
    try {
        const resp = await fetch(`${API_BASE}/engine-status`);
        if (resp.ok) {
            const data = await resp.json();
            if (data.success) {
                engineStatus = data.engines || {};
            }
        }
    } catch (e) { /* ignore */ }
    renderProviderStatus();
}

// ── Provider Settings Modal ──
let _currentProvKey = '';

const PROVIDER_INSTALL_GUIDES = {
    vibevoice: {
        title: 'VibeVoice chưa được cài đặt',
        desc: 'VibeVoice là engine TTS offline AI cần cài thêm dependencies Python.',
        steps: [
            { n: '1', text: 'Mở terminal trong thư mục extension' },
            { n: '2', text: 'Chạy: ', code: 'pip install vibevoice' },
            { n: '3', text: 'Hoặc dùng nút Install bên dưới ↓' },
        ],
        github: 'https://github.com/ggml-org/whisper.cpp',
        docs: null,
    },
    viterbox: {
        title: 'Viterbox chưa được cài đặt',
        desc: 'Viterbox là engine TTS tiếng Việt offline. Cần cài thêm model.',
        steps: [
            { n: '1', text: 'Mở terminal trong thư mục extension' },
            { n: '2', text: 'Chạy: ', code: 'pip install -r requirements_vibevoice.txt' },
            { n: '3', text: 'Tải model về thư mục ', code: 'engines/viterbox/' },
        ],
        github: 'https://github.com/NTT123/vietTTS',
        docs: null,
    },
    omnivoice: {
        title: 'OmniVoice chưa kết nối',
        desc: 'OmniVoice Studio server cần chạy local ở cổng 3900.',
        steps: [
            { n: '1', text: 'Tải và cài OmniVoice Studio' },
            { n: '2', text: 'Khởi động server trên cổng ', code: '3900' },
            { n: '3', text: 'Refresh trang này sau khi server sẵn sàng' },
        ],
        github: null,
        docs: 'http://localhost:3900',
    },
    edge: {
        title: 'Edge-TTS sẵn sàng',
        desc: 'Edge-TTS là online provider, không cần cài đặt thêm.',
        steps: [],
        github: 'https://github.com/rany2/edge-tts',
        docs: 'https://github.com/rany2/edge-tts#readme',
    },
    everai: {
        title: 'EverAI — Cloud Provider',
        desc: 'EverAI cần API Key để sử dụng.',
        steps: [
            { n: '1', text: 'Đăng ký tài khoản tại EverAI' },
            { n: '2', text: 'Lấy API Key từ dashboard' },
            { n: '3', text: 'Thêm key vào mục API Keys bên dưới' },
        ],
        github: null,
        docs: 'https://everai.ai',
    },
    gemini: {
        title: 'Gemini — Cloud Provider',
        desc: 'Gemini TTS cần Google API Key.',
        steps: [
            { n: '1', text: 'Truy cập Google AI Studio' },
            { n: '2', text: 'Tạo API Key' },
            { n: '3', text: 'Thêm key bên dưới để kích hoạt' },
        ],
        github: null,
        docs: 'https://aistudio.google.com',
    },
};

function openProviderSettings(provKey) {
    _currentProvKey = provKey;
    const prov = ALL_PROVIDERS.find(p => p.key === provKey);
    if (!prov) return;

    const info = engineStatus[provKey];
    const enabled = getProviderEnabled(provKey);
    const isCloud = CLOUD_PROVIDERS.includes(provKey);
    const isAvailable = info ? info.available : (provKey === 'edge' || isCloud);

    // Header
    document.getElementById('provSettingsIcon').textContent = prov.icon;
    document.getElementById('provSettingsTitle').textContent = `${prov.name} — Cài đặt`;
    document.getElementById('provSettingsSubtitle').textContent =
        prov.type === 'cloud' ? '☁️ Cloud Provider' :
        prov.type === 'local' ? '💻 Local / Offline' : '🌐 Online Service';

    // Status row
    const dot = document.getElementById('provStatusDot');
    const statusText = document.getElementById('provStatusText');
    dot.className = 'prov-status-dot';
    if (!enabled) {
        dot.classList.add('off');
        statusText.textContent = 'Đã tắt — provider bị vô hiệu hóa';
    } else if (isAvailable) {
        dot.classList.add('ok');
        statusText.textContent = info?.details || 'Sẵn sàng hoạt động';
    } else {
        dot.classList.add('err');
        statusText.textContent = info?.details || 'Chưa cài đặt hoặc chưa kết nối';
    }

    // Toggle
    document.getElementById('provToggle').checked = enabled;
    document.getElementById('provToggleHint').textContent = enabled
        ? 'Đang bật — voices và status sẽ được load'
        : 'Đang tắt — provider sẽ không được load';

    // Install guide (show if unavailable)
    const guide = document.getElementById('provInstallGuide');
    if (!isAvailable && enabled) {
        const g = PROVIDER_INSTALL_GUIDES[provKey] || {};
        document.getElementById('provInstallTitle').textContent = g.title || `${prov.name} chưa sẵn sàng`;
        document.getElementById('provInstallDesc').textContent = g.desc || info?.details || '';

        const stepsEl = document.getElementById('provInstallSteps');
        stepsEl.innerHTML = (g.steps || []).map(s =>
            `<div class="prov-install-step"><span style="color:var(--accent);font-weight:700;min-width:16px">${s.n}.</span> ${s.text}${s.code ? `<code>${s.code}</code>` : ''}</div>`
        ).join('');

        const linksEl = document.getElementById('provInstallLinks');
        linksEl.innerHTML = '';
        if (g.github) linksEl.innerHTML += `<a href="${g.github}" target="_blank" class="prov-install-link github">🐙 GitHub</a>`;
        if (g.docs)   linksEl.innerHTML += `<a href="${g.docs}" target="_blank" class="prov-install-link docs">📖 Docs / Link</a>`;

        guide.style.display = '';
    } else {
        guide.style.display = 'none';
    }

    // Details block
    const details = document.getElementById('provDetails');
    const detailsContent = document.getElementById('provDetailsContent');
    if (isAvailable && info) {
        const version = info.version ? `v${info.version}` : '';
        detailsContent.innerHTML = [
            info.details ? `<div>ℹ️ ${info.details}</div>` : '',
            version ? `<div>📦 Version: ${version}</div>` : '',
            prov.type === 'cloud' ? '<div>🔑 API Key required — manage in API Keys section below</div>' : '',
        ].filter(Boolean).join('');
        details.style.display = detailsContent.innerHTML ? '' : 'none';
    } else {
        details.style.display = 'none';
    }

    // Show API Keys shortcut if cloud
    const saveBtn = document.getElementById('provSaveBtn');
    if (isCloud) {
        saveBtn.style.display = '';
        saveBtn.textContent = '🔑 Quản lý API Keys';
        saveBtn.onclick = () => { closeProviderSettings(); openApiKeysModal(provKey, prov.name); };
    } else {
        saveBtn.style.display = 'none';
        saveBtn.onclick = saveProviderSettings;
    }

    document.getElementById('providerSettingsModal').classList.add('active');
}

function closeProviderSettings() {
    document.getElementById('providerSettingsModal').classList.remove('active');
    _currentProvKey = '';
}

function onProviderToggle() {
    const enabled = document.getElementById('provToggle').checked;
    document.getElementById('provToggleHint').textContent = enabled
        ? 'Đang bật — voices và status sẽ được load'
        : 'Đang tắt — provider sẽ không được load';
    const dot = document.getElementById('provStatusDot');
    dot.className = 'prov-status-dot ' + (enabled ? 'warn' : 'off');
    document.getElementById('provStatusText').textContent = enabled
        ? 'Đang bật — nhấn Lưu để áp dụng'
        : 'Đang tắt — nhấn Lưu để áp dụng';
    // Show save button when changed
    const saveBtn = document.getElementById('provSaveBtn');
    if (!CLOUD_PROVIDERS.includes(_currentProvKey)) {
        saveBtn.style.display = '';
        saveBtn.textContent = '💾 Lưu';
        saveBtn.onclick = saveProviderSettings;
    }
}

function saveProviderSettings() {
    if (!_currentProvKey) return;
    const enabled = document.getElementById('provToggle').checked;
    setProviderEnabled(_currentProvKey, enabled);
    log(enabled ? 'success' : 'info',
        `${ALL_PROVIDERS.find(p=>p.key===_currentProvKey)?.name || _currentProvKey} ${ enabled ? 'đã bật' : 'đã tắt' }`);
    closeProviderSettings();
    // Re-render badges & reload voices filtering disabled providers
    renderProviderStatus();
    reloadVoicesWithDisabled();
}

function reloadVoicesWithDisabled() {
    // Filter out voices from disabled providers
    const provSel = document.getElementById('filterProvider');
    const currentFilter = provSel.value;
    const disabledKey = !getProviderEnabled(currentFilter) ? currentFilter : null;
    if (disabledKey) {
        provSel.value = 'all';
        localStorage.setItem('tts_filter_provider', 'all');
    }
    // Rebuild voice list excluding disabled providers
    const enabledVoices = voices.filter(v => getProviderEnabled(v.engine || 'vibevoice'));
    buildFilterOptions(enabledVoices);
    applyVoiceFiltersFiltered(enabledVoices);
}

function applyVoiceFiltersFiltered(voicePool) {
    const provFilter = document.getElementById('filterProvider').value;
    const langFilter = document.getElementById('filterLanguage').value;
    localStorage.setItem('tts_filter_provider', provFilter);
    localStorage.setItem('tts_filter_language', langFilter);
    filteredVoices = voicePool.filter(v => {
        const eng = v.engine || 'vibevoice';
        const lang = v.language || v.locale?.substring(0, 2) || '?';
        if (provFilter !== 'all' && eng !== provFilter) return false;
        if (langFilter !== 'all' && lang !== langFilter) return false;
        return true;
    });
    populateVoiceSelect(filteredVoices);
}

// ── API Keys Modal ──
async function openApiKeysModal(provider, providerName) {
    _currentApiKeysProvider = provider;
    document.getElementById('apiKeysTitle').textContent = `🔑 ${providerName} — API Keys`;
    document.getElementById('newKeyLabel').value = 'default';
    document.getElementById('newKeyValue').value = '';

    const modal = document.getElementById('apiKeysModal');
    modal.classList.add('active');

    await loadApiKeys(provider);
}

function closeApiKeysModal() {
    document.getElementById('apiKeysModal').classList.remove('active');
    _currentApiKeysProvider = '';
}

async function loadApiKeys(provider) {
    const list = document.getElementById('apiKeysList');
    list.innerHTML = '<div style="text-align:center; color:var(--text-muted); padding:12px"><span class="spinner"></span> Loading...</div>';

    try {
        const resp = await fetch(`/api/v1/cloud-api/keys?provider=${provider}`);
        const data = await resp.json();
        // Response format: { keys: { "gemini": { "default": { masked_key, active, ... } } } }
        const providerKeys = (data.keys || {})[provider] || {};
        const labels = Object.keys(providerKeys);

        if (labels.length === 0) {
            list.innerHTML = `<div class="api-keys-empty">
                <span style="font-size:1.5rem">🔒</span>
                <p>No API keys saved for this provider</p>
                <p style="font-size:0.72rem; color:var(--text-muted)">Add a key below to get started</p>
            </div>`;
            return;
        }

        list.innerHTML = '';
        for (const label of labels) {
            const k = providerKeys[label];
            const activeTag = k.active ? '<span style="color:var(--success); font-size:0.68rem">● Active</span>' : '<span style="color:var(--danger); font-size:0.68rem">● Inactive</span>';
            const item = document.createElement('div');
            item.className = 'api-key-item';
            item.innerHTML = `
                <div class="api-key-info">
                    <div style="display:flex; align-items:center; gap:6px">
                        <span class="api-key-label">${label}</span>
                        ${activeTag}
                    </div>
                    <span class="api-key-masked">${k.masked_key || '••••••'}</span>
                </div>
                <div class="api-key-actions">
                    <button class="btn-tiny" title="Delete" onclick="deleteApiKey('${provider}','${label}')" style="color:var(--danger)">🗑️</button>
                </div>`;
            list.appendChild(item);
        }
    } catch (e) {
        list.innerHTML = `<div style="text-align:center; color:var(--danger); padding:12px">Failed to load keys: ${e.message}</div>`;
    }
}

async function addApiKey() {
    const provider = _currentApiKeysProvider;
    const label = document.getElementById('newKeyLabel').value.trim() || 'default';
    const apiKey = document.getElementById('newKeyValue').value.trim();

    if (!apiKey) {
        log('error', 'Please enter an API key');
        return;
    }

    try {
        const resp = await fetch('/api/v1/cloud-api/keys', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, api_key: apiKey, label }),
        });
        const data = await resp.json();

        if (resp.ok) {
            log('success', `✅ API key added for ${provider}`);
            document.getElementById('newKeyValue').value = '';
            await loadApiKeys(provider);
        } else {
            log('error', `Failed: ${data.detail || data.message || 'Unknown error'}`);
        }
    } catch (e) {
        log('error', `Error adding key: ${e.message}`);
    }
}

async function deleteApiKey(provider, label) {
    if (!confirm(`Delete key "${label}" for ${provider}?`)) return;

    try {
        const resp = await fetch('/api/v1/cloud-api/keys', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, label }),
        });

        if (resp.ok) {
            log('success', `🗑️ Key "${label}" deleted`);
            await loadApiKeys(provider);
        } else {
            const data = await resp.json();
            log('error', `Failed: ${data.detail || 'Unknown error'}`);
        }
    } catch (e) {
        log('error', `Error: ${e.message}`);
    }
}

// ── Load Model ──
async function loadModel() {
    try {
        const engine = getSelectedEngine();
        const resp = await fetch(`${API_BASE}/load`, { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ engine })
        });
        const data = await resp.json();
        log('info', data.message || 'Loading model...');
        checkModelStatus();
    } catch (e) {
        log('error', `Failed to load model: ${e.message}`);
    }
}

// ── Generate (supports both Text and SRT modes) ──
async function generateTTS() {
    if (currentInputMode === 'srt') {
        return generateSRT();
    }

    const text = document.getElementById('textInput').value.trim();
    if (!text) { log('error', 'Please enter some text'); return; }

    const voice = document.getElementById('voiceSelect').value;
    const engine = getSelectedEngine();
    const cfgScale = parseFloat(document.getElementById('cfgSlider').value);

    setGenerating(true);
    showProgress(engine === 'edge' ? 'Generating with Edge-TTS...' : 'Generating with VibeVoice AI...');
    hideAudio();
    log('info', `🎵 engine=${engine}, voice=${voice}, text="${text.substring(0, 60)}..."`);

    try {
        const resp = await fetch(`${API_BASE}/synthesize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, voice, engine, cfg_scale: cfgScale }),
        });
        const data = await resp.json();

        if (data.success && data.task_id) {
            currentTaskId = data.task_id;
            log('info', `Task started: ${data.task_id} (${engine})`);
            startPolling(data.task_id);
        } else {
            throw new Error(data.message || 'Failed to start synthesis');
        }
    } catch (e) {
        log('error', `Error: ${e.message}`);
        setGenerating(false);
        hideProgress();
    }
}

// ── SRT Mode Functions ──
function switchInputMode(mode) {
    currentInputMode = mode;
    document.querySelectorAll('.input-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.input-tab[data-mode="${mode}"]`).classList.add('active');

    document.getElementById('inputModeText').style.display = mode === 'text' ? '' : 'none';
    document.getElementById('inputModeSRT').style.display = mode === 'srt' ? '' : 'none';

    // Update generate button text
    const btnText = document.getElementById('btn-generate-text');
    if (btnText) btnText.textContent = mode === 'srt' ? 'Generate Voice Track' : 'Generate';
}

function handleSRTFile(event) {
    const file = event.target.files[0];
    if (file) readSRTFile(file);
}

function readSRTFile(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById('srtInput').value = e.target.result;
        parseSRTPreview();
        log('info', `📋 Loaded SRT file: ${file.name} (${(file.size / 1024).toFixed(1)}KB)`);
    };
    reader.readAsText(file);
}

function parseSRTPreview() {
    const srtText = document.getElementById('srtInput').value.trim();
    if (!srtText) {
        document.getElementById('srtInfo').style.display = 'none';
        return;
    }

    const segments = parseSRT(srtText);
    const info = document.getElementById('srtInfo');
    const countEl = document.getElementById('srtSegmentCount');
    const durEl = document.getElementById('srtDuration');

    if (segments.length > 0) {
        const totalMs = Math.max(...segments.map(s => s.end_ms));
        const minutes = Math.floor(totalMs / 60000);
        const seconds = Math.floor((totalMs % 60000) / 1000);
        countEl.textContent = `${segments.length} segments`;
        durEl.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
        info.style.display = 'flex';
    } else {
        info.style.display = 'none';
    }

    // Update char count
    const totalChars = segments.reduce((sum, s) => sum + s.text.length, 0);
    document.getElementById('charCount').textContent = `${totalChars} chars (${segments.length} segs)`;
}

function parseSRT(srtText) {
    const blocks = srtText.split(/\n\s*\n/);
    const segments = [];
    for (const block of blocks) {
        const lines = block.trim().split('\n');
        if (lines.length < 3) continue;
        const tsMatch = lines[1].trim().match(
            /(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})/
        );
        if (!tsMatch) continue;
        const g = tsMatch.slice(1);
        const start_ms = +g[0]*3600000 + +g[1]*60000 + +g[2]*1000 + +g[3];
        const end_ms = +g[4]*3600000 + +g[5]*60000 + +g[6]*1000 + +g[7];
        const text = lines.slice(2).join(' ').replace(/<[^>]+>/g, '').trim();
        if (text) segments.push({ index: lines[0].trim(), start_ms, end_ms, text });
    }
    return segments;
}

function previewSRT() {
    const srtText = document.getElementById('srtInput').value.trim();
    const segments = parseSRT(srtText);
    if (segments.length === 0) { log('error', 'No valid SRT segments'); return; }

    let preview = '📋 SRT Preview:\n';
    for (const seg of segments.slice(0, 10)) {
        const startSec = (seg.start_ms / 1000).toFixed(1);
        const endSec = (seg.end_ms / 1000).toFixed(1);
        preview += `  [${startSec}s → ${endSec}s] ${seg.text.substring(0, 50)}...\n`;
    }
    if (segments.length > 10) preview += `  ... and ${segments.length - 10} more segments`;
    log('info', preview);
}

async function generateSRT() {
    const srtContent = document.getElementById('srtInput').value.trim();
    if (!srtContent) { log('error', 'Please enter or upload SRT content'); return; }

    const segments = parseSRT(srtContent);
    if (segments.length === 0) { log('error', 'No valid SRT segments found'); return; }

    const voice = document.getElementById('voiceSelect').value;
    const engine = getSelectedEngine();
    const cfgScale = parseFloat(document.getElementById('cfgSlider').value);

    setGenerating(true);
    showProgress(`Generating voice track (${segments.length} segments)...`);
    hideAudio();
    log('info', `📋 SRT mode: ${segments.length} segments, engine=${engine}, voice=${voice}`);

    try {
        const resp = await fetch(`${API_BASE}/synthesize-srt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                srt_content: srtContent,
                voice,
                engine,
                cfg_scale: cfgScale,
            }),
        });
        const data = await resp.json();

        if (data.success && data.task_id) {
            currentTaskId = data.task_id;
            log('info', `SRT task started: ${data.task_id} (${data.segments_count} segments)`);
            startSRTPolling(data.task_id);
        } else {
            throw new Error(data.detail || data.message || 'Failed to start SRT synthesis');
        }
    } catch (e) {
        log('error', `SRT Error: ${e.message}`);
        setGenerating(false);
        hideProgress();
    }
}

function startSRTPolling(taskId) {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(async () => {
        try {
            const resp = await fetch(`${API_BASE}/status/${taskId}`);
            const data = await resp.json();

            const pct = data.progress || 0;
            const seg = data.current_segment || 0;
            const total = data.total_segments || 0;

            if (data.status === 'success' && data.result) {
                clearInterval(pollInterval); pollInterval = null;
                setGenerating(false); hideProgress();
                showAudioResult(data.result);
                log('success', `✅ Voice track done! ${data.result.segments_count} segments, ${data.result.duration}s, gen: ${data.result.generation_time}s`);
            } else if (data.status === 'error') {
                clearInterval(pollInterval); pollInterval = null;
                setGenerating(false); hideProgress();
                log('error', `❌ SRT Failed: ${data.result?.message || 'Unknown error'}`);
            } else if (data.status === 'stitching') {
                updateProgress(90, '🔧 Stitching segments into voice track...');
            } else {
                updateProgress(pct, `🎵 Generating segment ${seg}/${total}...`);
            }
        } catch (e) { /* continue polling */ }
    }, 2000);
}

function startPolling(taskId) {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(async () => {
        try {
            const resp = await fetch(`${API_BASE}/status/${taskId}`);
            const data = await resp.json();

            if (data.status === 'success' && data.result) {
                clearInterval(pollInterval);
                pollInterval = null;
                setGenerating(false);
                hideProgress();

                showAudioResult(data.result);
                const engine = data.result.engine || 'unknown';
                log('success', `✅ Done! (${engine}) Duration: ${data.result.duration}s, Gen: ${data.result.generation_time}s`);
            } else if (data.status === 'error') {
                clearInterval(pollInterval);
                pollInterval = null;
                setGenerating(false);
                hideProgress();
                log('error', `❌ Failed: ${data.result?.message || 'Unknown error'}`);
            } else {
                const pct = data.progress || 0;
                let msg = data.status === 'loading_model'
                    ? 'Loading AI model (first time may take a while)...'
                    : (data.status === 'stitching' ? 'Stitching audio chunks...' : `Generating audio... ${pct > 0 ? pct + '%' : ''}`);
                updateProgress(pct > 0 ? pct : 50, msg);
            }
        } catch (e) { /* polling error, continue */ }
    }, 1500);
}

function showAudioResult(result) {
    const section = document.getElementById('audioSection');
    const player = document.getElementById('audioPlayer');
    const info = document.getElementById('audioInfo');

    // Construct audio URL from output path
    const filename = result.output.split(/[/\\]/).pop();
    player.src = `${API_BASE}/audio/${encodeURIComponent(filename)}`;
    player.load();

    const sizeMB = (result.size / (1024 * 1024)).toFixed(2);
    const engineLabel = result.engine === 'edge' ? '⚡ Edge-TTS' : 
                        (result.engine === 'viterbox' ? '🇻🇳 Viterbox' : '🧠 VibeVoice');
    info.innerHTML = `
        ⏱️ Duration: <strong>${result.duration}s</strong> •
        ⚡ Gen: <strong>${result.generation_time}s</strong> •
        📊 RTF: <strong>${result.rtf}x</strong> •
        📁 Size: <strong>${sizeMB}MB</strong> •
        🎙️ Voice: <strong>${result.voice}</strong> •
        🔧 Engine: <strong>${engineLabel}</strong>
    `;

    section.style.display = 'flex';
    drawWaveformPlaceholder();
}

function drawWaveformPlaceholder() {
    const canvas = document.getElementById('waveformCanvas');
    const ctx = canvas.getContext('2d');
    const w = canvas.width; const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const gradient = ctx.createLinearGradient(0, 0, w, 0);
    gradient.addColorStop(0, '#6366f1');
    gradient.addColorStop(0.5, '#8b5cf6');
    gradient.addColorStop(1, '#a855f7');
    ctx.fillStyle = gradient;
    const barCount = 80;
    const barWidth = (w / barCount) * 0.7;
    const gap = (w / barCount) * 0.3;

    for (let i = 0; i < barCount; i++) {
        const barHeight = Math.random() * (h * 0.7) + h * 0.1;
        const x = i * (barWidth + gap);
        const y = (h - barHeight) / 2;
        ctx.fillRect(x, y, barWidth, barHeight);
    }
}

// ── Clone Voice UI (Modals) ──
let cloneSelectedFile = null;

function openCloneModal() {
    cloneSelectedFile = null;
    document.getElementById('cloneFileName').style.display = 'none';
    document.getElementById('cloneDropZone').style.borderColor = 'var(--border)';
    document.getElementById('cloneVoiceName').value = '';
    document.getElementById('cloneVoiceModal').classList.add('active');
    
    // Bind drop zone
    const dropZone = document.getElementById('cloneDropZone');
    dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add('dragover'); };
    dropZone.ondragleave = (e) => { e.preventDefault(); dropZone.classList.remove('dragover'); };
    dropZone.ondrop = (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if(e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleCloneFileSelect(e.dataTransfer.files[0]);
        }
    };
    
    document.getElementById('sysCloneInput').onchange = (e) => {
        if(e.target.files && e.target.files.length > 0) {
            handleCloneFileSelect(e.target.files[0]);
        }
    };
}

function handleCloneFileSelect(file) {
    if(!file.name.toLowerCase().match(/\.(wav|mp3|mpeg)$/)) {
        log('error', "Chỉ hỗ trợ file WAV hoặc MP3");
        return;
    }
    cloneSelectedFile = file;
    document.getElementById('cloneDropZone').style.borderColor = 'var(--success)';
    const nameEl = document.getElementById('cloneFileName');
    nameEl.textContent = "✅ " + file.name;
    nameEl.style.display = 'block';
    
    // Auto fill name if empty
    let cleanName = file.name.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9\s]/g, " ");
    if(!document.getElementById('cloneVoiceName').value) {
        document.getElementById('cloneVoiceName').value = cleanName.trim();
    }
}

function closeCloneModal() {
    document.getElementById('cloneVoiceModal').classList.remove('active');
}

function updateGenderPill() {
    const isMale = document.querySelector('input[name="cloneGender"]:checked').value === 'male';
    document.getElementById('pillMale').classList.toggle('active', isMale);
    document.getElementById('pillFemale').classList.toggle('active', !isMale);
}

async function submitCloneVoice() {
    if(!cloneSelectedFile) {
        log('warning', "Vui lòng chọn hoặc kéo thả file âm thanh!");
        return;
    }
    const voiceName = document.getElementById('cloneVoiceName').value.trim();
    if(!voiceName) {
        log('warning', "Vui lòng nhập tên cho giọng mới!");
        return;
    }
    
    const gender = document.querySelector('input[name="cloneGender"]:checked').value;
    
    const formData = new FormData();
    formData.append("voice_name", voiceName);
    formData.append("gender", gender);
    formData.append("file", cloneSelectedFile);

    const btn = document.getElementById('btnSubmitClone');
    const txt = document.getElementById('txtSubmitClone');
    txt.innerHTML = '<span class="spinner" style="margin-right:6px"></span> Đang tải lên...';
    btn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/upload-voice`, { method: 'POST', body: formData });
        const result = await response.json();
        if (result.success) {
            log('success', `Đã tải lên giọng: ${voiceName}`);
            await loadVoices();
            closeCloneModal();
            // auto select
            const sel = document.getElementById('voiceSelect');
            if (sel) {
                setTimeout(() => {
                    for (let i = 0; i < sel.options.length; i++) {
                        if (sel.options[i].value === result.voice_id) {
                            sel.selectedIndex = i;
                            if (typeof updateVoicePreview === 'function') updateVoicePreview();
                            sel.dispatchEvent(new Event('change'));
                            break;
                        }
                    }
                }, 300);
            }
        } else {
            log('error', result.message);
        }
    } catch (e) {
        log('error', `Lỗi kết nối: ${e.message}`);
    } finally {
        txt.innerHTML = '🚀 Upload & Clone';
        btn.disabled = false;
    }
}

// ── Voice Management (Sửa / Xoá) ──

function openManageModal() {
    document.getElementById('manageVoicesModal').classList.add('active');
    renderManageList();
}

function closeManageModal() {
    document.getElementById('manageVoicesModal').classList.remove('active');
}

function renderManageList() {
    const listDiv = document.getElementById('manageList');
    listDiv.innerHTML = '';
    
    const customVoices = voices.filter(v => v.id.startsWith("clone_"));
    if(customVoices.length === 0) {
        listDiv.innerHTML = '<p style="text-align:center;color:var(--text-muted);padding:20px;">Bạn chưa có giọng clone nào.</p>';
        return;
    }
    
    customVoices.forEach(v => {
        const item = document.createElement('div');
        item.className = 'manage-list-item';
        // HTML Structure for viewing and editing
        item.innerHTML = `
            <!-- View Mode -->
            <div class="manage-item-info" id="view-info-${v.id}">
                <div class="manage-item-title">${v.name}</div>
                <div class="manage-item-meta">${v.gender === 'male' ? '👨 Nam' : '👩 Nữ'} • ${v.language_name}</div>
            </div>
            <div class="manage-item-actions" id="view-actions-${v.id}">
                <button class="btn-tiny" onclick="startEditVoice('${v.id}', '${v.name.replace("Clone: ", "").replace(/'/g, "\\'")}', '${v.gender}')">✏️Sửa</button>
                <button class="btn-tiny" onclick="deleteVoice('${v.id}')">🗑️Xoá</button>
            </div>
            
            <!-- Edit Mode -->
            <div class="inline-edit-form" id="edit-form-${v.id}">
                <input type="text" id="edit-name-${v.id}" class="inline-input" placeholder="Tên" value="${v.name.replace("Clone: ", "")}">
                <select id="edit-gender-${v.id}" class="inline-select">
                    <option value="male" ${v.gender==='male' ? 'selected' : ''}>👨 Nam</option>
                    <option value="female" ${v.gender==='female' ? 'selected' : ''}>👩 Nữ</option>
                </select>
                <button class="btn-tiny" onclick="saveEditVoice('${v.id}')" style="border-color:var(--success);color:var(--success)">✅ Lưu</button>
                <button class="btn-tiny" onclick="cancelEditVoice('${v.id}')">❌Huỷ</button>
            </div>
        `;
        listDiv.appendChild(item);
    });
}

function startEditVoice(id, currentName, currentGender) {
    document.getElementById(`view-info-${id}`).classList.add('hidden');
    document.getElementById(`view-actions-${id}`).style.display = 'none';
    document.getElementById(`edit-form-${id}`).classList.add('active');
}

function cancelEditVoice(id) {
    document.getElementById(`view-info-${id}`).classList.remove('hidden');
    document.getElementById(`view-actions-${id}`).style.display = 'flex';
    document.getElementById(`edit-form-${id}`).classList.remove('active');
}

async function saveEditVoice(id) {
    const newName = document.getElementById(`edit-name-${id}`).value.trim();
    const newGender = document.getElementById(`edit-gender-${id}`).value;
    if(!newName) return;
    
    try {
        const btn = document.querySelector(`#edit-form-${id} button`);
        btn.textContent = '⏳';
        
        const resp = await fetch(`${API_BASE}/voice/${id}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({new_name: newName, gender: newGender})
        });
        const result = await resp.json();
        if(result.success) {
            log('success', "Đã đổi tên giọng clone.");
            await loadVoices();
            renderManageList();
            
            // Trigger UI update if previously selected
            const sel = document.getElementById('voiceSelect');
            if(sel.value === id) {
                sel.value = result.voice_id;
                if (typeof updateVoicePreview === 'function') updateVoicePreview();
            }
        } else {
            log('error', `Lỗi: ${result.message}`);
            cancelEditVoice(id);
        }
    } catch(e) {
        log('error', e.message);
        cancelEditVoice(id);
    }
}

async function deleteVoice(id) {
    if(!confirm("Bạn có chắc chắn muốn xoá giọng clone này vĩnh viễn?")) return;
    
    try {
        const resp = await fetch(`${API_BASE}/voice/${id}`, { method: 'DELETE' });
        const result = await resp.json();
        
        if(result.success) {
            log('success', "Đã xoá giọng clone.");
            await loadVoices();
            renderManageList();
            if (typeof updateVoicePreview === 'function') updateVoicePreview();
        } else {
            log('error', `Lỗi xoá giọng: ${result.message}`);
        }
    } catch(e) {
        log('error', e.message);
    }
}

// ── Stream (WebSocket, VibeVoice only) ──
async function streamTTS() {
    const text = document.getElementById('textInput').value.trim();
    if (!text) { log('error', 'Please enter some text'); return; }

    const voice = document.getElementById('voiceSelect').value;
    const cfgScale = document.getElementById('cfgSlider').value;

    setGenerating(true);
    hideAudio();
    showStream();
    log('info', `Streaming TTS: voice=${voice}`);

    streamAudioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    streamBuffers = [];
    streamTotalSamples = 0;
    let nextPlayTime = streamAudioContext.currentTime;

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}${API_BASE}/stream?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(voice)}&cfg=${cfgScale}`;

    try {
        wsConnection = new WebSocket(wsUrl);
        wsConnection.binaryType = 'arraybuffer';

        wsConnection.onopen = () => {
            updateStreamStatus('Connected, generating...');
            log('info', 'WebSocket connected');
        };

        wsConnection.onmessage = (event) => {
            if (typeof event.data === 'string') {
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === 'progress') updateStreamStatus(`Streaming: ${msg.generated_sec}s`);
                    else if (msg.type === 'complete') { updateStreamStatus(`✅ Complete: ${msg.total_sec}s`); log('success', `✅ Stream complete: ${msg.total_sec}s`); setGenerating(false); }
                    else if (msg.type === 'error') { updateStreamStatus(`❌ ${msg.message}`); log('error', msg.message); setGenerating(false); }
                    else if (msg.type === 'status') { updateStreamStatus(msg.message); log('info', msg.message); }
                } catch (e) {}
            } else {
                const pcm16 = new Int16Array(event.data);
                const float32 = new Float32Array(pcm16.length);
                for (let i = 0; i < pcm16.length; i++) float32[i] = pcm16[i] / 32768.0;

                const buffer = streamAudioContext.createBuffer(1, float32.length, SAMPLE_RATE);
                buffer.getChannelData(0).set(float32);
                const source = streamAudioContext.createBufferSource();
                source.buffer = buffer;
                source.connect(streamAudioContext.destination);
                if (nextPlayTime < streamAudioContext.currentTime) nextPlayTime = streamAudioContext.currentTime;
                source.start(nextPlayTime);
                nextPlayTime += buffer.duration;

                streamBuffers.push(float32);
                streamTotalSamples += float32.length;
                drawStreamVisualizer(float32);
            }
        };

        wsConnection.onerror = () => { log('error', 'WebSocket error'); setGenerating(false); };
        wsConnection.onclose = () => {
            log('info', 'WebSocket closed');
            if (isGenerating) setGenerating(false);
            if (streamBuffers.length > 0) createDownloadableAudioFromStream();
        };
    } catch (e) {
        log('error', `Stream error: ${e.message}`);
        setGenerating(false);
    }
}

function createDownloadableAudioFromStream() {
    const totalLength = streamBuffers.reduce((sum, buf) => sum + buf.length, 0);
    const combined = new Float32Array(totalLength);
    let offset = 0;
    for (const buf of streamBuffers) { combined.set(buf, offset); offset += buf.length; }

    const wav = float32ToWav(combined, SAMPLE_RATE);
    const blob = new Blob([wav], { type: 'audio/wav' });
    const url = URL.createObjectURL(blob);

    const section = document.getElementById('audioSection');
    const player = document.getElementById('audioPlayer');
    player.src = url; player.load();
    section.style.display = 'flex';

    const duration = (totalLength / SAMPLE_RATE).toFixed(2);
    document.getElementById('audioInfo').innerHTML = `⏱️ Duration: <strong>${duration}s</strong> • 🔊 Streamed • 📁 WAV`;
    drawWaveformPlaceholder();
}

function float32ToWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    function writeString(v, o, s) { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); }
    writeString(view, 0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(view, 8, 'WAVE'); writeString(view, 12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    writeString(view, 36, 'data'); view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return buffer;
}

function drawStreamVisualizer(chunk) {
    const canvas = document.getElementById('streamCanvas');
    const ctx = canvas.getContext('2d');
    const w = canvas.width; const h = canvas.height;
    const imageData = ctx.getImageData(4, 0, w - 4, h);
    ctx.putImageData(imageData, 0, 0);
    ctx.fillStyle = '#0d1225'; ctx.fillRect(w - 4, 0, 4, h);

    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, '#6366f1'); gradient.addColorStop(1, '#a855f7');
    ctx.fillStyle = gradient;
    const step = Math.max(1, Math.floor(chunk.length / 4));
    for (let i = 0; i < 4; i++) {
        const idx = i * step;
        if (idx < chunk.length) {
            const amplitude = Math.abs(chunk[idx]);
            const barHeight = amplitude * h * 0.9;
            ctx.fillRect(w - 4 + i, (h - barHeight) / 2, 1, barHeight);
        }
    }
}

// ── Stop ──
function stopGeneration() {
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    if (wsConnection) { wsConnection.close(); wsConnection = null; }
    setGenerating(false); hideProgress();
    log('info', 'Generation stopped');
}

// ── Download ──
function downloadAudio() {
    const player = document.getElementById('audioPlayer');
    if (player.src) {
        const a = document.createElement('a');
        a.href = player.src;
        a.download = `tts_output_${Date.now()}.mp3`;
        a.click();
    }
}

// ── UI Helpers ──
function updateCharCount() {
    const text = document.getElementById('textInput').value;
    document.getElementById('charCount').textContent = `${text.length} chars`;
}

function setGenerating(g) {
    isGenerating = g;
    document.getElementById('btnGenerate').disabled = g;
    document.getElementById('btnStream').disabled = g;
    document.getElementById('btnStop').style.display = g ? 'inline-flex' : 'none';
}

function showProgress(msg) {
    document.getElementById('progressSection').style.display = 'block';
    document.getElementById('progressText').textContent = msg;
    document.getElementById('progressFill').style.width = '0%';
}

function updateProgress(pct, msg) {
    document.getElementById('progressFill').style.width = `${pct}%`;
    if (msg) document.getElementById('progressText').textContent = msg;
}

function hideProgress() { document.getElementById('progressSection').style.display = 'none'; }
function hideAudio() { document.getElementById('audioSection').style.display = 'none'; }

function showStream() {
    document.getElementById('streamSection').style.display = 'block';
    const canvas = document.getElementById('streamCanvas');
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#0d1225';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function updateStreamStatus(msg) { document.getElementById('streamStatus').textContent = msg; }

function log(type, msg) {
    const output = document.getElementById('logOutput');
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    output.appendChild(entry);
    output.scrollTop = output.scrollHeight;
    while (output.children.length > 50) output.removeChild(output.firstChild);
}
