/* ── Theme ───────────────────────────────────────── */

function applyTheme(theme) {
  document.documentElement.classList.toggle('light', theme === 'light');
  document.getElementById('themeIconDark').style.display = theme === 'dark' ? 'block' : 'none';
  document.getElementById('themeIconLight').style.display = theme === 'light' ? 'block' : 'none';
}

/* ── Settings Modal ───────────────────────────────── */

function toggleModal() {
  const overlay = document.getElementById('modalOverlay');
  overlay.classList.toggle('open');
  if (overlay.classList.contains('open')) {
    // Load saved settings into fields
    fetch('/settings').then(r => r.json()).then(s => {
      if (s.anthropic_api_key) document.getElementById('anthropicKey').value = s.anthropic_api_key;
      if (s.openai_api_key) document.getElementById('openaiKey').value = s.openai_api_key;
      if (s.hf_token) document.getElementById('hfToken').value = s.hf_token;
      if (s.ollama_url) document.getElementById('ollamaUrl').value = s.ollama_url;
      if (s.custom_api_url) document.getElementById('customApiUrl').value = s.custom_api_url;
      if (s.custom_api_key) document.getElementById('customApiKey').value = s.custom_api_key;
    });
    // Auto-load data for active tab
    const activeTab = document.querySelector('.modal-tab.active');
    if (activeTab) {
      if (activeTab.textContent.includes('Голоса')) loadVoicesList();
      if (activeTab.textContent.includes('Модели')) { loadModels(); loadTtsModels(); }
    }
  }
}

function switchTab(tabId, el) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + tabId).classList.add('active');
  el.classList.add('active');
  if (tabId === 'voices') loadVoicesList();
  if (tabId === 'models') { loadModels(); loadTtsModels(); }
}

function toggleTheme() {
  const isLight = document.documentElement.classList.contains('light');
  const next = isLight ? 'dark' : 'light';
  localStorage.setItem('theme', next);
  applyTheme(next);
}

applyTheme(localStorage.getItem('theme') || 'dark');

/* ── State ───────────────────────────────────────── */

let currentJobId = null;
let videoEl = null;
let subtitles = [];
let syncInterval = null;
const STAGE_LABELS = {
  download: 'скачивание', transcribe: 'транскрипцию', translate: 'перевод',
  tts: 'синтез речи', build: 'сборку', lipsync: 'синхронизацию губ',
};
let currentStage = '';

const _ICON_PLAY = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const _ICON_STOP = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
let jobRunning = false;

let uploadedOriginal = null;
let uploadedTranslated = null;
// Есть ли настоящий перевод. Пока его нет, subtitles — это оригиналы, и
// сохранять/отправлять их как translated.srt нельзя.
let hasTranslation = false;
let resumeWorkDir = null;
let sourceVideoSrc = null;
let outputVideoPath = null;
let _playerShowingOutput = false;
let originalSubs = [];
const _svgUpload = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
const _svgCheck = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>';
function _setUploadState(btnId, iconId, loaded) {
  const btn = document.getElementById(btnId);
  const icon = document.getElementById(iconId);
  if (loaded) { btn.classList.add('loaded'); if (icon) icon.innerHTML = _svgCheck; }
  else { btn.classList.remove('loaded'); if (icon) icon.innerHTML = _svgUpload; }
  // Enable/disable delete button
  const delId = btnId === 'uploadOrigBtn' ? 'deleteOrigBtn' : 'deleteTransBtn';
  const del = document.getElementById(delId);
  if (del) del.disabled = !loaded;
}
let logMsgCount = 0;
let _subsScrollLocked = false;
let speakerMap = {};       // {index_str: "SPEAKER_00", ...}
let speakerVoiceMap = {};  // {"SPEAKER_00": {engine, voice}, ...}

const SPEAKER_COLORS = ['#3b82f6','#ef4444','#22c55e','#f59e0b','#8b5cf6','#ec4899','#06b6d4','#f97316','#14b8a6','#6366f1'];

