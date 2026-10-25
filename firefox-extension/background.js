/* Kidlat - Firefox background script */

const media = new Map();   // tabId -> [{url, size, type, name}]

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------
function extOf(nameOrUrl) {
  try {
    let p = nameOrUrl;
    if (/^https?:/i.test(p)) p = new URL(p).pathname;
    p = decodeURIComponent(p).split(/[\\/]/).pop();
    const i = p.lastIndexOf(".");
    return i > 0 ? p.slice(i + 1).toLowerCase() : "";
  } catch (e) {
    return "";
  }
}

function hostOf(url) {
  try { return new URL(url).hostname; } catch (e) { return ""; }
}

const VIDEO_SITES = [
  "youtube.com", "youtu.be", "youtube-nocookie.com", "facebook.com", "fb.watch",
  "tiktok.com", "twitter.com", "x.com", "instagram.com", "vimeo.com",
  "dailymotion.com", "twitch.tv", "reddit.com", "bilibili.com", "soundcloud.com",
  "streamable.com", "rumble.com", "bsky.app", "threads.net", "9gag.com"
];

function onSite(url, list) {
  const h = hostOf(url);
  return list.some(d => h === d || h.endsWith("." + d));
}

function isYouTube(url) {
  return onSite(url, ["youtube.com", "youtu.be", "youtube-nocookie.com"]);
}

function cleanTitle(t) {
  return (t || "").replace(/^\(\d+\)\s*/, "").replace(/\s+[-|–]\s+YouTube$/i, "")
    .replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 120);
}

// Send a "video site" page to Kidlat (it uses yt-dlp to find the video)
async function sendMediaPage(tab, pageUrl, fallbackUrl, title) {
  const item = await buildItem(pageUrl, { referrer: tab.url, storeId: tab.cookieStoreId });
  if (isYouTube(pageUrl)) item.cookies = "";     // YouTube works best without them
  item.kind = "media";
  item.fallbackUrl = fallbackUrl || "";
  item.title = cleanTitle(title || tab.title);
  return sendToKidlat(item);
}

function bestStream(tabId) {
  const s = (media.get(tabId) || []).filter(m => m.stream).pop();   // the first one seen
  return s ? s.url : "";
}

function baseName(path) {
  return (path || "").split(/[\\/]/).pop();
}

async function cookieHeader(url, storeId) {
  try {
    const q = { url, firstPartyDomain: null, partitionKey: {} };
    if (storeId) q.storeId = storeId;
    let list;
    try {
      list = await browser.cookies.getAll(q);
    } catch (e) {          // older Firefox: no partitionKey support
      delete q.partitionKey;
      list = await browser.cookies.getAll(q);
    }
    const seen = new Map();
    for (const c of list) {
      // prefer unpartitioned cookies when names clash
      if (!seen.has(c.name) || !c.partitionKey) seen.set(c.name, c.value);
    }
    return [...seen].map(([k, v]) => `${k}=${v}`).join("; ");
  } catch (e) {
    return "";
  }
}

async function buildItem(url, opts = {}) {
  return {
    url,
    filename: opts.filename || "",
    referrer: opts.referrer || "",
    cookies: await cookieHeader(url, opts.storeId),
    userAgent: navigator.userAgent
  };
}

async function sendToKidlat(items) {
  return kidlatFetch("/add", items, 5000);
}

async function tellTab(tabId, msg) {
  try {
    await browser.tabs.executeScript(tabId, { code: `alert(${JSON.stringify(msg)});` });
  } catch (e) {
    console.warn("Kidlat:", msg);
  }
}

function wantsType(s, name, url) {
  if (s.captureAll) return true;
  const list = s.extensions.toLowerCase().split(/[\s,;]+/).filter(Boolean);
  const e = extOf(name) || extOf(url);
  return e && list.includes(e);
}

function siteSkipped(s, url) {
  const h = hostOf(url);
  return s.skipSites.toLowerCase().split(/[\s,;]+/).filter(Boolean)
    .some(d => h === d || h.endsWith("." + d));
}

