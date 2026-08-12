(() => {
  const TOKEN_KEY = "automusic_account_token";
  const PAGE_SIZE = 8;
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
  let allJourneys = [];
  let page = 1;

  function token() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  /** 一律以台灣時間顯示（舊 UTC Z 也會轉成 +8） */
  function formatTaipei(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    try {
      return new Intl.DateTimeFormat("zh-TW", {
        timeZone: "Asia/Taipei",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
        .format(d)
        .replace(/\//g, "-");
    } catch (_) {
      return String(iso);
    }
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

  function statusBucket(status) {
    if (status === "done" || status === "finalized") return "done";
    if (status === "preview" || status === "composed") return "preview";
    if (status === "error") return "error";
    return "in_progress";
  }

  function filteredJourneys() {
    const q = ($("meSearch")?.value || "").trim().toLowerCase();
    const filter = $("meStatusFilter")?.value || "";
    return allJourneys.filter((j) => {
      if (filter && statusBucket(j.status) !== filter) return false;
      if (!q) return true;
      const blob = [
        j.title,
        j.destination,
        j.status,
        STATUS_LABEL[j.status],
        j.id,
        j.created,
      ]
        .join(" ")
        .toLowerCase();
      return blob.includes(q);
    });
  }

  async function coverSrc(j) {
    if (!j || !j.cover_url) return "";
    if (!j.cover_custom && j.cover_url.startsWith("/trip/")) {
      return j.cover_url_webp || j.cover_url;
    }
    return authedBlobUrl(j.cover_url);
  }

  function renderPager(total) {
    const pager = $("mePager");
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > totalPages) page = totalPages;
    if (total <= PAGE_SIZE) {
      pager.hidden = true;
      pager.innerHTML = "";
      return;
    }
    pager.hidden = false;
    pager.innerHTML = `
      <button type="button" class="me-page-btn" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一頁</button>
      <span class="me-page-info">${page} / ${totalPages}</span>
      <button type="button" class="me-page-btn" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>下一頁</button>
    `;
    pager.querySelectorAll("[data-page]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const n = Number(btn.getAttribute("data-page"));
        if (!Number.isFinite(n) || n < 1 || n > totalPages) return;
        page = n;
        renderList();
        $("meListCard")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  }

  function renderList() {
    const list = $("meJourneyList");
    const rows = filteredJourneys();
    const total = rows.length;
    const start = (page - 1) * PAGE_SIZE;
    const slice = rows.slice(start, start + PAGE_SIZE);
    if ($("meListMeta")) {
      $("meListMeta").textContent = total
        ? `共 ${total} 筆 · 台灣時間（UTC+8）`
        : "沒有符合的作品";
    }
    if (!allJourneys.length) {
      list.innerHTML = `<p class="me-empty">還沒有旅程作品。回首頁開始一趟吧。</p>`;
      renderPager(0);
      return;
    }
    if (!slice.length) {
      list.innerHTML = `<p class="me-empty">沒有符合篩選的作品。</p>`;
      renderPager(total);
      return;
    }
    list.innerHTML = slice
      .map((j) => {
        const status = STATUS_LABEL[j.status] || j.status || "—";
        const title = j.title || "未命名旅程";
        const cover = j.cover_url
          ? `<img class="me-thumb" data-cover="${esc(j.cover_url)}" data-webp="${esc(j.cover_url_webp || "")}" data-custom="${j.cover_custom ? "1" : "0"}" alt="" />`
          : `<div class="me-thumb me-thumb-empty" aria-hidden="true"></div>`;
        const flags = [
          j.has_final ? "成品" : null,
          j.sound_count ? `${j.sound_count} 錄音` : null,
        ]
          .filter(Boolean)
          .join(" · ");
        return `<article class="me-journey-item" data-id="${esc(j.id)}" tabindex="0">
        ${cover}
        <div class="me-journey-body">
          <div class="me-journey-top">
            <strong>${esc(title)}</strong>
            <span class="me-badge">${esc(status)}</span>
          </div>
          <span class="meta">${esc(formatTaipei(j.updated || j.created))} · ${esc(j.destination || "—")}${flags ? ` · ${esc(flags)}` : ""}</span>
        </div>
        <span class="me-chevron" aria-hidden="true">›</span>
      </article>`;
      })
      .join("");

    list.querySelectorAll(".me-journey-item").forEach((el) => {
      const open = () => el.dataset.id && openDetail(el.dataset.id);
      el.addEventListener("click", open);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
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
      } catch (_) {
        /* ignore */
      }
    });
    renderPager(total);
  }

  function bindTabs() {
    const tabs = document.querySelectorAll(".me-tab");
    const panels = document.querySelectorAll(".me-panel");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const name = tab.getAttribute("data-tab");
        tabs.forEach((t) => {
          t.classList.toggle("is-active", t === tab);
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        });
        panels.forEach((p) => {
          const on = p.getAttribute("data-panel") === name;
          p.classList.toggle("is-active", on);
          p.hidden = !on;
        });
      });
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
        `${STATUS_LABEL[j.status] || j.status || "—"} · ${j.destination || ""} · ${formatTaipei(j.updated || j.created)}`;

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
        ? sounds
            .map(
              (s) => `
            <div class="me-audio-row">
              <span>${esc(s.label || s.slot || "錄音")}</span>
              <audio controls preload="none" data-src="${esc(s.url)}"></audio>
            </div>`
            )
            .join("")
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
      if (j.preview_url)
        outs.push(
          `<div class="me-audio-row"><span>預覽伴奏</span><audio controls preload="none" src="${esc(j.preview_url)}"></audio></div>`
        );
      if (j.final_url) {
        outs.push(`<div class="me-audio-row"><span>AI 唱歌版</span><audio controls preload="none" src="${esc(j.final_url)}"></audio>
          <a class="me-btn" href="${esc(j.final_url)}" download>下載</a></div>`);
      }
      if (j.final_voice_url) {
        outs.push(`<div class="me-audio-row"><span>我的聲音版</span><audio controls preload="none" src="${esc(j.final_voice_url)}"></audio>
          <a class="me-btn" href="${esc(j.final_voice_url)}" download>下載</a></div>`);
      }
      $("meOutputs").innerHTML = outs.length
        ? outs.join("")
        : `<p class="me-empty">尚未產出音樂檔</p>`;

      $("meSounds")
        .querySelectorAll("audio[data-src]")
        .forEach((audio) => {
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
      await api(`/api/journey/${currentId}/title`, {
        method: "PATCH",
        json: { title },
      });
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

  function onFilterChange() {
    page = 1;
    renderList();
  }
  $("meSearch")?.addEventListener("input", onFilterChange);
  $("meStatusFilter")?.addEventListener("change", onFilterChange);

  async function loadList(resetView = true) {
    const me = await api("/api/account/me");
    const acc = me.account || {};
    $("meName").textContent = acc.display_name || "旅人";
    $("meEmail").textContent = acc.email || "";
    const q = acc.quota || {};
    $("meQuota").textContent = q.anonymous
      ? ""
      : `本月剩餘 ${q.remaining ?? "—"} / ${q.limit ?? "—"} 次成品`;
    allJourneys = me.journeys || [];
    if (resetView) page = 1;
    renderList();
    if (resetView) setStatus($("meStatus"), "");
    return me;
  }

  async function boot() {
    if (!token()) {
      location.href = "/login?next=/me";
      return;
    }
    bindTabs();
    try {
      setStatus($("meStatus"), "載入中…");
      await loadList(true);
      const q = new URLSearchParams(location.search).get("journey");
      if (q) await openDetail(q);
    } catch (e) {
      localStorage.removeItem(TOKEN_KEY);
      setStatus($("meStatus"), e.message || "請重新登入", "error");
      setTimeout(() => {
        location.href = "/login?next=/me";
      }, 800);
    }
  }

  $("btnLogout")?.addEventListener("click", logout);
  boot();
})();
