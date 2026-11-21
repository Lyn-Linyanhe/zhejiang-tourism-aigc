const state = { catalog: null, systemConfig: null, latestTaskId: null, pollTimer: null, libraryAssets: [], publicationLibraryAssets: [], libraryRequest: 0 };
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
    { name: "pexels", label: "Pexels 在线素材", available: true },
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
  const publication = draftFieldValue("visual_usage") === "publication";
  const source = $('.draft-editor [data-input-field="asset_source"]');
  if (source) {
    if (publication) source.value = "local-library";
    source.disabled = publication;
  }
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
      <label class="source-picker">使用模式<select ${draft("visual_usage")}><option value="preview" ${input.visual_usage === "preview" ? "selected" : ""}>技术预览（允许待审素材）</option><option value="publication" ${input.visual_usage === "publication" ? "selected" : ""}>正式发布（仅审核通过）</option></select></label>
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
      <label>补充要求<textarea ${draft("custom_brief")}>${escapeHtml(input.custom_brief)}</textarea></label>
    </section>
  </div>`;
}

function shotTagOptions(selected) {
  const tags = Object.entries(state.catalog?.shot_tags || {wide:"地点全景", detail:"建筑或器物细节", process:"工艺过程", performance:"人物或演出", ambient:"环境空镜"});
  return `<option value="">未指定</option>${tags.map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}`;
}

function entityOptions(city, selected) {
  const items = (state.catalog?.entities || []).filter(item => !city || item.city === String(city).replace(/市$/, ""));
  return `<option value="">自动识别</option>${items.map(item => `<option value="${escapeHtml(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.name)} / ${escapeHtml(item.type)}</option>`).join("")}`;
}

function libraryStatus(asset) {
  const labels = {pending:"待审核", approved:"已通过", rejected:"已驳回"};
  return `${labels[asset.review_status] || "待审核"} / 授权${labels[asset.license_status] || "待审核"}`;
}

async function loadLibraryAssets(filters = {}) {
  const params = new URLSearchParams({ type: "", usage: "preview", ...filters });
  const data = await api(`/api/library/assets?${params.toString()}`);
  return data.items || [];
}

function libraryAssetOptions(shot, usage = draftFieldValue("visual_usage") || state.activeTask?.input?.visual_usage) {
  const assets = usage === "publication"
    ? state.publicationLibraryAssets
    : state.libraryAssets.filter(asset => asset.review_status !== "rejected");
  const entityId = shot.entity_id || "";
  const shotTag = shot.shot_tag || "";
  if (!entityId || !shotTag) return "";
  const options = assets.filter(asset => asset.entity_id === entityId && (asset.shot_tags || []).includes(shotTag));
  const bound = shot.library_asset_id;
  const missing = bound && !options.some(asset => asset.id === bound)
    ? `<option value="${escapeHtml(bound)}" selected>已绑定素材（正在核对可用状态）</option>` : "";
  return missing + options.map(asset => `<option value="${escapeHtml(asset.id)}" ${asset.id === bound ? "selected" : ""}>${escapeHtml(asset.name)} / ${escapeHtml(libraryStatus(asset))}</option>`).join("");
}

function shotEditor(task) {
  return (task.shots || []).map((shot) => `<article class="shot-editor">
    <div class="shot-editor-header"><strong>镜头 ${String(shot.order).padStart(2, "0")}</strong><span>${escapeHtml(shot.id)}</span></div>
    <label>知识实体<select data-shot-field="entity_id" data-shot-id="${escapeHtml(shot.id)}">${entityOptions(task.input.city, shot.entity_id)}</select></label>
    <label>镜头意图<select data-shot-field="shot_tag" data-shot-id="${escapeHtml(shot.id)}">${shotTagOptions(shot.shot_tag)}</select></label>
    <label>绑定库素材<select data-shot-field="library_asset_id" data-shot-id="${escapeHtml(shot.id)}"><option value="">自动选材</option>${libraryAssetOptions(shot)}</select></label>
    <p class="section-note">正式发布只允许已审核、授权有效且文件指纹未变化的素材。</p>
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

