const state = { catalog: null, latestTaskId: null, pollTimer: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.message || payload.error || `Request failed: ${response.status}`);
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 3600);
}

function fillDatalist(id, values) {
  const element = document.getElementById(id);
  if (!element) return;
  element.innerHTML = values.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
}

function updateCityPreview() {
  const city = $("#city").value.trim();
  const data = state.catalog?.cities.find((item) => item.name === city);
  if (!data) return;
  $("#landmark").value ||= data.landmark;
  fillDatalist("landmark-options", [data.landmark, ...data.aliases]);
  $("#city-preview").innerHTML = `<div class="preview-city">${escapeHtml(data.name)}</div><div class="preview-landmark">${escapeHtml(data.landmark)} / ${escapeHtml(data.culture)}</div>`;
  $("#city-preview").style.background = `linear-gradient(145deg, ${data.color}, #274a51)`;
}

function updateInputMetrics() {
  const form = $("#create-form");
  if (!form) return;
  $("#metric-shots").textContent = form.elements.shot_count.value === "0" ? "自动" : form.elements.shot_count.value;
  $("#metric-resolution").textContent = form.elements.output_resolution.value === "1920x1080" ? "16:9" : form.elements.output_resolution.value === "1080x1080" ? "1:1" : "9:16";
}

async function loadCatalog() {
  state.catalog = await api("/api/catalog");
  fillDatalist("city-options", state.catalog.cities.map((item) => item.name));
  fillDatalist("festival-options", state.catalog.festivals);
  fillDatalist("audience-options", state.catalog.audiences);
  fillDatalist("style-options", state.catalog.styles);
  fillDatalist("voice-options", state.catalog.voices);
  fillDatalist("music-options", state.catalog.music_moods);
  fillDatalist("culture-options", state.catalog.cities.flatMap((item) => item.culture.split("、")));
  $("#city").addEventListener("input", updateCityPreview);
  $("#create-form").addEventListener("input", updateInputMetrics);
  updateCityPreview();
  updateInputMetrics();
}

function formData() {
  const form = $("#create-form");
  const data = Object.fromEntries(new FormData(form).entries());
  for (const key of ["duration", "shot_count", "fps"]) data[key] = Number(data[key]);
  for (const key of ["voice_rate", "voice_volume", "music_volume"]) data[key] = Number(data[key]);
  data.include_ai_label = form.elements.include_ai_label.checked;
  data.include_subtitles = form.elements.include_subtitles.checked;
  return data;
}

function stageMarkup(task) {
  const names = { preview: "预览", scripting: "脚本", storyboard: "分镜", audio: "配音", assets: "画面", rendering: "渲染", review: "完成" };
  const order = ["preview", "scripting", "storyboard", "audio", "assets", "rendering", "review"];
  const currentIndex = order.indexOf(task.stage);
  return order.map((stage, index) => `<div class="pipeline-step ${index < currentIndex ? "done" : ""} ${stage === task.stage ? "active" : ""}"><b>${String(index + 1).padStart(2, "0")}</b>${names[stage]}</div>`).join("");
}

