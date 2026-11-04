// Created by Codex on 2026/10/25.
// Copyright © 2026 CMIC. All rights reserved.
/* Gemini-only transport bridge. The dashboard UI and behavior remain in dashboard.js. */
(() => {
  const BASE = "http://127.0.0.1:8766";
  const token = window.__GEMINI_CAPABILITY_TOKEN__;
  const nativeFetch = window.fetch.bind(window);
  const objectUrls = new Map();
  document.body.classList.add("gemini-view");

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-Gemini-Token", token);
    return nativeFetch(new URL(path, BASE), { cache: "no-store", ...options, headers });
  }

  window.fetch = (input, options = {}) => {
    const raw = typeof input === "string" ? input : input?.url;
    if (typeof raw !== "string" || !raw.startsWith("/api/")) return nativeFetch(input, options);
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(options.headers || {}).forEach((value, key) => headers.set(key, value));
    headers.set("X-Gemini-Token", token);
    return nativeFetch(new URL(raw, BASE), { cache: "no-store", ...options, headers });
  };

  function remember(node, url) { objectUrls.set(node, url); }
  async function blobFor(path) {
    const response = await request(path);
    if (!response.ok) throw new Error("附件读取失败（" + response.status + "）。");
    return response.blob();
  }

  function renderAttachment({ row, href, type, name }) {
    if (type.startsWith("image/")) {
      const link = document.createElement("a"); link.className = "bug-attachment-preview"; link.href = "#";
      link.setAttribute("aria-label", "查看图片：" + name);
      const image = document.createElement("img"); image.alt = name; image.loading = "lazy"; link.append(image); row.append(link);
      blobFor(href).then(blob => {
        const url = URL.createObjectURL(blob); remember(image, url); remember(link, url); image.src = url; link.href = url;
      }).catch(error => { const note = document.createElement("p"); note.className = "bug-evidence-note"; note.textContent = error.message; row.append(note); });
      return;
    }
    if (type.startsWith("video/")) {
      const video = document.createElement("video"); video.className = "bug-attachment-video"; video.controls = true; video.preload = "none";
      video.setAttribute("aria-label", "播放视频：" + name); row.append(video);
      let pending = false;
      video.addEventListener("play", async () => {
        if (video.dataset.authorizedBlobReady === "true" || pending) return;
        pending = true; video.pause();
        try {
          const blob = await blobFor(href); const url = URL.createObjectURL(blob); remember(video, url);
          video.src = url; video.dataset.authorizedBlobReady = "true"; video.load(); await video.play();
        } catch (error) {
          const note = document.createElement("p"); note.className = "bug-evidence-note"; note.textContent = error.message; row.append(note);
        } finally { pending = false; }
      });
      return;
    }
    const link = document.createElement("a"); link.href = "#"; link.textContent = "打开本地归档附件";
    link.addEventListener("click", async event => {
      event.preventDefault(); const popup = window.open("about:blank", "_blank");
      try {
        const blob = await blobFor(href);
        if (popup) {
          const url = URL.createObjectURL(blob); popup.opener = null; popup.location.replace(url);
          setTimeout(() => URL.revokeObjectURL(url), 60000);
        }
        else downloadBlob(blob, name);
      } catch (error) { link.textContent = error.message; }
    });
    row.append(link);
  }

  function downloadBlob(blob, name) {
    const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = name || "工作台来源"; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function openSource(source, path) {
    if (source.type === "link" && /^https?:\/\//i.test(source.url || "")) { window.open(source.url, "_blank", "noopener,noreferrer"); return; }
    blobFor(path).then(blob => downloadBlob(blob, source.filename || source.name)).catch(error => window.alert(error.message));
  }

  const cleanup = new MutationObserver(() => {
    for (const [node, url] of objectUrls) {
      if (node && !node.isConnected) { URL.revokeObjectURL(url); objectUrls.delete(node); }
    }
  });
  cleanup.observe(document, { childList: true, subtree: true });
  document.addEventListener("click", event => {
    if (event.target.closest?.("#inspection-run")) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  window.workbenchTransport = { request, openSource, renderAttachment };
})();
