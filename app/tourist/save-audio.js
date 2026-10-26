/**
 * 下載音檔但留在原頁：手機瀏覽器遇到一般 <a download> 常直接打開播放頁，回不來。
 * 這裡先抓成 blob 再觸發下載；LINE／FB 內建瀏覽器多半不支援下載，改提示用外部瀏覽器開。
 */
(() => {
  const IN_APP = /\bLine\/|FBAN|FBAV|Instagram|MicroMessenger/i.test(navigator.userAgent);

  async function saveAudio(url, filename) {
    if (IN_APP) {
      return {
        ok: false,
        message: "這個 App 內建瀏覽器無法下載，請點右上角「用瀏覽器開啟」（Safari 或 Chrome）後再按下載。",
      };
    }
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = filename;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 60000);
      return { ok: true, message: "已開始下載。" };
    } catch (_) {
      return { ok: false, message: "下載失敗，請稍後再試。" };
    }
  }

  window.saveAudio = saveAudio;
})();
