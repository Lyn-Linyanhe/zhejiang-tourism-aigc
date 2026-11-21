const state = { catalog: null, systemConfig: null, latestTaskId: null, pollTimer: null };
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

function choiceOptions(values, selected) {
  return [...new Set([...values, ...(selected ? [selected] : [])])].map(value => `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value)}</option>`).join("");
}

function fillChoice(name, values, fallback = "", optional = false) {
  const field = $(`#create-form [name='${name}']`);
  const current = field.value || fallback;
  field.innerHTML = (optional ? '<option value="">未指定</option>' : "") + choiceOptions(values, current === "__custom__" ? "" : current) + '<option value="__custom__">自定义…</option>';
  field.value = current;
}

function createChoiceValue(name) {
  const field = $(`#create-form [name='${name}']`);
  return field.value === "__custom__" ? $(`[data-custom-for='${name}']`).value.trim() : field.value;
}

function voiceChoiceOptions(selected) {
  const voices = [
    {voice:"天依·本地社区音色", label:"天依 · 本地社区声库", engine:"tianyi"},
    ...(state.catalog?.voices || ["晓晓·温暖女声", "无配音"]).filter(voice => voice !== "无配音").map(voice => ({voice, label:voice, engine:"edge"})),
    {voice:"Windows 系统语音", label:"Windows 系统语音（本地）", engine:"windows"},
    {voice:"上传配音音频", label:"上传配音音频（按镜头绑定）", engine:"upload"},
    {voice:"无配音", label:"无配音", engine:"configured"},
  ];
  if (selected && !voices.some(item => item.voice === selected)) voices.push({voice:selected,label:selected,engine:"configured"});
  return voices.map(item => `<option value="${escapeHtml(item.voice)}" data-engine="${item.engine}" ${item.voice === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
}

function syncCreateVoice() {
  const form = $("#create-form");
  const engine = form.elements.voice.selectedOptions[0]?.dataset.engine || "configured";
  form.elements.voice_pitch.disabled = engine !== "edge";
  $("#create-voice-note").textContent = engine === "tianyi" ? "本地天依社区音色，语调由模型生成。" : engine === "upload" ? "生成脚本后，在配音工作台按镜头上传音频。" : engine === "windows" ? "使用本机系统语音。" : form.elements.voice.value === "无配音" ? "仅保留画面和所选背景音乐。" : "联网神经音色，可在脚本预览后试听和调校。";
}

function updateCityPreview() {
  const city = createChoiceValue("city");
  const data = state.catalog?.cities.find((item) => item.name === city);
  if (!data) {
    $("#city-preview").innerHTML = `<div class="preview-city">${escapeHtml(city || "自定义城市")}</div><div class="preview-landmark">可自行填写地标与文化线索</div>`;
    return;
  }
  const landmarks = [data.landmark, ...data.aliases];
  if ($("#landmark").value !== "__custom__" && !landmarks.includes($("#landmark").value)) $("#landmark").value = "";
  fillChoice("landmark", landmarks, data.landmark, true);
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
  fillChoice("city", state.catalog.cities.map(item => item.name), "杭州");
  fillChoice("festival", state.catalog.festivals, "", true);
  fillChoice("audience", state.catalog.audiences, "", true);
  fillChoice("style", state.catalog.styles, "诗意纪实");
  $("#create-form [name='voice']").innerHTML = voiceChoiceOptions($("#create-form [name='voice']").value);
  fillChoice("music_mood", state.catalog.music_moods, "无音乐");
  fillChoice("culture", state.catalog.cities.flatMap(item => item.culture.split("、")), "", true);
  $$("[data-custom-for]").forEach(custom => {
    const field = $(`#create-form [name='${custom.dataset.customFor}']`);
    custom.setAttribute("aria-label", `自定义${field.closest("label").firstChild.textContent.trim()}`);
    field.addEventListener("change", () => {
      custom.hidden = field.value !== "__custom__";
      custom.required = !custom.hidden;
      if (!custom.hidden) custom.focus();
    });
  });
  $("#city").addEventListener("input", updateCityPreview);
  $("[data-custom-for='city']").addEventListener("input", updateCityPreview);
  $("#create-form [name='voice']").addEventListener("change", syncCreateVoice);
  $("#create-form").addEventListener("input", updateInputMetrics);
  updateCityPreview();
  updateInputMetrics();
  syncCreateVoice();
}

