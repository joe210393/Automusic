(() => {
  const TOKEN_KEY = "automusic_admin_token";
  let token = sessionStorage.getItem(TOKEN_KEY) || "";
  let destCache = null;
  let editRouteId = null; // null = new route
  let destinations = [];

  const $ = (id) => document.getElementById(id);

  function setStatus(el, msg, kind) {
    if (!el) return;
    el.textContent = msg || "";
    el.classList.remove("error", "ok");
    if (kind) el.classList.add(kind);
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (token) headers["X-Admin-Token"] = token;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const res = await fetch(path, { ...opts, headers });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) {
      const detail = (data && data.detail) || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function parseHash() {
    const raw = (location.hash || "#/").replace(/^#/, "") || "/";
    const parts = raw.split("/").filter(Boolean);
    // /, /new, /activity, /:dest, /:dest/brand, /:dest/routes, /:dest/routes/new, /:dest/routes/:id, /:dest/moods
    if (!parts.length) return { page: "home" };
    if (parts[0] === "new") return { page: "new-dest" };
    if (parts[0] === "activity") return { page: "activity" };
    const destId = parts[0];
    if (parts.length === 1) return { page: "dest", destId };
    if (parts[1] === "brand") return { page: "brand", destId };
    if (parts[1] === "moods") return { page: "moods", destId };
    if (parts[1] === "routes") {
      if (parts.length === 2) return { page: "routes", destId };
      if (parts[2] === "new") return { page: "route-edit", destId, routeId: null };
      return { page: "route-edit", destId, routeId: parts[2] };
    }
    return { page: "dest", destId };
  }

  function go(path) {
    location.hash = path.startsWith("#") ? path : `#${path}`;
  }

  function showPage(name) {
    document.querySelectorAll(".page").forEach((el) => el.classList.add("hidden"));
    const page = $(`page-${name}`);
    if (page) page.classList.remove("hidden");
    $("navHome").hidden = name === "login" || name === "home";
  }

  function setTitle(text) {
    $("pageTitle").textContent = text;
  }

  async function ensureDest(destId) {
    if (destCache && destCache.id === destId) return destCache;
    destCache = await api(`/api/admin/destinations/${destId}`);
    return destCache;
  }

  async function refreshDestList() {
    const data = await api("/api/admin/destinations");
    destinations = data.destinations || [];
    const root = $("destList");
    root.innerHTML = "";
    if (!destinations.length) {
      root.innerHTML = "<p class='hint'>尚無目的地。</p>";
      return;
    }
    destinations.forEach((d) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "list-item";
      btn.innerHTML = `<div><strong>${esc(d.label || d.id)}</strong><span>${esc(d.tagline || d.id)}</span></div><div class="meta">${d.enabled ? "開放" : "關閉"}</div>`;
      btn.addEventListener("click", () => go(`/${d.id}`));
      root.appendChild(btn);
    });
  }

  const STATUS_LABEL = {
    created: "剛開始",
    route: "已選旅程",
    collecting: "錄音中",
    story: "已填關鍵字",
    style: "選歌手",
    composing: "作曲中",
    composed: "已作曲",
    preview: "已有預覽",
    voicing: "錄自己的聲音",
    finalizing: "製作中",
    finalized: "已完成",
    done: "已完成",
    error: "失敗",
  };

  async function authedAudioUrl(path) {
    const res = await fetch(path, { headers: { "X-Admin-Token": token } });
    if (!res.ok) throw new Error("無法載入音檔");
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  }

  async function renderActivity() {
    const root = $("activityList");
    const statsEl = $("activityStats");
    setStatus($("activityStatus"), "載入中…");
    root.innerHTML = "";
    const data = await api("/api/admin/activity?limit=120");
    const stats = data.stats || {};
    statsEl.innerHTML = `
      <span>旅程 ${stats.journeys ?? 0}</span>
      <span>帳號 ${stats.accounts ?? 0}</span>
      <span>有錄音 ${stats.with_sounds ?? 0}</span>
      <span>有成品 ${stats.with_final ?? 0}</span>
    `;
    const journeys = data.journeys || [];
    if (!journeys.length) {
      root.innerHTML = "<p class='hint'>尚無遊客旅程紀錄。</p>";
      setStatus($("activityStatus"), "");
      return;
    }
    for (const j of journeys) {
      const card = document.createElement("article");
      card.className = "activity-card";
      const acc = j.account;
      const who = acc
        ? `${esc(acc.display_name || "旅人")} · ${esc(acc.email || "")}`
        : "匿名遊客";
      const title = j.title || "尚未產出歌名";
      const status = STATUS_LABEL[j.status] || j.status || "—";
      const kw = (j.keywords || []).filter(Boolean).map(esc).join("、") || "（無）";
      const sounds = j.sounds || [];
      const soundHtml = sounds.length
        ? sounds.map((s, i) => `
            <div class="activity-sound">
              <span>${esc(s.label || s.slot || `錄音${i + 1}`)}</span>
              <audio controls preload="none" data-admin-src="${esc(s.url || "")}"></audio>
            </div>`).join("")
        : "<p class='hint'>尚未錄音</p>";
      const outputs = [];
      if (j.preview_url) outputs.push(`<div class="activity-sound"><span>預覽</span><audio controls preload="none" src="${esc(j.preview_url)}"></audio></div>`);
      if (j.final_url) outputs.push(`<div class="activity-sound"><span>成品</span><audio controls preload="none" src="${esc(j.final_url)}"></audio></div>`);
      if (j.share_public && j.slug) outputs.push(`<p><a href="/s/${esc(j.slug)}" target="_blank" rel="noopener">分享頁 /s/${esc(j.slug)}</a></p>`);
      if (!outputs.length) outputs.push("<p class='hint'>尚未產出音樂</p>");

      card.innerHTML = `
        <header>
          <div>
            <strong>${esc(title)}</strong>
            <span class="activity-who">${who}</span>
          </div>
          <span class="activity-badge">${esc(status)}</span>
        </header>
        <p class="activity-meta">
          ${esc(j.destination || "—")} · 路線 ${esc(j.route_id || "未選")} · 心情 ${esc(j.mood_id || "未選")}
          ${j.nickname ? ` · 暱稱「${esc(j.nickname)}」` : ""}
          <br />${esc(j.created || "")}
        </p>
        <div class="activity-block">
          <h3>玩了什麼</h3>
          <p>關鍵字：${kw}</p>
          ${j.memory ? `<p>回憶：${esc(j.memory)}</p>` : ""}
          ${j.feeling ? `<p>感覺：${esc(j.feeling)}</p>` : ""}
          ${j.voice_lines ? `<p>聲紋句數：${j.voice_lines}</p>` : ""}
        </div>
        <div class="activity-block">
          <h3>錄了什麼（${sounds.length}）</h3>
          ${soundHtml}
        </div>
        <div class="activity-block">
          <h3>產出了什麼</h3>
          ${outputs.join("")}
        </div>
        ${j.error ? `<p class="status error">${esc(j.error)}</p>` : ""}
      `;
      root.appendChild(card);
    }

    root.querySelectorAll("audio[data-admin-src]").forEach((audio) => {
      const src = audio.getAttribute("data-admin-src");
      if (!src) return;
      let loaded = false;
      audio.addEventListener("play", async () => {
        if (loaded) return;
        try {
          audio.pause();
          audio.src = await authedAudioUrl(src);
          loaded = true;
          await audio.play();
        } catch (e) {
          setStatus($("activityStatus"), e.message, "error");
        }
      });
    });
    setStatus($("activityStatus"), "");
  }

  async function renderDestHub(destId) {
    const d = await ensureDest(destId);
    const label = (d.brand && d.brand.place) || d.id;
    $("destHubTitle").textContent = label;
    $("destHubHint").textContent = `共 ${(d.routes || []).length} 條旅程 · ${(d.moodStyles || []).length} 種心情`;
    $("linkBrand").href = `#/${destId}/brand`;
    $("linkRoutes").href = `#/${destId}/routes`;
    $("linkMoods").href = `#/${destId}/moods`;
  }

  async function renderBrand(destId) {
    const d = await ensureDest(destId);
    $("brandBack").href = `#/${destId}`;
    const b = d.brand || {};
    $("brandPlace").value = b.place || "";
    $("brandHeadline").value = b.headline || "";
    $("brandSubhead").value = b.subhead || "";
    $("brandCta").value = b.cta || "";
    $("brandCore").value = b.coreLine || "";
    $("destEnabled").checked = d.enabled !== false;
    setStatus($("brandStatus"), "");
  }

  async function renderRoutes(destId) {
    const d = await ensureDest(destId);
    $("routesBack").href = `#/${destId}`;
    const root = $("routeList");
    root.innerHTML = "";
    const routes = d.routes || [];
    if (!routes.length) {
      root.innerHTML = "<p class='hint'>還沒有旅程。點右上角新增。</p>";
    }
    routes.forEach((r) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "list-item";
      const n = (r.soundTasks || []).length;
      btn.innerHTML = `<div><strong>${esc(r.label || r.id)}</strong><span>${esc(r.blurb || "")}</span></div><div class="meta">${n} 個聲音</div>`;
      btn.addEventListener("click", () => go(`/${destId}/routes/${r.id}`));
      root.appendChild(btn);
    });
    setStatus($("routeStatus"), "");
  }

  function taskRow(t) {
    const row = document.createElement("div");
    row.className = "task-row";
    row.innerHTML = `
      <label>任務 ID<input data-t="id" value="${esc(t.id || "")}" /></label>
      <label>顯示名稱<input data-t="label" value="${esc(t.label || "")}" /></label>
      <button type="button" class="ghost" data-rm>移除</button>
    `;
    row.querySelector("[data-rm]").addEventListener("click", () => row.remove());
    return row;
  }

  async function renderRouteEdit(destId, routeId) {
    const d = await ensureDest(destId);
    $("routeEditBack").href = `#/${destId}/routes`;
    editRouteId = routeId;
    const route = routeId
      ? (d.routes || []).find((r) => r.id === routeId)
      : null;
    if (routeId && !route) {
      setStatus($("routeEditStatus"), "找不到這條旅程", "error");
      go(`/${destId}/routes`);
      return;
    }
    $("routeEditTitle").textContent = route ? `編輯：${route.label}` : "新增旅程";
    $("routeId").value = route ? route.id : "";
    $("routeId").disabled = Boolean(route);
    $("routeLabel").value = route ? route.label || "" : "";
    $("routeBlurb").value = route ? route.blurb || "" : "";
    const tasks = $("taskEditor");
    tasks.innerHTML = "";
    const list = (route && route.soundTasks) || [
      { id: "sound1", label: "第一個聲音" },
      { id: "sound2", label: "第二個聲音" },
      { id: "sound3", label: "第三個聲音" },
    ];
    list.forEach((t) => tasks.appendChild(taskRow(t)));
    $("btnDeleteRoute").hidden = !route;
    setStatus($("routeEditStatus"), "");
  }

  function readTasks() {
    return [...$("taskEditor").querySelectorAll(".task-row")].map((row, i) => ({
      id: row.querySelector('[data-t="id"]').value.trim().toLowerCase() || `sound${i + 1}`,
      label: row.querySelector('[data-t="label"]').value.trim() || `聲音 ${i + 1}`,
    }));
  }

  async function renderMoods(destId) {
    const d = await ensureDest(destId);
    $("moodsBack").href = `#/${destId}`;
    const root = $("moodEditor");
    root.innerHTML = "";
    (d.moodStyles || []).forEach((m, idx) => root.appendChild(moodCard(m, idx)));
    if (!(d.moodStyles || []).length) {
      root.innerHTML = "<p class='hint'>尚無心情卡，點「新增心情」。</p>";
    }
    setStatus($("moodStatus"), "");
  }

  function moodCard(m, idx) {
    const card = document.createElement("div");
    card.className = "mood-card";
    card.innerHTML = `
      <h3><span>心情 ${idx + 1}</span><button type="button" class="ghost" data-rm>移除</button></h3>
      <div class="grid-form">
        <label>ID<input data-m="id" value="${esc(m.id || "")}" /></label>
        <label>名稱<input data-m="label" value="${esc(m.label || "")}" /></label>
        <label>engineStyle<input data-m="engineStyle" value="${esc(m.engineStyle || "pop")}" /></label>
        <label>符號<input data-m="emoji" value="${esc(m.emoji || "")}" /></label>
        <label class="span2">簡介<input data-m="blurb" value="${esc(m.blurb || "")}" /></label>
      </div>
    `;
    card.querySelector("[data-rm]").addEventListener("click", () => card.remove());
    return card;
  }

  function collectMoods() {
    return [...$("moodEditor").querySelectorAll(".mood-card")].map((card) => ({
      id: card.querySelector('[data-m="id"]').value.trim(),
      label: card.querySelector('[data-m="label"]').value.trim(),
      engineStyle: card.querySelector('[data-m="engineStyle"]').value.trim() || "pop",
      emoji: card.querySelector('[data-m="emoji"]').value.trim(),
      blurb: card.querySelector('[data-m="blurb"]').value.trim(),
    })).filter((m) => m.id && m.label);
  }

  async function savePartial(destId, patch) {
    const d = await ensureDest(destId);
    const payload = {
      enabled: d.enabled !== false,
      brand: d.brand || {},
      routes: d.routes || [],
      moodStyles: d.moodStyles || [],
      storyPrompts: d.storyPrompts || {},
      ...patch,
    };
    destCache = await api(`/api/admin/destinations/${destId}`, {
      method: "PUT",
      json: payload,
    });
    return destCache;
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  async function route() {
    const state = parseHash();
    if (!token && state.page !== "login") {
      showPage("login");
      setTitle("內容後台");
      return;
    }
    if (!token) {
      showPage("login");
      setTitle("內容後台");
      return;
    }

    try {
      switch (state.page) {
        case "home":
          showPage("home");
          setTitle("目的地列表");
          await refreshDestList();
          break;
        case "activity":
          showPage("activity");
          setTitle("遊客紀錄");
          await renderActivity();
          break;
        case "new-dest":
          showPage("new-dest");
          setTitle("新增目的地");
          setStatus($("newDestStatus"), "");
          break;
        case "dest":
          showPage("dest");
          setTitle("選擇要編輯的項目");
          await renderDestHub(state.destId);
          break;
        case "brand":
          showPage("brand");
          setTitle("品牌文案");
          await renderBrand(state.destId);
          break;
        case "routes":
          showPage("routes");
          setTitle("旅程路線");
          await renderRoutes(state.destId);
          break;
        case "route-edit":
          showPage("route-edit");
          setTitle(state.routeId ? "編輯旅程" : "新增旅程");
          await renderRouteEdit(state.destId, state.routeId);
          break;
        case "moods":
          showPage("moods");
          setTitle("心情風格");
          await renderMoods(state.destId);
          break;
        default:
          go("/");
      }
    } catch (e) {
      if (String(e.message).includes("金鑰") || String(e.message).includes("401")) {
        token = "";
        sessionStorage.removeItem(TOKEN_KEY);
        showPage("login");
        setStatus($("loginStatus"), e.message, "error");
        return;
      }
      alert(e.message);
      go("/");
    }
  }

  // —— events ——
  $("btnLogin").addEventListener("click", async () => {
    try {
      setStatus($("loginStatus"), "驗證中…");
      token = $("tokenInput").value.trim();
      await api("/api/admin/login", { method: "POST" });
      sessionStorage.setItem(TOKEN_KEY, token);
      setStatus($("loginStatus"), "");
      go("/");
      await route();
    } catch (e) {
      token = "";
      setStatus($("loginStatus"), e.message, "error");
    }
  });

  $("btnLogout").addEventListener("click", () => {
    token = "";
    sessionStorage.removeItem(TOKEN_KEY);
    destCache = null;
    go("/");
    showPage("login");
    setTitle("內容後台");
  });

  $("btnGoNewDest").addEventListener("click", () => go("/new"));
  $("navHome").addEventListener("click", (e) => {
    e.preventDefault();
    go("/");
  });

  $("btnCreateDest").addEventListener("click", async () => {
    const id = $("newDestId").value.trim().toLowerCase();
    const place = $("newDestPlace").value.trim();
    const headline = $("newDestHeadline").value.trim();
    if (!id || !place) {
      setStatus($("newDestStatus"), "請填 ID 與地名", "error");
      return;
    }
    try {
      await api("/api/admin/destinations", {
        method: "POST",
        json: { id, place, headline, enabled: true },
      });
      destCache = null;
      go(`/${id}`);
    } catch (e) {
      setStatus($("newDestStatus"), e.message, "error");
    }
  });

  $("btnGenerateBrand").addEventListener("click", async () => {
    const { destId } = parseHash();
    try {
      setStatus($("brandStatus"), "LM 生成中…");
      $("btnGenerateBrand").disabled = true;
      const data = await api(`/api/admin/destinations/${destId}/brand/generate`, {
        method: "POST",
        json: {
          place: $("brandPlace").value.trim(),
          hints: $("brandHints").value.trim(),
        },
      });
      const b = data.brand || {};
      if (b.place) $("brandPlace").value = b.place;
      if (b.headline) $("brandHeadline").value = b.headline;
      if (b.subhead) $("brandSubhead").value = b.subhead;
      if (b.cta) $("brandCta").value = b.cta;
      if (b.coreLine) $("brandCore").value = b.coreLine;
      const src = data.source === "lm_studio" ? "LM 已填入（尚未存檔）" : "LM 連不上，已填入模板（尚未存檔）";
      setStatus($("brandStatus"), src, "ok");
    } catch (e) {
      setStatus($("brandStatus"), e.message, "error");
    } finally {
      $("btnGenerateBrand").disabled = false;
    }
  });

  $("btnSaveBrand").addEventListener("click", async () => {
    const { destId } = parseHash();
    try {
      await savePartial(destId, {
        enabled: $("destEnabled").checked,
        brand: {
          ...(destCache.brand || {}),
          place: $("brandPlace").value.trim(),
          headline: $("brandHeadline").value.trim(),
          subhead: $("brandSubhead").value.trim(),
          cta: $("brandCta").value.trim(),
          coreLine: $("brandCore").value.trim(),
        },
      });
      setStatus($("brandStatus"), "已儲存", "ok");
    } catch (e) {
      setStatus($("brandStatus"), e.message, "error");
    }
  });

  $("btnAddRoute").addEventListener("click", () => {
    const { destId } = parseHash();
    go(`/${destId}/routes/new`);
  });

  $("btnAddTask").addEventListener("click", () => {
    const n = $("taskEditor").querySelectorAll(".task-row").length + 1;
    $("taskEditor").appendChild(taskRow({ id: `sound${n}`, label: `聲音 ${n}` }));
  });

  $("btnSaveRoute").addEventListener("click", async () => {
    const { destId } = parseHash();
    const route = {
      id: $("routeId").value.trim().toLowerCase(),
      label: $("routeLabel").value.trim(),
      blurb: $("routeBlurb").value.trim(),
      soundTasks: readTasks(),
    };
    if (!route.label) {
      setStatus($("routeEditStatus"), "請填旅程名稱", "error");
      return;
    }
    try {
      const data = await api(`/api/admin/destinations/${destId}/routes`, {
        method: "POST",
        json: route,
      });
      destCache = data.destination;
      setStatus($("routeEditStatus"), "已儲存", "ok");
      go(`/${destId}/routes/${data.route.id}`);
    } catch (e) {
      setStatus($("routeEditStatus"), e.message, "error");
    }
  });

  $("btnDeleteRoute").addEventListener("click", async () => {
    const { destId, routeId } = parseHash();
    if (!routeId || !confirm("確定刪除這條旅程？")) return;
    try {
      const data = await api(`/api/admin/destinations/${destId}/routes/${encodeURIComponent(routeId)}`, {
        method: "DELETE",
      });
      destCache = data.destination;
      go(`/${destId}/routes`);
    } catch (e) {
      setStatus($("routeEditStatus"), e.message, "error");
    }
  });

  $("btnAddMood").addEventListener("click", () => {
    const root = $("moodEditor");
    if (root.querySelector(".hint")) root.innerHTML = "";
    const n = root.querySelectorAll(".mood-card").length + 1;
    root.appendChild(moodCard({
      id: `mood_${n}`,
      label: "新心情",
      engineStyle: "pop",
      emoji: "",
      blurb: "",
    }, n - 1));
  });

  $("btnSaveMoods").addEventListener("click", async () => {
    const { destId } = parseHash();
    try {
      await savePartial(destId, { moodStyles: collectMoods() });
      setStatus($("moodStatus"), "已儲存", "ok");
      await renderMoods(destId);
    } catch (e) {
      setStatus($("moodStatus"), e.message, "error");
    }
  });

  $("btnRefreshActivity")?.addEventListener("click", async () => {
    try {
      await renderActivity();
    } catch (e) {
      setStatus($("activityStatus"), e.message, "error");
    }
  });

  window.addEventListener("hashchange", () => { route(); });

  (async () => {
    if (token) {
      try {
        await api("/api/admin/login", { method: "POST" });
      } catch {
        token = "";
        sessionStorage.removeItem(TOKEN_KEY);
      }
    }
    if (!location.hash || location.hash === "#") location.hash = "#/";
    await route();
  })();
})();
