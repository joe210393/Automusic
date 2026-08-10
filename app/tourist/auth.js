(() => {
  const TOKEN_KEY = "automusic_account_token";
  const mode = () => window.AUTH_MODE || "login";

  function setStatus(msg, kind) {
    const el = document.getElementById("authStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.classList.remove("error", "ok");
    if (kind) el.classList.add(kind);
  }

  async function submit(path, payload) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || res.statusText || "失敗");
    }
    return data;
  }

  function bind(formId) {
    const form = document.getElementById(formId);
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = document.getElementById("email").value.trim();
      const displayName = (document.getElementById("displayName") || {}).value || "";
      if (!email) {
        setStatus("請填寫 email", "error");
        return;
      }
      try {
        setStatus(mode() === "register" ? "註冊中…" : "登入中…");
        const path = mode() === "register" ? "/api/account/register" : "/api/account/login";
        const data = await submit(path, {
          email,
          display_name: String(displayName || "").trim(),
        });
        localStorage.setItem(TOKEN_KEY, data.token);
        setStatus(`歡迎，${data.account.display_name || data.account.email}`, "ok");
        const next = new URLSearchParams(location.search).get("next") || "/";
        setTimeout(() => { location.href = next; }, 500);
      } catch (err) {
        setStatus(err.message || "失敗", "error");
      }
    });
  }

  bind("loginForm");
  bind("registerForm");
})();