function formData() {
  const form = $("#create-form");
  const data = Object.fromEntries(new FormData(form).entries());
  $$("#create-form [data-custom-for]").forEach(field => { data[field.dataset.customFor] = createChoiceValue(field.dataset.customFor); });
  data.voice_engine = form.elements.voice.selectedOptions[0]?.dataset.engine || "configured";
  for (const key of ["duration", "shot_count", "fps"]) data[key] = Number(data[key]);
  for (const key of ["voice_rate", "voice_pitch", "voice_volume", "music_volume"]) data[key] = Number(form.elements[key].value);
  data.include_ai_label = form.elements.include_ai_label.checked;
  data.include_subtitles = form.elements.include_subtitles.checked;
  return data;
}

function stageMarkup(task) {
  const pipeline = state.systemConfig?.pipeline || [
    { name: "scripting", label: "脚本" }, { name: "storyboard", label: "分镜" },
    { name: "audio", label: "配音" }, { name: "assets", label: "画面" },
    { name: "rendering", label: "渲染" }, { name: "review", label: "审核" },
  ];
  const currentIndex = task.stage === "preview" ? 2 : pipeline.findIndex((item) => item.name === task.stage);
  return pipeline.map((item, index) => `<div class="pipeline-step ${index < currentIndex ? "done" : ""} ${item.name === task.stage ? "active" : ""}"><b>${String(index + 1).padStart(2, "0")}</b>${escapeHtml(item.label)}</div>`).join("");
}

function assetProviderOptions(selected) {
  const providers = state.systemConfig?.providers?.asset || [
    { name: "local-library", label: "本地素材库", available: true },
    { name: "upload", label: "逐镜头上传", available: true },
    { name: "pexels", label: "Pexels 视频优先素材", available: true },
    { name: "offline", label: "技术测试场景图", available: true },
  ];
  const configured = `<option value="configured" ${selected === "configured" ? "selected" : ""}>使用服务端配置</option>`;
  return configured + providers.map((item) => `<option value="${escapeHtml(item.name)}" ${selected === item.name ? "selected" : ""} ${item.available ? "" : "disabled"}>${escapeHtml(item.label)}${item.available ? "" : "（未配置）"}</option>`).join("");
}

// 只有"逐镜头上传"这条来源会去读用户传的文件。AI 生成、素材库、Pexels 都不读，
// 把上传框一直摆在那里只会让人以为传了就会被用上。
function sourceNeedsUpload(source) {
  const value = String(source || "").trim();
  if (value === "upload") return true;
  if (value === "configured") return String(state.systemConfig?.selected?.asset || "") === "upload";
  return false;
}

// 来源说明直接取后端注册表里的 description，不再手写一份必然过期的文案。
function describeSource(source) {
  if (source === "configured") {
    return `跟随服务端 .env 的 ASSET_PROVIDER，当前解析为 ${state.systemConfig?.selected?.asset || "未配置"}。`;
  }
  const providers = state.systemConfig?.providers?.asset || [];
  return providers.find((item) => item.name === source)?.description || "";
}

// 当前草稿里某个参数的值。条件显示要读"正在编辑的值"，不是任务快照里的旧值。
function draftFieldValue(name) {
  return $('.draft-editor [data-input-field="' + name + '"]')?.value ?? "";
}

// 哪些附加上传面板该出现。三个面板各自绑定一条规则，规则之外一律不显示——
// 显示了也用不上（无配音时上传的配音片段会被直接跳过，无音乐时上传的音乐同理）。
function attachVisibility() {
  return {
    "asset-upload": sourceNeedsUpload(draftFieldValue("asset_source")),
    "voice-upload": draftFieldValue("voice") !== "无配音",
    "music-upload": draftFieldValue("music_mood") !== "无音乐",
  };
}

function syncAttachPanels() {
  const visible = attachVisibility();
  $$(".draft-editor [data-when]").forEach((panel) => {
    panel.classList.toggle("is-hidden", !visible[panel.dataset.when]);
  });
  const note = $("#source-note");
  if (note) note.textContent = describeSource(draftFieldValue("asset_source"));
}

