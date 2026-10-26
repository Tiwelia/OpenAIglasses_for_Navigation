"use strict";
const $ = id => document.getElementById(id);
let status = null, busy = false, socket = null, retryTimer = null, imageUrl = null, answerText = "", stopped = false;

function notify(text, success = false) {
  $("message").textContent = text;
  $("message").className = success ? "notice success" : "notice";
  $("message").hidden = !text;
}
function pill(id, text, kind = "") { $(id).textContent = text; $(id).className = "pill " + kind; }
function displayFrame(blob) {
  const next = URL.createObjectURL(blob), previous = imageUrl;
  imageUrl = next;
  $("cameraImage").src = next;
  $("cameraImage").hidden = false;
  $("emptyPreview").hidden = true;
  if (previous) URL.revokeObjectURL(previous);
}
async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try { const body = await response.json(); if (typeof body.detail === "string") message = body.detail; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function updateControls() {
  $("analyzeButton").disabled = busy || !status || !status.has_frame || !status.ai_configured || status.stale || status.analyzing;
  $("uploadButton").disabled = busy;
  if (busy) return;
  $("analyzeLabel").textContent = "分析当前画面";
  $("analyzeHint").textContent = !status ? "服务连接中，请稍候。" : !status.ai_configured ? "AI尚未配置，请按课程版说明配置密钥后重启。" : !status.has_frame ? "先连接摄像头，或上传一张图片。" : status.stale ? "画面已超时，请重新连接摄像头或上传图片。" : status.analyzing ? "另一张图片正在分析，请稍候。" : "只分析点击时的一张图片，实时画面会继续更新。";
}
async function refreshStatus() {
  try {
    status = await request("/api/status");
    pill("serviceStatus", "服务在线", "ok");
    pill("aiStatus", status.ai_enabled === false ? "AI已停用" : status.ai_configured ? "AI已配置" : "AI未配置", status.ai_configured ? "ok" : "warn");
    const label = status.stale ? "画面已超时" : status.source === "upload" ? "已上传图片" : status.camera_connected ? "摄像头已连接" : status.has_frame ? "摄像头已断开" : "等待图像";
    pill("sourceStatus", label, status.stale ? "warn" : status.source === "upload" || status.camera_connected ? "ok" : "");
    $("frameSize").textContent = status.image_size ? status.image_size.join(" × ") : "尚无图像";
    $("frameFps").textContent = status.source === "camera" ? `接收 ${status.receive_fps} 帧/秒` : "静态图像";
    $("frameAge").textContent = status.frame_age_seconds === null ? "等待接入" : `${status.frame_age_seconds.toFixed(0)} 秒前更新`;
  } catch {
    status = null;
    pill("serviceStatus", "服务连接中断", "warn");
    pill("sourceStatus", "连接中断", "warn");
  }
  updateControls();
}
function connectViewer() {
  if (stopped) return;
  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/viewer`);
  socket.binaryType = "blob";
  socket.onmessage = event => { if (event.data instanceof Blob) displayFrame(event.data); };
  socket.onclose = () => { if (!stopped) retryTimer = setTimeout(connectViewer, 2000); };
  socket.onerror = () => socket.close();
}
const localTime = value => new Date(value).toLocaleString("zh-CN", { hour12: false });
function showRecord(record, shouldScroll = false) {
  answerText = record.answer;
  $("answer").textContent = record.answer;
  $("answer").classList.remove("placeholder");
  $("snapshotImage").src = record.image_url;
  $("snapshotLink").href = record.image_url;
  $("resultTime").textContent = localTime(record.created_at);
  $("resultMeta").textContent = `${(record.elapsed_ms / 1000).toFixed(1)} 秒 · ${record.model}`;
  $("resultEvidence").hidden = false;
  $("speakButton").disabled = !("speechSynthesis" in window);
  if (shouldScroll) $("analysisTitle").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}
async function refreshHistory() {
  try {
    const records = await request("/api/history");
    $("historyList").replaceChildren();
    $("historyEmpty").hidden = records.length > 0;
    $("historyCount").textContent = `显示最近 ${records.length} 条`;
    for (const record of records) {
      const button = document.createElement("button"); button.type = "button"; button.className = "history-item";
      const title = document.createElement("strong"), meta = document.createElement("span"), text = document.createElement("p");
      title.textContent = record.question; meta.textContent = `${localTime(record.created_at)} · ${(record.elapsed_ms / 1000).toFixed(1)} 秒`; text.textContent = record.answer;
      button.append(title, meta, text); button.addEventListener("click", () => showRecord(record, true)); $("historyList").append(button);
    }
  } catch { $("historyEmpty").textContent = "暂时无法读取记录，请检查服务是否运行。"; $("historyEmpty").hidden = false; }
}
$("analyzeButton").addEventListener("click", async () => {
  if (busy) return;
  busy = true; notify(""); updateControls();
  $("analyzeButton").setAttribute("aria-busy", "true");
  $("analyzeHint").textContent = "正在请求AI。视频会继续更新，本次结果对应点击时的截图。";
  const start = Date.now(), controller = new AbortController();
  $("analyzeLabel").textContent = "正在分析 · 0 秒";
  const ticker = setInterval(() => { $("analyzeLabel").textContent = `正在分析 · ${Math.floor((Date.now() - start) / 1000)} 秒`; }, 1000);
  const timeout = setTimeout(() => controller.abort(), 70000);
  try {
    const record = await request("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: $("question").value }), signal: controller.signal });
    showRecord(record); notify("分析完成，描述与截图已保存。", true); await refreshHistory();
  } catch (error) { notify(error.name === "AbortError" ? "等待超时，请检查服务状态后重试。" : error.message); }
  finally { clearInterval(ticker); clearTimeout(timeout); busy = false; $("analyzeButton").removeAttribute("aria-busy"); await refreshStatus(); }
});
$("uploadButton").addEventListener("click", () => $("imageInput").click());
$("imageInput").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 2 * 1024 * 1024) { notify("图片超过2MB，请缩小后重试。"); event.target.value = ""; return; }
  $("uploadButton").disabled = true;
  try {
    await request("/api/image", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
    displayFrame(file); notify(status?.camera_connected ? "图片已上传。摄像头仍连接时，下一帧会继续更新画面。" : "图片已就绪，可以开始分析。", true);
    await refreshStatus();
  } catch (error) { notify(error.message); }
  finally { $("uploadButton").disabled = busy; event.target.value = ""; }
});
document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => { $("question").value = button.dataset.question; }));
$("speakButton").addEventListener("click", () => {
  if (!answerText || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(answerText); utterance.lang = "zh-CN"; speechSynthesis.speak(utterance);
});
const displayHost = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname) ? `电脑局域网IP:${location.port || 80}` : location.host;
$("cameraEndpoint").textContent = `${location.protocol === "https:" ? "wss" : "ws"}://${displayHost}/ws/camera`;
connectViewer(); refreshStatus(); refreshHistory();
const poller = setInterval(refreshStatus, 2000);
window.addEventListener("beforeunload", () => { stopped = true; clearInterval(poller); clearTimeout(retryTimer); socket?.close(); if (imageUrl) URL.revokeObjectURL(imageUrl); if ("speechSynthesis" in window) speechSynthesis.cancel(); });