function ts() {
  return new Date().toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

function addLog(msg, cls) {
  const el = document.getElementById('log');
  const d = document.createElement('div');
  d.className = 'msg' + (cls ? ' ' + cls : '');
  d.innerHTML = `<span class="ts">${ts()}</span>${escHtml(msg)}`;
  el.appendChild(d);
  el.scrollTop = el.scrollHeight;
  logMsgCount++;
  document.getElementById('logCount').textContent = logMsgCount + ' записей';
  // Flash dot if collapsed
  const panel = document.getElementById('logPanel');
  if (panel.classList.contains('collapsed')) {
    const dot = document.getElementById('logDot');
    dot.classList.add('active');
    setTimeout(() => dot.classList.remove('active'), 2500);
  }
}

function downloadSubs(type) {
  const subs = type === 'original' ? originalSubs : subtitles;
  if (!subs.length) return;
  let srt = '';
  subs.forEach((s, i) => {
    const idx = s.index || i + 1;
    // округляем всю величину: иначе 1.9999 даёт ms=1000 и битый тайм-код
    const fmt = t => { const total = Math.max(0, Math.round(t*1000)); const h = Math.floor(total/3600000); const m = Math.floor(total%3600000/60000); const sec = Math.floor(total%60000/1000); const ms = total%1000; return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')},${String(ms).padStart(3,'0')}`; };
    srt += `${idx}\n${fmt(s.start)} --> ${fmt(s.end)}\n${s.text}\n\n`;
  });
  const blob = new Blob([srt], {type: 'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = type === 'original' ? 'original.srt' : 'translated.srt';
  a.click();
  URL.revokeObjectURL(a.href);
}

function _updateSubsDownloadButtons() {
  document.getElementById('downloadOrigSubs').style.display = originalSubs.length ? '' : 'none';
  document.getElementById('downloadTransSubs').style.display = (hasTranslation && subtitles.length) ? '' : 'none';
}

function toggleLog() {
  const panel = document.getElementById('logPanel');
  const handle = document.getElementById('logResizeHandle');
  const body = document.querySelector('.log-body');
  panel.classList.toggle('collapsed');
  const collapsed = panel.classList.contains('collapsed');
  handle.classList.toggle('hidden', collapsed);
  if (collapsed) {
    body.style.height = '0';
  } else {
    body.style.height = '150px';
    document.getElementById('logDot').classList.remove('active');
  }
}

function downloadLog() {
  const lines = Array.from(document.querySelectorAll('#log .msg')).map(el => el.textContent);
  if (!lines.length) return;
  const blob = new Blob([lines.join('\n')], {type: 'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'video-dub-log.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function setStep(key, state) {
  const el = document.querySelector(`.step[data-key="${key}"]`);
  if (!el) return;
  el.className = 'step ' + state;
}

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  const ms = Math.floor((s % 1) * 1000);
  return `${m}:${String(sec).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
}

function parseTime(str) {
  // Accepts: "1:23.456", "1:23", "83.5", "83"
  str = str.trim();
  let m = str.match(/^(\d+):(\d{1,2})(?:\.(\d+))?$/);
  if (m) {
    const mins = parseInt(m[1]);
    const secs = parseInt(m[2]);
    const ms = m[3] ? parseInt(m[3].padEnd(3, '0').substring(0, 3)) : 0;
    return mins * 60 + secs + ms / 1000;
  }
  m = str.match(/^(\d+(?:\.\d+)?)$/);
  if (m) return parseFloat(m[1]);
  return null;
}

/* ── Settings ────────────────────────────────────── */


// Dynamic model list from translate plugins
const MODEL_LIST = (() => {
  try {
    return JSON.parse(document.body.dataset.translateModels || '{}');
  } catch { return {}; }
})();
const savedModel = document.body.dataset.defaultModel || '';

// Debounced auto-save to .env
let _saveTimer = null;
function persistSetting(obj) {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    fetch('/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(obj)
    });
  }, 400);
}

function getTranslateModel() {
  const p = document.getElementById('translateProvider').value;
  if (p === 'ollama') return document.getElementById('ollamaModel').value.trim() || 'llama3.1';
  if (p === 'custom') return document.getElementById('customModel').value.trim();
  return document.getElementById('translateModel').value;
}

function _renderModelOptions(models, savedId) {
  const sel = document.getElementById('translateModel');
  sel.innerHTML = models.map(m =>
    `<option value="${m.id}" ${m.id === savedId ? 'selected' : ''}>${m.name}</option>`
  ).join('');
  return sel.value;
}

function onProviderChange() {
  const p = document.getElementById('translateProvider').value;

  // Hide all model and settings fields
  document.getElementById('modelField').style.display = 'none';
  document.getElementById('ollamaModelField').style.display = 'none';
  document.getElementById('customModelField').style.display = 'none';
  document.querySelectorAll('.provider-setting').forEach(el => el.style.display = 'none');

  let modelVal = '';

  // Check if plugin has a model list (static fallback)
  const pluginModels = MODEL_LIST[p];
  if (pluginModels && pluginModels.length > 0) {
    document.getElementById('modelField').style.display = 'block';
    modelVal = _renderModelOptions(pluginModels, savedModel);
  }

  // Provider-specific settings
  if (p === 'claude' || p === 'openai') {
    if (p === 'claude') document.getElementById('settingAnthropicKey').style.display = 'block';
    else document.getElementById('settingOpenaiKey').style.display = 'block';
    // Fetch fresh models from API (uses saved key from .env)
    document.getElementById('modelField').style.display = 'block';
    const sel = document.getElementById('translateModel');
    sel.innerHTML = '<option>Загрузка моделей...</option>';
    fetch('/translate-models/' + p)
      .then(r => r.json())
      .then(d => {
        if (!d.models || !d.models.length) {
          sel.innerHTML = '<option value="">Введите API ключ</option>';
          return;
        }
        const newVal = _renderModelOptions(d.models, savedModel);
        if (newVal !== modelVal) persistSetting({translate_model: newVal});
      })
      .catch(() => { sel.innerHTML = '<option value="">Ошибка загрузки</option>'; });
  } else if (p === 'ollama') {
    document.getElementById('ollamaModelField').style.display = 'block';
    document.getElementById('settingOllamaUrl').style.display = 'block';
    modelVal = document.getElementById('ollamaModel').value.trim() || 'llama3.1';
  } else if (p === 'custom') {
    document.getElementById('customModelField').style.display = 'block';
    document.getElementById('settingCustomUrl').style.display = 'block';
    document.getElementById('settingCustomKey').style.display = 'block';
    modelVal = document.getElementById('customModel').value.trim();
  }

  persistSetting({translate_provider: p, translate_model: modelVal});
}
onProviderChange();

function onTtsEngineChange() {
  const eng = document.getElementById('ttsEngine').value;
  const isBase = eng.includes('-base');
  const isCustom = eng.includes('-custom');
  const isEdge = eng === 'edge-tts';
  const isMacos = eng === 'macos-say';
  const isOmni = eng === 'omnivoice';
  const isElevenlabs = eng === 'elevenlabs';
  const isFish = eng === 'fish-audio';
  const isQwen = isBase || isCustom;
  const hasSeed = isQwen || isOmni;
  const isCloudWithClone = isElevenlabs || isFish;
  document.getElementById('ttsBaseVoiceField').style.display = isCustom ? 'block' : 'none';
  document.getElementById('ttsClonedVoiceField').style.display = (isBase || isOmni) ? 'block' : 'none';
  document.getElementById('ttsEdgeVoiceField').style.display = isEdge ? 'block' : 'none';
  document.getElementById('ttsMacosVoiceField').style.display = isMacos ? 'block' : 'none';
  document.getElementById('ttsVoiceModeField').style.display = isCloudWithClone ? 'block' : 'none';
  const showSeedRow = hasSeed || isQwen;
  document.getElementById('ttsSeedTempRow').style.display = showSeedRow ? 'grid' : 'none';
  document.getElementById('ttsSeedField').style.display = hasSeed ? 'block' : 'none';
  document.getElementById('ttsTempField').style.display = isQwen ? 'block' : 'none';
  document.getElementById('ttsElevenlabsKeyField').style.display = isElevenlabs ? 'block' : 'none';
  document.getElementById('ttsFishKeyField').style.display = isFish ? 'block' : 'none';
  if (isEdge) loadEdgeVoices();
  if (isMacos) loadMacosVoices();
  if (isCloudWithClone) {
    document.getElementById('ttsElevenlabsVoice').style.display = isElevenlabs ? 'block' : 'none';
    document.getElementById('ttsFishVoice').style.display = isFish ? 'block' : 'none';
    if (isElevenlabs) loadElevenlabsVoices();
    if (isFish) loadFishVoices();
    onVoiceModeChange();
  }
}

function getTtsSeed() {
  const val = parseInt(document.getElementById('ttsSeed').value) || 0;
  return val === 0 ? -1 : val;
}

function onVoiceModeChange() {
  const mode = document.querySelector('input[name="ttsVoiceMode"]:checked').value;
  document.getElementById('ttsPresetVoiceWrap').style.display = mode === 'preset' ? 'block' : 'none';
  document.getElementById('ttsCloneVoiceWrap').style.display = mode === 'clone' ? 'block' : 'none';
}

function getTtsVoice() {
  const eng = document.getElementById('ttsEngine').value;
  if (eng.includes('-custom')) return document.getElementById('ttsBaseVoice').value;
  if (eng === 'edge-tts') return document.getElementById('ttsEdgeVoice').value;
  if (eng === 'macos-say') return document.getElementById('ttsMacosVoice').value;
  if (eng === 'elevenlabs' || eng === 'fish-audio') {
    const mode = document.querySelector('input[name="ttsVoiceMode"]:checked').value;
    if (mode === 'clone') return document.getElementById('ttsCloneVoiceForCloud').value;
    if (eng === 'elevenlabs') return document.getElementById('ttsElevenlabsVoice').value;
    return document.getElementById('ttsFishVoice').value;
  }
  return document.getElementById('ttsVoice').value;
}

const LANG_TO_LOCALE = {
  Russian:'ru', English:'en', Spanish:'es', French:'fr', German:'de',
  Chinese:'zh', Japanese:'ja', Korean:'ko', Portuguese:'pt', Italian:'it',
  Polish:'pl', Turkish:'tr', Arabic:'ar', Hindi:'hi'
};

function getTargetLocale() {
  const lang = document.getElementById('language').value;
  return LANG_TO_LOCALE[lang] || '';
}

let _macosVoicesAll = [];
let _macosVoicesLoaded = false;
function loadMacosVoices() {
  if (_macosVoicesLoaded) { filterMacosVoices(); return; }
  fetch('/macos-voices')
    .then(r => r.json())
    .then(data => {
      _macosVoicesAll = data.voices || [];
      _macosVoicesLoaded = true;
      filterMacosVoices();
    });
}
function filterMacosVoices() {
  const sel = document.getElementById('ttsMacosVoice');
  const cur = sel.value;
  const locale = getTargetLocale();
  const filtered = locale ? _macosVoicesAll.filter(v => v.lang && v.lang.startsWith(locale)) : _macosVoicesAll;
  sel.innerHTML = '<option value="">По умолчанию</option>';
  filtered.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.name;
    opt.textContent = v.name + (v.lang && !v.name.includes('(') ? ' (' + v.lang + ')' : '');
    sel.appendChild(opt);
  });
  sel.value = cur;
}

let _edgeVoicesAll = [];
let _edgeVoicesLoaded = false;
function loadEdgeVoices() {
  if (_edgeVoicesLoaded) { filterEdgeVoices(); return; }
  fetch('/edge-voices')
    .then(r => r.json())
    .then(data => {
      _edgeVoicesAll = data.voices || [];
      _edgeVoicesLoaded = true;
      filterEdgeVoices();
    });
}
function filterEdgeVoices() {
  const sel = document.getElementById('ttsEdgeVoice');
  const cur = sel.value;
  const locale = getTargetLocale();
  const filtered = locale ? _edgeVoicesAll.filter(v => v.lang && v.lang.startsWith(locale)) : _edgeVoicesAll;
  sel.innerHTML = '<option value="">По умолчанию</option>';
  filtered.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.name;
    opt.textContent = v.name;
    sel.appendChild(opt);
  });
  sel.value = cur;
}

// ElevenLabs voices
let _elevenlabsVoicesLoaded = false, _elevenlabsVoicesAll = [];
function loadElevenlabsVoices() {
  if (_elevenlabsVoicesLoaded) return;
  fetch('/elevenlabs-voices')
    .then(r => r.json())
    .then(data => {
      _elevenlabsVoicesAll = data.voices || [];
      _elevenlabsVoicesLoaded = true;
      const sel = document.getElementById('ttsElevenlabsVoice');
      const cur = sel.value;
      sel.innerHTML = '<option value="">По умолчанию (Rachel)</option>';
      _elevenlabsVoicesAll.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = `${v.name}${v.lang ? ' — ' + v.lang : ''}`;
        sel.appendChild(opt);
      });
      sel.value = cur;
    });
}

// Fish Audio voices
let _fishVoicesLoaded = false, _fishVoicesAll = [];
function loadFishVoices() {
  if (_fishVoicesLoaded) return;
  const sel = document.getElementById('ttsFishVoice');
  sel.innerHTML = '<option value="">Загрузка...</option>';
  fetch('/fish-voices')
    .then(r => r.json())
    .then(data => {
      _fishVoicesAll = data.voices || [];
      _fishVoicesLoaded = true;
      const cur = sel.value;
      sel.innerHTML = '<option value="">По умолчанию</option>';
      _fishVoicesAll.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = `${v.name}${v.lang ? ' — ' + v.lang : ''}`;
        sel.appendChild(opt);
      });
      sel.value = cur;
    });
}

// Re-filter voices when language changes
document.getElementById('language').addEventListener('change', () => {
  if (_macosVoicesLoaded) filterMacosVoices();
  if (_edgeVoicesLoaded) filterEdgeVoices();
});

onTtsEngineChange();

// Описания движков транскрипции — показываются под выпадающим списком
const TRANSCRIBE_DESC = {
  'faster-whisper':
    '<span class="tag good">рекомендуется</span>Whisper large-v3 на движке CTranslate2: то же качество, ' +
    'что у оригинала, но в 3–4 раза быстрее и экономнее по памяти. Режет тишину (VAD), поэтому меньше ' +
    'выдумывает текст на паузах, и показывает фразы <b>прямо во время распознавания</b>. ' +
    'Работает офлайн. Не различает говорящих.',
  'whisperx':
    '<span class="tag">несколько голосов</span>Тот же Whisper large-v3, но с двумя доп. проходами: ' +
    'выравнивание по словам (wav2vec2) даёт <b>самые точные границы фраз</b>, а диаризация (pyannote) ' +
    'помечает, кто говорит — только так работает озвучка разными голосами. Примерно вдвое медленнее, ' +
    'требует <b>HuggingFace Token</b> и фразы появляются только в конце. Берите, если в видео 2+ человека.',
};

function _setEngineDesc(elId, engine) {
  const el = document.getElementById(elId);
  if (!el) return;
  const html = TRANSCRIBE_DESC[engine] || '';
  el.innerHTML = html;
  el.style.display = html ? '' : 'none';
}

function onEngineChange() {
  const eng = document.getElementById('transcribeEngine').value;
  const isWhisperX = eng === 'whisperx';
  document.getElementById('numSpeakersField').style.display = isWhisperX ? '' : 'none';
  document.getElementById('hfTokenField').style.display = isWhisperX ? '' : 'none';
  _setEngineDesc('transcribeEngineDesc', eng);
  persistSetting({transcribe_engine: eng});
}
onEngineChange();

function onDownloadEngineChange() {
  const eng = document.getElementById('downloadModelEngine').value;
  document.getElementById('downloadModelName').style.display = '';
  document.getElementById('whisperxExtraDownloads').style.display = eng === 'whisperx' ? '' : 'none';
  _setEngineDesc('downloadEngineDesc', eng);
}
onDownloadEngineChange();

// Background audio tracks synced with video
let _bgAudio = null;     // no_vocals.wav
let _vocAudio = null;    // vocals.wav (voiceover mode)
let _bgAudioMode = '';
let _bgAudioDir = '';    // для какой папки подключены дорожки
let _bgMissing = false;  // no_vocals.wav ещё не создан (идёт генерация)
let _jobWorkDir = '';    // папка запущенного сейчас задания

function _stopBgAudio() {
  if (_bgAudio) { _bgAudio.pause(); _bgAudio = null; }
  if (_vocAudio) { _vocAudio.pause(); _vocAudio = null; }
  _bgAudioMode = '';
  _bgAudioDir = '';
}

// Папка текущей работы: у восстановленного проекта — resumeWorkDir, у только
// что запущенного приходит с сервера (source_ready). Нужна, чтобы слушать
// перевод прямо во время генерации.
function activeWorkDir() {
  return resumeWorkDir || _jobWorkDir || '';
}

function _setupBgAudio() {
  const mode = document.getElementById('buildOriginalAudio').value;
  const wdir = activeWorkDir();
  const needsBg = (mode === 'no_vocals' || mode === 'voiceover') && wdir;
  if (!needsBg) { _stopBgAudio(); return; }
  if (mode === _bgAudioMode && _bgAudioDir === wdir) return;
  _stopBgAudio();
  _bgMissing = false;
  const wd = encodeURIComponent(wdir);
  _bgAudio = new Audio(`/project-audio?work_dir=${wd}&name=no_vocals.wav`);
  _bgAudio.preload = 'auto';
  // Разделение дорожек делается уже после TTS, поэтому во время генерации
  // фона может ещё не быть — тогда играем оригинал приглушённым
  _bgAudio.addEventListener('error', () => {
    if (_bgMissing) return;
    _bgMissing = true;
    addLog('ℹ️ Фоновая дорожка ещё не готова — предпросмотр с оригинальным звуком');
  });
  if (mode === 'voiceover') {
    _vocAudio = new Audio(`/project-audio?work_dir=${wd}&name=vocals.wav`);
    _vocAudio.preload = 'auto';
  }
  _bgAudioMode = mode;
  _bgAudioDir = wdir;
}

function _syncBgAudio() {
  if (!videoEl) return;
  const mode = document.getElementById('buildOriginalAudio').value;
  if (mode !== 'no_vocals' && mode !== 'voiceover') {
    if (_bgAudio) _bgAudio.pause();
    if (_vocAudio) _vocAudio.pause();
    return;
  }
  const bgVol = parseInt(document.getElementById('buildNoVocalsVolume').value) / 100;
  const vocVol = mode === 'voiceover' ? parseInt(document.getElementById('buildVocalsVolume').value) / 100 : 0;
  [_bgAudio, _vocAudio].forEach((audio, i) => {
    if (!audio) return;
    audio.volume = i === 0 ? bgVol : vocVol;
    if (videoEl.paused) {
      audio.pause();
    } else {
      if (Math.abs(audio.currentTime - videoEl.currentTime) > 0.15) {
        audio.currentTime = videoEl.currentTime;
      }
      audio.playbackRate = videoEl.playbackRate;
      if (audio.paused) audio.play().catch(() => {});
    }
  });
}

// Звук видео глушится в ноль, только если фон реально играет отдельными
// дорожками. Пока их нет (идёт генерация) — подмешиваем оригинал, иначе
// предпрослушивание получилось бы в тишине.
function _bgTracksPlaying() {
  return !_bgMissing && !!_bgAudio;
}
function getTtsDimVolume() {
  const mode = document.getElementById('buildOriginalAudio').value;
  if (mode === 'none') return 0;
  if (mode === 'no_vocals' || mode === 'voiceover') {
    if (_bgTracksPlaying()) return 0;
    return parseInt(document.getElementById('buildVocalsVolume').value) / 100 || 0.15;
  }
  if (mode === 'full') return parseInt(document.getElementById('buildOriginalVolume').value) / 100;
  return 0.08;
}
function getGapVolume() {
  const mode = document.getElementById('buildOriginalAudio').value;
  if (mode === 'none') return 0;
  if (mode === 'no_vocals' || mode === 'voiceover') return _bgTracksPlaying() ? 0 : 1;
  if (mode === 'full') return 1;
  return 1;
}

function onLipsyncToggle() {
  const chk = document.getElementById('lipsyncEnabled');
  if (!chk) return;
  const on = chk.checked;
  document.getElementById('lipsyncEngineField').style.display = on ? 'block' : 'none';
  const step = document.getElementById('lipsyncStep');
  const sep = document.getElementById('lipsyncStepSep');
  if (step) step.style.display = on ? '' : 'none';
  if (sep) sep.style.display = on ? '' : 'none';
}

function _updateLipsyncAvailability() {
  const label = document.getElementById('lipsyncLabel');
  const chk = document.getElementById('lipsyncEnabled');
  if (!label || !chk) return;
  const mode = document.getElementById('buildOriginalAudio').value;
  const allowed = mode === 'none' || mode === 'no_vocals';
  chk.disabled = !allowed;
  label.style.opacity = allowed ? '' : '0.4';
  label.title = allowed ? '' : 'Доступно только в режимах «Без оригинала» и «Только фон»';
  if (!allowed && chk.checked) {
    chk.checked = false;
    onLipsyncToggle();
  }
}

function onOriginalAudioChange() {
  const val = document.getElementById('buildOriginalAudio').value;
  document.getElementById('fullVolumeField').style.display = val === 'full' ? 'block' : 'none';
  document.getElementById('noVocalsVolumeField').style.display = (val === 'no_vocals' || val === 'voiceover') ? 'block' : 'none';
  document.getElementById('vocalsVolumeField').style.display = val === 'voiceover' ? 'block' : 'none';
  // Update player volume and bg audio
  if (videoEl) videoEl.volume = getGapVolume();
  _setupBgAudio();
  _syncBgAudio();
  _updateLipsyncAvailability();
}
onOriginalAudioChange();

// Auto-save on change for all parameter controls
document.getElementById('whisper').addEventListener('change', function() {
  persistSetting({whisper_model: this.value});
});
document.getElementById('translateModel').addEventListener('change', function() {
  persistSetting({translate_model: this.value});
});
document.getElementById('projectName').addEventListener('blur', function() {
  const title = this.value.trim();
  if (resumeWorkDir) {
    fetch('/rename-job', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: resumeWorkDir, title})
    });
    // Update in the resume list
    document.querySelectorAll('.resume-item').forEach(el => {
      if (el.dataset.path === resumeWorkDir) {
        const span = el.querySelector('span > span');
        if (span) span.textContent = title || el.dataset.path.split('/').pop();
      }
    });
  }
});
document.getElementById('anthropicKey').addEventListener('blur', function() {
  const v = this.value.trim();
  if (v) {
    persistSetting({anthropic_api_key: v});
    if (document.getElementById('translateProvider').value === 'claude') onProviderChange();
  }
});
document.getElementById('openaiKey').addEventListener('blur', function() {
  const v = this.value.trim();
  if (v) {
    persistSetting({openai_api_key: v});
    if (document.getElementById('translateProvider').value === 'openai') onProviderChange();
  }
});
document.getElementById('ollamaUrl').addEventListener('blur', function() {
  const v = this.value.trim();
  if (v) persistSetting({ollama_url: v});
});
document.getElementById('customApiUrl').addEventListener('blur', function() {
  const v = this.value.trim();
  if (v) persistSetting({custom_api_url: v});
});
document.getElementById('customApiKey').addEventListener('blur', function() {
  const v = this.value.trim();
  if (v) persistSetting({custom_api_key: v});
});

/* ── Video Upload ────────────────────────────────── */

document.getElementById('uploadVideo').addEventListener('change', function() {
  if (!this.files[0]) return;
  const area = document.getElementById('uploadVideoArea');
  const label = document.getElementById('uploadVideoLabel');
  const pctEl = document.getElementById('uploadPct');
  const bar = document.getElementById('uploadProgressBar');
  const file = this.files[0];
  const fd = new FormData();
  fd.append('file', file);
  fd.append('project_name', document.getElementById('projectName').value.trim());

  addLog('📤 Загружаю видео: ' + file.name + ' (' + (file.size / 1024 / 1024).toFixed(1) + ' MB)...');
  area.classList.remove('loaded');
  area.classList.add('uploading');
  label.textContent = file.name;
  pctEl.textContent = '0%';
  bar.style.width = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload-video');

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.round(e.loaded / e.total * 100);
      pctEl.textContent = pct + '%';
      bar.style.width = pct + '%';
    }
  };

  xhr.onload = () => {
    area.classList.remove('uploading');
    if (xhr.status !== 200) {
      addLog('❌ Ошибка загрузки', 'error');
      label.textContent = 'Нажмите для выбора видео';
      return;
    }
    const data = JSON.parse(xhr.responseText);
    if (data.error) {
      addLog('Ошибка: ' + data.error, 'error');
      label.textContent = 'Нажмите для выбора видео';
      return;
    }
    resumeWorkDir = data.work_dir;
    document.getElementById('url').value = data.path;
    area.classList.add('loaded');
    label.textContent = data.filename;
    addLog('✅ Видео загружено: ' + data.filename);
    showPlayer('/job-video?path=' + encodeURIComponent(data.path));
    refreshJobs();
  };

  xhr.onerror = () => {
    area.classList.remove('uploading');
    label.textContent = 'Нажмите для выбора видео';
    addLog('❌ Ошибка сети при загрузке', 'error');
  };

  xhr.send(fd);
});

/* ── URL Download ────────────────────────────────── */

const _dlBtnSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

function downloadUrl() {
  const url = document.getElementById('url').value.trim();
  if (!url) return alert('Укажите URL видео');
  if (!url.startsWith('http://') && !url.startsWith('https://')) return alert('Укажите ссылку (http/https)');

  const btn = document.getElementById('btnDownload');
  const prog = document.getElementById('downloadProgress');
  const bar = document.getElementById('downloadBar');
  const pctText = document.getElementById('downloadPct');

  btn.disabled = true;
  btn.textContent = 'Скачиваю...';
  prog.classList.add('active');
  bar.style.width = '0%';
  pctText.textContent = '0%';
  addLog('⬇️ Скачиваю видео: ' + url);

  const pname = document.getElementById('projectName').value.trim();
  const es = new EventSource('/download-video?url=' + encodeURIComponent(url) + '&project_name=' + encodeURIComponent(pname));

  es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'progress') {
      bar.style.width = data.pct + '%';
      pctText.textContent = data.pct + '%';
    } else if (data.type === 'log') {
      addLog(data.message);
    } else if (data.type === 'done') {
      es.close();
      btn.disabled = false;
      btn.innerHTML = _dlBtnSvg + ' Скачать';
      prog.classList.remove('active');
      resumeWorkDir = data.work_dir;
      document.getElementById('url').value = data.path;
      addLog('✅ Видео скачано: ' + data.filename);
      showPlayer('/job-video?path=' + encodeURIComponent(data.path));
      refreshJobs();
    } else if (data.type === 'error') {
      es.close();
      btn.disabled = false;
      btn.innerHTML = _dlBtnSvg + ' Скачать';
      prog.classList.remove('active');
      addLog('Ошибка: ' + data.message, 'error');
    }
  };

  es.onerror = () => {
    es.close();
    btn.disabled = false;
    btn.innerHTML = _dlBtnSvg + ' Скачать';
    prog.classList.remove('active');
  };
}

/* ── SRT Upload ──────────────────────────────────── */

document.getElementById('uploadOrig').addEventListener('change', function() {
  if (!this.files[0]) return;
  const fd = new FormData();
  fd.append('file', this.files[0]);
  fetch('/upload-srt', {method: 'POST', body: fd})
    .then(r => r.json())
    .then(data => {
      if (data.error) return addLog('❌ SRT: ' + data.error, 'error');
      uploadedOriginal = data.subtitles;
      originalSubs = data.subtitles;
      if (!subtitles.length) subtitles = data.subtitles;
      _setUploadState('uploadOrigBtn', 'uploadOrigIcon', true);
      document.getElementById('uploadOrigLabel').textContent = this.files[0].name;
      addLog(`📝 Загружены оригинальные субтитры: ${data.subtitles.length} фраз`);
      renderSubtitles();
      autoSaveSubs();
    });
});

document.getElementById('uploadTrans').addEventListener('change', function() {
  if (!this.files[0]) return;
  const fd = new FormData();
  fd.append('file', this.files[0]);
  fetch('/upload-srt', {method: 'POST', body: fd})
    .then(r => r.json())
    .then(data => {
      if (data.error) return addLog('❌ SRT: ' + data.error, 'error');
      uploadedTranslated = data.subtitles;
      subtitles = data.subtitles;
      hasTranslation = true;
      _setUploadState('uploadTransBtn', 'uploadTransIcon', true);
      document.getElementById('uploadTransLabel').textContent = this.files[0].name;
      addLog(`🌐 Загружены переведённые субтитры: ${data.subtitles.length} фраз`);
      renderSubtitles();
      autoSaveSubs();
    });
});

/* ── Resume ──────────────────────────────────────── */

function showConfirm(icon, title, message, onConfirm) {
  document.getElementById('confirmIcon').innerHTML = icon;
  document.getElementById('confirmTitle').textContent = title;
  document.getElementById('confirmMessage').innerHTML = message;
  document.getElementById('confirmDeleteBtn').onclick = () => { closeConfirm(); onConfirm(); };
  document.getElementById('confirmOverlay').classList.add('open');
}

function closeConfirm() {
  document.getElementById('confirmOverlay').classList.remove('open');
}

function deleteJob(e, path, btn) {
  e.stopPropagation();
  const name = path.split('/').pop();
  showConfirm('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>', 'Удалить проект?',
    'Папка <b>' + name + '</b> будет удалена с диска. Это действие нельзя отменить.',
    () => executeDeleteJob(path, btn, name)
  );
}

function executeDeleteJob(path, btn, name) {
  closeConfirm();

  // Close project first to release file handles (video player etc.)
  const wasOpen = resumeWorkDir === path;
  if (wasOpen) {
    closeProject();
  }

  // Small delay to let OS release file handles after closing player
  setTimeout(() => {
  fetch('/delete-job', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path})
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return addLog('❌ Ошибка: ' + data.error, 'error');
    addLog('🗑️ Проект ' + name + ' удалён');
    refreshJobs();
  })
  .catch(err => addLog('❌ Ошибка удаления: ' + err, 'error'));
  }, wasOpen ? 500 : 0);
}

function downloadJobZip(path) {
  const a = document.createElement('a');
  a.href = '/download-job-zip?path=' + encodeURIComponent(path);
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function lockStep(checkId, unlockId) {
  const chk = document.getElementById(checkId);
  chk.checked = false;
  chk.disabled = true;
  chk.parentElement.style.opacity = '.5';
  document.getElementById(unlockId).style.display = '';
  updateStartBtn();
}

function unlockStep(checkId, unlockId) {
  const chk = document.getElementById(checkId);
  chk.disabled = false;
  chk.checked = true;
  chk.parentElement.style.opacity = '';
  document.getElementById(unlockId).style.display = 'none';
  updateStartBtn();
}

function refreshJobs() {
  const icon = document.getElementById('refreshJobsIcon');
  icon.style.animation = 'spin .6s linear infinite';
  fetch('/past-jobs').then(r => r.json()).then(data => {
    icon.style.animation = '';
    _updatePastJobs(data);
  }).catch(() => { icon.style.animation = ''; });
}

function loadPastJobs() {
  fetch('/past-jobs').then(r => r.json()).then(data => _updatePastJobs(data));
}

function _updatePastJobs(data) {
  const panel = document.getElementById('resumePanel');
  const list = document.getElementById('resumeList');
  const countEl = document.getElementById('resumeCount');
  const jobs = data.jobs || [];
  if (countEl) countEl.textContent = `Проекты (${jobs.length})`;
  if (!jobs.length) {
    list.innerHTML = '';
    return;
  }
  list.innerHTML = jobs.map((j, i) => `
    <div class="resume-item" id="job-r${i}" onclick="resumeJob(this.dataset.path, this)" data-path="${j.path.replaceAll('\\', '/')}">
      <span style="display:flex;flex-direction:column;gap:1px">
        <span>${j.title || j.name.replace('job_', '').replaceAll('_', ' ')}</span>
        ${j.title ? '<span style="font-size:9px;color:var(--fg3)">' + j.name + '</span>' : ''}
      </span>
      <span class="badges">
        ${j.has_srt ? '<span class="badge badge-srt">srt</span>' : ''}
        ${j.has_trans ? '<span class="badge badge-trans">trn</span>' : ''}
        ${j.has_tts ? '<span class="badge badge-tts">tts</span>' : ''}
      </span>
      <button class="btn-job-action" onclick="event.stopPropagation();downloadJobZip(this.closest('.resume-item').dataset.path)" title="Скачать .zip">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      </button>
      <button class="btn-delete-job" onclick="deleteJob(event, this.closest('.resume-item').dataset.path, this)" title="Удалить">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
  `).join('');
}

function closeProject() {
  _stopBgAudio();
  resumeWorkDir = null;
  currentJobId = null;
  sourceVideoSrc = null;
  outputVideoPath = null;
  _playerShowingOutput = false;
  subtitles = [];
  originalSubs = [];
  uploadedOriginal = null;
  uploadedTranslated = null;
  hasTranslation = false;
  document.getElementById('url').value = '';
  document.getElementById('projectName').value = '';
  document.getElementById('subsList').innerHTML =
    '<div class="subs-empty"><div class="subs-empty-icon">T</div>Субтитры появятся после транскрипции или загрузки .srt файла</div>';
  document.getElementById('subsCount').textContent = '';
  const pw = document.getElementById('playerWrap');
  // Release video file handle before removing element (Windows keeps file locked otherwise)
  const vid = document.querySelector('#playerArea video');
  if (vid) { vid.pause(); vid.removeAttribute('src'); vid.load(); }
  document.getElementById('playerArea').innerHTML = '<div class="placeholder"><div class="placeholder-icon">V</div><span>Видео появится после загрузки</span></div>';
  document.getElementById('playerButtons').innerHTML = '';
  document.getElementById('videoInfo').classList.remove('visible');
  pw.classList.add('empty');
  pw.style.height = '';
  videoEl = null;
  stopTtsSync();
  if (syncInterval) { clearInterval(syncInterval); syncInterval = null; }
  _setUploadState('uploadOrigBtn', 'uploadOrigIcon', false);
  document.getElementById('uploadOrigLabel').textContent = 'Оригинал .srt';
  _setUploadState('uploadTransBtn', 'uploadTransIcon', false);
  document.getElementById('uploadTransLabel').textContent = 'Перевод .srt';
  ['doTranscribe','doTranslate','doTts','doBuild'].forEach(id => {
    const chk = document.getElementById(id);
    chk.checked = id === 'doTranscribe';
    chk.disabled = false;
    chk.parentElement.style.opacity = '';
  });
  document.getElementById('subsAndParams').style.display = 'none';
  document.getElementById('unlockTranscribe').style.display = 'none';
  document.getElementById('unlockTranslate').style.display = 'none';
  document.getElementById('unlockTts').style.display = 'none';
  ttsWorkDir = '';
  _jobWorkDir = '';
  _bgMissing = false;
  ttsSegments = new Set();
  aeDuration = 0; aePixelsPerSec = 0; aeZoomLevel = 10; aeSegments = []; aePeaks = [];
  document.getElementById('audioEditor').innerHTML = '<div class="placeholder"><div class="placeholder-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div><span>Редактор появится после загрузки</span></div>';
  document.getElementById('aeButtons').style.display = 'none';
  document.querySelectorAll('.step').forEach(s => s.className = 'step');
  document.querySelectorAll('.resume-item').forEach(e => e.style.background = '');
  document.getElementById('btnCloseProject').style.display = 'none';
  _setRunningUI(false);
  document.getElementById('sourceSection').style.display = '';
  speakerMap = {};
  speakerVoiceMap = {};
  document.getElementById('speakerMapping').style.display = 'none';
  document.getElementById('log').innerHTML = '';
  logMsgCount = 0;
  document.getElementById('logCount').textContent = '0 записей';
  _updateSubsDownloadButtons();
  loadPastJobs();
  addLog('📋 Проект закрыт. Готов к новому.');
}

function resumeJob(path, el) {
  // Reset state from previous project before loading new one
  _stopBgAudio();
  subtitles = [];
  originalSubs = [];
  uploadedOriginal = null;
  uploadedTranslated = null;
  hasTranslation = false;
  speakerMap = {};
  speakerVoiceMap = {};
  document.getElementById('speakerMapping').style.display = 'none';
  ttsWorkDir = '';
  _jobWorkDir = '';
  _bgMissing = false;
  ttsSegments = new Set();
  aeDuration = 0; aePixelsPerSec = 0; aeZoomLevel = 10; aeSegments = []; aePeaks = [];
  document.getElementById('audioEditor').innerHTML = '<div class="placeholder"><div class="placeholder-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div><span>Редактор появится после загрузки</span></div>';
  document.getElementById('aeButtons').style.display = 'none';
  document.getElementById('subsList').innerHTML = '';
  document.getElementById('subsCount').textContent = '';
  _setUploadState('uploadOrigBtn', 'uploadOrigIcon', false);
  document.getElementById('uploadOrigLabel').textContent = 'Оригинал .srt';
  _setUploadState('uploadTransBtn', 'uploadTransIcon', false);
  document.getElementById('uploadTransLabel').textContent = 'Перевод .srt';
  sourceVideoSrc = null;
  outputVideoPath = null;
  _playerShowingOutput = false;
  stopTtsSync();
  if (syncInterval) { clearInterval(syncInterval); syncInterval = null; }
  document.getElementById('log').innerHTML = '';
  logMsgCount = 0;
  document.getElementById('logCount').textContent = '0 записей';
  ['doTranscribe','doTranslate','doTts','doBuild'].forEach(id => {
    const chk = document.getElementById(id);
    chk.checked = id === 'doTranscribe';
    chk.disabled = false;
    chk.parentElement.style.opacity = '';
  });
  document.getElementById('unlockTranscribe').style.display = 'none';
  document.getElementById('unlockTranslate').style.display = 'none';
  document.getElementById('unlockTts').style.display = 'none';

  _updateSubsDownloadButtons();
  addLog('📂 Загружаю данные из ' + path.replace(/\\/g, '/').split('/').pop() + '...');
  fetch('/resume-job', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path})
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return addLog(data.error, 'error');
    resumeWorkDir = data.work_dir;
      document.getElementById('sourceSection').style.display = 'none';

    // Set project name from meta
    if (data.meta && data.meta.title) {
      document.getElementById('projectName').value = data.meta.title;
    }

    // Reset steps bar
    document.querySelectorAll('.step').forEach(s => s.className = 'step');

    // Auto-check skip for available data
    if (data.source_video) {
      document.getElementById('url').value = data.source_video;
      addLog('🎬 Найдено исходное видео');
      setStep('download', 'done');
    }
    if (data.original_subs && data.original_subs.length) {
      uploadedOriginal = data.original_subs;
      originalSubs = data.original_subs;
      lockStep('doTranscribe', 'unlockTranscribe');
      setStep('transcribe', 'done');
      _setUploadState('uploadOrigBtn', 'uploadOrigIcon', true);
      document.getElementById('uploadOrigLabel').textContent = 'Оригинал .srt';
      addLog(`📝 Загружены оригинальные субтитры: ${data.original_subs.length} фраз`);
    }
    if (data.translated_subs && data.translated_subs.length) {
      uploadedTranslated = data.translated_subs;
      hasTranslation = true;
      setStep('translate', 'done');
      lockStep('doTranslate', 'unlockTranslate');
      _setUploadState('uploadTransBtn', 'uploadTransIcon', true);
      document.getElementById('uploadTransLabel').textContent = 'Перевод .srt';
      addLog(`🌐 Загружены переведённые субтитры: ${data.translated_subs.length} фраз`);
    }

    // Load speaker mapping if available
    if (data.speaker_map) {
      speakerMap = data.speaker_map;
    }
    if (data.speaker_voice_mapping) {
      speakerVoiceMap = data.speaker_voice_mapping;
    }
    // Show speaker mapping panel if speakers detected
    const allSpeakers = new Set(Object.values(speakerMap));
    if (allSpeakers.size > 1) {
      showSpeakerMappingPanel([...allSpeakers].sort());
    }

    // Show subtitles immediately
    if (data.translated_subs && data.translated_subs.length) {
      subtitles = data.translated_subs;
      renderSubtitles();
    } else if (data.original_subs && data.original_subs.length) {
      subtitles = data.original_subs;
      renderSubtitles();
    }

    // TTS audio available
    if (data.has_tts) {
      ttsWorkDir = data.work_dir;
      setStep('tts', 'done');
      loadTtsSegments(ttsWorkDir, true);
    }

    // Output video available
    if (data.output_video) {
      setStep('build', 'done');
      addLog('🎬 Найдено переведённое видео');
    }

    // Always show source video in player, pass output path for download button
    if (data.source_video) {
      showPlayer(
        '/job-video?path=' + encodeURIComponent(data.source_video),
        data.output_video || null
      );
    }

    // Highlight selected item
    document.querySelectorAll('.resume-item').forEach(e => e.style.background = '');
    if (el) el.style.background = 'rgba(16,185,129,.1)';

    document.getElementById('btnCloseProject').style.display = '';
    document.getElementById('resumePanel').classList.remove('open');
    addLog('🚀 Готово к продолжению. Нажмите «Начать».');
    updateStartBtn();
  });
}

/* ── Start job ───────────────────────────────────── */

function deleteOrigSubs() {
  showConfirm(
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
    'Удалить оригинальные субтитры?',
    'Субтитры будут удалены из текущего проекта.',
    () => {
      closeConfirm();
      originalSubs = [];
      uploadedOriginal = null;
      if (!uploadedTranslated) subtitles = [];
      _setUploadState('uploadOrigBtn', 'uploadOrigIcon', false);
      document.getElementById('uploadOrigLabel').textContent = 'Оригинал .srt';
      const chk = document.getElementById('doTranscribe');
      chk.disabled = false; chk.checked = true; chk.parentElement.style.opacity = '';
      document.getElementById('unlockTranscribe').style.display = 'none';
      renderSubtitles();
      if (resumeWorkDir) {
        fetch('/save-subs', { method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({work_dir: resumeWorkDir, type: 'original', delete: true}) });
      }
      addLog('🗑️ Оригинальные субтитры удалены');
      updateStartBtn();
    }
  );
}

function deleteTransSubs() {
  showConfirm(
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
    'Удалить переведённые субтитры?',
    'Субтитры будут удалены из текущего проекта.',
    () => {
      closeConfirm();
      subtitles = originalSubs.length ? [...originalSubs] : [];
      uploadedTranslated = null;
      hasTranslation = false;
      _setUploadState('uploadTransBtn', 'uploadTransIcon', false);
      document.getElementById('uploadTransLabel').textContent = 'Перевод .srt';
      const chk = document.getElementById('doTranslate');
      chk.disabled = false; chk.checked = true; chk.parentElement.style.opacity = '';
      document.getElementById('unlockTranslate').style.display = 'none';
      renderSubtitles();
      if (resumeWorkDir) {
        fetch('/save-subs', { method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({work_dir: resumeWorkDir, type: 'translated', delete: true}) });
      }
      addLog('🗑️ Переведённые субтитры удалены');
      updateStartBtn();
    }
  );
}

function updateStartBtn() {
  // пока идёт обработка, кнопка работает как «Остановить» — не блокируем её
  if (jobRunning) return;
  const any = ['doTranscribe','doTranslate','doTts','doBuild'].some(id => {
    const chk = document.getElementById(id);
    return chk && chk.checked && !chk.disabled;
  });
  document.getElementById('btnStart').disabled = !any;
}
updateStartBtn();


/* Одна и та же кнопка: пока идёт обработка — «Остановить <этап>» */
function _setRunningUI(running, stage) {
  const btn = document.getElementById('btnStart');
  if (!btn) return;
  jobRunning = running;
  const label = document.getElementById('btnStartLabel');
  if (running) {
    btn.classList.remove('btn-start');
    btn.classList.add('btn-stop');
    btn.disabled = false;
    btn.innerHTML = _ICON_STOP + '<span id="btnStartLabel">Остановить' +
      (STAGE_LABELS[stage] ? ' ' + STAGE_LABELS[stage] : '') + '</span>';
  } else {
    btn.classList.remove('btn-stop');
    btn.classList.add('btn-start');
    btn.innerHTML = _ICON_PLAY + '<span id="btnStartLabel">Начать</span>';
    updateStartBtn();
  }
  if (label && !running) label.textContent = 'Начать';
}

function onMainButton() {
  if (jobRunning) stopJob();
  else startJob();
}

function stopJob() {
  if (!currentJobId) return;
  const btn = document.getElementById('btnStart');
  const label = document.getElementById('btnStartLabel');
  if (btn) btn.disabled = true;
  if (label) label.textContent = 'Останавливаю...';
  addLog('⏹️ Останавливаю ' + (STAGE_LABELS[currentStage] || 'обработку') + '...');
  fetch('/stop/' + currentJobId, {method: 'POST'})
    .then(r => r.json())
    .then(d => { if (d.error) { addLog('❌ ' + d.error, 'error'); _setRunningUI(true, currentStage); } })
    .catch(e => { addLog('❌ Не удалось остановить: ' + e, 'error'); _setRunningUI(true, currentStage); });
}

function startJob() {
  const url = document.getElementById('url').value.trim();
  const skipTranscribe = !document.getElementById('doTranscribe').checked;
  const skipTranslate = !document.getElementById('doTranslate').checked;
  const skipTts = !document.getElementById('doTts').checked;
  const skipBuild = !document.getElementById('doBuild').checked;

  if (!url && !resumeWorkDir) {
    return alert('Укажите URL видео или выберите процесс для восстановления');
  }

  document.getElementById('btnStart').disabled = true;
  document.getElementById('log').innerHTML = '';
  logMsgCount = 0;
  document.getElementById('logCount').textContent = '0 записей';
  // Expand log panel on start
  document.getElementById('logPanel').classList.remove('collapsed');
  document.getElementById('logResizeHandle').classList.remove('hidden');
  document.querySelectorAll('.step').forEach(s => s.className = 'step');
  if (!subtitles.length && !originalSubs.length) {
    document.getElementById('subsList').innerHTML =
      '<div class="subs-empty"><div class="subs-empty-icon">T</div>Ожидание...</div>';
    document.getElementById('subsCount').textContent = '';
  }

  // Mark skipped steps visually
  if (skipTranscribe) setStep('transcribe', 'skipped');
  if (skipTranslate) setStep('translate', 'skipped');
  if (skipTts) setStep('tts', 'skipped');
  if (skipBuild) setStep('build', 'skipped');

  // Only reset player if no video loaded yet
  if (!videoEl) {
    document.getElementById('playerArea').innerHTML = '<div class="placeholder"><div class="placeholder-icon">V</div><span>Загрузка...</span></div>';
    document.getElementById('playerWrap').classList.add('empty');
  }

  const body = {
    url,
    language: document.getElementById('language').value,
    project_name: document.getElementById('projectName').value.trim(),
    source_language: document.getElementById('sourceLanguage').value,
    whisper_model: document.getElementById('whisper').value,
    transcribe_engine: document.getElementById('transcribeEngine').value,
    skip_transcribe: skipTranscribe,
    skip_translate: skipTranslate,
    skip_tts: skipTts,
    skip_build: skipBuild,
    separate_vocals: document.getElementById('separateVocals').checked,
    merge_sentences: document.getElementById('mergeSentences')?.checked ?? true,
    translate_provider: document.getElementById('translateProvider').value,
    translate_model: getTranslateModel(),
    build_format: document.getElementById('buildFormat').value,
    build_codec: document.getElementById('buildCodec').value,
    build_preset: document.getElementById('buildPreset').value,
    build_audio_bitrate: document.getElementById('buildAudioBitrate').value,
    build_max_slowdown: parseFloat(document.getElementById('buildMaxSlowdown').value),
    build_original_audio: document.getElementById('buildOriginalAudio').value,
    build_original_volume: parseInt(document.getElementById('buildOriginalVolume').value) / 100,
    build_no_vocals_volume: parseInt(document.getElementById('buildNoVocalsVolume').value) / 100,
    build_vocals_volume: parseInt(document.getElementById('buildVocalsVolume').value) / 100,
    build_burn_subs: document.getElementById('buildBurnSubs').checked,
    build_start_sec: parseFloat(document.getElementById('buildStartSec').value) || 0,
    build_end_sec: parseFloat(document.getElementById('buildEndSec').value) || 0,
    lipsync_enabled: document.getElementById('lipsyncEnabled')?.checked || false,
    lipsync_engine: document.getElementById('lipsyncEngine')?.value || '',
    tts_engine: document.getElementById('ttsEngine').value,
    tts_voice: getTtsVoice(),
    tts_seed: getTtsSeed(),
    tts_temperature: parseFloat(document.getElementById('ttsTemperature').value) || 0.7,
    tts_speed: parseFloat(document.getElementById('ttsSpeed').value) || 1.0,
    num_speakers: parseInt(document.getElementById('numSpeakers').value) || 0,
    speaker_voice_map: Object.keys(speakerVoiceMap).length ? speakerVoiceMap : undefined,
  };
  if (hasTranslation && subtitles.length) body.translated_subs = subtitles;
  else if (uploadedTranslated) body.translated_subs = uploadedTranslated;
  if (originalSubs.length) body.original_subs = originalSubs;
  else if (uploadedOriginal) body.original_subs = uploadedOriginal;
  if (resumeWorkDir) body.work_dir = resumeWorkDir;

  fetch('/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) {
      addLog(data.error, 'error');
      _setRunningUI(false);
      return;
    }
    currentJobId = data.job_id;
    _setRunningUI(true, '');
    document.getElementById('btnCloseProject').style.display = '';
    addLog('⚡ Запущен процесс: ' + data.job_id);
    loadPastJobs();
    listenProgress(data.job_id);
  })
  .catch(e => {
    addLog('❌ Ошибка: ' + e, 'error');
    _setRunningUI(false);
  });
}

/* ── SSE Progress ────────────────────────────────── */

function listenProgress(jobId) {
  const es = new EventSource('/progress/' + jobId);

  es.addEventListener('log', e => {
    addLog(JSON.parse(e.data).message);
  });

  es.addEventListener('step', e => {
    const d = JSON.parse(e.data);
    setStep(d.key, d.state);
    if (d.state === 'active') { currentStage = d.key; _setRunningUI(true, d.key); }
  });

  es.addEventListener('cancelled', e => {
    const d = JSON.parse(e.data);
    addLog(d.message || '⏹️ Остановлено');
    if (d.stage) setStep(d.stage, '');
    _setRunningUI(false);
    currentStage = '';
    es.close();
  });

  es.addEventListener('source_ready', e => {
    const d = JSON.parse(e.data);
    if (d.work_dir) {
      _jobWorkDir = d.work_dir;
      _setupBgAudio();  // фоновые дорожки доступны и для нового задания
    }
    if (!videoEl) {
      showPlayer('/source/' + jobId, null, d.path);
    }
    loadPastJobs();
  });

  es.addEventListener('original_ready', () => {
    loadSubtitles(jobId, 'original').then(() => {
      const allSpeakers = new Set();
      (originalSubs.length ? originalSubs : subtitles).forEach(s => {
        if (s.speaker) { speakerMap[String(s.index)] = s.speaker; allSpeakers.add(s.speaker); }
      });
      if (allSpeakers.size > 1) showSpeakerMappingPanel([...allSpeakers].sort());
    });
    _setUploadState('uploadOrigBtn', 'uploadOrigIcon', true);
    document.getElementById('uploadOrigLabel').textContent = 'Оригинал .srt';
    lockStep('doTranscribe', 'unlockTranscribe');
  });

  es.addEventListener('subtitles_ready', e => {
    let d = {};
    try { d = JSON.parse(e.data) || {}; } catch (_) {}
    loadSubtitles(jobId, 'translated');
    // Перевод пропущен и субтитры — копия оригинала: замок не нужен
    if (d.translated === false) { hasTranslation = false; return; }
    hasTranslation = true;
    _setUploadState('uploadTransBtn', 'uploadTransIcon', true);
    document.getElementById('uploadTransLabel').textContent = 'Перевод .srt';
    lockStep('doTranslate', 'unlockTranslate');
  });

  let _subAddTimer = null;
  es.addEventListener('sub_add', e => {
    const d = JSON.parse(e.data);
    if (d.mode === 'original') {
      const sub = d.sub;
      if (sub) {
        originalSubs.push(sub);
        subtitles = originalSubs;
      }
    } else if (d.mode === 'translated') {
      hasTranslation = true;
      // Ensure subtitles is a separate array from originalSubs
      if (subtitles === originalSubs) subtitles = [...originalSubs];
      const subs = d.subs || [];
      subs.forEach(s => {
        const idx = subtitles.findIndex(x => x.index === s.index);
        if (idx >= 0) subtitles[idx] = s;
        else subtitles.push(s);
      });
    }
    const _chars = subtitles.reduce((s, sub) => s + (sub.text || '').length, 0);
    document.getElementById('subsCount').textContent = `${subtitles.length} фраз / ${_chars} симв.`;
    // Throttle rendering to avoid UI lag
    if (!_subAddTimer) {
      _subAddTimer = setTimeout(() => { _subAddTimer = null; renderSubtitles(); }, 300);
    }
  });

  let _ttsSegTimer = null;
  es.addEventListener('tts_segment', e => {
    const d = JSON.parse(e.data);
    ttsSegments.add(d.index);
    // Готовые сегменты можно слушать сразу, не дожидаясь конца генерации
    if (!ttsWorkDir) {
      ttsWorkDir = d.work_dir || activeWorkDir();
      if (ttsWorkDir) addLog('🎧 Первый сегмент готов — можно включить видео и слушать перевод');
    }
    // Перерисовку троттлим: на длинном списке она сбивала бы прокрутку
    if (!_ttsSegTimer) {
      _ttsSegTimer = setTimeout(() => {
        _ttsSegTimer = null;
        renderSubtitles();
        loadAeSegments();
      }, 400);
    }
  });

  es.addEventListener('tts_ready', e => {
    const d = JSON.parse(e.data);
    ttsWorkDir = d.work_dir;
    loadTtsSegments(ttsWorkDir, true);
    lockStep('doTts', 'unlockTts');
    loadPastJobs();
  });

  es.addEventListener('done', e => {
    const d = JSON.parse(e.data);
    addLog('🎉 Готово! ' + d.path);
    _setRunningUI(false);
    currentStage = '';
    // Update output path and rebuild player buttons without resetting video
    outputVideoPath = d.path || null;
    _rebuildPlayerButtons();
    es.close();
  });

  es.addEventListener('error', e => {
    try { addLog(JSON.parse(e.data).message, 'error'); } catch (_) {}
    _setRunningUI(false);
    currentStage = '';
    es.close();
  });

  es.addEventListener('ping', () => {});
}

/* ── Player ──────────────────────────────────────── */

function fmtDuration(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0 ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
               : `${m}:${String(s).padStart(2,'0')}`;
}

function loadVideoInfo(src) {
  const vi = document.getElementById('videoInfo');
  if (!vi) return;
  // Extract path from src URL or use direct path
  let path = '';
  if (src.startsWith('/')) {
    if (src.includes('?path=')) path = decodeURIComponent(src.split('path=')[1]);
  } else {
    path = src; // direct file path
  }
  if (!path) { vi.classList.remove('visible'); return; }

  fetch('/video-info?path=' + encodeURIComponent(path))
    .then(r => r.json())
    .then(d => {
      if (d.error) { vi.classList.remove('visible'); return; }
      let items = [];
      items.push(`<span class="vi-item"><span class="vi-label">Длительность:</span><span class="vi-val">${fmtDuration(d.duration)}</span></span>`);
      items.push(`<span class="vi-item"><span class="vi-label">Размер:</span><span class="vi-val">${d.size_mb} MB</span></span>`);
      items.push(`<span class="vi-item"><span class="vi-label">Битрейт:</span><span class="vi-val">${d.bitrate} kbps</span></span>`);
      if (d.video) {
        items.push(`<span class="vi-item"><span class="vi-label">Видео:</span><span class="vi-val">${d.video.codec} ${d.video.width}×${d.video.height}</span></span>`);
        if (d.video.fps) items.push(`<span class="vi-item"><span class="vi-label">FPS:</span><span class="vi-val">${typeof d.video.fps === 'number' ? d.video.fps.toFixed(1) : d.video.fps}</span></span>`);
        if (d.video.bitrate) items.push(`<span class="vi-item"><span class="vi-label">V-битрейт:</span><span class="vi-val">${d.video.bitrate} kbps</span></span>`);
      }
      if (d.audio) {
        items.push(`<span class="vi-item"><span class="vi-label">Аудио:</span><span class="vi-val">${d.audio.codec} ${d.audio.sample_rate}Hz ${d.audio.channels}ch</span></span>`);
        if (d.audio.bitrate) items.push(`<span class="vi-item"><span class="vi-label">A-битрейт:</span><span class="vi-val">${d.audio.bitrate} kbps</span></span>`);
      }
      vi.innerHTML = items.join('');
      vi.classList.add('visible');
      // Init audio editor
      initAudioEditor(path, d.duration);
    });
}

function showPlayer(src, outputPath, videoPath) {
  sourceVideoSrc = src;
  outputVideoPath = outputPath || null;
  _playerShowingOutput = false;
  document.getElementById('btnCloseProject').style.display = '';
  document.getElementById('sourceSection').style.display = 'none';
  document.getElementById('subsAndParams').style.display = '';
  document.getElementById('aeButtons').style.display = 'flex';
  const pw = document.getElementById('playerWrap');
  const pa = document.getElementById('playerArea');
  pw.classList.remove('empty');
  if (videoEl) {
    videoEl.src = src;
    videoEl.load();
  } else {
    pa.innerHTML = '';
    videoEl = document.createElement('video');
    videoEl.controls = true;
    videoEl.src = src;
    videoEl.addEventListener('pause', () => { stopTtsSync(); const b = document.getElementById('aePlayBtn'); if (b) b.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>'; });
    videoEl.addEventListener('play', () => { const b = document.getElementById('aePlayBtn'); if (b) b.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>'; });
    videoEl.addEventListener('seeking', () => { _ttsSeeking = true; });
    videoEl.addEventListener('seeked', () => {
      for (const audio of ttsSyncPlaying.values()) {
        audio.pause();
        audio.onloadedmetadata = null;
        audio.src = '';
      }
      ttsSyncPlaying.clear();
      ttsSyncDone.clear();
      // Sync bg audio position on seek
      if (_bgAudio) _bgAudio.currentTime = videoEl.currentTime;
      if (_vocAudio) _vocAudio.currentTime = videoEl.currentTime;
      // Delay TTS creation to avoid double-play race condition
      setTimeout(() => { _ttsSeeking = false; }, 150);
    });
    pa.appendChild(videoEl);
    // Re-add subtitle overlay
    const ps = document.createElement('div');
    ps.className = 'player-subs';
    ps.id = 'playerSubs';
    pa.appendChild(ps);
    startSubSync();
  }
  // Setup background audio for no_vocals/voiceover modes
  _setupBgAudio();
  // Load video info
  loadVideoInfo(videoPath || src);
  // Header buttons
  const pb = document.getElementById('playerButtons');
  pb.innerHTML = '';

  // Subtitles mode buttons
  const subsGroup = document.createElement('div');
  subsGroup.id = 'playerSubsMode';
  subsGroup.className = 'subs-mode-group';
  subsGroup.dataset.mode = 'off';
  subsGroup.style.cssText = 'display:inline-flex;gap:1px;background:var(--bg3);border-radius:4px;border:1px solid var(--border);overflow:hidden';
  const subsModes = [
    {mode:'off', title:'Выкл', svg:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="4" y1="4" x2="20" y2="20"/></svg>'},
    {mode:'translated', title:'Перевод', svg:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 10h8"/><path d="M8 14h4"/></svg>'},
    {mode:'original', title:'Оригинал', svg:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M12 8v4"/><circle cx="12" cy="16" r="0.5"/></svg>'},
    {mode:'both', title:'Оба', svg:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 9h8"/><path d="M8 13h8"/></svg>'},
  ];
  subsModes.forEach(m => {
    const btn = document.createElement('button');
    btn.title = m.title;
    btn.dataset.mode = m.mode;
    btn.style.cssText = 'background:none;border:none;cursor:pointer;padding:3px 5px;color:var(--fg3);display:inline-flex;align-items:center';
    if (m.mode === 'off') btn.style.color = 'var(--fg)';
    btn.innerHTML = m.svg;
    btn.onclick = () => {
      subsGroup.dataset.mode = m.mode;
      subsGroup.querySelectorAll('button').forEach(b => b.style.color = 'var(--fg3)');
      btn.style.color = 'var(--fg)';
    };
    subsGroup.appendChild(btn);
  });
  pb.appendChild(subsGroup);

  _rebuildPlayerButtons();
}

function _rebuildPlayerButtons() {
  const pb = document.getElementById('playerButtons');
  if (!pb) return;
  // Keep subtitle buttons (first child group), remove the rest
  const subsGroup = pb.querySelector('.subs-mode-group');
  pb.innerHTML = '';
  if (subsGroup) pb.appendChild(subsGroup);

  const src = sourceVideoSrc;
  const outputPath = outputVideoPath;
  const dlSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

  // Download source button
  if (src) {
    const dlSrc = document.createElement('a');
    dlSrc.href = src;
    dlSrc.download = '';
    dlSrc.title = 'Скачать исходное видео';
    dlSrc.style.cssText = 'text-decoration:none;cursor:pointer;opacity:.7;padding:2px 4px;color:var(--fg2);display:inline-flex;align-items:center';
    dlSrc.innerHTML = dlSvg;
    dlSrc.onmouseover = () => dlSrc.style.opacity = '1';
    dlSrc.onmouseout = () => dlSrc.style.opacity = '.7';
    pb.appendChild(dlSrc);
  }

  // Toggle original/translated video button
  if (outputPath) {
    const toggleBtn = document.createElement('button');
    toggleBtn.id = 'btnToggleVideo';
    toggleBtn.title = 'Переключить оригинал / перевод';
    toggleBtn.style.cssText = 'background:none;border:1px solid var(--border);border-radius:4px;cursor:pointer;padding:2px 8px;color:var(--fg2);display:inline-flex;align-items:center;gap:4px;font-size:11px;font-family:inherit';
    const switchSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>';
    toggleBtn.innerHTML = switchSvg + ' <span id="toggleVideoLabel">Переведённое</span>';
    if (_playerShowingOutput) {
      document.getElementById('toggleVideoLabel')?.remove();
      toggleBtn.style.color = '#10b981';
      toggleBtn.innerHTML = switchSvg + ' <span id="toggleVideoLabel">Оригинальное</span>';
    }
    toggleBtn.onclick = () => {
      const curTime = videoEl.currentTime;
      const wasPlaying = !videoEl.paused;
      if (_playerShowingOutput) {
        videoEl.src = sourceVideoSrc;
        _playerShowingOutput = false;
        document.getElementById('toggleVideoLabel').textContent = 'Переведённое';
        toggleBtn.style.color = 'var(--fg2)';
      } else {
        videoEl.src = '/job-video?path=' + encodeURIComponent(outputVideoPath);
        _playerShowingOutput = true;
        document.getElementById('toggleVideoLabel').textContent = 'Оригинальное';
        toggleBtn.style.color = '#10b981';
      }
      videoEl.load();
      videoEl.addEventListener('loadeddata', function onLoaded() {
        videoEl.removeEventListener('loadeddata', onLoaded);
        videoEl.currentTime = curTime;
        if (wasPlaying) videoEl.play().catch(() => {});
      });
    };
    pb.appendChild(toggleBtn);

    // Download output button (green)
    const dlOut = document.createElement('a');
    dlOut.href = '/job-video?path=' + encodeURIComponent(outputPath);
    dlOut.download = '';
    dlOut.title = 'Скачать переведённое видео';
    dlOut.style.cssText = 'text-decoration:none;cursor:pointer;opacity:.7;padding:2px 4px;color:#10b981;display:inline-flex;align-items:center';
    dlOut.innerHTML = dlSvg;
    dlOut.onmouseover = () => dlOut.style.opacity = '1';
    dlOut.onmouseout = () => dlOut.style.opacity = '.7';
    pb.appendChild(dlOut);

    // Delete output button
    const delBtn = document.createElement('button');
    delBtn.title = 'Удалить переведённое видео';
    delBtn.style.cssText = 'background:none;border:none;cursor:pointer;opacity:.7;padding:2px 4px;color:var(--red);display:inline-flex;align-items:center';
    delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';
    delBtn.onmouseover = () => delBtn.style.opacity = '1';
    delBtn.onmouseout = () => delBtn.style.opacity = '.7';
    delBtn.onclick = () => {
      showConfirm('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>', 'Удалить переведённое видео?', 'Файл будет удалён с диска.', () => {
        fetch('/delete-output', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path: outputVideoPath})
        })
        .then(r => r.json())
        .then(d => {
          if (d.error) return addLog('❌ ' + d.error, 'error');
          addLog('🗑️ Переведённое видео удалено');
          outputVideoPath = null;
          _playerShowingOutput = false;
          _rebuildPlayerButtons();
        });
      });
    };
    pb.appendChild(delBtn);
  }
}

function stopTtsSync() {
  for (const audio of ttsSyncPlaying.values()) {
    audio.pause();
    audio.onloadedmetadata = null;
    audio.src = '';
  }
  ttsSyncPlaying.clear();
  ttsSyncDone.clear();
  if (videoEl) { videoEl.volume = getGapVolume(); videoEl.playbackRate = 1; }
  if (_bgAudio) _bgAudio.pause();
  if (_vocAudio) _vocAudio.pause();
}

function startSubSync() {
  if (syncInterval) clearInterval(syncInterval);
  syncInterval = setInterval(() => {
    if (!videoEl || !subtitles.length) return;
    const t = videoEl.currentTime;

    // Find active subtitle
    let activeIdx = -1;
    document.querySelectorAll('.sub-row').forEach((row, i) => {
      const sub = subtitles[i];
      if (sub && t >= sub.start && t <= sub.end) {
        activeIdx = i;
        if (!row.classList.contains('active')) {
          row.classList.add('active');
          if (!_subsScrollLocked) row.scrollIntoView({block: 'nearest', behavior: 'smooth'});
        }
      } else {
        row.classList.remove('active');
      }
    });

    // Show subtitle on video
    const psEl = document.getElementById('playerSubs');
    const psModeEl = document.getElementById('playerSubsMode');
    const psMode = psModeEl ? (psModeEl.dataset.mode || 'off') : 'off';
    if (psEl) {
      if (psMode === 'off' || activeIdx < 0) {
        psEl.classList.remove('visible');
      } else {
        const orig = originalSubs[activeIdx]?.text || '';
        const trans = subtitles[activeIdx]?.text || '';
        let html = '';
        if (psMode === 'translated') html = trans || orig;
        else if (psMode === 'original') html = orig || trans;
        else if (psMode === 'both') {
          if (orig && trans && orig !== trans)
            html = '<span style="opacity:.7;font-size:13px">' + orig.replace(/</g,'&lt;') + '</span><br>' + trans.replace(/</g,'&lt;');
          else html = (trans || orig).replace(/</g,'&lt;');
        }
        if (html) {
          psEl.innerHTML = html;
          psEl.classList.add('visible');
        } else {
          psEl.classList.remove('visible');
        }
      }
    }

    // Update audio editor playhead
    updateAePlayhead();

    // TTS overlay playback — support multiple simultaneous segments
    if (!ttsWorkDir || !ttsSegments.size || videoEl.paused || _ttsSeeking) return;

    // Find all TTS segments active at current time
    const activeTtsIdxs = new Set();
    subtitles.forEach(sub => {
      if (sub && ttsSegments.has(sub.index) && t >= sub.start && t <= sub.end) {
        activeTtsIdxs.add(sub.index);
      }
    });

    // Stop segments that are no longer active
    for (const idx of ttsSyncPlaying.keys()) {
      if (!activeTtsIdxs.has(idx)) {
        const audio = ttsSyncPlaying.get(idx);
        audio.pause();
        audio.onloadedmetadata = null;
        audio.src = '';
        ttsSyncPlaying.delete(idx);
      }
    }
    // Clear done flag when segment leaves active zone (allows replay on seek)
    for (const idx of ttsSyncDone) {
      if (!activeTtsIdxs.has(idx)) ttsSyncDone.delete(idx);
    }

    // Start new segments and keep playing ones in sync
    const maxSlow = parseFloat(document.getElementById('buildMaxSlowdown').value) || 3;
    for (const idx of activeTtsIdxs) {
      const sub = subtitles.find(s => s && s.index === idx);
      const slotDur = sub ? sub.end - sub.start : 0;
      const videoOffset = sub ? t - sub.start : 0;

      if (ttsSyncPlaying.has(idx)) {
        continue;
      }

      if (ttsSyncDone.has(idx)) continue;

      const segFile = `seg_${String(idx).padStart(4,'0')}.wav`;
      const url = `/tts-audio?path=${encodeURIComponent(ttsWorkDir + '/tts_audio/' + segFile)}`;
      const audio = new Audio(url);
      audio.preload = 'auto';
      let cancelled = false;
      const startOffset = videoOffset;
      audio._ttsIdx = idx;
      audio.onloadedmetadata = () => {
        // Don't play if this audio was cancelled (seek/stop happened during load)
        if (cancelled || !ttsSyncPlaying.has(idx) || ttsSyncPlaying.get(idx) !== audio) {
          audio.src = '';
          return;
        }
        let factor = 1;
        if (slotDur > 0 && audio.duration > slotDur) {
          factor = Math.min(audio.duration / slotDur, maxSlow);
          videoEl.playbackRate = 1 / factor;
        }
        if (startOffset > 0.05) {
          audio.currentTime = Math.min(startOffset * factor, audio.duration - 0.05);
        }
        audio.play();
      };
      audio.onended = () => {
        if (ttsSyncPlaying.get(idx) === audio) {
          ttsSyncPlaying.delete(idx);
          ttsSyncDone.add(idx);
          if (ttsSyncPlaying.size === 0) videoEl.playbackRate = 1;
        }
      };
      // If there's already an audio for this idx (shouldn't happen, but safety)
      if (ttsSyncPlaying.has(idx)) {
        const old = ttsSyncPlaying.get(idx);
        old.pause(); old.onloadedmetadata = null; old.src = '';
      }
      ttsSyncPlaying.set(idx, audio);
    }

    // Dim/restore video volume — check if any TTS is near (within 0.8s)
    const anyTtsNear = activeTtsIdxs.size > 0 || subtitles.some(sub =>
      sub && ttsSegments.has(sub.index) && t >= sub.start - 0.8 && t <= sub.end + 0.8
    );
    if (activeTtsIdxs.size > 0) {
      videoEl.volume = getTtsDimVolume();
    } else if (!anyTtsNear && ttsSyncPlaying.size === 0) {
      videoEl.volume = getGapVolume();
      videoEl.playbackRate = 1;
    }
    // Sync background audio (no_vocals)
    _syncBgAudio();
  }, 100);
}

/* ── Subtitles ───────────────────────────────────── */

let ttsWorkDir = '';
let ttsSegments = new Set();
let ttsAudioEl = null;
let ttsPlayingIdx = null;      // какой сегмент играет сейчас — переживает перерисовку списка
let ttsSyncPlaying = new Map(); // index -> Audio
let ttsSyncDone = new Set();   // segments that finished in this playback
let _ttsSeeking = false;       // suppress TTS creation during seek

const _TTS_ICON_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const _TTS_ICON_PAUSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';

/** Кнопку ищем по id, а не по ссылке: список перерисовывается во время генерации. */
function _ttsPlayBtnState(index, playing) {
  const b = document.getElementById(`tts-play-${index}`);
  if (!b) return;
  b.classList.toggle('playing', playing);
  b.innerHTML = playing ? _TTS_ICON_PAUSE : _TTS_ICON_PLAY;
}

function _stopTtsPreview() {
  if (ttsAudioEl) { ttsAudioEl.pause(); ttsAudioEl.src = ''; ttsAudioEl = null; }
  if (ttsPlayingIdx !== null) {
    const idx = ttsPlayingIdx;
    ttsPlayingIdx = null;
    _ttsPlayBtnState(idx, false);
  }
}


function loadTtsSegments(workDir, thenRender) {
  fetch('/tts-segments?work_dir=' + encodeURIComponent(workDir))
    .then(r => r.json())
    .then(d => {
      ttsSegments = new Set(d.segments || []);
      if (thenRender) renderSubtitles();
      // Lock TTS step if all segments exist
      const total = Math.max(subtitles.length, originalSubs.length);
      if (total > 0 && ttsSegments.size >= total) {
        lockStep('doTts', 'unlockTts');
      }
      // Refresh audio editor segments
      loadAeSegments();
    });
}

function loadSubtitles(jobId, mode) {
  return fetch('/subtitles/' + jobId)
    .then(r => r.json())
    .then(data => {
      if (data.original) originalSubs = data.original;
      if (data.translated) subtitles = data.translated;

      if (mode === 'original' && data.original) {
        // After transcription — show originals as editable
        subtitles = data.original;
        renderSubtitles();
      } else if (mode === 'translated' && data.translated) {
        // After translation — show both
        subtitles = data.translated;
        renderSubtitles();
      }
    });
}

function renderSubtitles() {
  _updateSubsDownloadButtons();
  const list = document.getElementById('subsList');
  const savedScroll = list.scrollTop;
  list.innerHTML = '';
  const rows = Math.max(subtitles.length, originalSubs.length);
  const showBoth = originalSubs.length > 0;
  const _totalChars = subtitles.reduce((s, sub) => s + (sub.text || '').length, 0);
  document.getElementById('subsCount').textContent = rows ? `${rows} фраз / ${_totalChars} симв.` : '';

  if (rows === 0) {
    list.innerHTML = '<div class="subs-empty"><div class="subs-empty-icon">T</div>Субтитры появятся после транскрипции или загрузки .srt файла</div>';
    return;
  }

  for (let i = 0; i < rows; i++) {
    const sub = subtitles[i] || originalSubs[i];
    const row = document.createElement('div');
    row.className = 'sub-row';
    if (showBoth) row.classList.add('has-translation');

    row.onclick = e => {
      if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
      if (videoEl) { stopTtsSync(); videoEl.currentTime = sub.start; }
    };

    const idx = document.createElement('div');
    idx.className = 'idx';
    idx.textContent = sub.index;
    const spk = sub.speaker || (subtitles[i] && subtitles[i].speaker) || speakerMap[String(sub.index)];
    if (spk) {
      const allSpeakers = [...new Set(Object.values(speakerMap))].sort();
      const badge = document.createElement('div');
      badge.className = 'speaker-badge';
      const spkNum = parseInt(spk.replace(/\D/g, '')) || 0;
      badge.textContent = 'S' + spkNum;
      badge.style.background = SPEAKER_COLORS[spkNum % SPEAKER_COLORS.length];
      badge.style.cursor = 'pointer';
      badge.title = 'Клик — сменить спикера';
      badge.onclick = (e) => {
        e.stopPropagation();
        if (allSpeakers.length < 2) return;
        const curIdx = allSpeakers.indexOf(spk);
        const nextSpk = allSpeakers[(curIdx + 1) % allSpeakers.length];
        // Update speaker in all arrays
        if (subtitles[i]) subtitles[i].speaker = nextSpk;
        if (originalSubs[i]) originalSubs[i].speaker = nextSpk;
        speakerMap[String(sub.index)] = nextSpk;
        // Save speaker map
        if (resumeWorkDir) {
          fetch('/save-speaker-mapping', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ work_dir: resumeWorkDir, mapping: speakerMap, type: 'speaker_map' })
          });
        }
        autoSaveSubs();
        renderSubtitles();
        loadAeSegments();
      };
      idx.appendChild(badge);
    }

    const time = document.createElement('div');
    time.className = 'time';

    const del = document.createElement('button');
    del.className = 'btn-delete-sub';
    del.title = 'Удалить';
    del.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg><span style="font-size:9px;margin-left:2px">SUB</span>';
    del.onclick = (e) => {
      e.stopPropagation();
      const text = (subtitles[i]?.text || originalSubs[i]?.text || '').substring(0, 50);
      showConfirm('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>', 'Удалить субтитр?',
        `Субтитр #${sub.index}: <b>${text}${text.length >= 50 ? '...' : ''}</b>`,
        () => {
          if (subtitles.length > i) subtitles.splice(i, 1);
          if (originalSubs.length > i) originalSubs.splice(i, 1);
          subtitles.forEach((s, j) => s.index = j + 1);
          if (originalSubs.length) originalSubs.forEach((s, j) => s.index = j + 1);
          renderSubtitles();
          autoSaveSubs();
        }
      );
    };

    const startInput = document.createElement('input');
    startInput.type = 'text';
    startInput.className = 'time-input';
    startInput.value = fmtTime(sub.start);
    startInput.title = 'Начало';
    startInput.onclick = e => e.stopPropagation();
    startInput.onchange = () => {
      const val = parseTime(startInput.value);
      if (val !== null) {
        sub.start = val;
        if (subtitles[i]) subtitles[i].start = val;
        if (originalSubs[i]) originalSubs[i].start = val;
        autoSaveSubs();
        loadAeSegments();
      } else {
        startInput.value = fmtTime(sub.start);
      }
    };

    const endInput = document.createElement('input');
    endInput.type = 'text';
    endInput.className = 'time-input';
    endInput.value = fmtTime(sub.end);
    endInput.title = 'Конец';
    endInput.onclick = e => e.stopPropagation();
    endInput.onchange = () => {
      const val = parseTime(endInput.value);
      if (val !== null) {
        sub.end = val;
        if (subtitles[i]) subtitles[i].end = val;
        if (originalSubs[i]) originalSubs[i].end = val;
        autoSaveSubs();
        loadAeSegments();
      } else {
        endInput.value = fmtTime(sub.end);
      }
    };

    time.appendChild(startInput);
    time.appendChild(endInput);
    const timeActions = document.createElement('div');
    timeActions.className = 'time-actions';
    timeActions.appendChild(del);
    time.appendChild(timeActions);

    const texts = document.createElement('div');
    texts.className = 'texts';

    // Original text
    if (originalSubs[i]) {
      const orig = document.createElement('textarea');
      orig.className = 'text text-orig-edit';
      orig.value = originalSubs[i].text;
      orig.rows = 1;
      orig.placeholder = 'Оригинал';
      orig.oninput = () => { originalSubs[i].text = orig.value; autoResize(orig); autoSaveSubs(); };
      texts.appendChild(orig);
      row.classList.add('has-translation');
    }

    // Translation text — always show (empty if no translation yet)
    if (!subtitles[i] && originalSubs[i]) {
      subtitles[i] = { index: originalSubs[i].index, start: originalSubs[i].start, end: originalSubs[i].end, text: '' };
    }
    if (subtitles[i]) {
      const text = document.createElement('textarea');
      text.className = 'text';
      text.value = subtitles[i].text;
      text.rows = 1;
      text.placeholder = 'Перевод';
      text.oninput = () => { subtitles[i].text = text.value; autoResize(text); autoSaveSubs(); };
      texts.appendChild(text);
    }

    const actions = document.createElement('div');
    actions.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:2px;flex-shrink:0';

    // TTS play button (only if segment file exists)
    if (ttsWorkDir && sub.index && ttsSegments.has(sub.index)) {
      const segFile = `seg_${String(sub.index).padStart(4,'0')}.wav`;
      const audioUrl = `/tts-audio?path=${encodeURIComponent(ttsWorkDir + '/tts_audio/' + segFile)}`;
      const play = document.createElement('button');
      play.className = 'btn-play-tts';
      play.id = `tts-play-${sub.index}`;
      play.title = 'Прослушать TTS';
      // список перерисовывается на каждый новый сегмент — восстанавливаем вид играющей кнопки
      const isPlaying = ttsPlayingIdx === sub.index;
      play.classList.toggle('playing', isPlaying);
      play.innerHTML = isPlaying ? _TTS_ICON_PAUSE : _TTS_ICON_PLAY;
      play.onclick = (e) => {
        e.stopPropagation();
        const idx = sub.index;
        const wasPlaying = ttsPlayingIdx === idx;
        _stopTtsPreview();          // останавливаем и этот сегмент, и любой другой
        if (wasPlaying) return;     // повторный клик = пауза
        if (voicePreviewEl) { voicePreviewEl.pause(); voicePreviewEl = null; }
        document.querySelectorAll('.btn-play-tts.playing').forEach(b => {
          b.classList.remove('playing'); b.innerHTML = _TTS_ICON_PLAY;
        });
        const audio = new Audio(audioUrl + '&t=' + Date.now());
        ttsAudioEl = audio;
        ttsPlayingIdx = idx;
        _ttsPlayBtnState(idx, true);
        const finish = () => {
          if (ttsAudioEl !== audio) return;   // уже переключились на другой сегмент
          ttsAudioEl = null;
          ttsPlayingIdx = null;
          _ttsPlayBtnState(idx, false);
        };
        audio.onended = finish;
        audio.onerror = finish;
        audio.play().catch(finish);
      };
      actions.appendChild(play);

      // TTS delete button — in time column
      const delTts = document.createElement('button');
      delTts.className = 'btn-delete-sub';
      delTts.title = 'Удалить TTS сегмент';
      delTts.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg><span style="font-size:9px;margin-left:2px">TTS</span>';
      delTts.onclick = (e) => {
        e.stopPropagation();
        if (ttsPlayingIdx === sub.index) _stopTtsPreview();
        const segPath = ttsWorkDir + '/tts_audio/' + segFile;
        fetch('/delete-tts-segment', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path: segPath})
        })
        .then(r => r.json())
        .then(d => {
          if (d.error) return addLog('❌ ' + d.error, 'error');
          ttsSegments.delete(sub.index);
          renderSubtitles();
          loadAeSegments();
        });
      };
      timeActions.appendChild(delTts);
    }

    // TTS generate single segment button
    if (resumeWorkDir && sub.index && subtitles[i] && subtitles[i].text) {
      const gen = document.createElement('button');
      gen.className = 'btn-tts-gen';
      gen.title = 'Сгенерировать TTS для этого сегмента';
      gen.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
      gen.onclick = (e) => {
        e.stopPropagation();
        if (gen.classList.contains('generating')) return;
        gen.classList.add('generating');
        gen.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>';
        // Use speaker-specific voice if available
        const spk = sub.speaker || speakerMap[String(sub.index)];
        const spkCfg = spk && speakerVoiceMap[spk];
        const ttsEngine = spkCfg ? spkCfg.engine : document.getElementById('ttsEngine').value;
        const ttsVoice = spkCfg ? spkCfg.voice : getTtsVoice();
        const _genSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
        fetch('/tts-single', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            work_dir: resumeWorkDir,
            text: subtitles[i].text,
            index: sub.index,
            tts_engine: ttsEngine,
            tts_voice: ttsVoice,
            tts_seed: getTtsSeed(),
            tts_temperature: parseFloat(document.getElementById('ttsTemperature').value) || 0.7,
    tts_speed: parseFloat(document.getElementById('ttsSpeed').value) || 1.0,
          })
        })
        .then(r => r.json())
        .then(d => {
          if (d.error) { gen.classList.remove('generating'); gen.innerHTML = _genSvg; addLog('❌ TTS: ' + d.error, 'error'); return; }
          const es = new EventSource('/tts-task-progress/' + d.task_id);
          es.addEventListener('log', e => { addLog(JSON.parse(e.data).message); });
          es.addEventListener('done', () => {
            es.close();
            gen.classList.remove('generating');
            gen.innerHTML = _genSvg;
            addLog(`✅ TTS сегмент ${sub.index} сгенерирован`);
            ttsWorkDir = resumeWorkDir;
            ttsSegments.add(sub.index);
            stopTtsSync();
            renderSubtitles();
            loadAeSegments();
          });
          es.addEventListener('error', e => {
            es.close();
            gen.classList.remove('generating');
            gen.innerHTML = _genSvg;
            try { addLog('❌ TTS: ' + JSON.parse(e.data).message, 'error'); } catch(_) {}
          });
        });
      };
      actions.appendChild(gen);
    }

    row.append(idx, time, texts, actions);
    list.appendChild(row);
  }

  // autoResize after all rows are in DOM
  list.querySelectorAll('textarea').forEach(el => autoResize(el));
  list.scrollTop = savedScroll;
  // suppress auto-scroll from timeupdate briefly
  _subsScrollLocked = true;
  setTimeout(() => { _subsScrollLocked = false; }, 300);
}

