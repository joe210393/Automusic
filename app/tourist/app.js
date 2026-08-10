(() => {
  const TOKEN_KEY = "automusic_account_token";
  const DEST_KEY = "automusic_dest_id";
  const JOURNEY_KEY = "automusic_journey_id";

  let destId = localStorage.getItem(DEST_KEY) || "";
  let destination = null;
  let journey = null;
  let selectedRoute = null;
  let selectedMood = null;
  let selectedGender = "female";
  let selectedSinger = null;
  let singerCatalog = [];
  let activeSlot = null;
  let mediaRecorder = null;
  let chunks = [];
  let lastBlob = null;
  let recording = false;
  let recStream = null;
  let recAudioCtx = null;
  let recAnalyser = null;
  let recProcessor = null;
  let recSource = null;
  let recMute = null;
  let pcmChunks = [];
  let recStartedAt = 0;
  let recTimerId = null;
  let recRafId = 0;
  let recSampleRate = 44100;

  const $ = (id) => document.getElementById(id);
  const screens = {
    hub: $("screen-hub"),
    station: $("screen-station"),
    route: $("screen-route"),
    collect: $("screen-collect"),
    story: $("screen-story"),
    mood: $("screen-mood"),
    compose: $("screen-compose"),
    voice: $("screen-voice"),
    result: $("screen-result"),
  };

  const STEP_ORDER = ["route", "collect", "story", "mood", "compose", "voice", "result"];
  const LANDING = new Set(["hub", "station"]);

  function show(name) {
    Object.values(screens).forEach((el) => el && el.classList.remove("active"));
    if (screens[name]) screens[name].classList.add("active");
    document.body.classList.toggle("is-flow", !LANDING.has(name));
    const rail = $("stepRail");
    if (rail) {
      const idx = STEP_ORDER.indexOf(name);
      rail.querySelectorAll("li").forEach((li) => {
        const step = li.dataset.step;
        const si = STEP_ORDER.indexOf(step);
        li.classList.toggle("done", idx >= 0 && si < idx);
        li.classList.toggle("active", si === idx);
      });
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function iconSvg(kind) {
    const icons = {
      wave: '<path d="M2 12c3-6 5 6 8 0s5 6 8 0 5 6 8 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
      map: '<path d="M9 4l6 2 6-2v14l-6 2-6-2-6 2V6l6-2z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M9 4v14M15 6v14" fill="none" stroke="currentColor" stroke-width="1.8"/>',
      music: '<path d="M9 18V6l10-2v12" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="7" cy="18" r="2.5"/><circle cx="17" cy="16" r="2.5"/>',
      heart: '<path d="M12 20s-7-4.5-7-10a4 4 0 017-2 4 4 0 017 2c0 5.5-7 10-7 10z" fill="none" stroke="currentColor" stroke-width="1.8"/>',
    };
    return `<div class="choice-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${icons[kind] || icons.map}</svg></div>`;
  }

  function token() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function authHeaders(extra = {}) {
    const h = { ...extra };
    const t = token();
    if (t) h["X-Account-Token"] = t;
    return h;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: {
        ...(opts.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...authHeaders(opts.headers || {}),
      },
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) {
      const detail = (data && data.detail) || text || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function setStatus(el, msg, isError) {
    if (!el) return;
    el.textContent = msg || "";
    el.classList.toggle("error", !!isError);
  }

  function renderChips(container, items, onPick) {
    container.innerHTML = "";
    items.forEach((label) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = label;
      b.addEventListener("click", () => {
        container.querySelectorAll(".chip").forEach((c) => c.classList.remove("selected"));
        b.classList.add("selected");
        onPick(label);
      });
      container.appendChild(b);
    });
  }

  function stationLabel(d) {
    const place = d.label || d.id || "城鎮";
    return place.endsWith("站") ? place : `${place}站`;
  }

  const STATION_PHOTO = {
    // 燈塔／海面左側負空間放 CTA；港邊市集圖留給下方流程氛圍
    suao: { webp: "/trip/media/hero-lighthouse.webp", png: "/trip/media/hero-lighthouse.png", pos: "62% 48%" },
    default: { webp: "/trip/media/hero-stairs.webp", png: "/trip/media/hero-stairs.png", pos: "40% 50%" },
  };

  function pinIcon() {
    return `<span class="station-pin-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s7-5.2 7-11a7 7 0 10-14 0c0 5.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg></span>`;
  }

  async function initHub() {
    $("brandMark").textContent = "聲之旅";
    document.title = "把旅行變成一首歌";
    const data = await api("/api/destinations");
    const list = $("stationList");
    list.innerHTML = "";
    const items = (data.destinations || []).filter((d) => d.enabled !== false);
    if (!items.length) {
      list.innerHTML = `<p class="status error">目前還沒有開放的城鎮站。</p>`;
      return;
    }
    items.forEach((d) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "station-pin";
      btn.innerHTML = `
        ${pinIcon()}
        <span>
          <strong>${stationLabel(d)}</strong>
          <span>${d.tagline || "把這趟旅行，變成一首歌"}</span>
        </span>
      `;
      btn.addEventListener("click", () => enterStation(d.id));
      list.appendChild(btn);
    });
  }

  async function enterStation(id) {
    destId = id;
    localStorage.setItem(DEST_KEY, id);
    journey = null;
    selectedRoute = null;
    selectedMood = null;
    await loadStation();
    show("station");
  }

  async function loadStation() {
    if (!destId) throw new Error("請先選擇城鎮站");
    destination = await api(`/api/destinations/${destId}`);
    const b = destination.brand || {};
    const place = b.place || destination.id || "城鎮";
    $("stationPlace").textContent = place.endsWith("站") ? place : `${place}站`;
    $("stationHeadline").textContent = b.headline || `把今天的${place}，變成一首屬於你的歌`;
    $("stationSubhead").textContent =
      b.subhead || `在${place}的山海與日常裡，收集聲音，創造屬於你的旋律回憶。`;
    $("stationCore").textContent = b.coreLine || "";
    $("btnStart").textContent = b.cta || "開始創作你的歌曲";
    $("brandMark").textContent = `${place} · 聲之旅`;
    document.title = b.headline || `${place}｜把旅行變成一首歌`;

    const photo = STATION_PHOTO[destId] || STATION_PHOTO.default;
    const img = $("stationPhoto");
    const source = img && img.parentElement && img.parentElement.querySelector("source");
    if (source) source.srcset = photo.webp;
    if (img) {
      img.src = photo.png;
      img.style.objectPosition = photo.pos;
    }
  }

  function renderRoutes() {
    const list = $("routeList");
    list.innerHTML = "";
    (destination.routes || []).forEach((r) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice";
      btn.innerHTML = `${iconSvg("wave")}<strong>${r.label}</strong><span>${r.blurb || ""}</span>`;
      btn.addEventListener("click", async () => {
        list.querySelectorAll(".choice").forEach((c) => c.classList.remove("selected"));
        btn.classList.add("selected");
        selectedRoute = r;
        await api(`/api/journey/${journey.id}/route`, {
          method: "POST",
          body: (() => {
            const fd = new FormData();
            fd.append("route_id", r.id);
            return fd;
          })(),
          headers: authHeaders(),
        });
        setupSoundBox();
        show("collect");
      });
      list.appendChild(btn);
    });
  }

  function setupSoundBox() {
    const box = $("soundBox");
    box.innerHTML = "";
    const tasks = (selectedRoute && selectedRoute.soundTasks) || [
      { id: "sound1", label: "第一個聲音" },
      { id: "sound2", label: "第二個聲音" },
      { id: "sound3", label: "第三個聲音" },
    ];
    activeSlot = tasks[0].id;
    tasks.forEach((t, i) => {
      const row = document.createElement("div");
      row.className = "sound-slot";
      row.dataset.slot = t.id;
      row.innerHTML = `<div><strong>${i + 1}. ${t.label}</strong><div class="status" data-state>尚未收集</div></div><button class="secondary" type="button">選擇</button>`;
      row.addEventListener("click", () => {
        activeSlot = t.id;
        $("collectSlotHint").textContent = `正在收集：${t.label}`;
        box.querySelectorAll(".sound-slot").forEach((s) => {
          s.classList.toggle("is-active", s.dataset.slot === activeSlot);
        });
      });
      box.appendChild(row);
    });
    const first = box.querySelector(".sound-slot");
    if (first) first.classList.add("is-active");
    $("collectLead").textContent = selectedRoute
      ? `${selectedRoute.label}：找到這些聲音，再進入創作。`
      : "找一個最能代表今天的聲音。";
    $("collectSlotHint").textContent = `正在收集：${tasks[0].label}`;
    refreshSoundBoxState();
  }

  function refreshSoundBoxState() {
    const done = new Set((journey.sounds || []).map((s) => s.slot));
    document.querySelectorAll("#soundBox .sound-slot").forEach((row) => {
      const slot = row.dataset.slot;
      const state = row.querySelector("[data-state]");
      if (done.has(slot)) {
        row.classList.add("done");
        state.textContent = "已收集 ✓";
      } else {
        row.classList.remove("done");
        state.textContent = "尚未收集";
      }
    });
    const need = document.querySelectorAll("#soundBox .sound-slot").length;
    const have = done.size;
    $("btnCollectNext").disabled = have < Math.min(1, need);
    if (have >= 1) $("btnCollectNext").disabled = false;
  }

  async function ensureJourney() {
    if (journey && journey.id) return journey;
    journey = await api("/api/journey", {
      method: "POST",
      body: JSON.stringify({ destination: destId }),
    });
    localStorage.setItem(JOURNEY_KEY, journey.id);
    return journey;
  }

  function persistJourney() {
    if (journey && journey.id) localStorage.setItem(JOURNEY_KEY, journey.id);
  }

  function screenForStatus(status) {
    const s = status || "";
    if (s === "done" || s === "finalized") return "result";
    if (s === "voicing" || s === "finalizing") {
      return journey?.final_file ? "result" : "voice";
    }
    if (s === "style") return "voice";
    if (s === "preview" || s === "composing" || s === "error") return "compose";
    if (s === "story") return "mood";
    if (s === "collecting" || s === "route") return "collect";
    return "route";
  }

  function hydrateStoryFields() {
    if (!journey) return;
    if ($("nickname")) $("nickname").value = journey.nickname || "";
    if ($("keywordsInput")) $("keywordsInput").value = (journey.keywords || []).join("、");
    if ($("memory")) $("memory").value = journey.memory || "";
  }

  function hydrateRouteSelection() {
    if (!destination || !journey) return;
    selectedRoute = (destination.routes || []).find((r) => r.id === journey.route_id) || null;
  }

  function hydrateMoodSelection() {
    if (!destination || !journey) return;
    selectedMood = (destination.moodStyles || []).find((m) => m.id === journey.mood_id) || null;
  }

  function fillEduPanel(meta) {
    const m = (meta && meta.material) || {};
    if ($("eduMood")) $("eduMood").textContent = m.mood || "—";
    if ($("eduKey")) $("eduKey").textContent = (meta && meta.key) || "—";
    if ($("eduBpm")) {
      $("eduBpm").textContent = meta && meta.bpm != null
        ? `${Math.round(meta.bpm)} BPM`
        : "—";
    }
    if ($("eduStyle")) {
      $("eduStyle").textContent = m.style || (meta && meta.engine_style) || "—";
    }
    if ($("eduContour")) {
      const bits = [];
      if (m.contour) bits.push(m.contour);
      if (m.energy != null) bits.push(`能量 ${m.energy} 音/秒`);
      $("eduContour").textContent = bits.length ? bits.join(" · ") : "—";
    }
    if ($("eduNote")) {
      const parts = [];
      const moodLabel = selectedMood?.label
        || (destination?.moodStyles || []).find((x) => x.id === meta?.mood_id)?.label;
      if (moodLabel) parts.push(`你選的感覺：${moodLabel}`);
      if (m.progression) parts.push(`和弦進行：${m.progression}`);
      parts.push("這些來自你的旅行聲音與感覺選擇，決定旋律與伴奏的走向。");
      $("eduNote").textContent = parts.join(" ");
    }
  }

  function showComposeFromMeta() {
    const ly = journey.lyrics || {};
    show("compose");
    if (journey.preview_file || ly.title || ly.verse) {
      $("composeResult").style.display = "block";
      $("songTitle").textContent = `《${journey.title || ly.title || "旅行之歌"}》`;
      $("verseText").textContent = ly.verse || "";
      $("chorusText").textContent = ly.chorus || "";
      fillEduPanel(journey);
      if (journey.preview_file) {
        $("previewAudio").src = `/api/journey/${journey.id}/audio/preview?t=${Date.now()}`;
      }
      setStatus($("composeStatus"), journey.status === "error"
        ? (journey.error || "上次創作失敗，可再試一次")
        : "這是你上次的創作。想換感覺可按「換一個版本」。");
    } else {
      $("composeResult").style.display = "none";
      setStatus($("composeStatus"), "尚未完成創作。請選心情後再生成。");
      renderMoods();
      show("mood");
    }
  }

  function showResultFromMeta() {
    const ly = journey.lyrics || {};
    const title = journey.title || ly.title || "旅行之歌";
    show("result");
    $("finalTitle").textContent = `《${title}》`;
    $("finalMeta").textContent = `${(journey.nickname || "旅人")}的${(destination?.brand || {}).place || ""}旅行歌`;
    if ($("aiSingerMeta")) {
      $("aiSingerMeta").textContent = journey.ai_singer_label
        ? `演唱：${journey.ai_singer_label}`
        : "";
    }
    if (journey.final_file) {
      const url = `/api/journey/${journey.id}/audio/final?t=${Date.now()}`;
      $("finalAudio").src = url;
      $("btnDownload").href = url;
      $("btnDownload").download = `${title}-AI.mp3`;
    }
    syncVoiceVersionUi();
    setupResultVoiceprintUi();
    journey.share_path = journey.share_public ? `/s/${journey.slug}` : journey.share_path;
    setStatus($("resultStatus"), "這是你的 AI 唱歌版。想用人聲再做一版，可在下方開啟自己的聲音。");
  }

  async function resumeJourney(id) {
    journey = await api(`/api/journey/${id}`);
    destId = journey.destination || destId;
    if (destId) localStorage.setItem(DEST_KEY, destId);
    localStorage.setItem(JOURNEY_KEY, journey.id);
    await loadStation();
    hydrateRouteSelection();
    hydrateMoodSelection();
    hydrateStoryFields();
    setupStory();
    renderRoutes();
    const screen = screenForStatus(journey.status);
    if (screen === "collect") {
      if (selectedRoute) {
        setupSoundBox();
        show("collect");
      } else {
        show("route");
      }
    } else if (screen === "mood") {
      renderMoods();
      show("mood");
    } else if (screen === "compose") {
      if (selectedRoute) setupSoundBox();
      showComposeFromMeta();
    } else if (screen === "voice") {
      if (selectedRoute) setupSoundBox();
      renderSingers();
      show("voice");
    } else if (screen === "result") {
      showResultFromMeta();
    } else {
      show("route");
    }
  }

  function formatRecTime(ms) {
    const sec = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(sec / 60);
    const s = String(sec % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  function setRecUi(isRec, elapsedMs) {
    const label = $("recBtnLabel");
    const timer = $("recTimer");
    const wave = $("recWave");
    const btn = $("recBtn");
    if (!btn) return;
    btn.classList.toggle("recording", isRec);
    if (label) label.textContent = isRec ? "停止" : "開始錄音";
    if (timer) {
      timer.hidden = !isRec;
      if (isRec) timer.textContent = formatRecTime(elapsedMs || 0);
    }
    if (wave) wave.hidden = !isRec;
  }

  function drawRecWave() {
    const canvas = $("recWave");
    const analyser = recAnalyser;
    if (!canvas || !analyser || !recording) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const bins = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(bins);
    ctx.clearRect(0, 0, w, h);
    const bars = 28;
    const gap = 3;
    const barW = (w - gap * (bars - 1)) / bars;
    const step = Math.floor(bins.length / bars);
    for (let i = 0; i < bars; i++) {
      let sum = 0;
      for (let j = 0; j < step; j++) sum += bins[i * step + j] || 0;
      const avg = sum / step / 255;
      const barH = Math.max(4, avg * (h - 8));
      const x = i * (barW + gap);
      const y = (h - barH) / 2;
      ctx.fillStyle = `rgba(196, 92, 42, ${0.35 + avg * 0.65})`;
      ctx.beginPath();
      const r = Math.min(4, barW / 2);
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + barW, y, x + barW, y + barH, r);
      ctx.arcTo(x + barW, y + barH, x, y + barH, r);
      ctx.arcTo(x, y + barH, x, y, r);
      ctx.arcTo(x, y, x + barW, y, r);
      ctx.closePath();
      ctx.fill();
    }
    recRafId = requestAnimationFrame(drawRecWave);
  }

  function mergePcmChunks(list) {
    let total = 0;
    list.forEach((c) => { total += c.length; });
    const out = new Float32Array(total);
    let offset = 0;
    list.forEach((c) => {
      out.set(c, offset);
      offset += c.length;
    });
    return out;
  }

  function floatToWavBlob(float32, sampleRate) {
    const pcm = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return new Blob([encodeWav(pcm, sampleRate, 1)], { type: "audio/wav" });
  }

  function pickRecorderMime() {
    if (typeof MediaRecorder === "undefined") return "";
    const types = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/mp4",
      "audio/aac",
      "audio/ogg;codecs=opus",
    ];
    return types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
  }

  async function stopCollectRecording() {
    if (!recording) return;
    recording = false;
    if (recTimerId) {
      clearInterval(recTimerId);
      recTimerId = null;
    }
    if (recRafId) {
      cancelAnimationFrame(recRafId);
      recRafId = 0;
    }

    try {
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        await new Promise((resolve) => {
          const done = () => resolve();
          mediaRecorder.addEventListener("stop", done, { once: true });
          try { mediaRecorder.requestData(); } catch (_) { /* ignore */ }
          mediaRecorder.stop();
        });
      }
    } catch (_) { /* ignore */ }

    try {
      if (recProcessor) {
        recProcessor.onaudioprocess = null;
        recProcessor.disconnect();
      }
      if (recSource) recSource.disconnect();
      if (recAnalyser) recAnalyser.disconnect();
      if (recMute) recMute.disconnect();
      if (recAudioCtx && recAudioCtx.state !== "closed") await recAudioCtx.close();
    } catch (_) { /* ignore */ }

    if (recStream) {
      recStream.getTracks().forEach((t) => t.stop());
      recStream = null;
    }

    mediaRecorder = null;
    recProcessor = null;
    recSource = null;
    recAnalyser = null;
    recMute = null;
    recAudioCtx = null;

    try {
      let blob = null;
      if (pcmChunks.length) {
        blob = floatToWavBlob(mergePcmChunks(pcmChunks), recSampleRate);
      } else if (chunks.length) {
        const mime = chunks[0].type || "audio/webm";
        const raw = new Blob(chunks, { type: mime });
        blob = await blobToWav(raw);
      }
      if (!blob || blob.size < 44) {
        throw new Error("沒有錄到聲音，請再試一次");
      }
      lastBlob = blob;
      const preview = $("collectPreview");
      if (preview) {
        if (preview.src) URL.revokeObjectURL(preview.src);
        preview.src = URL.createObjectURL(lastBlob);
        preview.style.display = "block";
      }
      $("btnPlayLast").disabled = false;
      $("btnRedo").disabled = false;
      $("btnKeep").disabled = false;
      setRecUi(false);
      setStatus($("collectStatus"), `已錄 ${formatRecTime(Date.now() - recStartedAt)}，可以聽聽看或收進聲音盒`);
    } catch (err) {
      setRecUi(false);
      setStatus($("collectStatus"), err.message || "錄音失敗，請再試一次", true);
      lastBlob = null;
      $("btnKeep").disabled = true;
    } finally {
      pcmChunks = [];
      chunks = [];
    }
  }

  async function startRecording() {
    if (recording) {
      await stopCollectRecording();
      return;
    }
    if (!activeSlot) {
      setStatus($("collectStatus"), "請先點一個聲音空格再錄音", true);
      return;
    }
    try {
      recStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      setStatus($("collectStatus"), "無法使用麥克風，請允許權限後再試", true);
      throw err;
    }

    chunks = [];
    pcmChunks = [];
    lastBlob = null;
    recStartedAt = Date.now();
    recSampleRate = 44100;

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    recAudioCtx = new AudioCtx();
    if (recAudioCtx.state === "suspended") {
      try { await recAudioCtx.resume(); } catch (_) { /* ignore */ }
    }
    recSampleRate = recAudioCtx.sampleRate || 44100;
    recSource = recAudioCtx.createMediaStreamSource(recStream);
    recAnalyser = recAudioCtx.createAnalyser();
    recAnalyser.fftSize = 256;
    recSource.connect(recAnalyser);

    // PCM path：避免 MediaRecorder / decodeAudioData 在部分瀏覽器失敗
    const bufferSize = 4096;
    if (recAudioCtx.createScriptProcessor) {
      recProcessor = recAudioCtx.createScriptProcessor(bufferSize, 1, 1);
      recProcessor.onaudioprocess = (e) => {
        if (!recording) return;
        const input = e.inputBuffer.getChannelData(0);
        pcmChunks.push(new Float32Array(input));
      };
      recMute = recAudioCtx.createGain();
      recMute.gain.value = 0;
      recAnalyser.connect(recProcessor);
      recProcessor.connect(recMute);
      recMute.connect(recAudioCtx.destination);
    } else {
      // 備援：MediaRecorder
      const mime = pickRecorderMime();
      mediaRecorder = mime
        ? new MediaRecorder(recStream, { mimeType: mime })
        : new MediaRecorder(recStream);
      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size) chunks.push(e.data);
      };
      mediaRecorder.start(200);
    }

    recording = true;
    setRecUi(true, 0);
    setStatus($("collectStatus"), "錄音中…再按一次停止");
    recTimerId = setInterval(() => {
      if (!recording) return;
      setRecUi(true, Date.now() - recStartedAt);
    }, 200);
    drawRecWave();
  }

  async function keepRecording() {
    if (!lastBlob || !activeSlot) return;
    if (!journey || !journey.id) {
      setStatus($("collectStatus"), "旅程尚未建立，請返回重試", true);
      return;
    }
    setStatus($("collectStatus"), "上傳中…");
    try {
      const wavBlob = lastBlob.type === "audio/wav"
        ? lastBlob
        : await blobToWav(lastBlob);
      const fd = new FormData();
      fd.append("file", wavBlob, `${activeSlot}.wav`);
      fd.append("slot", activeSlot);
      const data = await api(`/api/journey/${journey.id}/sounds`, {
        method: "POST",
        body: fd,
        headers: authHeaders(),
      });
      journey.sounds = data.sounds;
      refreshSoundBoxState();
      setStatus($("collectStatus"), "已收進聲音盒");
      $("btnKeep").disabled = true;
    } catch (err) {
      setStatus($("collectStatus"), err.message || "上傳失敗", true);
    }
  }

  async function blobToWav(blob) {
    if (blob.type === "audio/wav") return blob;
    const buf = await blob.arrayBuffer();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    try {
      const audio = await ctx.decodeAudioData(buf.slice(0));
      const mono = audio.numberOfChannels > 1
        ? averageChannels(audio)
        : audio.getChannelData(0);
      const pcm = new Int16Array(mono.length);
      for (let i = 0; i < mono.length; i++) {
        const s = Math.max(-1, Math.min(1, mono[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      return new Blob([encodeWav(pcm, audio.sampleRate, 1)], { type: "audio/wav" });
    } finally {
      try { await ctx.close(); } catch (_) { /* ignore */ }
    }
  }

  function averageChannels(audio) {
    const a = audio.getChannelData(0);
    const b = audio.getChannelData(1);
    const out = new Float32Array(a.length);
    for (let i = 0; i < a.length; i++) out[i] = (a[i] + b[i]) / 2;
    return out;
  }

  function encodeWav(samples, sampleRate, numChannels) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
    writeStr(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * numChannels * 2, true);
    view.setUint16(32, numChannels * 2, true);
    view.setUint16(34, 16, true);
    writeStr(36, "data");
    view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for (let i = 0; i < samples.length; i++, offset += 2) view.setInt16(offset, samples[i], true);
    return buffer;
  }

  function parseUserKeywords(raw) {
    return String(raw || "")
      .split(/[,，、\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 6);
  }

  function setupStory() {
    $("btnStoryNext").onclick = async () => {
      const keywords = parseUserKeywords($("keywordsInput").value);
      if (!keywords.length) {
        setStatus($("storyStatus"), "請至少填一個歌詞關鍵字", true);
        $("keywordsInput").focus();
        return;
      }
      try {
        const body = {
          nickname: $("nickname").value.trim(),
          keywords,
          memory: $("memory").value.trim(),
          route_id: selectedRoute && selectedRoute.id,
        };
        const data = await api(`/api/journey/${journey.id}/story`, {
          method: "POST",
          body: JSON.stringify(body),
        });
        journey = data.meta;
        persistJourney();
        renderMoods();
        show("mood");
      } catch (e) {
        setStatus($("storyStatus"), e.message, true);
      }
    };
  }

  function renderMoods(opts = {}) {
    const list = $("moodList");
    list.innerHTML = "";
    selectedMood = null;
    $("btnMoodNext").disabled = true;
    if ($("moodLead")) {
      $("moodLead").textContent = opts.regen
        ? "請重新選一個感覺，我們會換一版旋律、歌詞與伴奏。"
        : "選一個感覺就好，不用懂音樂。";
    }
    if ($("btnMoodNext")) {
      $("btnMoodNext").textContent = opts.regen ? "換一版創作" : "開始創作";
    }
    (destination?.moodStyles || []).forEach((m) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice";
      const moodIcon = (m.id === "romance") ? "heart" : "music";
      btn.innerHTML = `${iconSvg(moodIcon)}<strong>${m.label}</strong><span>${m.blurb || ""}</span>`;
      btn.addEventListener("click", () => {
        list.querySelectorAll(".choice").forEach((c) => c.classList.remove("selected"));
        btn.classList.add("selected");
        selectedMood = m;
        $("btnMoodNext").disabled = false;
        setStatus($("moodStatus"), `已選「${m.label}」，按下按鈕開始換版創作。`);
      });
      list.appendChild(btn);
    });
    setStatus($("moodStatus"), opts.regen ? "選好感覺後，創作大約需要一兩分鐘。" : "");
  }

  function setComposeBusy(busy) {
    document.body.classList.toggle("compose-busy", !!busy);
    if ($("btnMoodNext")) $("btnMoodNext").disabled = busy || !selectedMood;
    if ($("btnRegenLyrics")) $("btnRegenLyrics").disabled = !!busy;
    if ($("btnToVoice")) $("btnToVoice").disabled = !!busy;
  }

  async function runCompose() {
    if (!selectedMood) {
      setStatus($("moodStatus"), "請先選一個感覺", true);
      return;
    }
    show("compose");
    $("composeResult").style.display = "none";
    setComposeBusy(true);
    const ul = $("composeProgress");
    ul.style.display = "";
    ul.innerHTML = ["整理旅行聲音", "創作旋律", "完成歌詞", "編排伴奏", "旅行歌曲誕生了"]
      .map((t) => `<li>${t}</li>`).join("");
    const first = ul.querySelector("li");
    if (first) first.classList.add("active");
    setStatus($("composeStatus"), "創作中，請稍候…（大約一兩分鐘，畫面會逐步打勾）");

    let stepIdx = 0;
    const tick = setInterval(() => {
      const items = ul.querySelectorAll("li");
      if (stepIdx < items.length) {
        items.forEach((li, i) => {
          li.classList.toggle("done", i < stepIdx);
          li.classList.toggle("active", i === stepIdx);
        });
        stepIdx += 1;
      }
    }, 1400);

    try {
      await api(`/api/journey/${journey.id}/mood`, {
        method: "POST",
        body: JSON.stringify({ mood_id: selectedMood.id }),
      });
      const data = await api(`/api/journey/${journey.id}/compose`, { method: "POST", body: "{}" });
      clearInterval(tick);
      ul.querySelectorAll("li").forEach((li) => { li.classList.add("done"); li.classList.remove("active"); });
      journey = data.meta || data;
      persistJourney();
      const ly = journey.lyrics || data.lyrics || {};
      $("songTitle").textContent = `《${journey.title || ly.title || "旅行之歌"}》`;
      $("verseText").textContent = ly.verse || "";
      $("chorusText").textContent = ly.chorus || "";
      fillEduPanel(journey);
      const previewUrl = data.preview_url || `/api/journey/${journey.id}/audio/preview`;
      $("previewAudio").src = previewUrl + (previewUrl.includes("?") ? "&" : "?") + "t=" + Date.now();
      $("composeResult").style.display = "block";
      setStatus($("composeStatus"), "完成！先聽聽伴奏版，確認歌詞後再用你的聲音唱。想再換感覺可按「換一個版本」。");
      setupVoiceLines(ly);
    } catch (e) {
      clearInterval(tick);
      setStatus($("composeStatus"), e.message, true);
    } finally {
      setComposeBusy(false);
    }
  }

  function startRegenVersion() {
    const audio = $("previewAudio");
    if (audio) {
      try { audio.pause(); } catch (_) {}
      audio.removeAttribute("src");
    }
    $("composeResult").style.display = "none";
    setStatus($("composeStatus"), "");
    renderMoods({ regen: true });
    show("mood");
  }

  function setupVoiceLines(lyrics) {
    const verse = (lyrics.verse || "").split(/\n+/).map((s) => s.trim()).filter(Boolean);
    const chorus = (lyrics.chorus || "").split(/\n+/).map((s) => s.trim()).filter(Boolean);
    const lines = [
      ...verse.slice(0, 4).map((text, index) => ({ section: "verse", index, text })),
      ...chorus.slice(0, 4).map((text, index) => ({ section: "chorus", index, text })),
    ].slice(0, 6);
    const box = $("voiceLines");
    if (!box) return;
    box.innerHTML = "";
    lines.forEach((line) => {
      const div = document.createElement("div");
      div.className = "voice-line";
      div.dataset.section = line.section;
      div.dataset.index = String(line.index);
      div.innerHTML = `
        <div class="voice-text">「${line.text}」</div>
        <div class="voice-actions">
          <button class="primary voice-rec" type="button">🎙️ 按住說話／點擊錄音</button>
          <button class="secondary voice-play" type="button" hidden>▶ 聽看看</button>
          <button class="secondary voice-redo" type="button" hidden>重錄</button>
        </div>
        <audio class="voice-preview" controls preload="metadata"></audio>
        <span class="status"></span>`;
      const btn = div.querySelector(".voice-rec");
      const playBtn = div.querySelector(".voice-play");
      const redoBtn = div.querySelector(".voice-redo");
      const audioEl = div.querySelector(".voice-preview");
      let rec = null;
      let localChunks = [];
      let localRecording = false;
      let objectUrl = null;

      function setPreviewUrl(url, { show = true } = {}) {
        if (objectUrl) {
          try { URL.revokeObjectURL(objectUrl); } catch (_) { /* ignore */ }
          objectUrl = null;
        }
        if (!url) {
          audioEl.removeAttribute("src");
          audioEl.classList.remove("is-visible");
          playBtn.hidden = true;
          redoBtn.hidden = true;
          return;
        }
        if (url.startsWith("blob:")) objectUrl = url;
        audioEl.src = url;
        audioEl.classList.toggle("is-visible", show);
        playBtn.hidden = false;
        redoBtn.hidden = false;
      }

      function markDone(count) {
        div.classList.add("done");
        btn.textContent = "✓ 已錄好";
        btn.hidden = true;
        playBtn.hidden = false;
        redoBtn.hidden = false;
        const status = div.querySelector(".status");
        status.textContent = count != null ? `已錄 ${count} 句` : "可聽看看，不滿意就重錄";
        refreshVoiceFinalizeEnabled(count);
      }

      function startFreshRecord() {
        try { audioEl.pause(); } catch (_) { /* ignore */ }
        setPreviewUrl(null);
        div.classList.remove("done");
        btn.hidden = false;
        btn.textContent = "🎙️ 按住說話／點擊錄音";
        div.querySelector(".status").textContent = "準備重新錄音…";
        if ($("btnFinalizeVoice")) $("btnFinalizeVoice").disabled = true;
      }

      playBtn.addEventListener("click", async () => {
        try {
          if (!audioEl.src) return;
          audioEl.classList.add("is-visible");
          await audioEl.play();
        } catch (err) {
          div.querySelector(".status").textContent = err.message || "無法播放";
        }
      });

      redoBtn.addEventListener("click", () => {
        startFreshRecord();
        btn.click();
      });

      btn.addEventListener("click", async () => {
        const status = div.querySelector(".status");
        try {
          if (!localRecording) {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            localChunks = [];
            const mime = pickRecorderMime();
            rec = mime
              ? new MediaRecorder(stream, { mimeType: mime })
              : new MediaRecorder(stream);
            rec.ondataavailable = (e) => { if (e.data && e.data.size) localChunks.push(e.data); };
            rec.onstop = async () => {
              stream.getTracks().forEach((t) => t.stop());
              localRecording = false;
              btn.textContent = "上傳中…";
              try {
                if (!localChunks.length) throw new Error("沒有錄到聲音");
                const blob = new Blob(localChunks, { type: localChunks[0]?.type || mime || "audio/webm" });
                const wav = await blobToWav(blob);
                setPreviewUrl(URL.createObjectURL(wav), { show: true });
                const fd = new FormData();
                fd.append("file", wav, "line.wav");
                fd.append("section", line.section);
                fd.append("index", String(line.index));
                fd.append("text", line.text);
                const data = await api(`/api/journey/${journey.id}/voice-lines`, {
                  method: "POST",
                  body: fd,
                  headers: authHeaders(),
                });
                markDone(data.count);
                setStatus($("resultVoiceStatus"), data.count >= 2
                  ? "已經記住你的聲音了。可以聽看看，確認後再製作。"
                  : "再錄一兩句會更好聽");
              } catch (err) {
                status.textContent = err.message;
                btn.hidden = false;
                btn.textContent = "🎙️ 再試一次";
              }
            };
            rec.start(200);
            localRecording = true;
            btn.hidden = false;
            btn.textContent = "⏹ 停止";
            status.textContent = "錄音中…再按一次停止";
          } else if (rec && rec.state !== "inactive") {
            try { rec.requestData(); } catch (_) { /* ignore */ }
            rec.stop();
          }
        } catch (err) {
          status.textContent = err.message;
        }
      });

      box.appendChild(div);
    });

    hydrateVoiceLines();
  }

  function refreshVoiceFinalizeEnabled(count) {
    const done = count != null
      ? count
      : document.querySelectorAll("#voiceLines .voice-line.done").length;
    if ($("btnFinalizeVoice")) {
      $("btnFinalizeVoice").disabled = done < 2 || document.body.classList.contains("finalize-busy");
    }
  }

  async function hydrateVoiceLines() {
    if (!journey?.id) return;
    try {
      const data = await api(`/api/journey/${journey.id}/voice-lines`);
      const byKey = new Map(
        (data.lines || []).map((l) => [`${l.section}:${l.index}`, l])
      );
      document.querySelectorAll("#voiceLines .voice-line").forEach(async (div) => {
        const key = `${div.dataset.section}:${div.dataset.index}`;
        const saved = byKey.get(key);
        if (!saved) return;
        div.classList.add("done");
        const btn = div.querySelector(".voice-rec");
        const playBtn = div.querySelector(".voice-play");
        const redoBtn = div.querySelector(".voice-redo");
        const audioEl = div.querySelector(".voice-preview");
        const status = div.querySelector(".status");
        if (btn) { btn.hidden = true; btn.textContent = "✓ 已錄好"; }
        if (playBtn) playBtn.hidden = false;
        if (redoBtn) redoBtn.hidden = false;
        if (status) status.textContent = "可聽看看，不滿意就重錄";
        if (audioEl && saved.filename) {
          try {
            const url = await fetchVoiceLineUrl(saved.filename);
            audioEl.src = url;
            audioEl.classList.add("is-visible");
          } catch (_) { /* 仍可重錄 */ }
        }
      });
      refreshVoiceFinalizeEnabled(data.count);
      if (data.count >= 2) {
        setStatus($("resultVoiceStatus"), "已經記住你的聲音了。可以聽看看，確認後再製作。");
      }
    } catch (_) { /* ignore */ }
  }

  async function fetchVoiceLineUrl(filename) {
    const res = await fetch(`/api/journey/${journey.id}/voice-lines/${encodeURIComponent(filename)}?t=${Date.now()}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error("無法載入錄音");
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  }

  const AI_FINALIZE_STEPS = [
    "準備製作",
    "連線 AI 唱歌引擎",
    "AI 正在作曲唱歌",
    "下載成品",
    "輸出成品",
    "完成",
  ];
  const VOICE_FINALIZE_STEPS = [
    "準備製作",
    "編排伴奏",
    "AI 底稿代唱",
    "套用你的聲音",
    "混音處理",
    "輸出成品",
    "完成",
  ];

  const STEP_PCT = {
    "準備製作": 8,
    "連線 AI 唱歌引擎": 20,
    "AI 正在作曲唱歌": 55,
    "下載成品": 85,
    "編排伴奏": 25,
    "套用演奏風格": 55,
    "AI 底稿代唱": 40,
    "套用你的聲音": 60,
    "混音處理": 80,
    "輸出成品": 90,
    "完成": 100,
  };

  async function loadSingerCatalog() {
    if (singerCatalog.length) return singerCatalog;
    const data = await api("/api/singers");
    singerCatalog = data.singers || [];
    return singerCatalog;
  }

  async function renderSingers() {
    await loadSingerCatalog();
    selectedSinger = singerCatalog.find((s) => s.id === journey?.ai_singer_id) || null;
    if (selectedSinger) selectedGender = selectedSinger.gender;
    document.querySelectorAll(".gender-choice").forEach((btn) => {
      btn.classList.toggle("selected", btn.dataset.gender === selectedGender);
    });
    const list = $("singerList");
    if (!list) return;
    list.innerHTML = "";
    const items = singerCatalog.filter((s) => s.gender === selectedGender);
    items.forEach((s) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice";
      if (selectedSinger && selectedSinger.id === s.id) btn.classList.add("selected");
      btn.innerHTML = `<strong>${s.label}</strong><span>${s.blurb || ""}</span>`;
      btn.addEventListener("click", () => {
        list.querySelectorAll(".choice").forEach((c) => c.classList.remove("selected"));
        btn.classList.add("selected");
        selectedSinger = s;
        $("btnFinalize").disabled = false;
        setStatus($("voiceStatus"), `已選「${s.label}」`);
      });
      list.appendChild(btn);
    });
    $("btnFinalize").disabled = !selectedSinger || document.body.classList.contains("finalize-busy");
    if (!selectedSinger) setStatus($("voiceStatus"), "先選明亮系或沉穩系，再挑一位 AI 歌手。");
  }

  function syncVoiceVersionUi() {
    const block = $("voiceVersionBlock");
    const upgrade = $("voiceprintUpgrade");
    const hasVoice = !!(journey?.final_voice_file);
    if (block) block.hidden = !hasVoice;
    if (hasVoice && journey?.id) {
      const title = journey.title || (journey.lyrics || {}).title || "旅行之歌";
      const url = `/api/journey/${journey.id}/audio/final-voice?t=${Date.now()}`;
      if ($("finalVoiceAudio")) $("finalVoiceAudio").src = url;
      if ($("btnDownloadVoice")) {
        $("btnDownloadVoice").href = url;
        $("btnDownloadVoice").download = `${title}-我的聲音.mp3`;
      }
      if (upgrade) upgrade.hidden = true;
    } else if (upgrade) {
      upgrade.hidden = false;
    }
  }

  function setupResultVoiceprintUi() {
    const consented = !!(journey?.voiceprint_consent?.accepted || journey?.voiceprint_consent === true);
    const hasVoice = !!(journey?.final_voice_file);
    if (hasVoice) return;
    if ($("consentBox")) $("consentBox").hidden = consented;
    if ($("voiceRecPanel")) $("voiceRecPanel").hidden = !consented;
    if (consented) {
      setupVoiceLines(journey.lyrics || {});
    }
    if ($("consentCheck")) {
      $("consentCheck").checked = false;
      if ($("btnConsentStart")) $("btnConsentStart").disabled = true;
    }
  }

  function setFinalizeBusy(busy, { panelId = "finalizeProgress", enableBtn = null } = {}) {
    document.body.classList.toggle("finalize-busy", !!busy);
    const panel = $(panelId);
    if (panel) panel.hidden = !busy;
    if (enableBtn === "ai" && $("btnFinalize")) {
      $("btnFinalize").disabled = busy || !selectedSinger;
    }
    if (enableBtn === "voice" && $("btnFinalizeVoice")) {
      refreshVoiceFinalizeEnabled();
      if (busy) $("btnFinalizeVoice").disabled = true;
    }
  }

  function renderProgress(panelPrefix, steps, pct, label) {
    const safePct = Math.max(0, Math.min(100, Math.round(pct || 0)));
    const pctEl = $(panelPrefix === "voice" ? "voiceFinalizePct" : "finalizePct");
    const barEl = $(panelPrefix === "voice" ? "voiceFinalizePctBar" : "finalizePctBar");
    const labelEl = $(panelPrefix === "voice" ? "voiceFinalizePctLabel" : "finalizePctLabel");
    const ul = $(panelPrefix === "voice" ? "voiceFinalizeSteps" : "finalizeSteps");
    if (pctEl) pctEl.textContent = `${safePct}%`;
    if (barEl) barEl.style.width = `${safePct}%`;
    if (labelEl) labelEl.textContent = label || "製作中";
    if (!ul) return;
    let activeIdx = steps.findIndex((s) => s === label);
    if (activeIdx < 0) {
      // 依百分比找最近步驟，但不亂跳：取不大於目前 pct 的最大步驟
      activeIdx = 0;
      steps.forEach((t, i) => {
        const p = STEP_PCT[t];
        if (p != null && p <= safePct) activeIdx = i;
      });
    }
    ul.innerHTML = steps.map((t, i) => {
      const cls = i < activeIdx ? "done" : (i === activeIdx ? "active" : "");
      return `<li class="${cls}">${t}</li>`;
    }).join("");
  }

  async function runProgressJob({ steps, panelPrefix, panelId, enableBtn, request, onDone, statusEl }) {
    setFinalizeBusy(true, { panelId, enableBtn });
    renderProgress(panelPrefix, steps, STEP_PCT[steps[0]] || 8, steps[0]);
    setStatus(statusEl, "正在製作，請稍候…");

    let stopPoll = false;
    let lastPct = 0;
    let lastLabel = steps[0];

    function applyServerProgress(fp) {
      if (!fp || fp.pct == null) return;
      const label = fp.label || lastLabel;
      // 百分比以步驟表為準，避免伺服器與前端兩套數字互搶
      const mapped = STEP_PCT[label] != null ? STEP_PCT[label] : Number(fp.pct);
      const next = Math.max(lastPct, Math.min(99, Math.round(mapped)));
      if (next === lastPct && label === lastLabel) return;
      lastPct = next;
      lastLabel = label;
      renderProgress(panelPrefix, steps, lastPct, label);
    }

    const poll = (async () => {
      while (!stopPoll) {
        try {
          const meta = await api(`/api/journey/${journey.id}`);
          if (meta.status === "finalizing") {
            applyServerProgress(meta.finalize_progress || {});
          }
        } catch (_) { /* ignore */ }
        await new Promise((r) => setTimeout(r, 700));
      }
    })();

    try {
      const data = await request();
      stopPoll = true;
      lastPct = 100;
      renderProgress(panelPrefix, steps, 100, "完成");
      await onDone(data);
    } catch (e) {
      stopPoll = true;
      setStatus(statusEl, e.message, true);
    } finally {
      stopPoll = true;
      try { await poll; } catch (_) { /* ignore */ }
      setFinalizeBusy(false, { panelId, enableBtn });
    }
  }

  async function finalize() {
    if (!selectedSinger) {
      setStatus($("voiceStatus"), "請先選擇 AI 歌手", true);
      return;
    }
    await runProgressJob({
      steps: AI_FINALIZE_STEPS,
      panelPrefix: "ai",
      panelId: "finalizeProgress",
      enableBtn: "ai",
      statusEl: $("voiceStatus"),
      request: async () => {
        await api(`/api/journey/${journey.id}/singer`, {
          method: "POST",
          body: JSON.stringify({ singer_id: selectedSinger.id }),
        });
        return api(`/api/journey/${journey.id}/finalize`, { method: "POST", body: "{}" });
      },
      onDone: async (data) => {
        journey = {
          ...(journey || {}),
          status: "done",
          final_file: "final.mp3",
          slug: data.slug,
          share_path: data.share_path,
          ai_singer_id: data.ai_singer_id || selectedSinger.id,
          ai_singer_label: data.ai_singer_label || selectedSinger.label,
          lyrics: data.lyrics || journey.lyrics,
          title: (data.lyrics && data.lyrics.title) || journey.title,
        };
        persistJourney();
        showResultFromMeta();
        if (data.final_url) {
          $("finalAudio").src = data.final_url + "?t=" + Date.now();
          $("btnDownload").href = data.final_url;
        }
        setStatus($("resultStatus"), "AI 唱歌版完成！可以下載，或在下方用自己的聲音再做一版。");
      },
    });
  }

  async function finalizeVoice() {
    await runProgressJob({
      steps: VOICE_FINALIZE_STEPS,
      panelPrefix: "voice",
      panelId: "voiceFinalizeProgress",
      enableBtn: "voice",
      statusEl: $("resultVoiceStatus"),
      request: () => api(`/api/journey/${journey.id}/finalize-voice`, { method: "POST", body: "{}" }),
      onDone: async (data) => {
        journey = {
          ...(journey || {}),
          status: "done",
          final_voice_file: "final-voice.mp3",
          slug: data.slug,
          share_path: data.share_path,
        };
        persistJourney();
        syncVoiceVersionUi();
        if (data.final_voice_url && $("finalVoiceAudio")) {
          $("finalVoiceAudio").src = data.final_voice_url + "?t=" + Date.now();
        }
        setStatus($("resultVoiceStatus"), "我的聲音版完成！現在你有兩首作品了。");
        setStatus($("resultStatus"), "你現在有 AI 版與自己的聲音版，兩首都可下載。");
      },
    });
  }

  async function acceptVoiceConsent() {
    try {
      const data = await api(`/api/journey/${journey.id}/voiceprint/consent`, {
        method: "POST",
        body: JSON.stringify({ accepted: true }),
      });
      journey.voiceprint_consent = data.voiceprint_consent || { accepted: true };
      journey.status = "voicing";
      if ($("consentBox")) $("consentBox").hidden = true;
      if ($("voiceRecPanel")) $("voiceRecPanel").hidden = false;
      setupVoiceLines(journey.lyrics || {});
      setStatus($("resultVoiceStatus"), "請錄至少兩句，再製作我的聲音版。");
    } catch (e) {
      setStatus($("resultStatus"), e.message, true);
    }
  }

  // events
  $("btnStart").addEventListener("click", async () => {
    try {
      if (!destination) await loadStation();
      await ensureJourney();
      renderRoutes();
      setupStory();
      show("route");
    } catch (e) {
      alert(e.message);
    }
  });

  $("btnBackHub").addEventListener("click", () => {
    journey = null;
    selectedRoute = null;
    destination = null;
    destId = "";
    localStorage.removeItem(DEST_KEY);
    localStorage.removeItem(JOURNEY_KEY);
    $("brandMark").textContent = "聲之旅";
    document.title = "把旅行變成一首歌";
    show("hub");
  });

  document.querySelectorAll("[data-back]").forEach((btn) => {
    btn.addEventListener("click", () => show(btn.dataset.back));
  });

  $("recBtn").addEventListener("click", () => {
    startRecording().catch((e) => setStatus($("collectStatus"), e.message || "錄音失敗", true));
  });
  $("btnPlayLast").addEventListener("click", () => $("collectPreview").play());
  $("btnRedo").addEventListener("click", () => {
    lastBlob = null;
    $("btnKeep").disabled = true;
    $("btnPlayLast").disabled = true;
    const preview = $("collectPreview");
    if (preview) {
      if (preview.src) URL.revokeObjectURL(preview.src);
      preview.removeAttribute("src");
      preview.style.display = "none";
    }
    setRecUi(false);
    setStatus($("collectStatus"), "再錄一次吧");
  });
  $("btnKeep").addEventListener("click", () => keepRecording());
  $("btnCollectNext").addEventListener("click", () => show("story"));
  $("btnMoodNext").addEventListener("click", () => runCompose());
  $("btnRegenLyrics").addEventListener("click", () => startRegenVersion());
  $("btnToVoice").addEventListener("click", async () => {
    await renderSingers();
    show("voice");
  });
  $("btnFinalize").addEventListener("click", () => finalize());
  document.querySelectorAll(".gender-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedGender = btn.dataset.gender || "female";
      selectedSinger = null;
      renderSingers();
    });
  });
  $("consentCheck")?.addEventListener("change", (e) => {
    if ($("btnConsentStart")) $("btnConsentStart").disabled = !e.target.checked;
  });
  $("btnConsentStart")?.addEventListener("click", () => acceptVoiceConsent());
  $("btnFinalizeVoice")?.addEventListener("click", () => finalizeVoice());
  $("btnCopyShare").addEventListener("click", async () => {
    const url = location.origin + (journey.share_path || `/s/${journey.slug}`);
    try {
      await navigator.clipboard.writeText(url);
      setStatus($("resultStatus"), "分享連結已複製：" + url);
    } catch {
      setStatus($("resultStatus"), url);
    }
  });
  $("btnNewTrip").addEventListener("click", async () => {
    journey = null;
    selectedRoute = null;
    selectedMood = null;
    localStorage.removeItem(JOURNEY_KEY);
    if (destId) {
      try {
        await loadStation();
        show("station");
        return;
      } catch (e) {
        console.warn(e);
      }
    }
    show("hub");
  });
  $("btnPayStub").addEventListener("click", async () => {
    try {
      const data = await api("/api/account/pay-stub", {
        method: "POST",
        body: JSON.stringify({ paid: true }),
      });
      setStatus($("resultStatus"), `已標記付費（開發用）。額度 ${data.account.quota.limit}/月`);
    } catch (e) {
      setStatus($("resultStatus"), e.message, true);
    }
  });

  function setAuthVisibility(loggedIn) {
    document.querySelectorAll("[data-auth='guest']").forEach((el) => {
      if (loggedIn) el.setAttribute("hidden", "");
      else el.removeAttribute("hidden");
    });
    document.querySelectorAll("[data-auth='user']").forEach((el) => {
      if (loggedIn) el.removeAttribute("hidden");
      else el.setAttribute("hidden", "");
    });
  }

  function fillUserChips(account) {
    const name = account.display_name || "旅人";
    const email = account.email || "";
    ["hubUserName", "flowUserName"].forEach((id) => {
      if ($(id)) $(id).textContent = name;
    });
    ["hubUserEmail", "flowUserEmail"].forEach((id) => {
      if ($(id)) $(id).textContent = email;
    });
  }

  function clearUserChips() {
    ["hubUserName", "flowUserName"].forEach((id) => {
      if ($(id)) $(id).textContent = "旅人";
    });
    ["hubUserEmail", "flowUserEmail"].forEach((id) => {
      if ($(id)) $(id).textContent = "";
    });
    setAuthVisibility(false);
  }

  async function refreshAuthState() {
    if (!token()) {
      clearUserChips();
      return null;
    }
    try {
      const me = await api("/api/account/me");
      setAuthVisibility(true);
      fillUserChips(me.account || {});
      if ($("resultStatus") && me.account) {
        setStatus($("resultStatus"), `已登入：${me.account.display_name || me.account.email}（${me.account.email}｜本月剩餘 ${me.account.quota?.remaining ?? "∞"} 次）`);
      }
      return me;
    } catch (_) {
      localStorage.removeItem(TOKEN_KEY);
      clearUserChips();
      return null;
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    clearUserChips();
    // 立刻回到訪客首頁狀態，避免畫面仍顯示登入中
    if (document.body.classList.contains("is-flow")) {
      show("hub");
    }
  }

  $("btnLogoutHub")?.addEventListener("click", logout);
  $("btnLogoutFlow")?.addEventListener("click", logout);

  refreshAuthState();

  async function boot() {
    const resumeId = new URLSearchParams(location.search).get("journey");
    if (resumeId) {
      try {
        await resumeJourney(resumeId);
        history.replaceState(null, "", "/");
        return;
      } catch (e) {
        console.warn(e);
        alert(e.message || "無法開啟這趟旅程");
      }
    }
    await initHub();
  }

  boot().catch((e) => {
    console.error(e);
    const list = $("stationList");
    if (list) list.innerHTML = `<p class="status error">${e.message || "載入城鎮站失敗"}</p>`;
  });
})();