async function loadSystemConfig() {
  state.systemConfig = await api("/api/config");
  const selected = state.systemConfig.selected || {};
  $("#metric-provider").textContent = selected.llm || "未配置";
  const assetSelect = $('#create-form [name="asset_source"]');
  if (assetSelect) assetSelect.innerHTML = assetProviderOptions(assetSelect.value || "configured");
  const pipeline = $("#system-pipeline");
  if (pipeline) pipeline.innerHTML = state.systemConfig.pipeline.map((item) => `<div><b>${String(item.order).padStart(2, "0")}</b><span>${escapeHtml(item.label)}</span><small>${escapeHtml(item.name)}</small></div>`).join("");
  const grid = $("#provider-registry");
  if (grid) grid.innerHTML = Object.entries(state.systemConfig.providers).map(([slot, providers]) => `<article><span class="section-index">${escapeHtml(slot.toUpperCase())}</span><h3>${escapeHtml(selected[slot] || "按任务选择")}</h3><div class="provider-list">${providers.map((item) => `<div class="provider-row"><i class="provider-state ${item.available ? "ready" : ""}"></i><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.name)} · ${item.available ? "可用" : "未配置"}</small></span></div>`).join("")}</div></article>`).join("");
}

// 上传面板共用的一套结构。镜头下拉是每个面板自己的（配音面板不能依赖素材面板的
// #upload-shot —— AI 生成时那个面板整个不渲染，共用会让配音上传直接失效）。
function attachPanel(kind, task, head, sub, controls) {
  return `<div class="attach-panel" data-when="${kind}">
    <div class="attach-head"><strong>${head}</strong><small>${sub}</small></div>
    <div class="attach-row">${controls}</div>
  </div>`;
}

function shotOptions(task) {
  return (task.shots || []).map((shot) => `<option value="${escapeHtml(shot.id)}">镜头 ${String(shot.order).padStart(2, "0")}</option>`).join("");
}

function assetAttachPanel(task) {
  return attachPanel(
    "asset-upload",
    task,
    "逐镜头上传素材",
    "每个镜头都要有图片或视频，缺一个就会在生成时报错。",
    `<label>绑定到镜头<select id="upload-shot">${shotOptions(task)}</select></label>
     <label>选择文件<input id="asset-file" type="file" accept="image/*,video/*" /></label>
     <button id="upload-asset" data-upload-task="${escapeHtml(task.id)}">上传素材</button>`
  );
}

function voiceAttachPanel(task) {
  return attachPanel(
    "voice-upload",
    task,
    "上传配音音频",
    "选择镜头并上传录音或已制作的配音。上传模式需每镜绑定；使用天依等合成来源时，仅覆盖已绑定的镜头。",
    `<label>绑定到镜头<select id="voice-shot">${shotOptions(task)}</select></label>
     <label>选择音频<input id="voice-file" type="file" accept="audio/*" /></label>
     <button data-upload-audio="voice" data-upload-task="${escapeHtml(task.id)}">上传配音</button>`
  );
}

function musicAttachPanel(task) {
  return attachPanel(
    "music-upload",
    task,
    "上传授权音乐",
    "上传后覆盖曲库里随机选中的那一首，授权信息会随成片存档。",
    `<label>授权说明<input id="music-license" value="已获授权" /></label>
     <label>作者<input id="music-author" value="待填写" /></label>
     <label>使用范围<input id="music-scope" value="比赛展示与内部审核" /></label>
     <label>选择音频<input id="music-file" type="file" accept="audio/*" /></label>
     <label class="toggle-label">已确认授权<input id="music-approved" type="checkbox" checked /><span class="toggle"></span></label>
     <button data-upload-audio="music" data-upload-task="${escapeHtml(task.id)}">上传音乐</button>`
  );
}