/* ── Resize handle (player / subtitles) ──────────── */

(function() {
  const handle = document.getElementById('resizeHandle');
  const subsPanel = document.querySelector('.subs-panel');
  const content = handle.parentElement;
  let dragging = false;
  let startX, startW;

  handle.addEventListener('mousedown', e => {
    e.preventDefault();
    dragging = true;
    startX = e.clientX;
    startW = subsPanel.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const delta = startX - e.clientX; // drag left = wider subs
    const newW = Math.max(250, Math.min(startW + delta, content.offsetWidth - 200));
    subsPanel.style.flex = 'none';
    subsPanel.style.width = newW + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

/* ── Resize handle (log panel) ────────────────────── */

(function() {
  const handle = document.getElementById('logResizeHandle');
  const logBody = document.querySelector('.log-body');
  let dragging = false;
  let startY, startH;

  handle.addEventListener('mousedown', e => {
    e.preventDefault();
    dragging = true;
    startY = e.clientY;
    startH = logBody.offsetHeight;
    handle.classList.add('dragging');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const delta = startY - e.clientY; // inverted: drag up = bigger
    const newH = Math.max(50, Math.min(startH + delta, window.innerHeight - 200));
    logBody.style.height = newH + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

/* ── Resize handle (player top/bottom) ───────────── */

(function() {
  const handle = document.getElementById('playerResizeHandle');
  const playerArea = document.getElementById('playerArea');
  const playerBottom = document.getElementById('playerBottom');
  let dragging = false;
  let startY, startTopH, startBotH;

  handle.addEventListener('mousedown', e => {
    e.preventDefault();
    dragging = true;
    startY = e.clientY;
    startTopH = playerArea.offsetHeight;
    startBotH = playerBottom.offsetHeight;
    handle.classList.add('dragging');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const delta = e.clientY - startY;
    const newTop = Math.max(60, startTopH + delta);
    const newBot = Math.max(40, startBotH - delta);
    playerArea.style.flex = 'none';
    playerArea.style.height = newTop + 'px';
    playerBottom.style.flex = 'none';
    playerBottom.style.height = newBot + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

/* ── Voice management ─────────────────────────────── */

let _voiceFile = null;
let _lastUploadedWav = '';

document.getElementById('voiceFile').addEventListener('change', function() {
  if (!this.files[0]) return;
  _voiceFile = this.files[0];
  _lastUploadedWav = '';
  document.getElementById('voiceFileLabel').textContent = _voiceFile.name;
});

function saveVoice() {
  const name = document.getElementById('voiceName').value.trim();
  const text = document.getElementById('voiceText').value.trim();
  if (!name) return alert('Укажите имя голоса');
  if (!_voiceFile) return alert('Выберите WAV файл');

  const fd = new FormData();
  fd.append('file', _voiceFile);
  fd.append('name', name);
  fd.append('text', text);

  fetch('/upload-voice', {method: 'POST', body: fd})
    .then(r => r.json())
    .then(data => {
      if (data.error) return addLog('❌ ' + data.error, 'error');
      addLog('🎤 Семпл добавлен в голос "' + data.name + '": ' + data.file);
      document.getElementById('voiceName').value = '';
      document.getElementById('voiceText').value = '';
      document.getElementById('voiceFileLabel').textContent = 'Выбрать WAV';
      _voiceFile = null;
      _lastUploadedWav = data.path;
      // Select this voice
      loadVoicesList().then(() => {
        document.getElementById('ttsVoice').value = data.name;
        persistSetting({tts_voice: data.name});
      });
    });
}

function _getTranscribeSettings() {
  return {
    engine: document.getElementById('transcribeEngine').value,
    model: document.getElementById('whisper').value,
  };
}

function _showTranscribeProgress(show) {
  document.getElementById('btnTranscribeVoice').disabled = show;
  document.getElementById('transcribeVoiceIcon').style.display = show ? 'none' : '';
  document.getElementById('transcribeVoiceSpinner').style.display = show ? 'inline-block' : 'none';
  document.getElementById('transcribeVoiceProgress').style.display = show ? 'block' : 'none';
  document.getElementById('transcribeVoiceStatus').textContent = show ? 'Транскрибирую...' : '';
}

function transcribeVoice() {
  if (!_voiceFile && !_lastUploadedWav) return alert('Сначала выберите WAV файл');
  const ts = _getTranscribeSettings();
  addLog('🎙️ Транскрибирую референс (' + ts.engine + ' / ' + ts.model + ')...');
  _showTranscribeProgress(true);

  if (_lastUploadedWav) {
    fetch('/transcribe-voice', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({wav: _lastUploadedWav, ...ts})
    })
    .then(r => r.json())
    .then(data => {
      _showTranscribeProgress(false);
      if (data.error) return addLog('❌ ' + data.error, 'error');
      document.getElementById('voiceText').value = data.text;
      addLog('✅ Транскрипция: ' + data.text.substring(0, 80));
    })
    .catch(() => _showTranscribeProgress(false));
    return;
  }

  const fd = new FormData();
  fd.append('file', _voiceFile);
  fd.append('name', '_temp_transcribe');
  fd.append('text', '');

  fetch('/upload-voice', {method: 'POST', body: fd})
    .then(r => r.json())
    .then(data => {
      if (data.error) { _showTranscribeProgress(false); return addLog('❌ ' + data.error, 'error'); }
      document.getElementById('transcribeVoiceStatus').textContent = 'Загружено, транскрибирую...';
      return fetch('/transcribe-voice', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({wav: data.path, ...ts})
      });
    })
    .then(r => r.json())
    .then(data => {
      _showTranscribeProgress(false);
      if (data.error) return addLog('❌ ' + data.error, 'error');
      document.getElementById('voiceText').value = data.text;
      addLog('✅ Транскрипция: ' + data.text.substring(0, 80));
      fetch('/delete-voice', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: '_temp_transcribe'})
      });
    })
    .catch(() => _showTranscribeProgress(false));
}

function loadVoicesList() {
  return fetch('/voices')
    .then(r => r.json())
    .then(data => {
      const list = document.getElementById('voicesList');
      const sel = document.getElementById('ttsVoice');
      if (!list) return;
      // Update select
      const curVal = sel.value;
      sel.innerHTML = '<option value="">По умолчанию</option>';
      (data.voices || []).forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.name; opt.textContent = v.name;
        sel.appendChild(opt);
      });
      sel.value = curVal;
      // Update list
      if (!data.voices.length) {
        list.innerHTML = '<div style="color:var(--fg3);font-size:11px;padding:8px 0">Нет сохранённых голосов</div>';
        return;
      }
      list.innerHTML = data.voices.map(v => `
        <div class="voice-card" style="padding:8px 0;border-bottom:1px solid var(--border)">
          <div style="display:flex;align-items:center;gap:8px;font-size:12px;margin-bottom:4px">
            <span style="font-family:'JetBrains Mono',monospace;font-weight:600"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg> ${v.name}</span>
            <span style="color:var(--fg3);font-size:10px">${v.samples[0]?.file || ''}</span>
            <button class="btn-play-tts" onclick="playVoiceWav('${v.name}','${v.samples[0]?.file || ''}',this)" title="Прослушать" style="margin-left:auto;opacity:1"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button>
            <button class="btn-delete-job" onclick="deleteVoice('${v.name}')" style="opacity:1" title="Удалить голос">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          ${v.samples.map(s => `
            <div style="padding:4px 0 4px 16px;font-size:11px">
              <textarea placeholder="Текст в записи..."
                     class="voice-text-area"
                     style="width:100%;padding:6px 8px;font-size:12px;background:var(--bg3);border:1px solid transparent;border-radius:4px;color:var(--fg);outline:none;font-family:inherit;resize:none;overflow-y:hidden;box-sizing:border-box;min-height:36px;line-height:1.5"
                     onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='transparent';updateSampleText('${v.name}','${s.file}',this.value)"
                     oninput="this.style.height='0';this.style.height=this.scrollHeight+'px'">${s.text || ''}</textarea>
            </div>
          `).join('')}
        </div>
      `).join('');
      requestAnimationFrame(() => {
        list.querySelectorAll('.voice-text-area').forEach(el => {
          el.style.height = '0';
          el.style.height = Math.max(el.scrollHeight, 36) + 'px';
        });
      });
    });
}

function deleteVoice(name) {
  showConfirm('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>', 'Удалить голос?',
    'Голос <b>' + name + '</b> и все его семплы будут удалены.',
    () => {
      fetch('/delete-voice', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name})
      })
      .then(r => r.json())
      .then(data => {
        if (data.error) return addLog('❌ ' + data.error, 'error');
        addLog('🗑️ Голос "' + name + '" удалён');
        const sel = document.getElementById('ttsVoice');
        for (const opt of sel.options) { if (opt.value === name) { opt.remove(); break; } }
        loadVoicesList();
      });
    }
  );
}

function updateSampleText(name, file, text) {
  fetch('/update-voice-sample', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, file, text})
  });
}