// --------------------------------------------------------------------------
// 1) Catch downloads that Firefox starts
// --------------------------------------------------------------------------
browser.downloads.onCreated.addListener(async (item) => {
  const s = await kidlatSettings();
  if (!s.enabled) return;
  if (!/^https?:/i.test(item.url)) return;            // blob:, data:, file:
  if (item.byExtensionId && item.byExtensionId !== browser.runtime.id) return;
  if (siteSkipped(s, item.url)) return;
  if (!wantsType(s, item.filename, item.url)) return;
  if (s.minSizeKB > 0 && item.totalBytes > 0 && item.totalBytes < s.minSizeKB * 1024) return;

  if (!(await kidlatAlive())) return;                 // app closed -> let Firefox do it

  try {
    await browser.downloads.cancel(item.id);
  } catch (e) {
    return;                                           // already finished - leave it
  }
  try { await browser.downloads.erase({ id: item.id }); } catch (e) { /* ignore */ }

  const payload = await buildItem(item.finalUrl || item.url, {
    filename: baseName(item.filename),
    referrer: item.referrer,
    storeId: item.cookieStoreId
  });
  try {
    await sendToKidlat(payload);
  } catch (e) {
    // app vanished between ping and send: give the download back to Firefox
    browser.downloads.download({ url: item.url, saveAs: false })
      .catch(() => {});
  }
});

// --------------------------------------------------------------------------
// 2) Right-click menu
// --------------------------------------------------------------------------
browser.contextMenus.removeAll().then(() => {
  browser.contextMenus.create({ id: "kidlat-link", title: "Download with Kidlat",
                                contexts: ["link"] });
  browser.contextMenus.create({ id: "kidlat-media", title: "Download this video/audio with Kidlat",
                                contexts: ["video", "audio"] });
  browser.contextMenus.create({ id: "kidlat-image", title: "Download image with Kidlat",
                                contexts: ["image"] });
  browser.contextMenus.create({ id: "kidlat-all", title: "Download all links with Kidlat",
                                contexts: ["page", "selection"] });
});

browser.contextMenus.onClicked.addListener(async (info, tab) => {
  const storeId = tab && tab.cookieStoreId;
  const referrer = (tab && tab.url) || info.pageUrl || "";
  if (!(await kidlatAlive())) {
    tellTab(tab.id, "Kidlat is not running.\nStart Kidlat Download Manager first, then try again.");
    return;
  }
  let urls = [];
  if (info.menuItemId === "kidlat-link") urls = [info.linkUrl];
  else if (info.menuItemId === "kidlat-media" || info.menuItemId === "kidlat-image") urls = [info.srcUrl];
  else if (info.menuItemId === "kidlat-all") {
    const s = await kidlatSettings();
    let links = [];
    try {
      const [res] = await browser.tabs.executeScript(tab.id, {
        code: `(() => {
          const sel = window.getSelection();
          const inSel = a => !sel || sel.isCollapsed || sel.containsNode(a, true);
          return [...document.links].filter(inSel).map(a => a.href);
        })();`
      });
      links = res || [];
    } catch (e) { /* page not scriptable */ }
    const list = s.extensions.toLowerCase().split(/[\s,;]+/).filter(Boolean);
    urls = [...new Set(links)].filter(u => /^https?:/i.test(u) && list.includes(extOf(u)));
    if (!urls.length) {
      tellTab(tab.id, "Kidlat: no downloadable files found in the links on this page.");
      return;
    }
  }
  urls = urls.filter(u => /^https?:/i.test(u || ""));
  if (!urls.length) {
    tellTab(tab.id, "Kidlat: this item has no direct web link (it may be a blob/stream).");
    return;
  }
  const items = [];
  for (const u of urls) items.push(await buildItem(u, { referrer, storeId }));
  try {
    await sendToKidlat(items.length === 1 ? items[0] : items);
  } catch (e) {
    tellTab(tab.id, "Kidlat: could not reach the app (" + e.message + ").");
  }
});

// --------------------------------------------------------------------------
// 3) Video / audio finder (direct media files playing on a page)
// --------------------------------------------------------------------------
function header(headers, name) {
  const h = headers.find(x => x.name.toLowerCase() === name);
  return h ? h.value : "";
}