function reviewInputMarkup(task) {
  const input = task.input;
  const fields = [
    ["city", "城市"], ["landmark", "核心地标"], ["theme", "主题"], ["culture", "文化线索"],
    ["festival", "节庆 / 活动"], ["audience", "目标受众"], ["style", "叙事风格"], ["voice", "配音"],
    ["music_mood", "背景音乐"],
  ];
  return `<div class="review-settings"><div class="section-index">CREATION SETTINGS</div><div class="review-settings-grid">
    ${fields.map(([key, label]) => `<label>${label}<input class="draft-input" data-input-field="${key}" value="${escapeHtml(input[key])}" /></label>`).join("")}
    <label>成片时长<select class="draft-input" data-input-field="duration">${[15, 30, 60].map((value) => `<option value="${value}" ${Number(input.duration) === value ? "selected" : ""}>${value} 秒</option>`).join("")}</select></label>
    <label>镜头数量<select class="draft-input" data-input-field="shot_count">${[0, 4, 6, 8].map((value) => `<option value="${value}" ${Number(input.shot_count) === value ? "selected" : ""}>${value || "自动"}${value ? " 个" : ""}</option>`).join("")}</select></label>
    <label>素材来源<select class="draft-input" data-input-field="asset_source">${[["configured", "使用服务端配置"], ["ai", "AI 生成画面"], ["local-library", "本地素材库"], ["upload", "上传素材"], ["pexels", "Pexels"], ["offline", "技术测试场景图"]].map(([value, label]) => `<option value="${value}" ${input.asset_source === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <label>输出规格<select class="draft-input" data-input-field="output_resolution">${["720x1280", "1080x1920", "1920x1080", "1080x1080"].map((value) => `<option value="${value}" ${input.output_resolution === value ? "selected" : ""}>${value}</option>`).join("")}</select></label>
    <label>帧率<select class="draft-input" data-input-field="fps">${[24, 25, 30, 60].map((value) => `<option value="${value}" ${Number(input.fps) === value ? "selected" : ""}>${value} fps</option>`).join("")}</select></label>
    <label>配音语速<input class="draft-input" data-input-field="voice_rate" type="number" min="0.5" max="2" step="0.1" value="${input.voice_rate}" /></label>
    <label>配音音量<input class="draft-input" data-input-field="voice_volume" type="number" min="0" max="2" step="0.05" value="${input.voice_volume}" /></label>
    <label>音乐音量<input class="draft-input" data-input-field="music_volume" type="number" min="0" max="1" step="0.05" value="${input.music_volume}" /></label>
    <label class="toggle-label">烧录字幕<input class="draft-input" data-input-field="include_subtitles" type="checkbox" ${input.include_subtitles ? "checked" : ""} /><span class="toggle"></span></label>
    <label class="toggle-label">AIGC 标识<input class="draft-input" data-input-field="include_ai_label" type="checkbox" ${input.include_ai_label ? "checked" : ""} /><span class="toggle"></span></label>
  </div><label>补充要求<textarea class="draft-input" data-input-field="custom_brief">${escapeHtml(input.custom_brief)}</textarea></label></div>`;
}

function shotEditor(task) {
  return (task.shots || []).map((shot) => `<article class="shot-editor">
    <div class="shot-editor-header"><strong>镜头 ${String(shot.order).padStart(2, "0")}</strong><span>${escapeHtml(shot.id)}</span></div>
    <label>镜头标题<input data-shot-field="title" data-shot-id="${escapeHtml(shot.id)}" value="${escapeHtml(shot.title)}" /></label>
    <label>画面描述<textarea data-shot-field="visual_prompt" data-shot-id="${escapeHtml(shot.id)}">${escapeHtml(shot.visual_prompt)}</textarea></label>
    <label>旁白<textarea data-shot-field="narration" data-shot-id="${escapeHtml(shot.id)}">${escapeHtml(shot.narration)}</textarea></label>
    <label>字幕<textarea data-shot-field="subtitle" data-shot-id="${escapeHtml(shot.id)}">${escapeHtml(shot.subtitle)}</textarea></label>
    <label>转场<select data-shot-field="transition" data-shot-id="${escapeHtml(shot.id)}"><option value="fade" ${shot.transition === "fade" ? "selected" : ""}>淡入淡出</option><option value="dissolve" ${shot.transition === "dissolve" ? "selected" : ""}>叠化</option><option value="slideleft" ${shot.transition === "slideleft" ? "selected" : ""}>向左滑动</option><option value="slideright" ${shot.transition === "slideright" ? "selected" : ""}>向右滑动</option><option value="zoom" ${shot.transition === "zoom" ? "selected" : ""}>推近</option></select></label>
    <label>时长（秒）<input type="number" min="1" max="20" step="0.1" data-shot-field="duration_sec" data-shot-id="${escapeHtml(shot.id)}" value="${shot.duration_sec}" /></label>
  </article>`).join("");
}

function visualCostLine(task) {
  const visual = Number(task.audio?.cost || 0);
  const cloudProviders = new Set(["cloud-i2v", "cloud-t2i", "pexels", "ai", "cloud"]);
  const sources = (task.assets || []).map((asset) => String(asset.source || "").trim().toLowerCase());
  const calledCloud = sources.some((source) => cloudProviders.has(source));
  if (visual === 0 && !calledCloud) return "未调用云端，费用为 0";
  return visual.toFixed(4);
}

function assetList(task) {
  if (!task.assets?.length) return `<p class="muted">尚未绑定素材。</p>`;
  return `<div class="asset-list">${task.assets.map((asset) => `<div class="asset-row"><span>镜头 ${escapeHtml(asset.shot_id || "-")}</span><strong>${escapeHtml(asset.name)}</strong><small>${escapeHtml(asset.source)} / ${escapeHtml(asset.license)}</small></div>`).join("")}</div>`;
}

function renderTask(task) {
  state.latestTaskId = task.id;
  const error = task.error ? `<div class="error-box"><strong>任务失败</strong><p>${escapeHtml(task.error)}</p><button class="text-button" data-retry="${escapeHtml(task.id)}">从脚本阶段重试</button></div>` : "";
  const render = task.render || {};
  const links = render.video_url ? `<div class="task-links"><a href="${render.video_url}" target="_blank">打开 MP4</a><a href="${render.video_url}" download>下载 MP4</a><a href="${render.subtitle_url}" target="_blank">下载 SRT</a>${task.audio?.voice_url ? `<a href="${task.audio.voice_url}" target="_blank">试听配音</a>` : ""}${task.audio?.music_url ? `<a href="${task.audio.music_url}" target="_blank">试听音乐</a>` : ""}</div><video class="result-video" controls playsinline src="${render.video_url}"></video>` : "";
  const draft = task.shots?.length && ["draft", "edited", "failed"].includes(task.status) ? `<div class="draft-editor">
    <div class="draft-editor-header"><div><span class="section-index">SCRIPT REVIEW</span><h4>审核并修改后再生成</h4></div><button class="primary-button" data-generate-task="${escapeHtml(task.id)}"><span>确认并生成实际视频</span><b>→</b></button></div>
    <label>标题<input class="draft-title" value="${escapeHtml(task.title)}" /></label><label>摘要<textarea class="draft-summary">${escapeHtml(task.summary)}</textarea></label><label>CTA<textarea class="draft-cta">${escapeHtml(task.cta)}</textarea></label><label>完整旁白<textarea class="draft-narration">${escapeHtml(task.narration)}</textarea></label>
    ${reviewInputMarkup(task)}<div class="shot-editor-grid">${shotEditor(task)}</div><button class="text-button" data-save-draft="${escapeHtml(task.id)}">保存脚本、分镜和参数修改</button>
  </div>` : "";
  const uploads = task.shots?.length ? `<div class="upload-box"><div><strong>上传镜头素材</strong><small>选择图片或视频并绑定到一个镜头。上传后会覆盖该镜头的自动素材。</small></div><select id="upload-shot">${task.shots.map((shot) => `<option value="${escapeHtml(shot.id)}">镜头 ${shot.order}</option>`).join("")}</select><input id="asset-file" type="file" accept="image/*,video/*" /><button id="upload-asset" data-upload-task="${escapeHtml(task.id)}">上传素材</button></div><div class="audio-upload-row"><label>上传配音<input id="voice-file" type="file" accept="audio/*" /></label><button data-upload-audio="voice" data-upload-task="${escapeHtml(task.id)}">上传配音</button><label>上传音乐<input id="music-file" type="file" accept="audio/*" /></label><button data-upload-audio="music" data-upload-task="${escapeHtml(task.id)}">上传音乐</button></div>` : "";
  $("#active-task").innerHTML = `<div class="task-progress"><div class="task-meta"><div><h4>${escapeHtml(task.title || "脚本预览")}</h4><p>${escapeHtml(task.summary || "请审核脚本和分镜后生成。")} / ${escapeHtml(task.status)} / ${escapeHtml(task.id)}</p></div><strong>${task.progress}%</strong></div><div class="progress-bar"><span style="width:${task.progress}%"></span></div><div class="pipeline">${stageMarkup(task)}</div>${error}${draft}${uploads}${links}<div class="asset-summary"><strong>素材记录</strong>${assetList(task)}</div><div class="trace-line">文本 provider: ${escapeHtml(task.trace?.provider || "pending")} / 模型: ${escapeHtml(task.trace?.model || "pending")} / fallback: ${String(Boolean(task.trace?.fallback_used))}<br />配音: ${escapeHtml(task.audio?.voice_provider || "pending")} / 音乐: ${escapeHtml(task.audio?.music_provider || "pending")} / 音频: ${render.has_audio ? "有" : "无"} / 烧录字幕: ${render.has_burned_subtitles ? "有" : "无"} / 画面费用: ${visualCostLine(task)}</div></div>`;
  $$("[data-retry]").forEach((button) => button.addEventListener("click", () => retryTask(button.dataset.retry)));
  $$("[data-generate-task]").forEach((button) => button.addEventListener("click", () => generateTask(button.dataset.generateTask)));
  $$("[data-save-draft]").forEach((button) => button.addEventListener("click", () => saveDraft(button.dataset.saveDraft)));
  const uploadButton = $("#upload-asset");
  if (uploadButton) uploadButton.addEventListener("click", () => uploadAsset(uploadButton.dataset.uploadTask));
  $$("[data-upload-audio]").forEach((button) => button.addEventListener("click", () => uploadAudio(button.dataset.uploadTask, button.dataset.uploadAudio)));
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function saveDraft(taskId) {
  try {
    const topLevel = { title: $(".draft-title").value, summary: $(".draft-summary").value, cta: $(".draft-cta").value, narration: $(".draft-narration").value };
    const input = {};
    $$("[data-input-field]").forEach((field) => {
      let value = field.type === "checkbox" ? field.checked : field.value;
      if (["duration", "shot_count", "fps"].includes(field.dataset.inputField)) value = Number(value);
      if (["voice_rate", "voice_volume", "music_volume"].includes(field.dataset.inputField)) value = Number(value);
      input[field.dataset.inputField] = value;
    });
    await api(`/api/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify({ ...topLevel, input }) });
    const grouped = {};
    $$("[data-shot-field]").forEach((field) => {
      const shotId = field.dataset.shotId;
      grouped[shotId] ||= {};
      grouped[shotId][field.dataset.shotField] = field.type === "number" ? Number(field.value) : field.value;
    });
    for (const [shotId, patch] of Object.entries(grouped)) await api(`/api/tasks/${taskId}/shots/${shotId}`, { method: "PATCH", body: JSON.stringify(patch) });
    showToast("修改已保存。确认后才会生成实际视频。");
    renderTask(await api(`/api/tasks/${taskId}`));
  } catch (error) { showToast(error.message); }
}

