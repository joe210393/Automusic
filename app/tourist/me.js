(() => {
  const TOKEN_KEY = "automusic_account_token";
  const STATUS_LABEL = {
    created: "剛開始",
    route: "已選旅程",
    collecting: "錄音中",
    story: "已填關鍵字",
    style: "選歌手",
    composing: "作曲中",
    preview: "已有預覽",
    composed: "已作曲",
    voicing: "錄自己的聲音",
    finalizing: "製作成品中",
    finalized: "已完成",
    done: "已完成",
    error: "失敗",
  };

  const $ = (id) => document.getElementById(id);
  let currentId = null;

  function token() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function setStatus(el, msg, kind) {
    if (!el) return;
    el.textContent = msg || "";
    el.classList.remove("error", "ok");
    if (kind) el.classList.add(kind);
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    const t = token();
    if (t) headers["X-Account-Token"] = t;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const res = await fetch(path, { ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || res.statusText || "載入失敗");
    }
    return data;
  }

  async function authedBlobUrl(path) {
    const res = await fetch(path, { headers: { "X-Account-Token": token() } });
    if (!res.ok) throw new Error("無法載入檔案");
    return URL.createObjectURL(await res.blob());
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    location.href = "/login?next=/me";
  }

  function showList() {
    currentId = null;
    $("meListCard").classList.remove("hidden");
    $("meProfileCard").classList.remove("hidden");
    $("meDetailCard").classList.add("hidden");
    history.replaceState(null, "", "/me");
  }

  function showDetail() {
    $("meListCard").classList.add("hidden");
    $("meProfileCard").classList.add("hidden");
    $("meDetailCard").classList.remove("hidden");
  }

  async function coverSrc(j) {
    if (!j || !j.cover_url) return "";
    if (!j.cover_custom && j.cover_url.startsWith("/trip/")) {
      return j.cover_url_webp || j.cover_url;
    }
    return authedBlobUrl(j.cover_url);
  }

  function renderList(journeys) {
    const list = $("meJourneyList");
    if (!journeys.length) {
      list.innerHTML = `<p class="me-empty">還沒有旅程作品。回首頁開始一趟吧。</p>`;
      return;
    }
    list.innerHTML = journeys.map((j) => {
      const status = STATUS_LABEL[j.status] || j.status || "—";
      const title = j.title || "未命名旅程";
      const cover = j.cover_url
        ? `<img class="me-thumb" data-cover="${esc(j.cover_url)}" data-webp="${esc(j.cover_url_webp || "")}" data-custom="${j.cover_custom ? "1" : "0"}" alt="" />`
        : `<div class="me-thumb me-thumb-empty" aria-hidden="true"></div>`;
      const action = j.is_complete ? "打開回看" : "繼續編輯";
      return `<article class="me-journey-item" data-id="${esc(j.id)}">
        ${cover}
        <div class="me-journey-body">
          <strong>${esc(title)}</strong>
          <span class="meta">${esc(j.created || "")} · ${esc(j.destination || "")} · ${esc(status)}
            ${j.has_final ? " · 已有成品" : ""}
            ${j.sound_count ? ` · ${j.sound_count} 段錄音` : ""}</span>
          <button type="button" class="me-open" data-id="${esc(j.id)}">${action}</button>
        </div>
      </article>`;
    }).join("");

    list.querySelectorAll(".me-journey-item").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.dataset.id) openDetail(el.dataset.id);
      });
    });

    list.querySelectorAll("img[data-cover]").forEach(async (img) => {
      try {
        const custom = img.getAttribute("data-custom") === "1";
        const webp = img.getAttribute("data-webp");
        const url = img.getAttribute("data-cover");
        if (!custom && url && url.startsWith("/trip/")) {
          img.src = webp || url;
        } else {
          img.src = await authedBlobUrl(url);
        }
      } catch (_) { /* ignore */ }
    });
  }

  async function openDetail(id) {
    currentId = id;
    showDetail();
    setStatus($("meDetailStatus"), "載入中…");
    try {
      const j = await api(`/api/journey/${id}/library`);
      history.replaceState(null, "", `/me?journey=${encodeURIComponent(id)}`);
      $("meTitleInput").value = j.title || "";
      $("meDetailMeta").textContent =
        `${STATUS_LABEL[j.status] || j.status || "—"} · ${j.destination || ""} · ${j.created || ""}`;

      const coverImg = $("meCoverImg");
      const coverEmpty = $("meCoverEmpty");
      if (j.cover_url) {
        coverImg.hidden = false;
        coverEmpty.hidden = true;
        coverImg.src = await coverSrc(j);
      } else {
        coverImg.hidden = true;
        coverImg.removeAttribute("src");
        coverEmpty.hidden = false;
      }

      const actions = $("meDetailActions");
      const resumeHref = `/?journey=${encodeURIComponent(id)}`;
      if (j.is_complete) {
        actions.innerHTML = `
          <a class="me-btn primary" href="${resumeHref}">打開成品回看</a>
          ${j.share_path ? `<a class="me-btn" href="${esc(j.share_path)}" target="_blank" rel="noopener">分享頁</a>` : ""}
        `;
      } else {
        actions.innerHTML = `<a class="me-btn primary" href="${resumeHref}">繼續編輯這趟旅程</a>`;
      }

      const sounds = j.sounds || [];
      $("meSounds").innerHTML = sounds.length
        ? sounds.map((s) => `
            <div class="me-audio-row">
              <span>${esc(s.label || s.slot || "錄音")}</span>
              <audio controls preload="none" data-src="${esc(s.url)}"></audio>
            </div>`).join("")
        : `<p class="me-empty">尚未錄音</p>`;

      const ly = j.lyrics;
      if (ly && (ly.verse || ly.chorus || ly.title)) {
        $("meLyrics").innerHTML = `
          <p class="me-lyric-title">《${esc(ly.title || j.title || "旅行之歌")}》</p>
          <p class="me-lyric-label">主歌</p>
          <pre>${esc(ly.verse || "（無）")}</pre>
          <p class="me-lyric-label">副歌</p>
          <pre>${esc(ly.chorus || "（無）")}</pre>
          ${(j.keywords || []).length ? `<p class="meta">關鍵字：${esc((j.keywords || []).join("、"))}</p>` : ""}
        `;
      } else {
        $("meLyrics").innerHTML = `<p class="me-empty">尚未產出歌詞${(j.keywords || []).length ? `（已有關鍵字：${esc(j.keywords.join("、"))}）` : ""}</p>`;
      }

      const outs = [];
      if (j.preview_url) outs.push(`<div class="me-audio-row"><span>旅途旋律伴奏</span><audio controls preload="none" src="${esc(j.preview_url)}"></audio></div>`);
      if (j.final_url) {
        outs.push(`<div class="me-audio-row"><span>AI 試聽版</span><audio controls preload="none" src="${esc(j.final_url)}"></audio>
          <a class="me-btn" href="${esc(j.final_url)}" download>下載</a></div>`);
      }
      if (j.final_full_url) {
        outs.push(`<div class="me-audio-row"><span>AI 完整版</span><audio controls preload="none" src="${esc(j.final_full_url)}"></audio>
          <a class="me-btn" href="${esc(j.final_full_url)}" download>下載</a></div>`);
      }
      if (j.final_voice_url) {
        outs.push(`<div class="me-audio-row"><span>我的聲音版</span><audio controls preload="none" src="${esc(j.final_voice_url)}"></audio>
          <a class="me-btn" href="${esc(j.final_voice_url)}" download>下載</a></div>`);
      }
      $("meOutputs").innerHTML = outs.length ? outs.join("") : `<p class="me-empty">尚未產出音樂檔</p>`;

      $("meSounds").querySelectorAll("audio[data-src]").forEach((audio) => {
        let loaded = false;
        audio.addEventListener("play", async () => {
          if (loaded) return;
          try {
            audio.pause();
            audio.src = await authedBlobUrl(audio.getAttribute("data-src"));
            loaded = true;
            await audio.play();
          } catch (err) {
            setStatus($("meDetailStatus"), err.message, "error");
          }
        });
      });

      setStatus($("meDetailStatus"), "");
    } catch (e) {
      setStatus($("meDetailStatus"), e.message, "error");
    }
  }

  $("btnBackList")?.addEventListener("click", showList);

  $("btnSaveTitle")?.addEventListener("click", async () => {
    if (!currentId) return;
    try {
      const title = $("meTitleInput").value.trim();
      await api(`/api/journey/${currentId}/title`, { method: "PATCH", json: { title } });
      setStatus($("meDetailStatus"), "名稱已儲存", "ok");
      await loadList(false);
    } catch (e) {
      setStatus($("meDetailStatus"), e.message, "error");
    }
  });

  $("btnUploadCover")?.addEventListener("click", async () => {
    if (!currentId) return;
    const input = $("meCoverFile");
    if (!input.files || !input.files[0]) {
      setStatus($("meDetailStatus"), "請先選擇照片", "error");
      return;
    }
    try {
      setStatus($("meDetailStatus"), "上傳封面中…");
      const fd = new FormData();
      fd.append("file", input.files[0]);
      const res = await fetch(`/api/journey/${currentId}/cover`, {
        method: "POST",
        headers: { "X-Account-Token": token() },
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "上傳失敗");
      setStatus($("meDetailStatus"), "封面已更新", "ok");
      await openDetail(currentId);
      await loadList(false);
    } catch (e) {
      setStatus($("meDetailStatus"), e.message, "error");
    }
  });

  async function loadList(resetView = true) {
    const me = await api("/api/account/me");
    const acc = me.account || {};
    $("meName").textContent = acc.display_name || "旅人";
    $("meEmail").textContent = acc.email || "";
    const q = acc.quota || {};
    $("meQuota").textContent = q.anonymous
      ? ""
      : `本月剩餘 ${q.remaining ?? "—"} / ${q.limit ?? "—"} 次成品`;
    renderList(me.journeys || []);
    if (resetView) setStatus($("meStatus"), "");
    return me;
  }

  async function boot() {
    if (!token()) {
      location.href = "/login?next=/me";
      return;
    }
    try {
      setStatus($("meStatus"), "載入中…");
      await loadList(true);
      const q = new URLSearchParams(location.search).get("journey");
      if (q) await openDetail(q);
    } catch (e) {
      localStorage.removeItem(TOKEN_KEY);
      setStatus($("meStatus"), e.message || "請重新登入", "error");
      setTimeout(() => { location.href = "/login?next=/me"; }, 800);
    }
  }

  $("btnLogout")?.addEventListener("click", logout);
  boot();
})();