browser.webRequest.onHeadersReceived.addListener((d) => {
  if (d.tabId < 0) return;
  const type = header(d.responseHeaders, "content-type").split(";")[0].trim().toLowerCase();
  const ext = extOf(d.url);
  const isMedia = /^(video|audio)\//.test(type) ||
    ["mp4", "webm", "mkv", "mov", "m4v", "mp3", "m4a", "ogg", "opus", "flac", "wav"].includes(ext);
  const isStream = /mpegurl|dash\+xml/.test(type) || ["m3u8", "mpd"].includes(ext);
  if (!isMedia && !isStream) return;
  if (/mp2t/.test(type) || ["ts", "m4s"].includes(ext)) return;          // stream pieces
  if (/videoplayback/.test(d.url) || /googlevideo\.com$/.test(hostOf(d.url))) return; // YouTube pieces

  let size = -1;
  const cr = header(d.responseHeaders, "content-range");
  const m = cr.match(/\/(\d+)/);
  if (m) size = parseInt(m[1], 10);
  else if (d.statusCode === 200) size = parseInt(header(d.responseHeaders, "content-length") || "-1", 10);
  if (!isStream && size > 0 && size < 300 * 1024) return;   // ads, tiny sounds

  const list = media.get(d.tabId) || [];
  const key = d.url.split("#")[0];
  if (list.some(x => x.url === key)) return;
  let name = baseName(new URL(key).pathname) || "media";
  try { name = decodeURIComponent(name); } catch (e) { /* keep */ }
  if (isStream) {
    if (list.filter(x => x.stream).length >= 3) return;      // first ones are the main playlists
    name = "Streamed video (" + (ext === "mpd" || /dash/.test(type) ? "DASH" : "HLS") + ")";
    size = -1;
  }
  list.unshift({ url: key, size, type: isStream ? "stream" : (type || ext), name, stream: isStream });
  media.set(d.tabId, list.slice(0, 30));
  browser.browserAction.setBadgeText({ tabId: d.tabId, text: String(media.get(d.tabId).length) });
  browser.browserAction.setBadgeBackgroundColor({ tabId: d.tabId, color: "#2b6cb0" });
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "object", "other"] },
["responseHeaders"]);

browser.tabs.onUpdated.addListener((tabId, change) => {
  if (change.url) {
    media.delete(tabId);
    browser.browserAction.setBadgeText({ tabId, text: "" });
  }
});
browser.tabs.onRemoved.addListener(tabId => media.delete(tabId));

// --------------------------------------------------------------------------
// messages from the popup
// --------------------------------------------------------------------------
browser.runtime.onMessage.addListener(async (msg, sender) => {
  if (msg.type === "media") return media.get(msg.tabId) || [];
  if (msg.type === "alive") return kidlatAlive();
  if (msg.type === "show") { try { await kidlatFetch("/show"); } catch (e) { /* ignore */ } return true; }
  if (msg.type === "video") {        // floating button on a video
    const tab = sender.tab;
    if (!(await kidlatAlive())) return { ok: false, error: "Start Kidlat first" };
    const src = msg.src || "";
    const pageUrl = sender.frameId === 0 ? tab.url : (msg.frameUrl || tab.url);
    const direct = /^https?:/i.test(src) && !/\.(m3u8|mpd)([?#]|$)/i.test(src);
    try {
      if (direct && !onSite(pageUrl, VIDEO_SITES)) {
        // a plain video file: Kidlat downloads it directly with many connections
        const item = await buildItem(src, { referrer: pageUrl, storeId: tab.cookieStoreId });
        const t = cleanTitle(msg.title || tab.title);
        const ext = extOf(src) || "mp4";
        if (t) item.filename = `${t}.${ext}`;
        await sendToKidlat(item);
      } else {
        const fallback = direct ? src : (bestStream(tab.id) ||
                         (pageUrl !== tab.url ? tab.url : ""));
        await sendMediaPage(tab, pageUrl, fallback, msg.title || tab.title);
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, error: "Couldn't reach Kidlat" };
    }
  }
  if (msg.type === "page-video") {   // popup: "Download video from this page"
    const tab = await browser.tabs.get(msg.tabId);
    await sendMediaPage(tab, tab.url, bestStream(tab.id), tab.title);
    return true;
  }
  if (msg.type === "grab" && msg.stream) {
    const tab = await browser.tabs.get(msg.tabId);
    await sendMediaPage(tab, msg.url, tab.url, tab.title);
    return true;
  }
  if (msg.type === "grab") {
    const tab = await browser.tabs.get(msg.tabId);
    let title = (tab.title || "").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 120);
    const item = await buildItem(msg.url, { referrer: tab.url, storeId: tab.cookieStoreId });
    const ext = extOf(msg.url);
    if (title && ext) item.filename = `${title}.${ext}`;
    await sendToKidlat(item);
    return true;
  }
  if (msg.type === "add-url") {
    await sendToKidlat(await buildItem(msg.url, {}));
    return true;
  }
});