let voicePreviewEl = null;
function playVoiceWav(name, file, btn) {
  if (btn.classList.contains('playing')) {
    if (voicePreviewEl) { voicePreviewEl.pause(); voicePreviewEl = null; }
    btn.classList.remove('playing'); btn.innerHTML = _TTS_ICON_PLAY;
    return;
  }
  if (voicePreviewEl) { voicePreviewEl.pause(); voicePreviewEl = null; }
  _stopTtsPreview();
  document.querySelectorAll('.btn-play-tts.playing').forEach(b => { b.classList.remove('playing'); b.innerHTML = _TTS_ICON_PLAY; });
  const url = `/voice-audio?name=${encodeURIComponent(name)}&file=${encodeURIComponent(file)}`;
  voicePreviewEl = new Audio(url);
  btn.classList.add('playing');
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
  voicePreviewEl.play();
  voicePreviewEl.onended = () => { btn.classList.remove('playing'); btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>'; voicePreviewEl = null; };
  voicePreviewEl.onerror = () => { btn.classList.remove('playing'); btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>'; voicePreviewEl = null; };
}

function testTtsVoice() {
  const text = document.getElementById('ttsTestText').value.trim();
  if (!text) return;
  const btn = document.getElementById('btnTtsTest');
  btn.disabled = true;
  document.getElementById('ttsTestIcon').style.display = 'none';
  document.getElementById('ttsTestSpinner').style.display = 'inline-block';

  const ttsEngine = document.getElementById('ttsEngine').value;
  const ttsVoice = getTtsVoice();

  fetch('/tts-test', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text, tts_engine: ttsEngine, tts_voice: ttsVoice, tts_seed: getTtsSeed(), tts_temperature: parseFloat(document.getElementById('ttsTemperature').value) || 0.7, tts_speed: parseFloat(document.getElementById('ttsSpeed').value) || 1.0 })
  })
  .then(r => r.json())
  .then(d => {
    if (d.error) { addLog('❌ TTS тест: ' + d.error, 'error'); btn.disabled = false; document.getElementById('ttsTestIcon').style.display = ''; document.getElementById('ttsTestSpinner').style.display = 'none'; return; }
    const es = new EventSource('/tts-task-progress/' + d.task_id);
    es.addEventListener('log', e => { addLog(JSON.parse(e.data).message); });
    es.addEventListener('done', e => {
      es.close();
      btn.disabled = false;
      document.getElementById('ttsTestIcon').style.display = '';
      document.getElementById('ttsTestSpinner').style.display = 'none';
      const data = JSON.parse(e.data);
      const audio = new Audio('/tts-test-audio?path=' + encodeURIComponent(data.audio_path));
      audio.play();
    });
    es.addEventListener('error', e => {
      es.close();
      btn.disabled = false;
      document.getElementById('ttsTestIcon').style.display = '';
      document.getElementById('ttsTestSpinner').style.display = 'none';
      try { addLog('❌ TTS тест: ' + JSON.parse(e.data).message, 'error'); } catch(_) {}
    });
  });
}


