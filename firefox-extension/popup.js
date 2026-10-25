(async () => {
  const $ = id => document.getElementById(id);
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  const s = await kidlatSettings();

  $("enabled").checked = s.enabled;
  $("enabled").addEventListener("change", e =>
    browser.storage.local.set({ enabled: e.target.checked }));

  const alive = await browser.runtime.sendMessage({ type: "alive" });
  $("status").textContent = alive ? "App is running" : "App is not running - start Kidlat";
  $("status").className = "status " + (alive ? "ok" : "bad");

  const pv = $("pagevideo");
  const webPage = /^https?:/i.test(tab.url || "");
  pv.disabled = !alive || !webPage;
  pv.addEventListener("click", async () => {
    pv.disabled = true;
    pv.textContent = "Sending…";
    try {
      await browser.runtime.sendMessage({ type: "page-video", tabId: tab.id });
      pv.textContent = "✓ Sent - choose quality in Kidlat";
    } catch (e) {
      pv.textContent = "Couldn't reach Kidlat";
    }
  });

  const list = await browser.runtime.sendMessage({ type: "media", tabId: tab.id });
  $("nomedia").hidden = list.length > 0;
  for (const m of list) {
    const li = document.createElement("li");
    const info = document.createElement("div");
    info.className = "info";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = m.name;
    name.title = m.url;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [m.type, kidlatHumanSize(m.size)].filter(Boolean).join(" • ");
    info.append(name, meta);
    const btn = document.createElement("button");
    btn.textContent = "Download";
    btn.disabled = !alive;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await browser.runtime.sendMessage({ type: "grab", url: m.url, tabId: tab.id,
                                            stream: !!m.stream });
        btn.textContent = "Sent ✓";
      } catch (e) {
        btn.textContent = "Failed";
      }
    });
    li.append(info, btn);
    $("media").append(li);
  }

  $("addbtn").addEventListener("click", async () => {
    const u = $("url").value.trim();
    if (!/^https?:\/\//i.test(u)) { $("url").focus(); return; }
    try {
      await browser.runtime.sendMessage({ type: "add-url", url: u });
      $("url").value = "";
      $("addbtn").textContent = "Sent ✓";
    } catch (e) {
      $("addbtn").textContent = "Failed";
    }
  });

  $("open").addEventListener("click", async () => {
    await browser.runtime.sendMessage({ type: "show" });
    window.close();
  });
  $("open").disabled = !alive;
  $("opts").addEventListener("click", e => {
    e.preventDefault();
    browser.runtime.openOptionsPage();
    window.close();
  });
})();