async function generateTask(taskId) {
  if (!window.confirm("确认使用当前脚本、分镜、素材和音频参数生成实际视频吗？")) return;
  try { await api(`/api/tasks/${taskId}/generate`, { method: "POST", body: "{}" }); showToast("实际视频生成已开始。"); pollTask(taskId); } catch (error) { showToast(error.message); }
}

async function uploadAsset(taskId) {
  const file = $("#asset-file").files[0];
  if (!file) return showToast("请先选择图片或视频。");
  try {
    await api("/api/assets", { method: "POST", body: JSON.stringify({ task_id: taskId, shot_id: $("#upload-shot").value, name: file.name, data_base64: await readFileAsDataUrl(file), license: "待团队确认", author: "待团队确认", usage_scope: "比赛展示与内部审核" }) });
    showToast("素材已上传并绑定镜头。");
    renderTask(await api(`/api/tasks/${taskId}`));
  } catch (error) { showToast(error.message); }
}

async function uploadAudio(taskId, kind) {
  const input = kind === "voice" ? $("#voice-file") : $("#music-file");
  const file = input?.files[0];
  if (!file) return showToast(`请先选择${kind === "voice" ? "配音" : "音乐"}文件。`);
  try {
    await api("/api/uploads", { method: "POST", body: JSON.stringify({ task_id: taskId, kind, name: file.name, data_base64: await readFileAsDataUrl(file) }) });
    showToast("音频已上传。");
    renderTask(await api(`/api/tasks/${taskId}`));
  } catch (error) { showToast(error.message); }
}