// 审核面板按"你要决定什么"分三段，附加上传紧跟它所属的那段，而不是垫在整块分镜
// 编辑器之后。素材来源是决定后面出现什么的关键选择，单独提到第一段的最前面。
function reviewSettingsMarkup(task) {
  const input = task.input;
  const draft = (key) => `class="draft-input" data-input-field="${key}"`;
  return `<div class="review-settings">
    <section class="review-block">
      <div class="review-block-head"><span class="section-index">01 / 画面</span><h5>镜头画面从哪来</h5></div>
      <label class="source-picker">素材来源<select ${draft("asset_source")}>${assetProviderOptions(input.asset_source)}</select></label>
      <p class="section-note" id="source-note">${escapeHtml(describeSource(input.asset_source))}</p>
      ${assetAttachPanel(task)}
      <div class="review-settings-grid">
        <label>输出规格<select ${draft("output_resolution")}>${["720x1280", "1080x1920", "1920x1080", "1080x1080"].map((value) => `<option value="${value}" ${input.output_resolution === value ? "selected" : ""}>${value}</option>`).join("")}</select></label>
        <label>帧率<select ${draft("fps")}>${[24, 25, 30, 60].map((value) => `<option value="${value}" ${Number(input.fps) === value ? "selected" : ""}>${value} fps</option>`).join("")}</select></label>
        <label class="toggle-label">烧录字幕<input ${draft("include_subtitles")} type="checkbox" ${input.include_subtitles ? "checked" : ""} /><span class="toggle"></span></label>
        <label class="toggle-label">AIGC 标识<input ${draft("include_ai_label")} type="checkbox" ${input.include_ai_label ? "checked" : ""} /><span class="toggle"></span></label>
      </div>
    </section>

    <section class="review-block">
      <div class="review-block-head"><span class="section-index">02 / 声音</span><h5>念什么，配什么</h5></div>
      <div class="review-settings-grid">
        <label>配音<select ${draft("voice")}>${voiceChoiceOptions(input.voice)}</select></label>
        <label>语速<input ${draft("voice_rate")} type="number" min="0.5" max="2" step="0.1" value="${input.voice_rate}" /></label>
        <label>音高 (Hz)<input ${draft("voice_pitch")} type="number" min="-50" max="50" step="5" value="${input.voice_pitch ?? 0}" /></label>
        <label>配音音量<input ${draft("voice_volume")} type="number" min="0" max="2" step="0.05" value="${input.voice_volume}" /></label>
        <label>背景音乐<select ${draft("music_mood")}>${choiceOptions(state.catalog?.music_moods || [], input.music_mood)}</select></label>
        <label>音乐音量<input ${draft("music_volume")} type="number" min="0" max="1" step="0.01" value="${input.music_volume}" /></label>
        <label>旁白出现时<select ${draft("music_ducking")}>${(state.catalog?.music_ducking || ["不压低", "轻微", "标准"]).map((value) => `<option value="${escapeHtml(value)}" ${input.music_ducking === value ? "selected" : ""}>${escapeHtml(value === "不压低" ? "保持音量不变" : value + "压低")}</option>`).join("")}</select></label>
      </div>
      ${voiceStudioMarkup(task)}
      ${voiceAttachPanel(task)}
      ${musicAttachPanel(task)}
    </section>

    <section class="review-block">
      <div class="review-block-head"><span class="section-index">03 / 创作参数</span><h5>这支片子讲什么</h5></div>
      <div class="review-settings-grid">
        ${[["city", "城市"], ["landmark", "核心地标"], ["theme", "主题"], ["culture", "文化线索"], ["festival", "节庆 / 活动"], ["audience", "目标受众"], ["style", "叙事风格"]].map(([key, label]) => `<label>${label}<input ${draft(key)} value="${escapeHtml(input[key])}" /></label>`).join("")}
        <label>成片时长<select ${draft("duration")}>${[15, 30, 60].map((value) => `<option value="${value}" ${Number(input.duration) === value ? "selected" : ""}>${value} 秒</option>`).join("")}</select></label>
        <label>镜头数量<select ${draft("shot_count")}>${[0, 4, 6, 8].map((value) => `<option value="${value}" ${Number(input.shot_count) === value ? "selected" : ""}>${value || "自动"}${value ? " 个" : ""}</option>`).join("")}</select></label>
      </div>
      <label>主体锁定<textarea ${draft("subject_lock")} placeholder="同一座断桥、同一件器物或同一位人物的固定外观">${escapeHtml(input.subject_lock || "")}</textarea><small class="field-note">逐镜复用主体描述；填写参考图路径时可进一步启用图像条件。</small></label>
      <label>主体参考图路径<input ${draft("subject_reference_path")} value="${escapeHtml(input.subject_reference_path || "")}" placeholder="可选：本地图片绝对路径" /></label>
      <label>补充要求<textarea ${draft("custom_brief")}>${escapeHtml(input.custom_brief)}</textarea></label>
    </section>
  </div>`;
}

