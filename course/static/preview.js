"use strict";
const el = id => document.getElementById(id);
const canvas = el("liveCanvas"), context = canvas.getContext("2d", { alpha: false });
let socket, retry, stopped = false, lastBitmap = null, decoding = false, pending = null;
let turns = 0, lastPaint = 0, painted = 0, state = null, captureCount = 0;
const snapshots = [];
function render() {
  if (!lastBitmap) return;
  const vertical = turns % 2;
  canvas.width = vertical ? lastBitmap.height : lastBitmap.width;
  canvas.height = vertical ? lastBitmap.width : lastBitmap.height;
  context.save(); context.translate(canvas.width / 2, canvas.height / 2); context.rotate(turns * Math.PI / 2);
  context.drawImage(lastBitmap, -lastBitmap.width / 2, -lastBitmap.height / 2); context.restore();
  canvas.hidden = false; el("waiting").hidden = true;
  el("dimensions").textContent = `${canvas.width} × ${canvas.height}`;
}
async function paint(blob) {
  // Decode at most one frame at a time; retain only the newest pending frame.
  if (decoding) { pending = blob; return; }
  decoding = true;
  try {
    const bitmap = await createImageBitmap(blob);
    lastBitmap?.close(); lastBitmap = bitmap; render();
    lastPaint = performance.now(); painted++;
    canvas.dataset.paintedFrames = String(painted);
  } catch { el("notice").textContent = "收到的画面无法解码，正在等待下一帧。"; }
  finally { decoding = false; if (pending) { const next = pending; pending = null; void paint(next); } }
}
function connect() {
  if (stopped) return;
  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/viewer`);
  socket.binaryType = "blob";
  socket.onmessage = event => { if (event.data instanceof Blob) void paint(event.data); };
  socket.onclose = () => { if (!stopped) retry = setTimeout(connect, 2000); };
  socket.onerror = () => socket.close();
}
async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("status");
    state = await response.json();
    const active = state.camera_connected && !state.stale && lastPaint > 0 && performance.now() - lastPaint < 2500;
    el("connection").textContent = active ? "画面传输中" : state.camera_connected ? "等待新画面" : "设备未连接";
    el("connection").className = "pill " + (active ? "ok" : "warn");
    el("transport").textContent = {usb:"USB 实拍",wifi:"Wi-Fi 实拍",replay:"调试回放",unknown:"来源待确认"}[state.transport] || "等待设备";
    el("fps").textContent = `接收 ${state.receive_fps} 帧/秒`;
    el("age").textContent = state.frame_age_seconds === null ? "尚无图像" : `${state.frame_age_seconds.toFixed(1)} 秒前更新`;
    el("staleWarning").hidden = !lastBitmap || active;
    el("captureButton").disabled = !active;
    el("rotateButton").disabled = !lastBitmap;
  } catch {
    state = null; el("connection").textContent = "服务连接中断"; el("connection").className = "pill warn";
    el("captureButton").disabled = true; el("staleWarning").hidden = !lastBitmap;
  }
}
el("rotateButton").addEventListener("click", () => { turns = (turns + 1) % 4; render(); });
el("captureButton").addEventListener("click", async () => {
  if (!lastBitmap || !state?.camera_connected || performance.now() - lastPaint > 2500) return;
  // Freeze the exact displayed canvas synchronously before asynchronous encoding.
  const frozen = document.createElement("canvas"); frozen.width = canvas.width; frozen.height = canvas.height;
  frozen.getContext("2d").drawImage(canvas, 0, 0);
  const takenAt = new Date();
  const blob = await new Promise(resolve => frozen.toBlob(resolve, "image/jpeg", .93));
  if (!blob) { el("notice").textContent = "截图失败，请重试。"; return; }
  const url = URL.createObjectURL(blob), filename = `esp32-${takenAt.toISOString().replace(/[:.]/g,"-")}.jpg`;
  const card = document.createElement("figure"); card.className = "capture-card";
  const image = document.createElement("img"); image.src = url; image.alt = "手动截取的ESP32画面";
  const caption = document.createElement("figcaption"), time = document.createElement("span"), link = document.createElement("a");
  time.textContent = takenAt.toLocaleTimeString("zh-CN", {hour12:false}); link.href = url; link.download = filename; link.textContent = "下载 JPG";
  caption.append(time,link); card.append(image,caption); el("captures").prepend(card);
  snapshots.unshift({url,card});
  if (snapshots.length > 12) { const old = snapshots.pop(); old.card.remove(); URL.revokeObjectURL(old.url); }
  captureCount++; el("captureCount").textContent = `${captureCount} 张`; el("capturesEmpty").hidden = true;
  el("notice").textContent = "已截取当前显示画面。点击下方“下载 JPG”保存到电脑；未调用 AI。";
});
connect(); void refresh(); const poller = setInterval(refresh, 1000);
window.addEventListener("beforeunload", () => { stopped = true; clearTimeout(retry); clearInterval(poller); socket?.close(); lastBitmap?.close(); snapshots.forEach(s => URL.revokeObjectURL(s.url)); });