async function pollTask(taskId) {
  try {
    const task = await api(`/api/tasks/${taskId}`);
    renderTask(task);
    if (!["completed", "failed", "draft"].includes(task.status)) state.pollTimer = window.setTimeout(() => pollTask(taskId), 1000);
    else if (task.status === "completed") { await loadProjects(); showToast("实际 MP4 已生成，请检查画面、音频、字幕和素材授权。"); }
  } catch (error) { showToast(error.message); }
}

async function createTask(event) {
  event.preventDefault();
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  button.querySelector("span").textContent = "正在生成脚本...";
  try { const task = await api("/api/previews", { method: "POST", body: JSON.stringify(formData()) }); renderTask(task); showToast(task.status === "failed" ? task.error : "脚本和分镜已生成，请审核后确认。"); } catch (error) { showToast(error.message); } finally { button.disabled = false; button.querySelector("span").textContent = "生成脚本预览"; }
}

async function retryTask(taskId) {
  try { await api(`/api/tasks/${taskId}/generate`, { method: "POST", body: "{}" }); pollTask(taskId); } catch (error) { showToast(error.message); }
}

async function loadProjects() {
  const data = await api("/api/tasks?limit=50");
  const container = $("#project-list");
  if (!data.items.length) return (container.innerHTML = `<div class="empty-state">还没有任务。</div>`);
  container.innerHTML = data.items.map((task) => `<article class="project-card"><img src="${task.render?.cover_url || ""}" alt="" /><div><span class="status ${task.status === "failed" ? "failed" : ""}">${escapeHtml(task.status.toUpperCase())}</span><h3>${escapeHtml(task.title || task.input.theme)}</h3><p>${escapeHtml(task.input.city)} / ${escapeHtml(task.input.landmark || "未指定地标")} / ${task.input.duration}s<br />${escapeHtml(task.updated_at)}</p></div><div class="project-card-actions">${task.render?.video_url ? `<a href="${task.render.video_url}" target="_blank">打开 MP4</a>` : ""}<button data-open-task="${escapeHtml(task.id)}">打开任务</button></div></article>`).join("");
  $$("[data-open-task]").forEach((button) => button.addEventListener("click", () => { showView("create"); pollTask(button.dataset.openTask); }));
}

function showView(name) {
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $("#page-title").textContent = name === "create" ? "创作工作台" : name === "projects" ? "作品档案" : "系统与配置";
  if (name === "projects") loadProjects().catch((error) => showToast(error.message));
}

async function boot() {
  try {
    const health = await api("/api/health");
    $("#service-status").textContent = `服务已连接 / ${health.provider}`;
    $("#provider-chip").textContent = `${health.provider} / ${health.model}`;
    $(".status-dot").style.background = "#75c58b";
    await loadCatalog();
    await loadProjects();
  } catch (error) {
    $("#service-status").textContent = "服务不可用";
    showToast("请先运行 scripts/start.ps1。");
  }
  $("#create-form").addEventListener("submit", createTask);
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $("#refresh-button").addEventListener("click", () => window.location.reload());
  $("#view-latest").addEventListener("click", () => state.latestTaskId ? pollTask(state.latestTaskId) : showToast("还没有任务。"));
}

boot();