function downloadWhisperxExtra(type) {
  const engine = 'whisperx-' + type;
  // For align, use source language; for diarize, model name doesn't matter
  const model = type === 'align'
    ? (document.getElementById('sourceLanguage').value || 'ru')
    : 'pyannote';
  const btn = document.getElementById(type === 'align' ? 'btnDownloadAlign' : 'btnDownloadDiarize');
  const status = document.getElementById('downloadExtraStatus');
  const msg = document.getElementById('downloadExtraMsg');
  btn.disabled = true;
  status.style.display = 'block';
  msg.textContent = 'Загружаю...';

  fetch('/download-model', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({engine, model})
  }).then(r => {
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    function read() {
      reader.read().then(({done, value}) => {
        if (done) { finish(); return; }
        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const d = JSON.parse(line.substring(6));
            if (d.type === 'log') { msg.textContent = d.message; addLog(d.message); }
            if (d.type === 'done') { msg.textContent = d.message; addLog(d.message); finish(); return; }
            if (d.type === 'error') { msg.textContent = d.message; addLog('❌ ' + d.message, 'error'); finish(); return; }
          } catch(_) {}
        }
        read();
      });
    }
    function finish() {
      btn.disabled = false;
      setTimeout(() => { status.style.display = 'none'; }, 3000);
      loadModels();
    }
    read();
  });
}

