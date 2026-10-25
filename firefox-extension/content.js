/* Kidlat - floating "Download this video" button (IDM-style) */
(() => {
  if (window.__kidlatInjected) return;
  window.__kidlatInjected = true;

  const MIN_W = 200, MIN_H = 110;
  const LABEL = "⚡ Download this video";
  let host = null, btn = null, current = null, hideTimer = null;
  let dismissed = false, overBar = false, busy = false;

  function ensureUI() {
    if (host) return;
    host = document.createElement("kidlat-bar");
    host.style.cssText = "all:initial;position:fixed;z-index:2147483647;top:0;left:0;display:none;";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        .wrap{display:flex;align-items:center;font:600 13px/1 system-ui,"Segoe UI",Arial,sans-serif;
          background:#2b6cb0;color:#fff;border-radius:18px;overflow:hidden;
          box-shadow:0 2px 10px rgba(0,0,0,.4);opacity:.97;user-select:none}
        button{all:unset;cursor:pointer;padding:8px 12px;color:#fff;white-space:nowrap}
        button:hover{background:#2c5282}
        .x{padding:8px 10px;opacity:.85;border-left:1px solid rgba(255,255,255,.3)}
      </style>
      <div class="wrap"><button class="dl"></button><button class="x" title="Hide on this page">✕</button></div>`;
    btn = root.querySelector(".dl");
    btn.textContent = LABEL;
    btn.addEventListener("click", onClick);
    root.querySelector(".x").addEventListener("click", () => { dismissed = true; hide(); });
    host.addEventListener("mouseenter", () => { overBar = true; clearTimeout(hideTimer); });
    host.addEventListener("mouseleave", () => { overBar = false; scheduleHide(1200); });
  }

  function attachHost() {
    const fs = document.fullscreenElement;
    const parent = fs && fs.tagName !== "VIDEO" ? fs : document.documentElement;
    if (host.parentNode !== parent) parent.appendChild(host);
  }

  function goodVideo(v) {
    if (!v || !v.isConnected) return false;
    const r = v.getBoundingClientRect();
    if (r.width < MIN_W || r.height < MIN_H) return false;
    const st = getComputedStyle(v);
    return st.visibility !== "hidden" && st.display !== "none" && st.opacity !== "0";
  }

  function place() {
    if (!current || !host || host.style.display === "none") return;
    if (!goodVideo(current)) { hide(); return; }
    const r = current.getBoundingClientRect();
    const w = host.offsetWidth || 230;
    const top = Math.max(4, Math.min(r.top + 10, innerHeight - 44));
    const left = Math.max(4, Math.min(r.right - w - 10, innerWidth - w - 4));
    host.style.top = top + "px";
    host.style.left = left + "px";
  }

  function show(v) {
    if (dismissed) return;
    ensureUI();
    attachHost();
    current = v;
    clearTimeout(hideTimer);
    host.style.display = "block";
    place();
  }

  function hide() {
    if (host) host.style.display = "none";
    if (!busy) current = null;
  }

  function scheduleHide(ms) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { if (!overBar && !busy) hide(); }, ms);
  }

  function videoAt(x, y) {
    for (const v of document.querySelectorAll("video")) {
      if (!goodVideo(v)) continue;
      const r = v.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return v;
    }
    return null;
  }

  let lastMove = 0;
  document.addEventListener("mousemove", (e) => {
    const now = Date.now();
    if (now - lastMove < 120) return;
    lastMove = now;
    const v = videoAt(e.clientX, e.clientY);
    if (v) show(v);
    else if (host && host.style.display !== "none" && !overBar) scheduleHide(1500);
  }, { passive: true, capture: true });

  document.addEventListener("play", (e) => {
    const v = e.target;
    if (v && v.tagName === "VIDEO" && goodVideo(v)) {
      show(v);
      scheduleHide(4000);
    }
  }, true);

  addEventListener("scroll", place, { passive: true, capture: true });
  addEventListener("resize", place, { passive: true });
  document.addEventListener("fullscreenchange", () => { if (host) { attachHost(); place(); } });

  async function onClick(e) {
    e.preventDefault();
    e.stopPropagation();
    const v = current;
    if (!v || busy) return;
    busy = true;
    btn.textContent = "Sending to Kidlat…";
    let src = v.currentSrc || v.src || "";
    if (!src) {
      const s = v.querySelector("source[src]");
      if (s) src = s.src;
    }
    let res;
    try {
      res = await browser.runtime.sendMessage({
        type: "video", src, frameUrl: location.href, title: document.title
      });
    } catch (err) {
      res = { ok: false, error: "Extension error" };
    }
    btn.textContent = res && res.ok ? "✓ Sent to Kidlat" : "✕ " + ((res && res.error) || "Kidlat isn't running");
    setTimeout(() => {
      btn.textContent = LABEL;
      busy = false;
      if (!overBar) scheduleHide(800);
    }, 2500);
  }
})();