function libraryPanelMarkup(task) {
  const entityList = (state.catalog?.entities || []).filter(item => item.city === String(task.input.city || "").replace(/市$/, ""));
  const entities = `<option value="">未关联实体</option>${entityList.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}`;
  const tags = Object.entries(state.catalog?.shot_tags || {wide:"地点全景", detail:"建筑或器物细节", process:"工艺过程", performance:"人物或演出", ambient:"环境空镜"});
  const tagOptions = `<option value="">未指定意图</option>${tags.map(([key,label]) => `<option value="${key}">${escapeHtml(label)}</option>`).join("")}`;
  return `<section class="review-block library-panel" id="library-assets-panel">
    <div class="review-block-head"><span class="section-index">05 / 素材库</span><h5>实体素材与授权审核</h5></div>
    <p class="section-note">新上传素材默认为待审核和仅预览。授权证据、来源和有效期核实后，再单独审核通过。</p>
    <div class="library-filter-grid">
      <label>筛选实体<select id="library-filter-entity"><option value="">全部实体</option>${entityList.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</select></label>
      <label>筛选镜头意图<select id="library-filter-tag">${tagOptions}</select></label>
      <label>审核状态<select id="library-filter-status"><option value="">全部状态</option><option value="pending">待审核</option><option value="approved">已通过</option><option value="rejected">已驳回</option></select></label>
    </div>
    <div class="library-upload-form">
      <div class="library-upload-heading"><strong>加入素材库</strong><span>图片或短视频，单文件不超过 20 MB</span></div>
      <div class="library-edit-grid">
        <label>文件<input id="library-file" type="file" accept="image/*,video/mp4,video/quicktime,video/webm" /></label>
        <label>知识实体<select id="library-upload-entity">${entities}</select></label>
        <label>镜头意图<select id="library-upload-tag">${tagOptions}</select></label>
        <label>绑定任务镜头<select id="library-upload-shot"><option value="">仅登记素材库</option>${shotOptions(task)}</select></label>
        <label>来源页面<input id="library-source-url" type="url" placeholder="https://..." /></label>
        <label>来源标题<input id="library-source-title" placeholder="素材原始页面标题" /></label>
        <label>发布方<input id="library-source-publisher" placeholder="网站或机构" /></label>
        <label>作者 / 摄影者<input id="library-author" placeholder="署名原文" /></label>
        <label>许可名称<input id="library-license" placeholder="CC BY 4.0 / 团队书面授权" /></label>
        <label>许可链接<input id="library-license-url" type="url" placeholder="https://creativecommons.org/..." /></label>
        <label>许可证据<input id="library-license-evidence" placeholder="授权凭证或核验记录" /></label>
        <label>使用范围<input id="library-usage-scope" placeholder="发布渠道与许可限制" /></label>
        <label>有效期<input id="library-valid-until" type="date" /></label>
      </div>
      <button type="button" id="library-upload">上传并登记为待审核</button>
    </div>
    <div id="library-assets-list" class="library-assets-list"><p class="muted">正在读取素材库…</p></div>
  </section>`;
}