function downloadModel() {
  const engine = document.getElementById('downloadModelEngine').value;
  const model = engine === 'tts-qwen3' ? 'qwen3-tts' : document.getElementById('downloadModelName').value;
  const btn = document.getElementById('btnDownloadModel');
  const status = document.getElementById('downloadModelStatus');
  const msg = document.getElementById('downloadModelMsg');

  btn.disabled = true;
  document.getElementById('downloadModelIcon').style.display = 'none';
  document.getElementById('downloadModelSpinner').style.display = 'inline-block';
  status.style.display = 'block';
  msg.textContent = 'Загружаю...';

  fetch('/download-model', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({engine, model})
  }).then(r => {
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    function read() {
      reader.read().then(({done, value}) => {
        if (done) { finish(); return; }
        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const d = JSON.parse(line.substring(6));
            if (d.type === 'log') { msg.textContent = d.message; addLog(d.message); }
            if (d.type === 'done') { msg.textContent = d.message; addLog(d.message); finish(); return; }
            if (d.type === 'error') { msg.textContent = d.message; addLog('❌ ' + d.message, 'error'); finish(); return; }
          } catch(_) {}
        }
        read();
      });
    }

    function finish() {
      btn.disabled = false;
      document.getElementById('downloadModelIcon').style.display = '';
      document.getElementById('downloadModelSpinner').style.display = 'none';
      setTimeout(() => { status.style.display = 'none'; }, 3000);
      loadModels();
    }

    read();
  });
}