function shotEditor(task) {
  return (task.shots || []).map((shot) => `<article class="shot-editor">
    <div class="shot-editor-header"><strong>镜头 ${String(shot.order).padStart(2, "0")}</strong><span>${escapeHtml(shot.id)}</span></div>
    <label>镜头标题<input data-shot-field="title" data-shot-id="${escapeHtml(shot.id)}" value="${escapeHtml(shot.title)}" /></label>
    <label>画面描述<textarea data-shot-field="visual_prompt" data-shot-id="${escapeHtml(shot.id)}">${escapeHtml(shot.visual_prompt)}</textarea></label>
    <p class="section-note">旁白请在上方声音轨道的对应片段中编辑。</p>
    <label>字幕<textarea data-shot-field="subtitle" data-shot-id="${escapeHtml(shot.id)}">${escapeHtml(shot.subtitle)}</textarea></label>
    <label>进入本镜头的转场<select data-shot-field="transition" data-shot-id="${escapeHtml(shot.id)}">${[["fade","淡变"],["dissolve","叠化"],["smoothleft","平滑左移"],["smoothright","平滑右移"],["circleopen","圆形展开"],["wipeleft","向左划像"],["wiperight","向右划像"],["zoom","推近"],["slideleft","左移（兼容）"],["slideright","右移（兼容）"]].map(([value,label]) => `<option value="${value}" ${shot.transition === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <label>静图运动<select data-shot-field="motion" data-shot-id="${escapeHtml(shot.id)}">${[["auto","自动交替"],["push","聚焦推近"],["pull","缓慢拉远"],["gentle","轻微推近"]].map(([value,label]) => `<option value="${value}" ${(shot.motion || "auto") === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <p class="section-note">焦点坐标可留空自动识别；0 为左/上，1 为右/下。图像生成后也可通过镜头接口修正。</p>
    <label>焦点 X<input type="number" min="0" max="1" step="0.01" data-shot-field="focus_x" data-shot-id="${escapeHtml(shot.id)}" value="${shot.focus_x ?? ""}" placeholder="自动" /></label>
    <label>焦点 Y<input type="number" min="0" max="1" step="0.01" data-shot-field="focus_y" data-shot-id="${escapeHtml(shot.id)}" value="${shot.focus_y ?? ""}" placeholder="自动" /></label>
    <label>时长（秒）<input type="number" min="1" max="20" step="0.1" data-shot-field="duration_sec" data-shot-id="${escapeHtml(shot.id)}" value="${shot.duration_sec}" /></label>
  </article>`).join("");
}

function assetList(task) {
  if (!task.assets?.length) return `<p class="muted">尚未绑定素材。</p>`;
  return `<div class="asset-list">${task.assets.map((asset) => `<div class="asset-row"><span>镜头 ${escapeHtml(asset.shot_id || "-")}</span><strong>${escapeHtml(asset.name)}</strong><small>${escapeHtml(asset.source)} / ${escapeHtml(asset.media_type || "未标注")} / ${escapeHtml(asset.license)}</small></div>`).join("")}</div>`;
}

function visualQaMarkup(task) {
  const qa = task.visual_qa;
  if (!qa || !qa.version) return "";
  const requirements = qa.requirements || {};
  const cross = qa.cross_shot || {};
  const failures = Array.isArray(qa.failures) && qa.failures.length
    ? `<ul>${qa.failures.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : "<p>未发现阻断项。</p>";
  return `<section class="review-block"><div class="review-block-head"><span class="section-index">QA / VISUAL</span><h5>画面与主体一致性验收</h5></div>
    <div class="mini-metrics"><div><strong>${qa.passed ? "通过" : "未通过"}</strong><span>总结果</span></div><div><strong>${Number(requirements.video_shot_count || 0)} / ${Number(requirements.min_video_shots || 0)}</strong><span>视频镜头</span></div><div><strong>${qa.generation_contract?.passed ? "一致" : "不一致"}</strong><span>主体契约</span></div></div>
    <p class="section-note">亮度波动 ${escapeHtml(cross.brightness_cv ?? "未测")} / 色彩相似度 ${escapeHtml(cross.min_color_hist_similarity ?? "未测")}。验收读取实际像素；语义级身份核验仍需接入视觉嵌入或主体识别模型。</p>
    ${failures}<p><a href="/media/${encodeURIComponent(task.id)}/visual_qa.json" target="_blank">打开完整视觉验收 JSON</a></p>
  </section>`;
}

function renderTask(task) {
  // Old drafts retain their uploaded audio when the retired source is opened.
  if (task.input?.voice_engine === "xstudio") task = {...task, input:{...task.input, voice_engine:"upload", voice:"上传配音音频"}};
  state.latestTaskId = task.id;
  const error = task.error ? `<div class="error-box"><strong>任务失败</strong><p>${escapeHtml(task.error)}</p><button class="text-button" data-retry="${escapeHtml(task.id)}">从脚本阶段重试</button></div>` : "";
  const render = task.render || {};
  const links = render.video_url ? `<div class="task-links"><a href="${render.video_url}" target="_blank">打开 MP4</a><a href="${render.video_url}" download>下载 MP4</a><a href="${render.subtitle_url}" target="_blank">下载 SRT</a>${task.audio?.voice_url ? `<a href="${task.audio.voice_url}" target="_blank">试听配音</a>` : ""}${task.audio?.music_url ? `<a href="${task.audio.music_url}" target="_blank">试听音乐</a>` : ""}</div><video class="result-video" controls playsinline src="${render.video_url}"></video>` : "";
  const draft = task.shots?.length && ["draft", "edited", "failed"].includes(task.status) ? `<div class="draft-editor">
    <div class="draft-editor-header"><div><span class="section-index">SCRIPT REVIEW</span><h4>审核并修改后再生成</h4></div><button class="primary-button" data-generate-task="${escapeHtml(task.id)}"><span>确认并生成实际视频</span><b>→</b></button></div>
    <label>标题<input class="draft-title" value="${escapeHtml(task.title)}" /></label><label>摘要<textarea class="draft-summary">${escapeHtml(task.summary)}</textarea></label><label>CTA<textarea class="draft-cta">${escapeHtml(task.cta)}</textarea></label><label>完整旁白<textarea class="draft-narration">${escapeHtml(task.narration)}</textarea></label>
    ${reviewSettingsMarkup(task)}
    <section class="review-block">
      <div class="review-block-head"><span class="section-index">04 / 分镜脚本</span><h5>逐镜要念的、要拍的</h5></div>
      <div class="shot-editor-grid">${shotEditor(task)}</div>
    </section>
    <button class="text-button" data-save-draft="${escapeHtml(task.id)}">保存脚本、分镜和参数修改</button>
  </div>` : "";
  $("#active-task").innerHTML = `<div class="task-progress"><div class="task-meta"><div><h4>${escapeHtml(task.title || "脚本预览")}</h4><p>${escapeHtml(task.summary || "请审核脚本和分镜后生成。")} / ${escapeHtml(task.status)} / ${escapeHtml(task.id)}</p></div><strong>${task.progress}%</strong></div><div class="progress-bar"><span style="width:${task.progress}%"></span></div><div class="pipeline">${stageMarkup(task)}</div>${error}${draft}${links}${visualQaMarkup(task)}<div class="asset-summary"><strong>素材记录</strong>${assetList(task)}</div><div class="trace-line">文本 provider: ${escapeHtml(task.trace?.provider || "pending")} / 模型: ${escapeHtml(task.trace?.model || "pending")} / fallback: ${String(Boolean(task.trace?.fallback_used))}<br />配音: ${escapeHtml(task.audio?.voice_provider || "pending")} / 音乐: ${escapeHtml(task.audio?.music_provider || "pending")} / 音频: ${render.has_audio ? "有" : "无"} / 烧录字幕: ${render.has_burned_subtitles ? "有" : "无"}<br />Provider 报告成本: ${Number(task.cost?.total || 0).toFixed(4)}（${escapeHtml(task.cost?.unit || "等待统计")}）</div></div>`;
  $$("[data-retry]").forEach((button) => button.addEventListener("click", () => retryTask(button.dataset.retry)));
  $$("[data-generate-task]").forEach((button) => button.addEventListener("click", () => generateTask(button.dataset.generateTask)));
  $$("[data-save-draft]").forEach((button) => button.addEventListener("click", () => saveDraft(button.dataset.saveDraft)));
  const uploadButton = $("#upload-asset");
  if (uploadButton) uploadButton.addEventListener("click", () => uploadAsset(uploadButton.dataset.uploadTask));
  $$("[data-upload-audio]").forEach((button) => button.addEventListener("click", () => uploadAudio(button.dataset.uploadTask, button.dataset.uploadAudio)));
  // 改素材来源/配音/音乐要立刻反映到上传面板上：选 AI 生成就不该再看到上传框。
  // 只切 class 不重渲染，免得把用户还没保存的改动冲掉。
  const editor = $(".draft-editor");
  if (editor) {
    editor.addEventListener("input", (event) => { if (event.target.dataset?.inputField) syncAttachPanels(); });
    editor.addEventListener("change", (event) => { if (event.target.dataset?.inputField) syncAttachPanels(); });
  }
  syncAttachPanels();
  bindVoiceStudio(task);
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
      if (["voice_rate", "voice_pitch", "voice_volume", "music_volume", "voice_expressiveness"].includes(field.dataset.inputField)) value = Number(value);
      input[field.dataset.inputField] = value;
    });
    await api(`/api/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify({ ...topLevel, input }) });
    const grouped = {};
    $$("[data-shot-field]").forEach((field) => {
      const shotId = field.dataset.shotId;
      grouped[shotId] ||= {};
      grouped[shotId][field.dataset.shotField] = ["voice_pause_after", "focus_x", "focus_y"].includes(field.dataset.shotField) && field.value === "" ? null : field.type === "number" ? Number(field.value) : field.value;
    });
    for (const [shotId, patch] of Object.entries(grouped)) await api(`/api/tasks/${taskId}/shots/${shotId}`, { method: "PATCH", body: JSON.stringify(patch) });
    showToast("修改已保存。确认后才会生成实际视频。");
    renderTask(await api(`/api/tasks/${taskId}`));
    return true;
  } catch (error) { showToast(error.message); return false; }
}