function renderLibraryItems(task) {
  const root = $("#library-assets-list");
  if (!root) return;
  const entity = $("#library-filter-entity")?.value || "";
  const tag = $("#library-filter-tag")?.value || "";
  const status = $("#library-filter-status")?.value || "";
  const assets = state.libraryAssets.filter(asset =>
    (!entity || asset.entity_id === entity) &&
    (!tag || (asset.shot_tags || []).includes(tag)) &&
    (!status || (asset.review_status || "pending") === status) &&
    ["image", "video"].includes(asset.asset_type)
  );
  if (!assets.length) {
    root.innerHTML = `<p class="muted">没有符合筛选条件的素材。</p>`;
    return;
  }
  root.innerHTML = assets.map(asset => `<article class="library-asset-row" data-library-asset="${escapeHtml(asset.id)}">
    <div class="library-asset-preview">${asset.asset_type === "video" ? `<video controls preload="metadata" src="${escapeHtml(asset.url)}"></video>` : `<img loading="lazy" src="${escapeHtml(asset.url)}" alt="${escapeHtml(asset.name)}" />`}</div>
    <div class="library-asset-meta"><div class="library-asset-heading"><strong>${escapeHtml(asset.name)}</strong><span class="library-state state-${escapeHtml(asset.review_status || "pending")}">${escapeHtml(libraryStatus(asset))}</span></div>
      <small>${escapeHtml(asset.city || "未关联城市")} · ${escapeHtml(entityListName(asset.entity_id))} · ${escapeHtml((asset.shot_tags || []).map(tag => state.catalog?.shot_tags?.[tag] || tag).join("、") || "未指定意图")}</small>
      <p class="section-note">${state.publicationLibraryAssets.some(item => item.id === asset.id) ? "可用于正式发布" : "当前仅供预览，尚未满足发布条件"} · 已核验 ${escapeHtml(asset.checked_at || "未核验")} · ${escapeHtml(asset.sha256?.slice(0, 16) || "未记录指纹")}</p>
      <div class="library-evidence-grid">
        <label>来源标题<input data-library-field="source_title" value="${escapeHtml(asset.source_title || "")}" /></label>
        <label>发布方<input data-library-field="source_publisher" value="${escapeHtml(asset.source_publisher || "")}" /></label>
        <label>来源页面<input data-library-field="source_url" value="${escapeHtml(asset.source_url || "")}" /></label>
        <label>作者<input data-library-field="author" value="${escapeHtml(asset.author || "")}" /></label>
        <label>许可<input data-library-field="license" value="${escapeHtml(asset.license || "")}" /></label>
        <label>许可链接<input data-library-field="license_url" value="${escapeHtml(asset.license_url || "")}" /></label>
        <label>许可证据<input data-library-field="license_evidence" value="${escapeHtml(asset.license_evidence || "")}" /></label>
        <label>有效期<input data-library-field="valid_until" type="date" value="${escapeHtml(asset.valid_until || "")}" /></label>
        <label>使用范围<input data-library-field="usage_scope" value="${escapeHtml(asset.usage_scope || "")}" /></label>
      </div>
      <div class="library-asset-actions"><button type="button" data-library-save="${escapeHtml(asset.id)}">保存许可信息</button><button type="button" data-library-review="approved" data-library-id="${escapeHtml(asset.id)}">审核通过并允许发布</button><button type="button" data-library-review="rejected" data-library-id="${escapeHtml(asset.id)}">驳回</button></div>
    </div>
  </article>`).join("");
  root.querySelectorAll("[data-library-save]").forEach(button => button.addEventListener("click", () => saveLibraryMetadata(button.dataset.librarySave)));
  root.querySelectorAll("[data-library-review]").forEach(button => button.addEventListener("click", () => reviewLibraryAsset(button.dataset.libraryId, button.dataset.libraryReview)));
}

function entityListName(entityId) {
  return (state.catalog?.entities || []).find(item => item.id === entityId)?.name || "未关联实体";
}

function refreshShotLibraryOptions() {
  $$("[data-shot-field='library_asset_id']").forEach(select => {
    const current = select.value;
    const editor = select.closest(".shot-editor");
    const shot = state.activeTask?.shots?.find(item => item.id === select.dataset.shotId);
    if (!shot || !editor) return;
    const edited = {
      ...shot,
      entity_id: editor.querySelector('[data-shot-field="entity_id"]').value,
      shot_tag: editor.querySelector('[data-shot-field="shot_tag"]').value,
      library_asset_id: current,
    };
    select.innerHTML = `<option value="">自动选材</option>${libraryAssetOptions(edited)}`;
    if (current && !Array.from(select.options).some(option => option.value === current)) {
      const unavailable = document.createElement("option");
      unavailable.value = current;
      unavailable.textContent = "当前绑定不符合筛选条件，请重新选择";
      select.append(unavailable);
    }
    select.value = current;
  });
}

async function loadTaskLibrary(task) {
  if (!task || !$("#library-assets-panel")) return;
  const requestId = ++state.libraryRequest;
  try {
    const [preview, publication] = await Promise.all([
      loadLibraryAssets({city: task.input.city || ""}),
      loadLibraryAssets({city: task.input.city || "", usage: "publication"}),
    ]);
    if (state.activeTask?.id !== task.id || state.libraryRequest !== requestId) return;
    state.libraryAssets = preview;
    state.publicationLibraryAssets = publication;
    refreshShotLibraryOptions();
    renderLibraryItems(task);
  } catch (error) {
    if (state.activeTask?.id !== task.id || state.libraryRequest !== requestId) return;
    const root = $("#library-assets-list");
    if (root) root.innerHTML = `<p class="error-box">${escapeHtml(error.message)}</p>`;
  }
}