function downloadTtsModel() {
  const engine = document.getElementById('downloadTtsEngine').value;
  const model = document.getElementById('downloadTtsModel').value.trim() || engine;
  const btn = document.getElementById('btnDownloadTts');
  const status = document.getElementById('downloadTtsStatus');
  const msg = document.getElementById('downloadTtsMsg');

  btn.disabled = true;
  document.getElementById('downloadTtsIcon').style.display = 'none';
  document.getElementById('downloadTtsSpinner').style.display = 'inline-block';
  status.style.display = 'block';
  msg.textContent = 'Загружаю...';

  fetch('/download-model', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({engine, model})
  }).then(r => {
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    function read() {
      reader.read().then(({done, value}) => {
        if (done) { finish(); return; }
        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const d = JSON.parse(line.substring(6));
            if (d.type === 'log') { msg.textContent = d.message; addLog(d.message); }
            if (d.type === 'done') { msg.textContent = d.message; addLog(d.message); finish(); return; }
            if (d.type === 'error') { msg.textContent = d.message; addLog('❌ ' + d.message, 'error'); finish(); return; }
          } catch(_) {}
        }
        read();
      });
    }

    function finish() {
      btn.disabled = false;
      document.getElementById('downloadTtsIcon').style.display = '';
      document.getElementById('downloadTtsSpinner').style.display = 'none';
      setTimeout(() => { status.style.display = 'none'; }, 3000);
      loadTtsModels();
    }

    read();
  });
}

function loadModels() {
  const list = document.getElementById('modelsList');
  list.innerHTML = 'Загрузка...';
  fetch('/downloaded-models?category=whisper')
    .then(r => r.json())
    .then(data => {
      if (!data.models.length) {
        list.innerHTML = '<div style="color:var(--fg3);padding:8px 0">Нет загруженных моделей</div>';
        return;
      }
      list.innerHTML = data.models.map(m => `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
          <span style="font-family:'JetBrains Mono',monospace;font-weight:500;color:var(--fg)">${m.name}</span>
          <span style="color:var(--fg3);font-size:10px">${m.engine}</span>
          <span style="color:var(--fg3);font-size:10px;margin-left:auto">${m.size}</span>
          <button class="btn-delete-job" onclick="deleteModel('${m.path}','${m.name}',this)" style="opacity:1;padding:2px" title="Удалить">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
      `).join('');
    });
}

function loadTtsModels() {
  const list = document.getElementById('ttsModelsList');
  if (!list) return;
  list.innerHTML = 'Загрузка...';
  fetch('/downloaded-models?category=tts')
    .then(r => r.json())
    .then(data => {
      if (!data.models.length) {
        list.innerHTML = '<div style="color:var(--fg3);padding:8px 0">Нет загруженных моделей</div>';
        return;
      }
      list.innerHTML = data.models.map(m => `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
          <span style="font-family:'JetBrains Mono',monospace;font-weight:500;color:var(--fg)">${m.name}</span>
          <span style="color:var(--fg3);font-size:10px;margin-left:auto">${m.size}</span>
          <button class="btn-delete-job" onclick="deleteModel('${m.path}','${m.name}',this,'tts')" style="opacity:1;padding:2px" title="Удалить">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
      `).join('');
    });
}

function deleteModel(path, name, btn, category) {
  showConfirm('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>', 'Удалить модель?',
    'Модель <b>' + name + '</b> будет удалена с диска.',
    () => {
      fetch('/delete-model', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path})
      })
      .then(r => r.json())
      .then(data => {
        if (data.error) return addLog('❌ ' + data.error, 'error');
        addLog('🗑️ Модель удалена');
        if (category === 'tts') loadTtsModels();
        else loadModels();
      });
    }
  );
}

function deleteVoiceSample(name, file) {
  fetch('/delete-voice-sample', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, file})
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return addLog('❌ ' + data.error, 'error');
    addLog('🗑️ Семпл ' + file + ' удалён');
    if (data.remaining === 0) {
      // Remove entire voice
      deleteVoice(name);
    } else {
      // Just refresh
      location.reload();
    }
  });
}

/* ── Auto-resize textareas ────────────────────────── */

function autoResize(el) {
  el.style.height = '0';
  el.style.height = Math.max(el.scrollHeight, 28) + 'px';
}

/* ── Auto-save subtitles ─────────────────────────── */

