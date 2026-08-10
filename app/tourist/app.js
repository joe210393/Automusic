(() => {
  const TOKEN_KEY = "automusic_account_token";
  const DEST_KEY = "automusic_dest_id";
  const JOURNEY_KEY = "automusic_journey_id";

  let destId = localStorage.getItem(DEST_KEY) || "";
  let destination = null;
  let journey = null;
  let selectedRoute = null;
  let selectedMood = null;
  let activeSlot = null;
  let mediaRecorder = null;
  let chunks = [];
  let lastBlob = null;
  let recording = false;

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
    if (s === "voicing" || s === "finalizing") return "voice";
    if (s === "preview" || s === "composing" || s === "style" || s === "error") return "compose";
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

  function showComposeFromMeta() {
    const ly = journey.lyrics || {};
    show("compose");
    if (journey.preview_file || ly.title || ly.verse) {
      $("composeResult").style.display = "block";
      $("songTitle").textContent = `《${journey.title || ly.title || "旅行之歌"}》`;
      $("verseText").textContent = ly.verse || "";
      $("chorusText").textContent = ly.chorus || "";
      if (journey.preview_file) {
        $("previewAudio").src = `/api/journey/${journey.id}/audio/preview?t=${Date.now()}`;
      }
      setupVoiceLines(ly);
      setStatus($("composeStatus"), journey.status === "error"
        ? (journey.error || "上次創作失敗，可再試一次")
        : "這是你上次的創作，可繼續或重製歌詞。");
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
    $("finalMeta").textContent = `${(journey.nickname || "旅人")}的${(destination.brand || {}).place || ""}旅行歌`;
    if (journey.final_file) {
      const url = `/api/journey/${journey.id}/audio/final?t=${Date.now()}`;
      $("finalAudio").src = url;
      $("btnDownload").href = url;
      $("btnDownload").download = `${title}.mp3`;
    }
    journey.share_path = journey.share_public ? `/s/${journey.slug}` : journey.share_path;
    setStatus($("resultStatus"), "這是你完成的旅行歌曲，可以再聽、下載或分享。");
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
      showComposeFromMeta();
      show("voice");
      $("btnFinalize").disabled = false;
    } else if (screen === "result") {
      showResultFromMeta();
    } else {
      show("route");
    }
  }

  async function startRecording() {
    if (recording) {
      mediaRecorder && mediaRecorder.stop();
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      lastBlob = new Blob(chunks, { type: "audio/webm" });
      $("collectPreview").src = URL.createObjectURL(lastBlob);
      $("collectPreview").style.display = "block";
      $("btnPlayLast").disabled = false;
      $("btnRedo").disabled = false;
      $("btnKeep").disabled = false;
      const label = $("recBtnLabel");
      if (label) label.textContent = "開始錄音";
      $("recBtn").classList.remove("recording");
      recording = false;
    };
    mediaRecorder.start();
    recording = true;
    const label = $("recBtnLabel");
    if (label) label.textContent = "停止";
    $("recBtn").classList.add("recording");
    setStatus($("collectStatus"), "錄音中…");
  }

  async function keepRecording() {
    if (!lastBlob || !activeSlot) return;
    setStatus($("collectStatus"), "上傳中…");
    // webm → 仍以 wav 副檔名存；後端只當 bytes。為相容轉成 wav 較佳，先直接上傳 webm 內容但用 .wav 名可能壞 compose。
    // 用 AudioContext 轉 PCM wav
    const wavBlob = await webmToWav(lastBlob);
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
  }

  async function webmToWav(blob) {
    const buf = await blob.arrayBuffer();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const audio = await ctx.decodeAudioData(buf.slice(0));
    const length = audio.length;
    const ch = 1;
    const rate = audio.sampleRate;
    const mono = audio.numberOfChannels > 1
      ? averageChannels(audio)
      : audio.getChannelData(0);
    const pcm = new Int16Array(length);
    for (let i = 0; i < length; i++) {
      const s = Math.max(-1, Math.min(1, mono[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    const wav = encodeWav(pcm, rate, ch);
    await ctx.close();
    return new Blob([wav], { type: "audio/wav" });
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

  function renderMoods() {
    const list = $("moodList");
    list.innerHTML = "";
    selectedMood = null;
    $("btnMoodNext").disabled = true;
    (destination.moodStyles || []).forEach((m) => {
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
      });
      list.appendChild(btn);
    });
  }

  async function runCompose() {
    show("compose");
    $("composeResult").style.display = "none";
    const ul = $("composeProgress");
    ul.innerHTML = ["整理旅行聲音", "創作旋律", "完成歌詞", "編排伴奏", "旅行歌曲誕生了"]
      .map((t) => `<li>${t}</li>`).join("");
    setStatus($("composeStatus"), "創作中，可能需要一兩分鐘…");

    const tick = setInterval(() => {
      const items = ul.querySelectorAll("li");
      const done = [...items].filter((li) => li.classList.contains("done")).length;
      if (done < items.length) {
        items.forEach((li, i) => {
          li.classList.toggle("done", i < done);
          li.classList.toggle("active", i === done);
        });
        if (items[done]) items[done].classList.add("done");
      }
    }, 1200);

    try {
      await api(`/api/journey/${journey.id}/mood`, {
        method: "POST",
        body: JSON.stringify({ mood_id: selectedMood.id }),
      });
      const data = await api(`/api/journey/${journey.id}/compose`, { method: "POST", body: "{}" });
      clearInterval(tick);
      ul.querySelectorAll("li").forEach((li) => { li.classList.add("done"); li.classList.remove("active"); });
      journey = data.meta;
      persistJourney();
      const ly = data.lyrics || {};
      $("songTitle").textContent = `《${journey.title || ly.title || "旅行之歌"}》`;
      $("verseText").textContent = ly.verse || "";
      $("chorusText").textContent = ly.chorus || "";
      $("previewAudio").src = data.preview_url + "?t=" + Date.now();
      $("composeResult").style.display = "block";
      setStatus($("composeStatus"), "完成！先聽聽伴奏版，確認歌詞後再用你的聲音唱。");
      setupVoiceLines(ly);
    } catch (e) {
      clearInterval(tick);
      setStatus($("composeStatus"), e.message, true);
    }
  }

  function setupVoiceLines(lyrics) {
    const verse = (lyrics.verse || "").split(/\n+/).map((s) => s.trim()).filter(Boolean);
    const chorus = (lyrics.chorus || "").split(/\n+/).map((s) => s.trim()).filter(Boolean);
    const lines = [
      ...verse.slice(0, 4).map((text, index) => ({ section: "verse", index, text })),
      ...chorus.slice(0, 4).map((text, index) => ({ section: "chorus", index, text })),
    ].slice(0, 6);
    const box = $("voiceLines");
    box.innerHTML = "";
    lines.forEach((line) => {
      const div = document.createElement("div");
      div.className = "voice-line";
      div.dataset.section = line.section;
      div.dataset.index = String(line.index);
      div.innerHTML = `
        <div class="voice-text">「${line.text}」</div>
        <button class="primary voice-rec" type="button">🎙️ 按住說話／點擊錄音</button>
        <span class="status"></span>`;
      const btn = div.querySelector("button");
      let rec = null;
      let localChunks = [];
      let localRecording = false;
      btn.addEventListener("click", async () => {
        const status = div.querySelector(".status");
        try {
          if (!localRecording) {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            localChunks = [];
            rec = new MediaRecorder(stream);
            rec.ondataavailable = (e) => { if (e.data.size) localChunks.push(e.data); };
            rec.onstop = async () => {
              stream.getTracks().forEach((t) => t.stop());
              localRecording = false;
              btn.textContent = "上傳中…";
              try {
                const blob = new Blob(localChunks, { type: "audio/webm" });
                const wav = await webmToWav(blob);
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
                div.classList.add("done");
                btn.textContent = "✓ 完成";
                status.textContent = `已錄 ${data.count} 句`;
                $("btnFinalize").disabled = data.count < 2;
                setStatus($("voiceStatus"), data.count >= 2 ? "已經記住你的聲音了。" : "再錄一兩句會更好聽");
              } catch (err) {
                status.textContent = err.message;
                btn.textContent = "🎙️ 再試一次";
              }
            };
            rec.start();
            localRecording = true;
            btn.textContent = "⏹ 停止";
          } else {
            rec.stop();
          }
        } catch (err) {
          status.textContent = err.message;
        }
      });
      box.appendChild(div);
    });
  }

  async function finalize() {
    setStatus($("voiceStatus"), "正在製作你的旅行歌曲，可能需要幾分鐘…");
    $("btnFinalize").disabled = true;
    try {
      const data = await api(`/api/journey/${journey.id}/finalize`, {
        method: "POST",
        body: "{}",
      });
      show("result");
      const title = (data.lyrics && data.lyrics.title) || "旅行之歌";
      $("finalTitle").textContent = `《${title}》`;
      $("finalMeta").textContent = `${(journey.nickname || "旅人")}的${destination.brand.place}旅行歌`;
      $("finalAudio").src = data.final_url + "?t=" + Date.now();
      $("btnDownload").href = data.final_url;
      $("btnDownload").download = `${title}.mp3`;
      journey.slug = data.slug;
      journey.share_path = data.share_path;
      setStatus($("resultStatus"), "完成！可以下載或分享給朋友。");
    } catch (e) {
      setStatus($("voiceStatus"), e.message, true);
      $("btnFinalize").disabled = false;
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

  $("recBtn").addEventListener("click", () => startRecording().catch((e) => setStatus($("collectStatus"), e.message, true)));
  $("btnPlayLast").addEventListener("click", () => $("collectPreview").play());
  $("btnRedo").addEventListener("click", () => {
    lastBlob = null;
    $("btnKeep").disabled = true;
    setStatus($("collectStatus"), "再錄一次吧");
  });
  $("btnKeep").addEventListener("click", () => keepRecording().catch((e) => setStatus($("collectStatus"), e.message, true)));
  $("btnCollectNext").addEventListener("click", () => show("story"));
  $("btnMoodNext").addEventListener("click", () => runCompose());
  $("btnRegenLyrics").addEventListener("click", async () => {
    try {
      const data = await api(`/api/journey/${journey.id}/lyrics/regenerate`, { method: "POST", body: "{}" });
      $("songTitle").textContent = `《${data.lyrics.title}》`;
      $("verseText").textContent = data.lyrics.verse;
      $("chorusText").textContent = data.lyrics.chorus;
      setupVoiceLines(data.lyrics);
    } catch (e) {
      setStatus($("composeStatus"), e.message, true);
    }
  });
  $("btnToVoice").addEventListener("click", () => show("voice"));
  $("btnFinalize").addEventListener("click", () => finalize());
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
      el.hidden = loggedIn;
    });
    document.querySelectorAll("[data-auth='user']").forEach((el) => {
      el.hidden = !loggedIn;
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
