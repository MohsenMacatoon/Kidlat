(async () => {
  const $ = id => document.getElementById(id);
  const s = await kidlatSettings();
  $("enabled").checked = s.enabled;
  $("captureAll").checked = s.captureAll;
  $("extensions").value = s.extensions;
  $("skipSites").value = s.skipSites;
  $("minSizeKB").value = s.minSizeKB;
  $("port").value = s.port;

  $("save").addEventListener("click", async () => {
    await browser.storage.local.set({
      enabled: $("enabled").checked,
      captureAll: $("captureAll").checked,
      extensions: $("extensions").value.trim() || KIDLAT_DEFAULTS.extensions,
      skipSites: $("skipSites").value.trim(),
      minSizeKB: Math.max(0, parseInt($("minSizeKB").value, 10) || 0),
      port: parseInt($("port").value, 10) || 9614
    });
    $("saved").textContent = "Saved ✓";
    setTimeout(() => ($("saved").textContent = ""), 1500);
  });
})();
