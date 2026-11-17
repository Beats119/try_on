"use strict";
/* Phone Mouse client — thin, fast forwarder.
 *
 * The phone streams RAW touches (pointer id + x/y) over WebSocket; the
 * laptop's gesture engine interprets them (tap, scroll, pinch, ...).
 * This page only: tracks pointers, coalesces moves to animation frames,
 * keeps the link alive (reconnect + heartbeat + latency readout), and
 * gives instant local feedback (touch dots, haptics, wake lock).
 */
(function () {
  var pad = document.getElementById("touchpad");
  var statusEl = document.getElementById("status");
  var statusText = document.getElementById("statusText");
  var latencyEl = document.getElementById("latency");
  var tokenRow = document.getElementById("tokenRow");
  var tokenInput = document.getElementById("tokenInput");
  var cursorEl = document.getElementById("cursor");
  var dotsEl = document.getElementById("touchDots");
  var logEl = document.getElementById("log");
  var sensEl = document.getElementById("sens");
  var sensVal = document.getElementById("sensVal");
  var invertEl = document.getElementById("invertY");

  var params = new URLSearchParams(location.search);
  var token = params.get("token") || "";
  var ws = null;
  var seq = 0;
  var reconnectDelay = 500;
  var reconnectTimer = null;
  var pointers = new Map(); // pointerId -> {x, y, ox, oy, t0}
  var moveDirty = false;
  var pingTimer = null;
  var wakeLock = null;

  // ---- settings (persisted) -------------------------------------------
  var sens = parseFloat(localStorage.getItem("pm.sens") || "1");
  var invertY = localStorage.getItem("pm.invertY") === "1";
  sensEl.value = String(sens);
  sensVal.textContent = sens.toFixed(1) + "×";
  invertEl.checked = invertY;
  sensEl.addEventListener("input", function () {
    sens = parseFloat(sensEl.value);
    sensVal.textContent = sens.toFixed(1) + "×";
    localStorage.setItem("pm.sens", String(sens));
  });
  invertEl.addEventListener("change", function () {
    invertY = invertEl.checked;
    localStorage.setItem("pm.invertY", invertY ? "1" : "0");
  });

  function log(msg) {
    var line = new Date().toLocaleTimeString() + "  " + msg;
    logEl.textContent = (logEl.textContent ? logEl.textContent + "\n" : "") + line;
    var lines = logEl.textContent.split("\n");
    if (lines.length > 40) logEl.textContent = lines.slice(-40).join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setStatus(mode, text) {
    statusEl.className = "status " + mode;
    statusText.textContent = text;
  }

  // ---- connection -------------------------------------------------------
  function connect() {
    if (!token) {
      tokenRow.hidden = false;
      setStatus("disconnected", "enter pairing token");
      return;
    }
    tokenRow.hidden = true;
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/ws?token=" + encodeURIComponent(token);
    setStatus("disconnected", "connecting…");
    log("connecting to " + url.replace(/token=[^&]+/, "token=***"));
    ws = new WebSocket(url);
    ws.onopen = function () {
      reconnectDelay = 500;
      setStatus("connected", "connected — touchpad ready");
      log("connected");
      requestWakeLock();
      startPing();
      // Harmless if the URL token already pre-authenticated us.
      sendMsg("hello", { token: token, name: navigator.platform || "phone", version: 1 });
    };
    ws.onmessage = onMessage;
    ws.onclose = function () {
      stopPing();
      releaseWakeLock();
      setStatus("disconnected", "disconnected — retrying…");
      log("connection closed");
      scheduleReconnect();
    };
    ws.onerror = function () {
      log("socket error");
      try { ws.close(); } catch (e) { /* already closed */ }
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    var delay = reconnectDelay + Math.random() * 250;
    reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function onMessage(ev) {
    var msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.t === "pong") {
      var rtt = Math.max(0, Math.round((Date.now() / 1000 - msg.data.ts) * 1000));
      latencyEl.textContent = rtt + " ms";
    } else if (msg.t === "ping") {
      sendMsg("pong", { ts: msg.data.ts });
    } else if (msg.t === "state") {
      // Demo mode: draw the virtual cursor where the laptop says it is.
      cursorEl.hidden = false;
      cursorEl.style.left = msg.data.x + "px";
      cursorEl.style.top = msg.data.y + "px";
    } else if (msg.t === "error") {
      log("server error: " + msg.data.code + " — " + msg.data.message);
      if (msg.data.code === "auth_failed" || msg.data.code === "auth_required") {
        setStatus("disconnected", "bad token — re-enter it");
        tokenRow.hidden = false;
        tokenInput.focus();
      }
    } else if (msg.t === "ack") {
      log("paired. backend: " + (msg.data.backend || "?"));
      setStatus("connected", "connected — touchpad ready");
    }
  }

  function sendMsg(type, data) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    seq += 1;
    ws.send(JSON.stringify({ t: type, seq: seq, ts: Date.now() / 1000, data: data }));
  }

  function startPing() {
    stopPing();
    pingTimer = setInterval(function () {
      sendMsg("ping", { ts: Date.now() / 1000 });
    }, 5000);
  }
  function stopPing() {
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
  }

  // ---- wake lock ----------------------------------------------------------
  function requestWakeLock() {
    if (navigator.wakeLock) {
      navigator.wakeLock.request("screen").then(function (lock) {
        wakeLock = lock;
      }).catch(function () { /* unsupported or denied: fine */ });
    }
  }
  function releaseWakeLock() {
    if (wakeLock) { try { wakeLock.release(); } catch (e) { /* noop */ } wakeLock = null; }
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && ws && ws.readyState === WebSocket.OPEN) {
      requestWakeLock();
    }
  });

  // ---- touch streaming ------------------------------------------------------
  function points() {
    var out = [];
    pointers.forEach(function (p, id) {
      out.push({ id: id, x: Math.round(p.x), y: Math.round(p.y) });
    });
    return out;
  }

  function transform(p) {
    // Apply speed / invert-Y around the touch origin so deltas scale.
    return {
      x: p.ox + (p.x - p.ox) * sens,
      y: p.oy + (p.y - p.oy) * sens * (invertY ? -1 : 1),
    };
  }

  function drawDots() {
    dotsEl.innerHTML = "";
    pointers.forEach(function (p) {
      var d = document.createElement("div");
      d.className = "tdot";
      d.style.left = p.x + "px";
      d.style.top = p.y + "px";
      dotsEl.appendChild(d);
    });
  }

  function scheduleMoveSend() {
    if (moveDirty) return;
    moveDirty = true;
    requestAnimationFrame(function () {
      moveDirty = false;
      if (pointers.size) sendMsg("touch_move", { points: points() });
    });
  }

  pad.addEventListener("pointerdown", function (e) {
    pad.setPointerCapture(e.pointerId);
    var t = transform({ x: e.clientX, y: e.clientY, ox: e.clientX, oy: e.clientY });
    pointers.set(e.pointerId, { x: t.x, y: t.y, ox: t.x, oy: t.y, t0: performance.now() });
    pad.classList.add("active");
    drawDots();
    sendMsg("touch_start", { points: points() });
    e.preventDefault();
  });

  pad.addEventListener("pointermove", function (e) {
    var p = pointers.get(e.pointerId);
    if (!p) return;
    var t = transform({ x: e.clientX, y: e.clientY, ox: p.ox, oy: p.oy });
    p.x = t.x; p.y = t.y;
    drawDots();
    scheduleMoveSend();
    e.preventDefault();
  });

  function endPointer(e) {
    var p = pointers.get(e.pointerId);
    if (!p) return;
    pointers.delete(e.pointerId);
    if (!pointers.size) pad.classList.remove("active");
    drawDots();
    sendMsg("touch_end", { points: [{ id: e.pointerId, x: Math.round(p.x), y: Math.round(p.y) }] });
    // Haptic tick for taps (client-side heuristic; server is authoritative).
    var dt = performance.now() - p.t0;
    if (dt < 250 && navigator.vibrate) navigator.vibrate(8);
  }
  pad.addEventListener("pointerup", endPointer);
  pad.addEventListener("pointercancel", endPointer);
  pad.addEventListener("touchmove", function (e) { e.preventDefault(); }, { passive: false });

  // ---- fallback buttons (synthesize touches) --------------------------------
  function synthTap(nFingers, gapMs, done) {
    var cx = window.innerWidth / 2, cy = window.innerHeight / 2;
    var ids = [];
    for (var i = 0; i < nFingers; i++) {
      var id = 9000 + i;
      ids.push({ id: id, x: cx + (i - (nFingers - 1) / 2) * 40, y: cy });
    }
    sendMsg("touch_start", { points: ids });
    setTimeout(function () {
      sendMsg("touch_end", { points: ids });
      if (done) done();
    }, gapMs || 60);
  }
  document.getElementById("leftBtn").addEventListener("click", function () { synthTap(1); });
  document.getElementById("rightBtn").addEventListener("click", function () { synthTap(2); });
  document.getElementById("dblBtn").addEventListener("click", function () {
    synthTap(1, 60, function () { setTimeout(function () { synthTap(1); }, 120); });
  });

  // ---- misc UI ---------------------------------------------------------------
  document.getElementById("fsBtn").addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(function () { /* noop */ });
  });
  document.getElementById("connectBtn").addEventListener("click", function () {
    token = tokenInput.value.trim();
    if (token) { localStorage.setItem("pm.token", token); connect(); }
  });
  tokenInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("connectBtn").click();
  });

  // ---- boot -------------------------------------------------------------------
  if (!token) {
    token = localStorage.getItem("pm.token") || "";
    if (token) tokenInput.value = token;
  }
  log("Phone Mouse client ready");
  connect();
})();