async function uploadLibraryAsset(taskId) {
  const file = $("#library-file")?.files?.[0];
  if (!file) return showToast("请选择图片或视频文件。");
  if (file.size > 20 * 1024 * 1024) return showToast("素材文件不能超过 20 MB。");
  const metadata = {
    name: file.name,
    city: state.activeTask?.input?.city || "",
    entity_id: $("#library-upload-entity")?.value || "",
    shot_tags: $("#library-upload-tag")?.value ? [$("#library-upload-tag").value] : [],
    source_url: $("#library-source-url")?.value?.trim() || "",
    source_title: $("#library-source-title")?.value?.trim() || "",
    source_publisher: $("#library-source-publisher")?.value?.trim() || "",
    author: $("#library-author")?.value?.trim() || "",
    license: $("#library-license")?.value?.trim() || "",
    license_url: $("#library-license-url")?.value?.trim() || "",
    license_evidence: $("#library-license-evidence")?.value?.trim() || "",
    usage_scope: $("#library-usage-scope")?.value?.trim() || "",
    valid_until: $("#library-valid-until")?.value || null,
  };
  if (!metadata.entity_id || !metadata.shot_tags.length) return showToast("上传前请选择知识实体和镜头意图。");
  const shotId = $("#library-upload-shot")?.value || "";
  const button = $("#library-upload");
  button.disabled = true;
  try {
    metadata.data_base64 = await readFileAsDataUrl(file);
    if (taskId && shotId) {
      metadata.task_id = taskId;
      metadata.shot_id = shotId;
      if (!await saveDraft(taskId, {refresh:false, notify:false})) return;
    }
    const asset = await api("/api/library/assets", {method:"POST", body:JSON.stringify(metadata)});
    if (shotId) applyUploadedLibraryBinding(shotId, asset);
    showToast(asset.review_status === "approved" ? "文件已存在，保留原审核信息。" : "素材已登记为待审核，仅可用于预览。");
    await loadTaskLibrary(state.activeTask);
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; }
}

function applyUploadedLibraryBinding(shotId, asset) {
  const shot = state.activeTask?.shots?.find(item => item.id === shotId);
  if (!shot) return;
  Object.assign(shot, {entity_id:asset.entity_id, shot_tag:shot.shot_tag || asset.shot_tags?.[0] || "", library_asset_id:asset.id});
  for (const field of ["entity_id", "shot_tag", "library_asset_id"]) {
    const select = $(`[data-shot-id="${CSS.escape(shotId)}"][data-shot-field="${field}"]`);
    if (!select) continue;
    if (field === "library_asset_id" && !Array.from(select.options).some(option => option.value === asset.id)) {
      const option = document.createElement("option");
      option.value = asset.id;
      option.textContent = `${asset.name} / ${libraryStatus(asset)}`;
      select.append(option);
    }
    select.value = shot[field];
  }
}

async function saveLibraryMetadata(assetId) {
  const row = $(`[data-library-asset="${CSS.escape(assetId)}"]`);
  const patch = {};
  row?.querySelectorAll("[data-library-field]").forEach(field => { patch[field.dataset.libraryField] = field.value.trim() || null; });
  try {
    const updated = await api(`/api/library/assets/${encodeURIComponent(assetId)}`, {method:"PATCH", body:JSON.stringify(patch)});
    showToast(updated.review_status === "pending" ? "许可信息已保存，请重新审核发布条件。" : "素材许可信息已保存。");
    await loadTaskLibrary(state.activeTask);
  } catch (error) { showToast(error.message); }
}

