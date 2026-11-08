/* Kidlat - floating "Download this video" button (IDM-style) */
(() => {
  if (window.__kidlatInjected) return;
  window.__kidlatInjected = true;

  const MIN_W = 200, MIN_H = 110;
  const LABEL = "⚡ Download this video";
  let host = null, btn = null, current = null, hideTimer = null;
  let dismissed = false, overBar = false, busy = false;
  let recBtn = null, startBtn = null, hereBtn = null, doneBtn = null, xBtn = null, rec = null;
  let spdBtn = null;
  const SPEEDS = [1, 1.5, 2];
  let chosenSpeed = 1;           // speed to record at (saved back to normal speed on Finish)
  browser.storage.local.get("kidlatRecSpeed").then(o => {
    if (SPEEDS.includes(o.kidlatRecSpeed)) { chosenSpeed = o.kidlatRecSpeed; if (spdBtn) speedLabel(); }
  }).catch(() => {});
  let flashing = false;   // rec = the recording in progress

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
        .rec,.start,.here,.done{border-left:1px solid rgba(255,255,255,.3)}
        .spd{background:rgba(0,0,0,.18);padding:8px 10px}.spd.fast{background:#d69e2e;color:#1a202c}
        .done{background:#2f855a}.done:hover{background:#276749}
        .rec.on{background:#c53030}
      </style>
      <div class="wrap"><button class="dl"></button><button class="spd" title="Record faster. Kidlat turns it back into a normal-speed video when you Finish (needs FFmpeg)."></button><button class="rec"></button><button class="start" title="Rewind to 0:00 and record the whole video">⏮ Record from start</button><button class="here" title="Record the next part from where the video is now"></button><button class="done" title="Save the recording as one video">✓ Finish</button><button class="x" title="Hide on this page">✕</button></div>`;
    btn = root.querySelector(".dl");
    recBtn = root.querySelector(".rec");
    recBtn.addEventListener("click", onRecordClick);
    spdBtn = root.querySelector(".spd");
    spdBtn.addEventListener("click", onSpeedClick);
    speedLabel();
    startBtn = root.querySelector(".start");
    startBtn.addEventListener("click", onStartClick);
    hereBtn = root.querySelector(".here");
    hereBtn.addEventListener("click", onHereClick);
    doneBtn = root.querySelector(".done");
    doneBtn.addEventListener("click", onDoneClick);
    idleLabel();
    xBtn = root.querySelector(".x");
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
    if (rec && v !== rec.video) return;          // stay on the video being recorded
    if (dismissed && !rec) return;
    ensureUI();
    attachHost();
    current = v;
    clearTimeout(hideTimer);
    host.style.display = "block";
    place();
  }

  function hide() {
    if (rec) return;                              // keep the Stop button visible
    if (host) host.style.display = "none";
    if (!busy) current = null;
  }

  function scheduleHide(ms) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { if (!overBar && !busy && !rec) hide(); }, ms);
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

  // ---------------------------------------------------------------------
  // Recording: Firefox captures the <video> itself (picture + sound), so it
  // keeps working while you use other windows. Saved by the Kidlat app.
  // Stop any time and continue later - even after refreshing the page.
  // ---------------------------------------------------------------------
  // which page a stopped recording belongs to (YouTube changes the address
  // without reloading, so look it up every time)
  const pageKey = () => "kidlatRec:" + location.href.split("#")[0];
  let recKey = null;        // the key the current recording was saved under
  let paused = null;        // { session, pos } - a stopped recording that can continue

  function clock(sec) {
    const t = Math.max(0, Math.floor(sec || 0)), h = Math.floor(t / 3600);
    const m = String(Math.floor(t / 60) % 60).padStart(2, "0"), s = String(t % 60).padStart(2, "0");
    return h ? `${h}:${m}:${s}` : `${m}:${s}`;
  }

  function idleLabel() {
    recBtn.classList.remove("on");
    if (paused) {
      recBtn.textContent = "⏺ Continue from " + clock(paused.pos);
      recBtn.title = "Jumps the video to " + clock(paused.pos) + " and records the next part";
      doneBtn.style.display = "";
      // the video was moved somewhere else: offer to continue from there too
      const at = current ? current.currentTime : NaN;
      if (isFinite(at) && at >= 1 && Math.abs(at - paused.pos) >= 2) {
        hereBtn.textContent = "⏺ Continue from here (" + clock(at) + ")";
        hereBtn.style.display = "";
      } else {
        hereBtn.style.display = "none";
      }
    } else {
      recBtn.textContent = "⏺ Record";
      recBtn.title = "Record from where the video is now (picture and sound)";
      doneBtn.style.display = "none";
      hereBtn.style.display = "none";
    }
    startBtn.style.display = "";                  // always available
  }

  async function onStartClick(e) {
    e.preventDefault();
    e.stopPropagation();
    const v = current;
    if (!v || rec) return;
    if (paused) {
      // a stopped recording was waiting: save it as it is, then start a new one
      const old = paused.session;
      paused = null;
      browser.runtime.sendMessage({ type: "rec-done", session: old }).catch(() => {});
    }
    try {
      v.currentTime = 0;                          // rewind, then record everything
      await startRecording(v, null);
    } catch (err) {
      rec = null;
      flash("✕ " + (err.message || "Can't record this video"), 5000);
    }
  }

  function speedLabel() {
    spdBtn.textContent = chosenSpeed === 1 ? "1×" : `⏩ ${chosenSpeed}×`;
    spdBtn.classList.toggle("fast", chosenSpeed !== 1);
  }

  function onSpeedClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (rec) return;                              // can't change speed mid-recording
    chosenSpeed = SPEEDS[(SPEEDS.indexOf(chosenSpeed) + 1) % SPEEDS.length];
    speedLabel();
    browser.storage.local.set({ kidlatRecSpeed: chosenSpeed }).catch(() => {});
    if (chosenSpeed !== 1) {
      flash(`⏩ ${chosenSpeed}×: faster recording, saved at normal speed`, 3500);
    }
  }

  // keep "Continue from here (…)" up to date while you move around the video
  setInterval(() => {
    if (!rec && !flashing && host && host.style.display !== "none") idleLabel();
  }, 500);

  async function onHereClick(e) {
    e.preventDefault();
    e.stopPropagation();
    const v = current;
    if (!v || !paused || rec) return;
    try {
      await startRecording(v, paused.session);    // no jump: starts where the video is
    } catch (err) {
      rec = null;
      flash("✕ " + (err.message || "Can't record this video"), 5000);
    }
  }

  function flash(text, ms = 3500) {
    flashing = true;
    recBtn.textContent = text;
    recBtn.classList.remove("on");
    hereBtn.style.display = "none";
    setTimeout(() => { flashing = false; if (!rec) idleLabel(); }, ms);
  }

  async function remember(session) {
    try {
      if (session) {
        recKey = pageKey();
        await browser.storage.local.set({ [recKey]: session });
      } else {
        await browser.storage.local.remove(recKey || pageKey());
        recKey = null;
      }
    } catch (err) { /* not important */ }
  }

  async function onRecordClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (rec) { stopRecording(false); return; }
    const v = current;
    if (!v) return;
    try {
      if (paused) {
        // jump to where the last part ended, then record the next part
        if (isFinite(v.duration) && paused.pos < v.duration) v.currentTime = paused.pos;
        await startRecording(v, paused.session);
      } else {
        await startRecording(v, null);
      }
    } catch (err) {
      rec = null;
      flash("✕ " + (err.message || "Can't record this video"), 5000);
    }
  }

  async function onDoneClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!paused) return;
    const was = paused;
    paused = null;
    doneBtn.style.display = "none";
    recBtn.textContent = "Saving…";
    try {
      await browser.runtime.sendMessage({ type: "rec-done", session: was.session });
      remember(null);
      flash("✓ Recording saved");
    } catch (err) {
      paused = was;                               // nothing lost: Finish/Continue stay
      flash("✕ Couldn't reach Kidlat - try Finish again", 5000);
    }
  }

  async function startRecording(v, session) {
    if (!window.MediaRecorder || !(v.mozCaptureStream || v.captureStream)) {
      throw new Error("Recording isn't supported here");
    }
    // The captured stream is reused for the same video. If the player has loaded
    // another video since (its old picture/sound tracks ended), capture again.
    const alive = st => st && (st.getTracks ? st.getTracks() : [...st.getVideoTracks(), ...st.getAudioTracks()])
      .some(t => t.readyState !== "ended");
    let stream = alive(v.__kidlatStream) ? v.__kidlatStream : null;
    if (!stream) {
      try {
        stream = v.mozCaptureStream ? v.mozCaptureStream() : v.captureStream();
      } catch (err) {
        throw new Error("This video is protected and can't be recorded");
      }
      v.__kidlatStream = stream;
      // While a video is captured Firefox stops sending its sound to the speakers.
      // Send it back so you can still hear it.
      if (stream.getAudioTracks().length) {
        try {
          if (v.__kidlatAudio) v.__kidlatAudio.close();
        } catch (err) { /* already closed */ }
        try {
          const ctx = new AudioContext();
          ctx.createMediaStreamSource(stream).connect(ctx.destination);
          if (ctx.resume) ctx.resume().catch(() => {});
          v.__kidlatAudio = ctx;
        } catch (err) { /* recording still works without hearing it */ }
      }
    }
    // A video without sound still gets an empty sound channel from Firefox, and
    // the recorder then waits forever for sound that never comes (Stop never
    // finishes). So only include sound when the video really has some.
    const hasAudio = stream.getAudioTracks().length > 0 && v.mozHasAudio !== false;
    const recStream = hasAudio ? stream : new MediaStream(stream.getVideoTracks());
    const mimeType = (hasAudio
      ? ["video/webm;codecs=vp8,opus", "video/webm;codecs=vp9,opus", "video/webm"]
      : ["video/webm;codecs=vp8", "video/webm;codecs=vp9", "video/webm"])
      .find(t => MediaRecorder.isTypeSupported(t)) || "";
    // Fast recording: play at the chosen speed with "tape" sound (pitch goes up
    // instead of being stretched), which Kidlat can undo exactly afterwards.
    const speed = chosenSpeed;
    const before = { rate: v.playbackRate || 1, pitch: v.preservesPitch,
                     mozPitch: v.mozPreservesPitch };
    const applySpeed = () => {
      try {
        if ("preservesPitch" in v) v.preservesPitch = speed === 1 ? true : false;
        if ("mozPreservesPitch" in v) v.mozPreservesPitch = speed === 1;
        if (Math.abs(v.playbackRate - speed) > 0.01) v.playbackRate = speed;
      } catch (err) { /* player refused */ }
    };
    applySpeed();
    let recorder;
    try {
      const opts = { mimeType, videoBitsPerSecond: Math.round(2500000 * speed) };
      if (hasAudio) opts.audioBitsPerSecond = speed === 1 ? 128000 : 192000;
      recorder = new MediaRecorder(recStream, opts);
      recorder.start(3000);                       // a piece every 3 seconds
    } catch (err) {
      throw new Error("This video is protected and can't be recorded");
    }
    const r = { video: v, recorder, speed, id: null, session: null, memory: null, seq: 0,
                failed: "", final: false, elapsed: 0, lastTick: Date.now(), waiting: false,
                pos: v.currentTime || 0, queue: Promise.resolve() };
    rec = r;
    paused = null;
    doneBtn.style.display = "none";
    hereBtn.style.display = "none";
    startBtn.style.display = "none";
    // Ask Kidlat to save it to disk; without Kidlat it is kept in memory instead.
    r.ready = browser.runtime.sendMessage({ type: "rec-start", title: document.title,
                                            url: location.href, session, speed })
      .then(res => {
        if (res && res.id) {
          r.id = res.id; r.session = res.session; r.file = res.file || null; r.bytes = 0;
          remember(res.session);
        } else r.memory = [];
      })
      .catch(() => { r.memory = []; });

    // Kidlat closed this part (e.g. the PC slept a long time): start a new part
    // automatically and keep recording, instead of stopping.
    function lostKidlat() {
      if (r.reconnect || r.failed || r.finalized) return;
      r.reconnect = true;
      r.stop(false);
    }
    // Stop THIS recording. If Firefox's recorder hangs and never reports that it
    // stopped, save what we have anyway after a few seconds.
    r.stop = (final) => {
      r.final = r.final || !!final;
      try {
        if (recorder.state !== "inactive") recorder.stop();
      } catch (err) { /* already stopped */ }
      clearTimeout(r.stopTimer);
      r.stopTimer = setTimeout(() => finalize(), 6000);
    };
    // Tells Kidlat "still recording" while paused, so it doesn't close the file.
    const KEEPALIVE = {};
    function queuePiece(piece, pos) {
      if (r.finalized) return;
      r.queue = r.queue.then(async () => {
        await r.ready;
        if (r.memory) { if (piece !== KEEPALIVE) r.memory.push(piece); return; }
        const seq = r.seq++;
        const data = piece === KEEPALIVE ? new ArrayBuffer(0) : await piece.arrayBuffer();
        const send = () => browser.runtime.sendMessage({ type: "rec-chunk", id: r.id, seq, data, pos });
        let ok = await send();
        if (!ok && r.file && r.bytes > 0) {
          // Kidlat restarted (or closed the part after a long sleep): carry on in the
          // same file, so this piece isn't lost and no new part is needed
          const re = await browser.runtime.sendMessage({ type: "rec-reopen", session: r.session,
                                                         file: r.file, size: r.bytes }).catch(() => null);
          if (re && re.id) { r.id = re.id; ok = await send(); }
        }
        if (ok) r.bytes += data.byteLength;
        else lostKidlat();
      }).catch(() => lostKidlat());
    }
    recorder.ondataavailable = (ev) => {
      if (!ev.data || !ev.data.size) return;
      r.pieces = (r.pieces || 0) + 1;
      queuePiece(ev.data, v.currentTime);          // remember how far the video got
    };
    const finalize = async () => {
      if (r.finalized) return;
      r.finalized = true;
      clearTimeout(r.stopTimer);
      clearInterval(r.timer);
      clearInterval(r.watch);
      if (speed !== 1) {                          // give the player back its normal speed
        try {
          v.playbackRate = 1;
          if ("preservesPitch" in v) v.preservesPitch = before.pitch !== false;
          if ("mozPreservesPitch" in v) v.mozPreservesPitch = before.mozPitch !== false;
        } catch (err) { /* ignore */ }
      }
      clearInterval(r.keepAlive);
      if (r.unhook) r.unhook();
      if (!r.pieces && !r.failed) r.failed = "Nothing was recorded - Firefox's recorder didn't start";
      recBtn.textContent = "Saving…";
      recBtn.classList.remove("on");
      try {
        await r.queue;
        await r.ready;
        if (r.memory) {
          await browser.runtime.sendMessage({
            type: "rec-save", title: document.title,
            blob: new Blob(r.memory, { type: "video/webm" }) });
          rec = null;
          flash(r.failed ? "✕ " + r.failed : "✓ Recording saved", r.failed ? 6000 : 3500);
        } else if (r.id) {
          const res = await browser.runtime.sendMessage({ type: "rec-finish", id: r.id });
          const pos = res && typeof res.pos === "number" ? res.pos : r.pos;
          rec = null;
          if (r.final) {                          // the video ended: save it as one file
            await browser.runtime.sendMessage({ type: "rec-done", session: r.session });
            remember(null);
            flash("✓ Recording saved");
          } else if (r.reconnect && canReconnect()) {   // keep going in a new part
            paused = { session: r.session, pos };
            try {
              await startRecording(v, r.session);
              recBtn.title = "Reconnected to Kidlat - recording continues";
              return;
            } catch (err) {
              flash("✕ Recording stopped at " + clock(pos) + " - press Continue", 6000);
            }
          } else {                                // stopped: can continue later
            paused = { session: r.session, pos };
            if (r.reconnect) r.failed = "Kidlat isn't responding - stopped at " + clock(pos);
            flash(r.failed ? "✕ " + r.failed : "✓ Stopped at " + clock(pos), r.failed ? 6000 : 2500);
          }
        } else {
          rec = null;
          flash("✕ Couldn't save the recording", 6000);
        }
      } catch (err) {
        rec = null;
        flash("✕ Couldn't save the recording", 6000);
      }
      xBtn.style.display = "";
      spdBtn.style.display = "";
      if (!overBar) scheduleHide(paused ? 8000 : 4000);
    };
    recorder.onstop = () => finalize();
    recorder.onerror = () => r.stop(false);
    const onEnded = () => r.stop(true);
    v.addEventListener("ended", onEnded, { once: true });

    // Pause the recording while the video is stuck loading (slow internet) or
    // paused, so the saved file has no frozen parts. Continue when it plays.
    const hold = () => {
      r.waiting = true;
      try { if (recorder.state === "recording") recorder.pause(); } catch (err) { /* ignore */ }
      tick();
    };
    const go = () => {
      if (v.paused || v.seeking) return;          // only continue when it really plays
      r.waiting = false;
      try { if (recorder.state === "paused") recorder.resume(); } catch (err) { /* ignore */ }
      tick();
    };
    const events = { ratechange: () => { if (Math.abs(v.playbackRate - speed) > 0.01) applySpeed(); },
                     waiting: hold, pause: hold, seeking: hold,
                     playing: go, seeked: () => { if (!v.paused) go(); } };
    for (const [name, fn] of Object.entries(events)) v.addEventListener(name, fn);
    r.unhook = () => {
      for (const [name, fn] of Object.entries(events)) v.removeEventListener(name, fn);
      v.removeEventListener("ended", onEnded);
    };

    recBtn.classList.add("on");
    spdBtn.style.display = "none";
    recBtn.title = "Stop recording (you can continue later)";
    xBtn.style.display = "none";
    const tick = () => {
      const now = Date.now();
      if (recorder.state === "recording") {
        r.elapsed += now - r.lastTick;
        r.pos = v.currentTime;
      }
      r.lastTick = now;
      const actual = v.playbackRate || 1;
      recBtn.textContent = (r.waiting ? "⏸ Waiting" : "■ Stop") +
        (speed !== 1 ? `  ·  ⏩ ${speed}×` : "") +
        "  ·  Recorded " + clock(r.elapsed * speed / 1000) + "  ·  Video " + clock(v.currentTime) +
        (Math.abs(actual - speed) > 0.01 ? `  ·  ⚠ the player is at ${actual}×` : "");
    };
    // Don't trust the player to report lag: watch the video's own clock.
    // If it stops moving (lag, buffering, frozen player), pause the recording;
    // as soon as it moves again, continue.
    r.lastVT = v.currentTime;
    r.stillSince = 0;
    r.watch = setInterval(() => {
      const t = v.currentTime;
      const moving = !v.paused && !v.seeking && !v.ended && t !== r.lastVT;
      r.lastVT = t;
      if (moving) {
        r.stillSince = 0;
        if (recorder.state === "paused") go();
      } else if (recorder.state === "recording") {
        // the clock didn't move for two checks in a row: the picture is stuck
        if (!r.stillSince) r.stillSince = Date.now();
        else if (Date.now() - r.stillSince >= 100) hold();
      }
    }, 100);
    r.keepAlive = setInterval(() => {
      if (recorder.state === "paused") queuePiece(KEEPALIVE, r.pos);
    }, 30000);
    tick();
    r.timer = setInterval(tick, 1000);
    show(v);
    if (v.paused || v.seeking) {
      r.waiting = true;
      recorder.pause();                           // starts when the video plays
      v.play().catch(() => {});
    }
  }

  // at most 3 automatic reconnects per minute, so a broken setup can't loop forever
  const reconnects = [];
  function canReconnect() {
    const now = Date.now();
    while (reconnects.length && now - reconnects[0] > 60000) reconnects.shift();
    if (reconnects.length >= 3) return false;
    reconnects.push(now);
    return true;
  }

  function stopRecording(final) {
    if (rec && rec.stop) rec.stop(final);
  }

  // After a refresh: is there a stopped recording of this page to continue?
  (async () => {
    let session = null;
    const key = pageKey();
    try {
      session = (await browser.storage.local.get(key))[key];
    } catch (err) { /* ignore */ }
    if (!session) return;
    recKey = key;
    let found = null;
    try {
      found = await browser.runtime.sendMessage({ type: "rec-lookup", url: location.href });
    } catch (err) { /* ignore */ }
    if (!found || !found.session) { remember(null); return; }
    if (rec) return;                               // already recording again
    paused = { session: found.session, pos: found.pos || 0 };
    // show "Continue from ..." on the video as soon as the player is ready
    for (let i = 0; i < 60 && !rec; i++) {
      const v = [...document.querySelectorAll("video")].find(goodVideo);
      if (v) {
        dismissed = false;
        show(v);
        idleLabel();
        clearTimeout(hideTimer);
        return;
      }
      await new Promise(res => setTimeout(res, 1000));
    }
  })();

  async function onClick(e) {
    e.preventDefault();
    e.stopPropagation();
    const v = current;
    if (!v || busy) return;
    busy = true;
    btn.textContent = "Starting Kidlat…";
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