let _subsSaveTimer = null;
function autoSaveSubs() {
  clearTimeout(_subsSaveTimer);
  _subsSaveTimer = setTimeout(() => {
    const body = {};
    if (originalSubs.length) body.original = originalSubs;
    // без перевода subtitles — это оригиналы; писать их в translated.srt нельзя
    if (hasTranslation && subtitles.length) body.translated = subtitles;
    if (!Object.keys(body).length) return;

    // Save via job if active, otherwise via work_dir
    if (currentJobId) {
      fetch('/subtitles/' + currentJobId, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
    } else if (resumeWorkDir) {
      body.work_dir = resumeWorkDir;
      fetch('/save-srt', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
    }
  }, 800);
}

/* ── Audio Editor ──────────────────────────────────── */

let aeDuration = 0;
let aePixelsPerSec = 0;
let aeZoomLevel = 10;
let aeSegments = []; // {index, start, end, el}
let aePeaks = [];

function aeSeek(sec) {
  if (!videoEl) return;
  videoEl.currentTime = Math.max(0, Math.min(videoEl.currentTime + sec, aeDuration));
}

document.addEventListener('keydown', e => {
  if (e.code === 'Space' && !e.target.matches('input,textarea,select')) {
    e.preventDefault();
    aePlayPause();
  }
});

function aePlayPause() {
  if (!videoEl) return;
  if (videoEl.paused) {
    videoEl.play();
    document.getElementById('aePlayBtn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
  } else {
    videoEl.pause();
    document.getElementById('aePlayBtn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
  }
}

function aeZoom(dir) {
  aeZoomLevel = Math.max(0.5, Math.min(aeZoomLevel + dir * 0.5, 20));
  if (aePeaks.length) {
    drawWaveform(aePeaks);
    drawTimeRuler();
    loadAeSegments();
  }
}

function initAudioEditor(audioPath, duration) {
  const editor = document.getElementById('audioEditor');
  aeDuration = duration || 1;
  aeZoomLevel = 10;

  // Determine speakers for TTS tracks
  const allSubs = subtitles.length ? subtitles : originalSubs;
  const speakers = [...new Set(allSubs.map(s => s.speaker).filter(Boolean))].sort();
  let ttsTracks = '';
  if (speakers.length > 1) {
    speakers.forEach((spk, idx) => {
      const spkNum = parseInt(spk.replace(/\D/g, '')) || idx;
      const color = SPEAKER_COLORS[spkNum % SPEAKER_COLORS.length];
      ttsTracks += `<div class="ae-track tts" data-speaker="${spk}" style="border-left:3px solid ${color}">
        <span class="ae-track-label">S${spkNum}</span>
      </div>`;
    });
  } else {
    ttsTracks = `<div class="ae-track tts" data-speaker="">
      <span class="ae-track-label">TTS</span>
    </div>`;
  }
  editor.innerHTML = `
    <div class="ae-timeline" id="aeTimeline">
      <div class="ae-timeline-inner" id="aeTimelineInner">
        <div class="ae-time-ruler" id="aeRuler"></div>
        <div class="ae-track main" id="aeTrackMain">
          <span class="ae-track-label">Аудио</span>
          <canvas class="ae-waveform" id="aeWaveform"></canvas>
        </div>
        ${ttsTracks}
        <div class="ae-playhead" id="aePlayhead" style="left:0"></div>
      </div>
    </div>
  `;

  // Load waveform
  fetch('/audio-waveform?path=' + encodeURIComponent(audioPath))
    .then(r => r.json())
    .then(d => {
      if (d.error) return;
      if (d.duration) aeDuration = d.duration;
      aePeaks = d.peaks;
      drawWaveform(aePeaks);
      drawTimeRuler();
      loadAeSegments();
    });

  // Detect user scrolling — suppress auto-scroll for 2 seconds
  const tl = document.getElementById('aeTimeline');
  if (tl) {
    tl.addEventListener('scroll', () => {
      aeUserScrolling = true;
      clearTimeout(aeScrollTimer);
      aeScrollTimer = setTimeout(() => { aeUserScrolling = false; }, 2000);
    }, {passive: true});

    // Click on timeline/ruler to seek
    tl.addEventListener('click', e => {
      if (!videoEl || !aePixelsPerSec) return;
      if (e.target.closest('.ae-segment')) return; // don't seek when clicking segment
      const rect = tl.getBoundingClientRect();
      const x = e.clientX - rect.left + tl.scrollLeft;
      const t = x / aePixelsPerSec;
      if (t >= 0 && t <= aeDuration) {
        videoEl.currentTime = t;
      }
    });
  }
  const ruler = document.getElementById('aeRuler');
  if (ruler) {
    ruler.addEventListener('click', e => {
      if (!videoEl || !aePixelsPerSec) return;
      const rect = ruler.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const t = x / aePixelsPerSec;
      if (t >= 0 && t <= aeDuration) {
        videoEl.currentTime = t;
      }
    });
  }
}

function drawWaveform(peaks) {
  const canvas = document.getElementById('aeWaveform');
  if (!canvas) return;
  const track = document.getElementById('aeTrackMain');
  const timeline = document.getElementById('aeTimeline');
  const inner = document.getElementById('aeTimelineInner');
  const baseW = timeline.offsetWidth;
  const w = Math.max(baseW, baseW * aeZoomLevel);
  aePixelsPerSec = w / aeDuration;

  // Set sizes
  inner.style.width = w + 'px';
  track.style.width = w + 'px';
  inner.querySelectorAll('.ae-track.tts').forEach(t => t.style.width = w + 'px');
  canvas.width = w;
  canvas.height = track.offsetHeight;

  const ctx = canvas.getContext('2d');
  const h = canvas.height;
  const mid = h / 2;
  const barW = Math.max(1, w / peaks.length);

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#10b981';
  ctx.globalAlpha = 0.6;

  for (let i = 0; i < peaks.length; i++) {
    const amp = peaks[i] * mid * 0.9;
    ctx.fillRect(i * barW, mid - amp, Math.max(1, barW - 0.5), amp * 2);
  }
}

function drawTimeRuler() {
  const ruler = document.getElementById('aeRuler');
  if (!ruler) return;
  const inner = document.getElementById('aeTimelineInner');
  const w = inner ? inner.offsetWidth : 0;
  if (!w) return;
  ruler.style.width = w + 'px';
  ruler.innerHTML = '';

  // Choose interval based on pixels per second
  const pps = aePixelsPerSec;
  let interval = 1;
  if (pps < 2) interval = 60;
  else if (pps < 5) interval = 30;
  else if (pps < 10) interval = 10;
  else if (pps < 30) interval = 5;
  else if (pps < 80) interval = 2;

  for (let t = 0; t <= aeDuration; t += interval) {
    const x = t * aePixelsPerSec;
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    const label = m > 0 ? `${m}:${String(s).padStart(2,'0')}` : `${s}s`;
    const mark = document.createElement('span');
    mark.className = 'ae-time-mark';
    mark.style.left = x + 'px';
    mark.textContent = label;
    ruler.appendChild(mark);
  }
}

function loadAeSegments() {
  const inner = document.getElementById('aeTimelineInner');
  if (!inner || !aePixelsPerSec) return;
  const ttsTracks = inner.querySelectorAll('.ae-track.tts');
  if (!ttsTracks.length) return;

  // Clear old segments from all TTS tracks
  ttsTracks.forEach(t => t.querySelectorAll('.ae-segment').forEach(el => el.remove()));
  aeSegments = [];

  // Build from subtitles + ttsSegments
  const subs = subtitles.length ? subtitles : originalSubs;
  subs.forEach((sub, i) => {
    if (!ttsSegments.has(sub.index)) return;
    const left = sub.start * aePixelsPerSec;
    const width = Math.max(4, (sub.end - sub.start) * aePixelsPerSec);

    const el = document.createElement('div');
    el.className = 'ae-segment';
    el.style.left = left + 'px';
    el.style.width = width + 'px';
    el.textContent = sub.index;
    el.title = `#${sub.index}: ${sub.text?.substring(0, 40) || ''}`;
    // Color by speaker
    const spk = sub.speaker || speakerMap[String(sub.index)];
    if (spk) {
      const spkNum = parseInt(spk.replace(/\D/g, '')) || 0;
      const clr = SPEAKER_COLORS[spkNum % SPEAKER_COLORS.length];
      el.style.background = clr + '40';
      el.style.borderColor = clr;
    }

    // Drag to move
    let dragStartX = 0, origLeft = 0, dragScrollLeft = 0;
    el.addEventListener('mousedown', e => {
      e.preventDefault();
      e.stopPropagation();
      const timeline = document.getElementById('aeTimeline');
      dragStartX = e.clientX;
      dragScrollLeft = timeline ? timeline.scrollLeft : 0;
      origLeft = parseFloat(el.style.left);
      el.classList.add('dragging');
      document.body.style.cursor = 'grabbing';
      document.body.style.userSelect = 'none';

      const onMove = ev => {
        const scrollDelta = (timeline ? timeline.scrollLeft : 0) - dragScrollLeft;
        const dx = ev.clientX - dragStartX + scrollDelta;
        const newLeft = Math.max(0, origLeft + dx);
        el.style.left = newLeft + 'px';
      };
      const onUp = () => {
        el.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);

        // Update subtitle timing in all arrays
        const newStart = parseFloat(el.style.left) / aePixelsPerSec;
        const duration = sub.end - sub.start;
        const newEnd = Math.round((newStart + duration) * 100) / 100;
        const startRounded = Math.round(newStart * 100) / 100;
        sub.start = startRounded;
        sub.end = newEnd;
        // Sync both arrays
        if (subtitles[i]) {
          subtitles[i].start = startRounded;
          subtitles[i].end = newEnd;
        }
        if (originalSubs[i]) {
          originalSubs[i].start = startRounded;
          originalSubs[i].end = newEnd;
        }
        autoSaveSubs();
        renderSubtitles();
        // Seek video to new segment position and reset TTS sync
        if (videoEl) videoEl.currentTime = sub.start;
        stopTtsSync();
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });

    // Find target track by speaker
    let targetTrack = ttsTracks[0]; // default: first track
    if (spk && ttsTracks.length > 1) {
      const found = inner.querySelector(`.ae-track.tts[data-speaker="${spk}"]`);
      if (found) targetTrack = found;
    }
    targetTrack.appendChild(el);
    aeSegments.push({index: sub.index, start: sub.start, end: sub.end, el});
  });
}

let aeUserScrolling = false;
let aeScrollTimer = null;

function updateAePlayhead() {
  const ph = document.getElementById('aePlayhead');
  if (!ph || !videoEl || !aePixelsPerSec) return;
  const x = videoEl.currentTime * aePixelsPerSec;
  ph.style.left = x + 'px';

  // Auto-scroll only if user is not manually scrolling
  if (!aeUserScrolling) {
    const timeline = document.getElementById('aeTimeline');
    if (timeline) {
      const viewW = timeline.offsetWidth;
      if (x > timeline.scrollLeft + viewW - 50 || x < timeline.scrollLeft + 50) {
        timeline.scrollLeft = x - viewW / 2;
      }
    }
  }
}

/* ── Speaker Mapping ────────────────────────────── */

function showSpeakerMappingPanel(speakers) {
  const panel = document.getElementById('speakerMapping');
  const list = document.getElementById('speakerMappingList');
  if (!panel || !list || speakers.length < 2) { if (panel) panel.style.display = 'none'; return; }
  panel.style.display = '';
  list.innerHTML = '';

  speakers.forEach(spk => {
    const spkNum = parseInt(spk.replace(/\D/g, '')) || 0;
    const row = document.createElement('div');
    row.className = 'speaker-mapping-row';

    // Badge
    const badge = document.createElement('span');
    badge.className = 'speaker-badge';
    badge.textContent = 'S' + spkNum;
    badge.style.background = SPEAKER_COLORS[spkNum % SPEAKER_COLORS.length];

    // Engine select
    const engSel = document.createElement('select');
    engSel.id = 'spkEngine_' + spk;
    engSel.style.cssText = 'font-size:11px;flex:1;min-width:0';
    // Copy options from main ttsEngine select
    const mainEng = document.getElementById('ttsEngine');
    engSel.innerHTML = mainEng.innerHTML;

    // Voice select
    const voiceSel = document.createElement('select');
    voiceSel.id = 'spkVoice_' + spk;
    voiceSel.style.cssText = 'font-size:11px;flex:1;min-width:0';

    // Populate voice options based on engine
    const updateVoices = () => {
      const eng = engSel.value;
      voiceSel.innerHTML = '';
      if (eng.includes('-custom')) {
        // Built-in Qwen voices
        ['Vivian','Serena','Uncle_Fu','Dylan','Eric','Ryan','Aiden','Ono_Anna','Sohee'].forEach(v => {
          voiceSel.add(new Option(v, v));
        });
      } else if (eng === 'edge-tts') {
        voiceSel.add(new Option('Загрузка...', ''));
        fetch('/edge-voices').then(r=>r.json()).then(d => {
          voiceSel.innerHTML = '';
          const locale = getTargetLocale();
          (d.voices||[]).forEach(v => {
            if (!locale || v.lang.startsWith(locale)) voiceSel.add(new Option(v.name, v.name));
          });
        });
      } else if (eng === 'macos-say') {
        voiceSel.add(new Option('Загрузка...', ''));
        fetch('/macos-voices').then(r=>r.json()).then(d => {
          voiceSel.innerHTML = '';
          (d.voices||[]).forEach(v => voiceSel.add(new Option(v.name, v.name)));
        });
      } else if (eng === 'elevenlabs') {
        voiceSel.add(new Option('Загрузка...', ''));
        fetch('/elevenlabs-voices').then(r=>r.json()).then(d => {
          voiceSel.innerHTML = '<option value="">По умолчанию (Rachel)</option>';
          (d.voices||[]).forEach(v => voiceSel.add(new Option(v.name + (v.lang ? ' — ' + v.lang : ''), v.id)));
          const s = speakerVoiceMap[spk];
          if (s) setTimeout(() => { voiceSel.value = s.voice || ''; }, 100);
        });
      } else if (eng === 'fish-audio') {
        voiceSel.add(new Option('Загрузка...', ''));
        fetch('/fish-voices').then(r=>r.json()).then(d => {
          voiceSel.innerHTML = '<option value="">По умолчанию</option>';
          (d.voices||[]).forEach(v => voiceSel.add(new Option(v.name + (v.lang ? ' — ' + v.lang : ''), v.id)));
          const s = speakerVoiceMap[spk];
          if (s) setTimeout(() => { voiceSel.value = s.voice || ''; }, 100);
        });
      } else {
        // Qwen base / OmniVoice — cloned voices
        const mainVoice = document.getElementById('ttsVoice');
        voiceSel.innerHTML = mainVoice.innerHTML;
      }
      // Restore saved selection
      const saved = speakerVoiceMap[spk];
      if (saved) {
        setTimeout(() => { voiceSel.value = saved.voice || ''; }, 200);
      }
    };

    engSel.onchange = () => { updateVoices(); saveSpeakerVoiceMap(); };
    voiceSel.onchange = () => { saveSpeakerVoiceMap(); };

    // Restore saved engine
    const saved = speakerVoiceMap[spk];
    if (saved && saved.engine) engSel.value = saved.engine;

    row.append(badge, engSel, voiceSel);
    list.appendChild(row);
    updateVoices();
  });
}

function saveSpeakerVoiceMap() {
  const panel = document.getElementById('speakerMappingList');
  if (!panel) return;
  const speakers = [...new Set(Object.values(speakerMap))].sort();
  speakerVoiceMap = {};
  speakers.forEach(spk => {
    const eng = document.getElementById('spkEngine_' + spk);
    const voice = document.getElementById('spkVoice_' + spk);
    if (eng && voice) {
      speakerVoiceMap[spk] = { engine: eng.value, voice: voice.value };
    }
  });
  // Auto-save to server
  if (resumeWorkDir) {
    fetch('/save-speaker-mapping', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ work_dir: resumeWorkDir, mapping: speakerVoiceMap })
    });
  }
}