async function reviewLibraryAsset(assetId, status) {
  const row = $(`[data-library-asset="${CSS.escape(assetId)}"]`);
  const patch = {review_status:status};
  if (status === "approved") {
    const fields = {};
    row?.querySelectorAll("[data-library-field]").forEach(field => { fields[field.dataset.libraryField] = field.value.trim(); });
    if (!fields.author || !fields.license || !fields.source_url || !(fields.license_url || fields.license_evidence)) {
      return showToast("通过审核前需填写作者、许可、来源和许可凭证。");
    }
    Object.assign(patch, fields, {license_status:"approved", allowed_usages:["preview", "publication"]});
  } else {
    patch.license_status = "rejected";
    patch.allowed_usages = ["preview"];
  }
  try {
    await api(`/api/library/assets/${encodeURIComponent(assetId)}`, {method:"PATCH", body:JSON.stringify(patch)});
    showToast(status === "approved" ? "素材已审核，可用于正式发布。" : "素材已驳回。" );
    await loadTaskLibrary(state.activeTask);
  } catch (error) { showToast(error.message); }
}

function assetList(task) {
  if (!task.assets?.length) return `<p class="muted">尚未绑定素材。</p>`;
  return `<div class="asset-list">${task.assets.map((asset) => `<div class="asset-row"><span>镜头 ${escapeHtml(asset.shot_id || "-")}</span><strong>${escapeHtml(asset.name)}</strong><small>${escapeHtml(asset.source)} / ${escapeHtml(asset.license)}</small></div>`).join("")}</div>`;
}

