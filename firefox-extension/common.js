/* Shared settings + helpers (used by background, popup and options). */

const KIDLAT_DEFAULTS = {
  enabled: true,          // catch Firefox downloads
  port: 9614,
  captureAll: false,      // true = every file type
  minSizeKB: 0,           // skip files smaller than this (when size is known)
  extensions:
    "zip rar 7z tar gz bz2 xz tgz iso exe msi apk jar dmg deb rpm " +
    "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts " +
    "mp3 m4a aac wav flac ogg opus wma " +
    "pdf doc docx xls xlsx ppt pptx epub mobi " +
    "bin img torrent psd",
  skipSites: ""           // space separated domains never caught, e.g. "drive.google.com"
};

async function kidlatSettings() {
  const s = await browser.storage.local.get(KIDLAT_DEFAULTS);
  return Object.assign({}, KIDLAT_DEFAULTS, s);
}

function kidlatBase(port) {
  return `http://127.0.0.1:${port}`;
}

async function kidlatFetch(path, body, timeoutMs = 2500) {
  const s = await kidlatSettings();
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(kidlatBase(s.port) + path, {
      method: body ? "POST" : "GET",
      headers: Object.assign({ "X-Kidlat": "1" },
        body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      cache: "no-store"
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

async function kidlatAlive() {
  try {
    const r = await kidlatFetch("/ping", null, 1500);
    return r && r.app === "kidlat";
  } catch (e) {
    return false;
  }
}

function kidlatHumanSize(n) {
  if (!n || n < 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + " " + u[i];
}