async function generateTask(taskId) {
  if (!window.confirm("确认使用当前脚本、分镜、素材和音频参数生成实际视频吗？")) return;
  if (!await saveDraft(taskId)) return;
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
    const payload = { task_id: taskId, kind, name: file.name, data_base64: await readFileAsDataUrl(file) };
    if (kind === "voice") {
      // 配音面板有自己的镜头下拉，不能借用素材面板的 #upload-shot —
      // 选 AI 生成时那个面板整个不渲染，共用会让配音上传直接失效。
      payload.shot_id = $("#voice-shot")?.value || "";
      if (!payload.shot_id) return showToast("请先选择一个镜头。");
    } else {
      // 音乐上传必须带授权字段并确认已获授权。mood 直接取表单里的音乐情绪：
      // 单独一个输入框时，填得和表单不一致就会静默匹配不上，上传了却不出声。
      payload.mood = draftFieldValue("music_mood").trim();
      payload.license = $("#music-license")?.value?.trim() || "";
      payload.author = $("#music-author")?.value?.trim() || "";
      payload.usage_scope = $("#music-scope")?.value?.trim() || "";
      payload.approved = Boolean($("#music-approved")?.checked);
    }
    if (!await saveDraft(taskId)) return;
    await api("/api/uploads", { method: "POST", body: JSON.stringify(payload) });
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
  $$("[data-open-task]").forEach((button) => button.addEventListener("click", () => openTask(button.dataset.openTask)));
}

// 打开一条任务并把 id 写进地址栏。这样刷新、收藏、把链接发给别人，
// 都能直接回到同一条片子，而不是回到空白的创作工作台。
function openTask(taskId) {
  if (!taskId) return;
  showView("create");
  window.location.hash = `task=${encodeURIComponent(taskId)}`;
  pollTask(taskId);
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
    await loadSystemConfig();
    await loadCatalog();
    await loadProjects();
  } catch (error) {
    $("#service-status").textContent = "服务不可用";
    showToast("请先运行 scripts/start.ps1。");
  }
  $("#create-form").addEventListener("submit", createTask);
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $("#refresh-button").addEventListener("click", () => window.location.reload());
  $("#view-latest").addEventListener("click", () => state.latestTaskId ? openTask(state.latestTaskId) : showToast("还没有任务。"));
  // #task=<id> 深链：刷新或从别处打开链接时直接恢复那条任务
  const linked = window.location.hash.match(/^#task=(.+)$/);
  if (linked) openTask(decodeURIComponent(linked[1]));
}

boot();