function renderTask(task) {
  // Old drafts retain their uploaded audio when the retired source is opened.
  if (task.input?.voice_engine === "xstudio") task = {...task, input:{...task.input, voice_engine:"upload", voice:"上传配音音频"}};
  state.latestTaskId = task.id;
  state.activeTask = task;
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
    ${libraryPanelMarkup(task)}
    <button class="text-button" data-save-draft="${escapeHtml(task.id)}">保存脚本、分镜和参数修改</button>
  </div>` : "";
  $("#active-task").innerHTML = `<div class="task-progress"><div class="task-meta"><div><h4>${escapeHtml(task.title || "脚本预览")}</h4><p>${escapeHtml(task.summary || "请审核脚本和分镜后生成。")} / ${escapeHtml(task.status)} / ${escapeHtml(task.id)}</p></div><strong>${task.progress}%</strong></div><div class="progress-bar"><span style="width:${task.progress}%"></span></div><div class="pipeline">${stageMarkup(task)}</div>${error}${draft}${links}<div class="asset-summary"><strong>素材记录</strong>${assetList(task)}</div><div class="trace-line">文本 provider: ${escapeHtml(task.trace?.provider || "pending")} / 模型: ${escapeHtml(task.trace?.model || "pending")} / fallback: ${String(Boolean(task.trace?.fallback_used))}<br />配音: ${escapeHtml(task.audio?.voice_provider || "pending")} / 音乐: ${escapeHtml(task.audio?.music_provider || "pending")} / 音频: ${render.has_audio ? "有" : "无"} / 烧录字幕: ${render.has_burned_subtitles ? "有" : "无"}<br />Provider 报告成本: ${Number(task.cost?.total || 0).toFixed(4)}（${escapeHtml(task.cost?.unit || "等待统计")}）</div></div>`;
  $$("[data-retry]").forEach((button) => button.addEventListener("click", () => retryTask(button.dataset.retry)));
  $$("[data-generate-task]").forEach((button) => button.addEventListener("click", () => generateTask(button.dataset.generateTask)));
  $$("[data-save-draft]").forEach((button) => button.addEventListener("click", () => saveDraft(button.dataset.saveDraft)));
  const uploadButton = $("#upload-asset");
  if (uploadButton) uploadButton.addEventListener("click", () => uploadAsset(uploadButton.dataset.uploadTask));
  const libraryUploadButton = $("#library-upload");
  if (libraryUploadButton) libraryUploadButton.addEventListener("click", () => uploadLibraryAsset(task.id));
  ["library-filter-entity", "library-filter-tag", "library-filter-status"].forEach(id => {
    const field = $(`#${id}`);
    if (field) field.addEventListener("change", () => renderLibraryItems(task));
  });
  loadTaskLibrary(task);
  $$("[data-upload-audio]").forEach((button) => button.addEventListener("click", () => uploadAudio(button.dataset.uploadTask, button.dataset.uploadAudio)));
  // 改素材来源/配音/音乐要立刻反映到上传面板上：选 AI 生成就不该再看到上传框。
  // 只切 class 不重渲染，免得把用户还没保存的改动冲掉。
  const editor = $(".draft-editor");
  if (editor) {
    editor.addEventListener("input", (event) => { if (event.target.dataset?.inputField) syncAttachPanels(); });
    editor.addEventListener("change", (event) => {
      if (event.target.dataset?.inputField) syncAttachPanels();
      if (["entity_id", "shot_tag"].includes(event.target.dataset?.shotField) || event.target.dataset?.inputField === "visual_usage") refreshShotLibraryOptions();
    });
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

async function saveDraft(taskId, {refresh = true, notify = true} = {}) {
  try {
    const topLevel = { title: $(".draft-title").value, summary: $(".draft-summary").value, cta: $(".draft-cta").value, narration: $(".draft-narration").value };
    const input = {};
    $$("[data-input-field]").forEach((field) => {
      let value = field.type === "checkbox" ? field.checked : field.value;
      if (["duration", "shot_count", "fps"].includes(field.dataset.inputField)) value = Number(value);
      if (["voice_rate", "voice_pitch", "voice_volume", "music_volume", "voice_expressiveness"].includes(field.dataset.inputField)) value = Number(value);
      input[field.dataset.inputField] = value;
    });
    if (input.visual_usage === "publication" && (input.asset_source === "upload" || input.asset_source === "pexels")) {
      throw new Error("正式发布必须使用已审核的本地实体素材库；Pexels/上传候选不能直接进入成片。");
    }
    const grouped = {};
    $$(`[data-shot-field]`).forEach((field) => {
      const shotId = field.dataset.shotId;
      grouped[shotId] ||= {};
      const empty = field.value === "" && ["voice_pause_after", "focus_x", "focus_y", "library_asset_id", "entity_id", "shot_tag"].includes(field.dataset.shotField);
      grouped[shotId][field.dataset.shotField] = empty ? null : field.type === "number" ? Number(field.value) : field.value;
    });
    const shots = Object.entries(grouped).map(([id, patch]) => ({id, ...patch}));
    const saved = await api(`/api/tasks/${taskId}`, {method:"PATCH", body:JSON.stringify({...topLevel, input, shots})});
    state.activeTask = saved;
    if (notify) showToast("修改已保存。确认后才会生成实际视频。");
    if (refresh) renderTask(saved);
    return true;
  } catch (error) { showToast(error.message); return false; }
}

async function generateTask(taskId) {
  if (!window.confirm("确认使用当前脚本、分镜、素材和音频参数生成实际视频吗？")) return;
  if (!await saveDraft(taskId)) return;
  try { await api(`/api/tasks/${taskId}/generate`, { method: "POST", body: "{}" }); showToast("实际视频生成已开始。"); pollTask(taskId); } catch (error) { showToast(error.message); }
}

async function uploadAsset(taskId) {
  const file = $("#asset-file")?.files?.[0];
  const shotId = $("#upload-shot")?.value || "";
  if (!file) return showToast("请先选择图片或视频。");
  if (file.size > 20 * 1024 * 1024) return showToast("素材文件不能超过 20 MB。");
  const entityId = $(`[data-shot-id="${CSS.escape(shotId)}"][data-shot-field="entity_id"]`)?.value || "";
  const shotTag = $(`[data-shot-id="${CSS.escape(shotId)}"][data-shot-field="shot_tag"]`)?.value || "";
  if (!entityId || !shotTag) return showToast("请先为目标镜头选择知识实体和镜头意图。");
  const button = $("#upload-asset");
  button.disabled = true;
  try {
    const payload = {
      task_id:taskId, shot_id:shotId, name:file.name, data_base64:await readFileAsDataUrl(file),
      city:state.activeTask.input.city, entity_id:entityId, shot_tags:[shotTag],
    };
    if (!await saveDraft(taskId, {refresh:false, notify:false})) return;
    const asset = await api("/api/library/assets", {method:"POST", body:JSON.stringify(payload)});
    applyUploadedLibraryBinding(shotId, asset);
    await loadTaskLibrary(state.activeTask);
    showToast("素材已入库并绑定镜头。请在素材库补齐许可并审核。");
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; }
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
