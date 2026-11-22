const state = {
  catalog: null, systemConfig: null, latestTaskId: null, recentTaskId: null,
  view: "create", taskDetail: false, pollTimer: null, taskRequest: 0,
  projects: [], trashedProjects: [], projectRequest: 0,
  activeTask: null,
  libraryAssets: [], publicationLibraryAssets: [], libraryRequest: 0, musicLibrary: null,
};
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

function fillChoice(name, values, fallback = "", optional = false, allowCustom = true) {
  const field = $(`#create-form [name='${name}']`);
  const current = field.value || fallback;
  const customOption = allowCustom ? '<option value="__custom__">自定义…</option>' : "";
  field.innerHTML = (optional ? '<option value="">未指定</option>' : "") + choiceOptions(values, current === "__custom__" ? "" : current) + customOption;
  field.value = current;
}

function createChoiceValue(name) {
  const field = $(`#create-form [name='${name}']`);
  if (field.value !== "__custom__") return field.value;
  // 风格与背景音乐只允许固定选项，没有自定义输入框；这里兜住旧缓存或手改 DOM 的情况。
  const custom = $(`[data-custom-for='${name}']`);
  return custom ? custom.value.trim() : field.value;
}

function voiceChoiceOptions(selected) {
  const voices = [
    ...Object.entries(state.catalog?.bank_voices || {}).map(([voice,label]) => ({voice,label,engine:"voicebank"})),
    ...(state.catalog?.voices || ["晓晓·温暖女声", "无配音"]).filter(voice => voice !== "无配音").map(voice => ({voice, label:voice, engine:"edge"})),
    {voice:"Windows 系统语音", label:"Windows 系统语音（本地）", engine:"windows"},
    {voice:"上传配音音频", label:"上传配音音频（按镜头绑定）", engine:"upload"},
    {voice:"无配音", label:"无配音", engine:"configured"},
  ];
  if (selected === "请选择新音色") voices.push({voice:selected,label:selected,engine:"reselect"});
  if (selected && !voices.some(item => item.voice === selected)) voices.push({voice:selected,label:selected,engine:"configured"});
  return voices.map(item => `<option value="${escapeHtml(item.voice)}" data-engine="${item.engine}" ${item.voice === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
}

function syncCreateVoice() {
  const form = $("#create-form");
  const engine = form.elements.voice.selectedOptions[0]?.dataset.engine || "configured";
  form.elements.voice_pitch.disabled = engine !== "edge";
  $("#create-voice-note").textContent = engine === "voicebank" ? "使用服务端配置的声库音色。" : engine === "upload" ? "生成脚本后，在配音工作台按镜头上传音频。" : engine === "windows" ? "使用本机系统语音。" : form.elements.voice.value === "无配音" ? "仅保留画面和所选背景音乐。" : "联网神经音色，可在脚本预览后试听和调校。";
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
  // 风格与背景音乐必须是曲库/知识库认识的标准值，不给「自定义…」，
  // 否则手填「国风」这类词会导致选曲失败。
  fillChoice("style", state.catalog.styles, "诗意纪实", false, false);
  $("#create-form [name='voice']").innerHTML = voiceChoiceOptions($("#create-form [name='voice']").value);
  fillChoice("music_mood", state.catalog.music_moods, "无音乐", false, false);
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
  // 时长是自由输入，快捷档只是把常用值填进去，不锁死选项。
  $$("[data-duration-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = $("#create-form [name='duration']");
      if (!input) return;
      input.value = button.dataset.durationPreset;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
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

const ONLINE_ASSET_SOURCES = new Set(["pixabay", "pexels", "stock"]);
const ASSET_ALIASES = {
  pixabay: "stock", pexels: "stock", library: "local-library",
  ai: "ai-video", cloud: "ai-video", "cloud-i2v": "ai-video",
  "cloud-t2i": "ai-image", "cloud-t2v": "ai-t2v",
};

function visibleAssetSource(source) {
  return ASSET_ALIASES[source] || source;
}

function assetProviderOptions(source) {
  const providers = state.systemConfig?.providers?.asset || [
    { name: "local-library", label: "本地素材库", available: true },
    { name: "upload", label: "逐镜头上传", available: true },
    { name: "stock", label: "在线素材", available: true },
    { name: "pexels", label: "Pexels 视频优先素材", available: true },
    { name: "offline", label: "技术测试场景图", available: true },
  ];
  const selected = visibleAssetSource(source);
  const stock = {name: "stock", label: "在线素材", available: providers.some(item => ONLINE_ASSET_SOURCES.has(item.name) && item.available)};
  const options = [{name: "configured", label: "使用服务端配置", available: true}];
  for (const item of providers) {
    if (!ONLINE_ASSET_SOURCES.has(item.name)) options.push(item);
    else if (!options.includes(stock)) options.push(stock);
  }
  return options.map(({name, label, available}) =>
    `<option value="${escapeHtml(name)}"${name === selected ? " selected" : ""}${available ? "" : " disabled"}>${escapeHtml(label)}${available ? "" : "（未配置）"}</option>`
  ).join("");
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
  const field = $(`.draft-editor [data-input-field="${name}"]`);
  return field?.dataset.savedSource || field?.value || "";
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

let providerSettings = null;
let providerSettingsLoading = false;

function setProviderSettingsStatus(message, error = false) {
  const status = $("#provider-settings-status");
  status.textContent = message;
  status.classList.toggle("provider-settings-error", error);
}

function providerField(field) {
  const name = escapeHtml(field.name);
  const disabled = field.locked ? " disabled" : "";
  const attrs = `data-provider-field="${name}"${disabled}`;
  const hint = field.locked ? "从进程环境读取"
    : field.kind === "password" ? field.configured ? "已配置" : "尚未配置" : "";
  const caption = `<span class="provider-field-label">${escapeHtml(field.label)}${hint ? `<small>${hint}</small>` : ""}</span>`;
  if (field.kind === "password") {
    const placeholder = field.configured ? "留空以保留" : "粘贴服务商提供的 KEY";
    return `<div class="provider-secret-field">
      <label for="provider-${name}">${caption}</label>
      <div class="provider-secret-control">
        <input id="provider-${name}" type="password" ${attrs} placeholder="${placeholder}" autocomplete="new-password" spellcheck="false" autocapitalize="none" />
        <button type="button" class="text-button" data-show-provider-key="${name}" aria-controls="provider-${name}" aria-pressed="false" aria-label="显示 ${name}">显示</button>
      </div>
    </div>`;
  }
  const value = escapeHtml(field.value ?? "");
  let control;
  if (field.kind === "catalog") {
    control = `<textarea ${attrs} rows="2" spellcheck="false">${value}</textarea>`;
  } else if (field.kind === "select") {
    const choices = [...new Set([...field.choices, field.value])];
    control = `<select ${attrs}>${choiceOptions(choices, field.value)}</select>`;
  } else {
    control = `<input type="${field.kind}" ${attrs} autocomplete="off" value="${value}" spellcheck="false" autocapitalize="none" />`;
  }
  return `<label>${caption}${control}</label>`;
}

function renderProviderSettings(config) {
  providerSettings = config;
  $("#provider-settings-fields").innerHTML = config.groups.map(group => `<fieldset>
    <legend>${escapeHtml(group.title)}</legend>
    ${group.fields.map(providerField).join("")}
    ${group.title === "Pixabay" ? '<p class="section-note">使用 Pixabay 官方 API。</p>' : ""}
  </fieldset>`).join("");
  for (const field of config.groups.flatMap(group => group.fields)) {
    if (field.kind === "password") $(`#provider-${field.name}`).value = field.value || "";
  }
  const note = $("#provider-settings-note");
  note.textContent = config.local_speech ? "配音声库当前连接本机 Windows 语音接口，也可以填写第三方兼容 TTS 地址。" : "";
  note.hidden = !config.local_speech;
}

function readCurrentProviderSettings() {
  return api("/api/provider-settings/current", {method: "POST", body: "{}"});
}

function setProviderKeyVisibility(button, visible) {
  const name = button.dataset.showProviderKey;
  $(`#provider-${name}`).type = visible ? "text" : "password";
  button.textContent = visible ? "隐藏" : "显示";
  button.setAttribute("aria-pressed", String(visible));
  button.setAttribute("aria-label", `${button.textContent} ${name}`);
}

async function loadProviderSettings() {
  if (providerSettings || providerSettingsLoading) return;
  providerSettingsLoading = true;
  try {
    renderProviderSettings(await readCurrentProviderSettings());
    $("#provider-settings-save").disabled = false;
    setProviderSettingsStatus("");
  } catch (error) {
    setProviderSettingsStatus(error.message, true);
  } finally {
    providerSettingsLoading = false;
  }
}

async function saveProviderSettings(event) {
  event.preventDefault();
  const button = $("#provider-settings-save");
  if (!providerSettings || button.disabled) return;
  const updates = {};
  for (const field of providerSettings.groups.flatMap(group => group.fields)) {
    if (field.locked) continue;
    const control = $(`[data-provider-field="${field.name}"]`);
    const value = control.value.trim();
    const changed = value !== (field.value ?? "");
    if (field.kind === "password" ? value && (changed || control.dataset.keyEdited) : changed) {
      updates[field.name] = value;
    }
  }
  if (!Object.keys(updates).length) {
    setProviderSettingsStatus("配置没有变化。");
    return;
  }
  button.disabled = true;
  const controls = $$("#provider-settings-fields [data-provider-field]:not(:disabled)");
  controls.forEach(input => { input.disabled = true; });
  setProviderSettingsStatus("正在保存…");
  try {
    await api("/api/provider-settings", {method: "POST", body: JSON.stringify(updates)});
    setProviderSettingsStatus("配置已保存并应用。");
    try {
      renderProviderSettings(await readCurrentProviderSettings());
      await loadSystemConfig();
      state.catalog = await api("/api/catalog");
      const voice = $("#create-form [name='voice']");
      voice.innerHTML = voiceChoiceOptions(voice.value);
      syncCreateVoice();
      const health = await api("/api/health");
      $("#provider-chip").textContent = `${health.provider} / ${health.model}`;
    } catch {
      setProviderSettingsStatus("配置已应用。刷新页面可重新读取服务状态。");
    }
  } catch (error) {
    setProviderSettingsStatus(error.message, true);
  } finally {
    controls.forEach(input => { input.disabled = false; });
    button.disabled = false;
  }
}

function setMusicLibraryStatus(message, error = false) {
  const status = $("#music-library-status");
  status.textContent = message;
  status.classList.toggle("provider-settings-error", error);
}

async function loadMusicLibrary() {
  try {
    const {items, max_bytes} = await api("/api/music-library");
    $("#library-audio-file").dataset.maxBytes = max_bytes;
    $("#music-library-count").textContent = items.length;
    $("#music-library-list").innerHTML = items.map(track => `<li>
      <strong>${escapeHtml(track.title)}</strong>
      <span>${escapeHtml(track.moods.join("、"))} · ${escapeHtml(track.styles.join("、"))}</span>
      <small>${escapeHtml(track.author)} · ${escapeHtml(track.license)} · ${escapeHtml(track.usage_scope)}</small>
    </li>`).join("") || "<li>曲库还没有可用曲目。</li>";
  } catch (error) {
    setMusicLibraryStatus(error.message, true);
  }
}

async function saveMusicLibraryTrack(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("#music-library-import");
  if (button.disabled) return;
  const input = $("#library-audio-file");
  const file = input.files[0];
  if (!file) return;
  const maxBytes = Number(input.dataset.maxBytes) || 25 * 1024 * 1024;
  if (file.size > maxBytes) {
    setMusicLibraryStatus("曲库单个音频文件最大为 25 MB。", true);
    return;
  }
  const values = Object.fromEntries(new FormData(form).entries());
  button.disabled = true;
  setMusicLibraryStatus("正在验证并导入…");
  try {
    const track = await api("/api/music-library", {
      method: "POST",
      body: JSON.stringify({...values, name:file.name, data_base64:await readFileAsDataUrl(file)}),
    });
    form.reset();
    setMusicLibraryStatus(`已导入《${track.title}》。`);
    $(".music-library-items").open = true;
    await loadMusicLibrary();
    loadSystemConfig().catch(() => {});
  } catch (error) {
    setMusicLibraryStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
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
     <label>来源链接<input id="asset-source-url" type="url" maxlength="500" /></label>
     <label>作者<input id="asset-author" maxlength="500" /></label>
     <label>授权方<input id="asset-grantor" maxlength="500" /></label>
     <label>授权条款<input id="asset-license" maxlength="500" /></label>
     <label>使用范围<input id="asset-scope" maxlength="500" /></label>
     <label>授权凭证<input id="asset-authorization-ref" maxlength="500" /></label>
     <button id="upload-asset" data-upload-task="${escapeHtml(task.id)}">上传素材</button>`
  );
}

function voiceAttachPanel(task) {
  return attachPanel(
    "voice-upload",
    task,
    "上传配音音频",
    "选择镜头并上传录音或已制作的配音。上传模式需每镜绑定；使用合成声库时，仅覆盖已绑定的镜头。",
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
    `<label>授权说明<input id="music-license" placeholder="授权协议或许可来源" /></label>
     <label>作者<input id="music-author" placeholder="音乐作者" /></label>
     <label>使用范围<input id="music-scope" placeholder="留空按完整授权记录" /></label>
     <label>授权凭证编号或链接<input id="music-authorization-ref" maxlength="200" /></label>
     <label>选择音频<input id="music-file" type="file" accept="audio/*" /></label>
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
      <label class="source-picker">素材来源<select ${draft("asset_source")}${visibleAssetSource(input.asset_source) !== input.asset_source ? ` data-saved-source="${escapeHtml(input.asset_source)}"` : ""}>${assetProviderOptions(input.asset_source)}</select></label>
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
      <label>主体锁定<textarea ${draft("subject_lock")} placeholder="同一座断桥、同一件器物或同一位人物的固定外观">${escapeHtml(input.subject_lock || "")}</textarea><small class="field-note">逐镜复用主体描述；填写参考图路径时可进一步启用图像条件。</small></label>
      <label>主体参考图路径<input ${draft("subject_reference_path")} value="${escapeHtml(input.subject_reference_path || "")}" placeholder="可选：本地图片绝对路径" /></label>
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
    <label>连续主体组（图生视频）<input data-shot-field="subject_group" data-shot-id="${escapeHtml(shot.id)}" maxlength="80" value="${escapeHtml(shot.subject_group || "")}" placeholder="相同主体填写相同组名" /></label>
    <label>组内参考图（每组一张）<input data-reference-file type="file" accept="image/jpeg,image/png" /></label>
    <input type="hidden" data-shot-field="subject_reference" data-shot-id="${escapeHtml(shot.id)}" value="${escapeHtml(shot.subject_reference || "")}" />
    <small data-ref-status>${shot.subject_reference ? "已绑定参考图" : ""}</small><button type="button" data-clear-reference>解除参考图</button>
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
  return `<div class="asset-list">${task.assets.map((asset) => `<div class="asset-row"><span>镜头 ${escapeHtml(asset.shot_id || "-")}</span><strong>${escapeHtml(asset.name)}</strong><small>${escapeHtml(asset.source)} / ${escapeHtml(asset.media_type || "未标注")} / ${escapeHtml(asset.author)} / ${escapeHtml(asset.license)} ${asset.page_url?.startsWith("https://") ? `<a href="${escapeHtml(asset.page_url)}" target="_blank" rel="noopener">原始素材</a>` : ""}</small></div>`).join("")}</div>`;
}

const RECORD_FIELDS = [
  ["city", "城市"], ["landmark", "核心地标"], ["theme", "传播主题"],
  ["culture", "文化线索"], ["festival", "节庆 / 活动"], ["audience", "目标受众"], ["style", "叙事风格"],
  ["duration", "成片时长", value => `${value} 秒`],
  ["shot_count", "镜头数量", value => Number(value) === 0 ? "自动匹配" : `${value} 个镜头`],
  ["asset_source", "素材来源", recordSourceLabel],
  ["output_resolution", "输出规格", value => String(value).replace("x", " × ")],
  ["fps", "帧率", value => `${value} fps`],
  ["voice", "配音音色", value => state.catalog?.bank_voices?.[value] || value],
  ["voice_engine", "声音来源", value => ({
    edge: "Edge 神经音色", windows: "Windows 系统语音", upload: "上传配音音频", xstudio: "X Studio 导入",
    configured: "跟随服务端配置", voicebank: "服务端声库", tianyi: "历史天依声库", reselect: "请选择新音色",
  })[value] || value],
  ["voice_rate", "配音语速", value => `${value} 倍`], ["voice_pitch", "配音音高", value => `${value} Hz`],
  ["voice_style", "全局语气", value => state.catalog?.voice_styles?.[value]?.label || value],
  ["voice_expressiveness", "语气强度"], ["voice_volume", "配音音量"],
  ["music_mood", "背景音乐"], ["music_volume", "音乐音量"], ["music_ducking", "旁白出现时的音乐"],
  ["include_subtitles", "烧录字幕", value => value ? "开启" : "关闭"],
  ["include_ai_label", "保留 AIGC 标识", value => value ? "开启" : "关闭"],
  ["custom_brief", "补充要求"],
];

function recordSourceLabel(source) {
  if (source === "stock") return "在线素材";
  const labels = { configured: "使用服务端配置", pixabay: "Pixabay（历史选项）", pexels: "Pexels（历史选项）" };
  return labels[source] || state.systemConfig?.providers?.asset.find(item => item.name === visibleAssetSource(source))?.label || source;
}

function taskRecordFields(input) {
  return `<dl>${RECORD_FIELDS.map(([key, label, format]) => {
    const value = input[key];
    const display = value == null ? "未记录" : value === "" ? "未指定" : format ? format(value) : value;
    return `<div data-record-field="${key}"${key === "custom_brief" ? ' class="record-brief"' : ""}><dt>${label}</dt><dd>${escapeHtml(display)}</dd></div>`;
  }).join("")}</dl>`;
}

function taskRecordMarkup(input, initial) {
  const changed = initial && RECORD_FIELDS.some(([key]) => input[key] !== initial[key]);
  const title = changed ? "最后保存的创作参数" : initial ? "初次提交的创作参数" : "已保存的创作参数";
  return `<section class="task-record" aria-labelledby="task-record-title">
    <h5 id="task-record-title">${title}</h5>
    ${taskRecordFields(input)}
    ${changed ? `<details><summary>查看初次提交的参数</summary>${taskRecordFields(initial)}</details>` : ""}
  </section>`;
}

const SHOT_REVIEW_LABELS = {
  pending: "待核验", match: "贴合", partial: "部分贴合", mismatch: "不贴合", unverifiable: "地点或要素无法确认",
};
const REVIEW_SUMMARY_LABELS = { needs_revision: "有问题待修订", pending: "待核验", needs_verification: "有要素待确认", passed: "核验通过" };
const SHOT_TECH_LABELS = { complete: "取样齐全", near_black: "含近黑样本", outside_final: "成片时长内无画面", sample_error: "取样失败" };

function visualReviewMarkup(task) {
  const review = task.visual_review;
  if (!review) return task.status === "completed" ? "<p>逐镜画面证据暂不可用。</p>" : "";
  const done = review.shots.filter(shot => shot.decision.status !== "pending").length;
  return `<details class="delivery-review">
    <summary>逐镜画面核验 · 已核验 ${done}/${review.shots.length} · ${escapeHtml(REVIEW_SUMMARY_LABELS[review.summary?.status] || "")}</summary>
    ${review.delivery ? `<p>成片画幅 · ${review.delivery.width} × ${review.delivery.height}${review.delivery.vertical ? " · 竖屏" : " · 非竖屏"}</p>` : ""}
    ${review.shots.map(shot => `<details class="delivery-shot">
      <summary>${shot.order}. ${escapeHtml(shot.title)} · ${escapeHtml(SHOT_REVIEW_LABELS[shot.decision.status])} · ${escapeHtml(SHOT_TECH_LABELS[shot.technical])}</summary>
      <p>旁白 · ${escapeHtml(shot.narration)}</p><p>预期画面 · ${escapeHtml(shot.visual_prompt)}</p>
      ${shot.targets.map(target => `<p>核对条目 · ${escapeHtml(target.name)} · ${(target.sources || []).map(source => `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(source.title)}</a>`).join("、")}</p>`).join("")}
      <div class="review-frames">${shot.samples.map(frame => `<figure><a href="${escapeHtml(frame.url)}" target="_blank" rel="noopener"><img src="${escapeHtml(frame.url)}" loading="lazy" alt="镜头 ${shot.order} 的取样画面" /></a><figcaption>${frame.second.toFixed(2)} 秒${frame.near_black ? " · 近黑" : ""}</figcaption></figure>`).join("")}</div>
      <form data-review-shot="${escapeHtml(shot.shot_id)}">
        <label>结论<select name="status"><option value="">选择结论</option>${Object.entries(SHOT_REVIEW_LABELS).map(([key, label]) => key === "pending" ? "" : `<option value="${key}" ${key === shot.decision.status ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <label>核验记录<textarea name="note" maxlength="1000" required>${escapeHtml(shot.decision.note || "")}</textarea></label>
        <label>核验人<input name="reviewer" maxlength="80" value="${escapeHtml(shot.decision.reviewer || "")}" /></label>
        <button class="text-button" type="submit">保存核验</button><span role="status"></span>
      </form>
    </details>`).join("")}
  </details>`;
}

function billingMarkup(task) {
  const billing = task.cost?.billing;
  if (!billing?.entries.length) return "";
  const totals = Object.entries(billing.actual_by_currency).map(([unit, amount]) => `${unit} ${amount}`).join("、");
  const payable = billing.entries.filter(row => row.billing_status !== "no_external_charge");
  return `<details class="delivery-review"><summary>费用台账 · ${billing.pending_count} 项待对账</summary>
    <p>${totals ? "账单确认金额 · " + escapeHtml(totals) : (payable.length ? "暂无账单确认金额" : "已记录步骤无外部服务费用")}</p>
    ${payable.length ? `<form data-cost-reconcile>
      <label>调用记录<select name="entry_id">${payable.map(row => `<option value="${escapeHtml(row.id)}">${escapeHtml([row.slot, row.provider, row.model, row.shot_id, row.request_id || row.remote_task_id || row.id, row.billing_status === "reconciled" ? "已对账" : "待对账"].filter(Boolean).join(" · "))}</option>`).join("")}</select></label>
      <label>账单币种<input name="currency" value="CNY" maxlength="3" required /></label>
      <label>账单金额<input name="amount" inputmode="decimal" required /></label>
      <label>账单编号或凭证链接<input name="reference" maxlength="500" required /></label>
      <button class="text-button" type="submit">保存对账</button><span role="status"></span>
    </form>` : ""}
  </details>`;
}

function bindDeliveryForms(task) {
  $$("[data-review-shot], [data-cost-reconcile]").forEach(form => form.addEventListener("submit", async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const shot = form.dataset.reviewShot;
    const suffix = shot ? "visual-review" : "cost";
    if (shot) Object.assign(values, {shot_id: shot, video_sha256: task.visual_review.video_sha256});
    const status = form.querySelector('[role="status"]');
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await api(`/api/tasks/${encodeURIComponent(task.id)}/${suffix}`, {method: "POST", body: JSON.stringify(values)});
      const open = [...$("#active-task").querySelectorAll("details")].map(detail => detail.open);
      renderTask(await api(`/api/tasks/${encodeURIComponent(task.id)}`));
      $("#active-task").querySelectorAll("details").forEach((detail, index) => { detail.open = Boolean(open[index]); });
      showToast("记录已保存。");
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }));
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
  const record = taskRecordMarkup(task.input || {}, task.initial_input);
  // Old drafts retain their uploaded audio when the retired source is opened.
  if (task.input?.voice_engine === "xstudio") task = {...task, input:{...task.input, voice_engine:"upload", voice:"上传配音音频"}};
  if (task.input?.voice_engine === "tianyi") task = {...task, input:{...task.input, voice_engine:"reselect", voice:"请选择新音色"}};
  state.latestTaskId = task.id;
  state.activeTask = task;
  const error = task.error ? `<div class="error-box"><strong>任务失败</strong><p>${escapeHtml(task.error)}</p><button class="text-button" data-retry="${escapeHtml(task.id)}">从脚本阶段重试</button></div>` : "";
  const render = task.render || {};
  const mediaLinks = render.video_url ? `<a href="${render.video_url}" target="_blank">打开 MP4</a><a href="${render.video_url}" download>下载 MP4</a><a href="${render.subtitle_url}" target="_blank">下载 SRT</a>${task.audio?.voice_url ? `<a href="${task.audio.voice_url}" target="_blank">试听配音</a>` : ""}${task.audio?.music_url ? `<a href="${task.audio.music_url}" target="_blank">试听音乐</a>` : ""}` : "";
  const links = `<div class="task-links"><a href="/api/tasks/${encodeURIComponent(task.id)}/export?format=md" download title="导出已保存版本；未保存的修改请先保存">导出文案</a>${mediaLinks}</div>${render.video_url ? `<video class="result-video" controls playsinline src="${render.video_url}"></video>` : ""}`;
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
  $("#active-task").innerHTML = `<div class="task-progress"><div class="task-meta"><div><h4>${escapeHtml(task.title || "脚本预览")}</h4><p>${escapeHtml(task.summary || "请审核脚本和分镜后生成。")} / ${escapeHtml(task.status)} / ${escapeHtml(task.id)}</p></div><strong>${task.progress}%</strong></div><div class="progress-bar"><span style="width:${task.progress}%"></span></div><div class="pipeline">${stageMarkup(task)}</div>${record}${error}${draft}${links}<div class="task-links"><a href="/api/tasks/${encodeURIComponent(task.id)}/ledger" download>下载任务底账</a></div>${visualReviewMarkup(task)}${billingMarkup(task)}${(task.audio?.subject_reviews || []).map(s => s.url ? `<a href="${escapeHtml(s.url)}" target="_blank">复核 ${escapeHtml(s.group)} · 首中尾帧</a>` : escapeHtml(s.error)).join(" ")}${task.audio?.subtitle_alignment ? '<p>字幕对齐 · ' + task.audio.subtitle_alignment.segments.map(s=>escapeHtml(s.shot_id + " " + (s.source === "sentence" ? "句级（"+s.reason+"）" : s.source))).join(" / ") + '</p>' : ""}<p>${escapeHtml(task.audio?.music_notice || (task.audio?.music_match?.id ? (task.audio.music_match.title || task.audio.music_match.id) + " · " + task.audio.music_match.license : ""))}</p><div class="asset-summary"><strong>素材记录</strong>${assetList(task)}</div>${visualQaMarkup(task)}<div class="trace-line">文本 provider: ${escapeHtml(task.trace?.provider || "pending")} / 模型: ${escapeHtml(task.trace?.model || "pending")} / fallback: ${String(Boolean(task.trace?.fallback_used))}<br />配音: ${escapeHtml(task.audio?.voice_provider || "pending")} / 音乐: ${escapeHtml(task.audio?.music_provider || "pending")} / 音频: ${render.has_audio ? "有" : "无"} / 烧录字幕: ${render.has_burned_subtitles ? "有" : "无"}<br />Provider 报告成本: ${Number(task.cost?.total || 0).toFixed(4)}（${escapeHtml(task.cost?.unit || "等待统计")}）</div></div>`;
  $$("[data-retry]").forEach((button) => button.addEventListener("click", () => retryTask(button.dataset.retry)));
  $$("[data-generate-task]").forEach((button) => button.addEventListener("click", () => generateTask(button.dataset.generateTask)));
  $$("[data-save-draft]").forEach((button) => button.addEventListener("click", () => saveDraft(button.dataset.saveDraft)));
  $$("[data-reference-file]").forEach(field => field.addEventListener("change", async () => {
    const row = field.closest(".shot-editor");
    try {
      const file = field.files[0];
      if (!file) return;
      if (file.size>12*1024*1024) throw new Error("请选择 12 MB 以内的图片");
      const response = await api(`/api/tasks/${task.id}/subject-reference`, {method:"POST",body:JSON.stringify({data_base64:await readFileAsDataUrl(file)})});
      row.querySelector('[data-shot-field="subject_reference"]').value = response.reference;
      row.querySelector("[data-ref-status]").textContent = "已选择，保存草稿后生效";
    } catch (error) {showToast(error.message);}
  }));
  $$("[data-clear-reference]").forEach(button => button.onclick = () => {
    const row = button.closest(".shot-editor");
    row.querySelector('[data-shot-field="subject_reference"]').value = "";
    row.querySelector("[data-reference-file]").value = "";
    row.querySelector("[data-ref-status]").textContent = "";
  });
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
      const field = event.target;
      if (field.dataset.inputField === "asset_source") delete field.dataset.savedSource;
      if (field.dataset?.inputField) syncAttachPanels();
      if (["entity_id", "shot_tag"].includes(field.dataset?.shotField) || field.dataset?.inputField === "visual_usage") refreshShotLibraryOptions();
    });
  }
  syncAttachPanels();
  bindVoiceStudio(task);
  bindDeliveryForms(task);
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
      let value = field.type === "checkbox" ? field.checked : field.dataset.savedSource || field.value;
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
  try { await api(`/api/tasks/${taskId}/generate`, { method: "POST", body: "{}" }); showToast("实际视频生成已开始。"); watchTask(taskId, true); } catch (error) { showToast(error.message); }
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
      license:$("#asset-license")?.value?.trim() || "",
      author:$("#asset-author")?.value?.trim() || "",
      usage_scope:$("#asset-scope")?.value?.trim() || "",
      source_url:$("#asset-source-url")?.value?.trim() || "",
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
      payload.mood = draftFieldValue("music_mood").trim();
      payload.license = $("#music-license")?.value?.trim() || "";
      payload.author = $("#music-author")?.value?.trim() || "";
      payload.usage_scope = $("#music-scope")?.value?.trim() || "";
      payload.authorization_ref = $("#music-authorization-ref")?.value?.trim() || "";
    }
    if (!await saveDraft(taskId)) return;
    await api("/api/uploads", { method: "POST", body: JSON.stringify(payload) });
    showToast("音频已上传。");
    renderTask(await api(`/api/tasks/${taskId}`));
  } catch (error) { showToast(error.message); }
}

function stopTaskPolling() {
  clearTimeout(state.pollTimer);
  return ++state.taskRequest;
}

function watchTask(taskId, notify = false) {
  return pollTask(taskId, stopTaskPolling(), notify);
}

async function pollTask(taskId, request, notify) {
  try {
    const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (request !== state.taskRequest) return;
    renderTask(task);
    if (["queued", "running"].includes(task.status)) {
      state.pollTimer = window.setTimeout(() => pollTask(taskId, request, notify), 1000);
    } else if (task.status === "completed" && notify) {
      await loadProjects();
      if (request === state.taskRequest) showToast("实际 MP4 已生成，请检查画面、音频、字幕和素材授权。");
    }
  } catch (error) {
    if (request !== state.taskRequest) return;
    $("#active-task").innerHTML = `<p class="task-load-error" role="alert">${escapeHtml(error.message)}</p>`;
    showToast(error.message);
  }
}

async function createTask(event) {
  event.preventDefault();
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  button.querySelector("span").textContent = "正在生成脚本...";
  try { const task = await api("/api/previews", { method: "POST", body: JSON.stringify(formData()) }); stopTaskPolling(); state.recentTaskId = task.id; renderTask(task); showToast(task.status === "failed" ? task.error : "脚本和分镜已生成，请审核后确认。"); } catch (error) { showToast(error.message); } finally { button.disabled = false; button.querySelector("span").textContent = "生成脚本预览"; }
}

async function retryTask(taskId) {
  try { await api(`/api/tasks/${taskId}/generate`, { method: "POST", body: "{}" }); watchTask(taskId, true); } catch (error) { showToast(error.message); }
}

function projectCard(task, trashed = false) {
  const id = escapeHtml(task.id);
  const title = escapeHtml(task.title || task.input.theme);
  const poster = task.render?.cover_url;
  const busy = ["queued", "running"].includes(task.status)
    || (task.status === "draft" && ["preview", "scripting"].includes(task.stage) && task.progress < 35);
  const actions = trashed ? `<button type="button" data-restore-task="${id}">恢复任务</button>` : `
    ${task.render?.video_url ? `<a href="${escapeHtml(task.render.video_url)}" target="_blank" rel="noopener">打开 MP4</a>` : ""}
    <button type="button" data-open-task="${id}">打开任务</button>
    <button type="button" class="text-button" data-add-observation="${id}">记录平台观测</button>
    <button type="button" class="text-button" data-add-billing="${id}">登记实际账单</button>
    <button type="button" class="project-delete" data-delete-task="${id}" aria-label="删除任务：${title}"
      title="${busy ? "任务正在生成" : "删除任务"}" ${busy ? "disabled" : ""}>
      <svg aria-hidden="true" focusable="false" width="24" height="26" viewBox="0 0 24 26"
        fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2 6h20M9 6V5a3 3 0 0 1 6 0v1M4 6l1.6 17c.05.6.55 1 1.15 1h10.5c.6 0 1.1-.4 1.15-1L20 6M10 11v8m4-8v8" />
      </svg>
    </button>`;
  const {costLabel, durationLabel} = taskMetricsLabels(task);
  return `<article class="project-card">
    ${poster ? `<img src="${escapeHtml(poster)}" alt="" loading="lazy" />` : '<div class="project-placeholder" aria-hidden="true"></div>'}
    <div>
      <span class="status ${task.status === "failed" ? "failed" : ""}">${escapeHtml(task.status.toUpperCase())}</span>
      <h3>${title}</h3>
      <p>${escapeHtml(task.input.city)} / ${escapeHtml(task.input.landmark || "未指定地标")} / ${escapeHtml(task.input.duration)}s
        <br />${trashed ? `移入时间 ${escapeHtml(new Date(task.trashed_at).toLocaleString("zh-CN"))}` : escapeHtml(task.updated_at)}
        ${trashed ? "" : `<br />${escapeHtml(costLabel)}<br />${escapeHtml(durationLabel)}`}</p>
    </div>
    <div class="project-card-actions">${actions}</div>
  </article>`;
}

function renderProjects() {
  state.recentTaskId = state.projects[0]?.id || null;
  $("#project-list").innerHTML = state.projects.map(task => projectCard(task)).join("")
    || '<div class="empty-state">还没有任务。</div>';
  $("#trash-list").innerHTML = state.trashedProjects.map(task => projectCard(task, true)).join("")
    || '<div class="empty-state">回收站为空。</div>';
  $("#trash-count").textContent = state.trashedProjects.length;
}

async function loadProjects() {
  const request = ++state.projectRequest;
  const [projects, trash] = await Promise.all([api("/api/tasks?limit=50"), api("/api/trash")]);
  if (request !== state.projectRequest) return;
  state.projects = projects.items;
  state.trashedProjects = trash.items;
  renderProjects();
}

function confirmDeleteTask(taskId) {
  const task = state.projects.find(item => item.id === taskId);
  const dialog = $("#trash-dialog");
  if (!task || dialog.open) return;
  dialog.dataset.taskId = taskId;
  $("#trash-dialog-task").textContent = task.title || task.input.theme;
  $("#trash-dialog-error").hidden = true;
  dialog.showModal();
}

async function deleteConfirmedTask() {
  const dialog = $("#trash-dialog");
  const yes = $("#trash-confirm");
  const no = $("#trash-cancel");
  if (!dialog.open || yes.disabled) return;
  yes.disabled = no.disabled = true;
  const taskId = dialog.dataset.taskId;
  const message = $("#trash-dialog-error");
  message.hidden = true;
  try {
    const {trashed_at} = await api(`/api/tasks/${encodeURIComponent(taskId)}`, {method: "DELETE"});
    const task = state.projects.find(item => item.id === taskId);
    ++state.projectRequest;
    state.projects = state.projects.filter(item => item.id !== taskId);
    if (task && !state.trashedProjects.some(item => item.id === taskId)) {
      state.trashedProjects.unshift({...task, trashed_at});
    }
    renderProjects();
    dialog.close();
    ($(".project-delete:not(:disabled)") || $(".project-trash summary")).focus({preventScroll: true});
    showToast("任务已移入回收站。");
    loadProjects().catch(() => {});
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
  } finally {
    yes.disabled = no.disabled = false;
  }
}

async function restoreTask(taskId, button) {
  button.disabled = true;
  try {
    await api(`/api/trash/${encodeURIComponent(taskId)}`, {method: "POST"});
    const task = state.trashedProjects.find(item => item.id === taskId);
    ++state.projectRequest;
    state.trashedProjects = state.trashedProjects.filter(item => item.id !== taskId);
    if (task && !state.projects.some(item => item.id === taskId)) {
      state.projects = [...state.projects, task].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 50);
    }
    renderProjects();
    $(".project-trash summary").focus({preventScroll: true});
    showToast("任务已恢复到作品档案。");
    loadProjects().catch(() => {});
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}


// 作品档案顶部的指标总览（PR #3）：成本报告状态、人工登记账单、平台观测。
// 列表本体仍由 renderProjects/projectCard 渲染，这里只画总览。
function renderMetricsOverview(tasks) {
  const overview = $("#metrics-overview");
  if (!overview) return;
  const billingCurrencies = new Map();
  for (const task of tasks) {
    for (const [currency, amount] of Object.entries(task.metrics?.billing?.by_currency || {})) {
      billingCurrencies.set(currency, (billingCurrencies.get(currency) || 0) + Number(amount || 0));
    }
  }
  const billingMarkup = [...billingCurrencies.entries()].sort(([a],[b]) => a.localeCompare(b)).map(([currency, amount]) => `<article class="metric-tile"><span>已记录账单 · ${escapeHtml(currency)}</span><strong>${Number(amount).toFixed(2)} ${escapeHtml(currency)}</strong><small>人工登记金额，不代表已完成对账</small></article>`).join("");
  const unknownCostCount = tasks.filter((task) => task.metrics?.cost?.amount_status !== "reported").length;
  const costMarkup = `<article class="metric-tile"><span>Provider 成本状态</span><strong>${unknownCostCount} / ${tasks.length} 待完整报告</strong><small>未报告或不完整金额按未知处理；不推断为免费</small></article>`;
  const observations = tasks.flatMap((task) => (task.metrics?.publication_observations || []).map((item) => ({...item, title:task.title || task.input?.theme || task.id})));
  const observationMarkup = observations.length ? observations.slice(0, 12).map((item) => `<article class="observation-card"><strong>${escapeHtml(item.platform)} · ${escapeHtml(item.title)}</strong><small>${escapeHtml(item.published_at)} / 发布后 ${Number(item.window_days)} 天观测 / ${escapeHtml(item.observed_at)}</small><a href="${escapeHtml(item.post_url)}" target="_blank" rel="noopener noreferrer">查看原帖</a><div class="observation-metrics">${Object.entries(item.metrics || {}).map(([key,value]) => `<span>${escapeHtml(key)} <b>${Number(value).toLocaleString()}</b></span>`).join("")}</div></article>`).join("") : `<p class="metrics-empty">尚无平台观测数据。需录入平台帖子与带时间窗口的后台指标；未录入不代表 0。</p>`;
  overview.innerHTML = `${costMarkup}${billingMarkup}<div class="observation-list">${observationMarkup}</div>`;
}

function taskMetricsLabels(task) {
  const cost = task.metrics?.cost || {};
  const costLabel = cost.amount_status === "reported" ? `${Number(cost.total).toFixed(4)} ${cost.currency || "币种未声明"} · Provider 报告` : cost.amount_status === "incomplete" ? `部分报告 ${Number(cost.reported_total || 0).toFixed(4)} · 未完整` : "成本未报告";
  const execution = task.metrics?.execution || {};
  const durationLabel = execution.last_seconds == null ? "运行耗时未记录" : `最近运行 ${Number(execution.last_seconds).toFixed(1)} 秒`;
  return {costLabel, durationLabel};
}


async function recordBilling(taskId) {
  const amount = Number(prompt("账单金额（不含币种符号）:"));
  if (!Number.isFinite(amount) || amount < 0) return showToast("金额必须是非负数字。");
  const currency = prompt("币种代码（3 位大写 ISO，如 CNY、USD）:", "CNY");
  if (!currency) return;
  const source = prompt("账单/发票/后台记录来源（建议含单号）:");
  if (!source) return;
  const capturedAt = prompt("记录日期 YYYY-MM-DD:", new Date().toISOString().slice(0, 10));
  if (!capturedAt) return;
  const note = prompt("备注（可选）:", "");
  try {
    await api(`/api/tasks/${encodeURIComponent(taskId)}/billing`, {method:"POST", body:JSON.stringify({amount,currency,source,captured_at:capturedAt,note:note || ""})});
    showToast("人工账单记录已保存，与 provider 报告成本分开列示。");
    await loadProjects();
  } catch (error) { showToast(error.message); }
}

async function recordPublicationObservation(taskId) {
  const platform = prompt("平台 slug: douyin / xiaohongshu / bilibili / youtube / weibo / wechat-channels / other");
  if (!platform) return;
  const postUrl = prompt("公开帖子 URL:");
  if (!postUrl) return;
  const publishedAt = prompt("发布日期 YYYY-MM-DD:");
  if (!publishedAt) return;
  const observedAt = prompt("数据抓取日期 YYYY-MM-DD:");
  if (!observedAt) return;
  const windowDays = Number(prompt("发布后第几天观测 (1–365):", "7"));
  const metricsText = prompt("输入后台可见指标 JSON；只填实际报告值，例如 {\"views\":123,\"likes\":4}。未显示的指标不要填 0:");
  if (!metricsText) return;
  let metrics;
  try { metrics = JSON.parse(metricsText); } catch { return showToast("指标必须是有效 JSON 对象。"); }
  const sourceNote = prompt("数据来源/截图备注（可选）:", "手工读取平台公开页面");
  try {
    await api(`/api/tasks/${encodeURIComponent(taskId)}/publication-observations`, {method:"POST", body:JSON.stringify({platform,post_url:postUrl,published_at:publishedAt,observed_at:observedAt,window_days:windowDays,metrics,source_note:sourceNote || ""})});
    showToast("平台观测已记录；这是描述性数据，不代表因果归因或 ROI。");
    await loadProjects();
  } catch (error) { showToast(error.message); }
}

// 打开一条任务并把 id 写进地址栏。这样刷新、收藏、把链接发给别人，
// 都能直接回到同一条片子，而不是回到空白的创作工作台。
function openTask(taskId, updateUrl = true) {
  if (!taskId) return;
  const hash = `#task=${encodeURIComponent(taskId)}`;
  if (updateUrl && window.location.hash !== hash) history.pushState(null, "", hash);
  showView("create", true);
  $("#active-task").innerHTML = '<p class="empty-state" role="status">正在读取保存的任务参数…</p>';
  $("#task-panel-title").focus({preventScroll: true});
  window.scrollTo(0, 0);
  return watchTask(taskId);
}

function browseView(name) {
  if (state.taskDetail) {
    stopTaskPolling();
    $("#active-task").innerHTML = '<p class="empty-state">提交创作需求后，这里会显示任务进度。</p>';
    state.latestTaskId = null;
  }
  if (window.location.hash.startsWith("#task=")) history.replaceState(null, "", location.pathname + location.search);
  showView(name);
}

function showView(name, taskDetail = false) {
  const changed = state.view !== name || state.taskDetail !== taskDetail;
  state.view = name;
  state.taskDetail = taskDetail;
  $("#new-task-workspace").hidden = taskDetail;
  $("#view-latest").hidden = taskDetail;
  $("#back-to-projects").hidden = !taskDetail;
  $("#task-panel-title").textContent = taskDetail ? "任务详情" : "当前任务";
  $$(".nav-item").forEach((button) => {
    const active = button.dataset.view === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const view = $(`#view-${name}`);
  $$(".view").forEach((section) => {
    section.classList.toggle("active", section === view);
    if (changed) section.classList.remove("entering");
  });
  if (changed) {
    window.scrollTo(0, 0);
    // Re-apply the entrance animation when detail mode changes within the same view.
    void view.offsetWidth;
    view.classList.add("entering");
  }
  const titles = {create: taskDetail ? "任务详情" : "创作工作台", projects: "作品档案", about: "系统与配置"};
  $("#page-title").textContent = titles[name];
  if (name === "projects") loadProjects().catch(error => showToast(error.message));
  if (name === "about") {
    loadProviderSettings();
    loadMusicLibrary();
  } else {
    $$("[data-show-provider-key][aria-pressed='true']").forEach(button => setProviderKeyVisibility(button, false));
  }
}

function restoreLinkedTask() {
  const task = window.location.hash.match(/^#task=([a-z0-9_%-]+)$/i);
  if (task) {
    try { openTask(decodeURIComponent(task[1]), false); }
    catch { showToast("任务链接无效。"); }
  } else if (state.taskDetail) {
    browseView("projects");
  }
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
  const providerForm = $("#provider-settings-form");
  providerForm.addEventListener("submit", saveProviderSettings);
  providerForm.addEventListener("click", (event) => {
    const button = event.target.closest("[data-show-provider-key]");
    if (button) setProviderKeyVisibility(button, button.getAttribute("aria-pressed") !== "true");
  });
  providerForm.addEventListener("input", (event) => {
    if (event.target.dataset.providerField?.endsWith("_API_KEY")) event.target.dataset.keyEdited = "true";
  });
  const moods = state.catalog?.music_moods || ["舒缓", "热烈", "雅致", "轻快", "无音乐"];
  $("#library-mood").innerHTML = choiceOptions(moods.filter(mood => mood !== "无音乐"), "舒缓");
  $("#library-style").innerHTML = '<option value="">全部预设风格</option>'
    + choiceOptions(state.catalog?.styles || ["诗意纪实", "城市漫游", "国风雅韵", "青春活力", "节庆热烈"], "");
  $("#music-library-form").addEventListener("submit", saveMusicLibraryTrack);
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => browseView(button.dataset.view)));
  $("#refresh-button").addEventListener("click", () => window.location.reload());
  $("#view-latest").addEventListener("click", () => state.recentTaskId ? openTask(state.recentTaskId) : showToast("还没有任务。"));
  $("#back-to-projects").addEventListener("click", () => browseView("projects"));
  $("#view-projects").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.openTask) openTask(button.dataset.openTask);
    if (button.dataset.deleteTask) confirmDeleteTask(button.dataset.deleteTask);
    if (button.dataset.restoreTask) restoreTask(button.dataset.restoreTask, button);
    if (button.dataset.addObservation) recordPublicationObservation(button.dataset.addObservation);
    if (button.dataset.addBilling) recordBilling(button.dataset.addBilling);
  });
  $("#trash-confirm").addEventListener("click", deleteConfirmedTask);
  $("#trash-dialog").addEventListener("cancel", (event) => {
    if ($("#trash-confirm").disabled) event.preventDefault();
  });
  bindMusicLibrary();
  window.addEventListener("popstate", restoreLinkedTask);
  restoreLinkedTask();
}

// ---- 曲库浏览 ----
// 曲目标签由后端 catalog 提供，前端只负责展示与筛选，避免两边各写一份词表。
async function loadMusicLibrary() {
  if (state.musicLibrary) return state.musicLibrary;
  const payload = await api("/api/music/library");
  state.musicLibrary = payload;
  return payload;
}

// 曲目风格目前对全部曲目都是「全风格通用」（catalog 里就是这么标的），
// 所以逐首列出 5 个风格只是噪音，这里合并成一个「通用」标签；
// 哪天有曲目真正限定风格，就会自动显示成它自己的标签。
function musicStyleTag(track, library) {
  const all = (library.styles || []).length;
  const own = (track.styles || []).length;
  if (all > 0 && own >= all) return "通用";
  return own ? (track.styles || []).join("、") : "未标风格";
}

function renderMusicList(library, mood) {
  const list = $("#music-list");
  if (!list) return;
  const tracks = (library.tracks || []).filter((track) => {
    if (mood && !(track.moods || []).includes(mood)) return false;
    return true;
  });
  if (!tracks.length) {
    list.innerHTML = '<p class="field-note">没有符合筛选条件的曲目，试试放宽情绪。</p>';
    return;
  }
  list.innerHTML = tracks.map((track) => `
    <div class="music-row">
      <div class="music-row-main">
        <strong>${escapeHtml(track.name)}</strong>
        <span class="music-tags">${(track.moods || []).map((m) => `<em>${escapeHtml(m)}</em>`).join("")}</span>
        <span class="music-tags music-tags-style"><em>${escapeHtml(musicStyleTag(track, library))}</em></span>
      </div>
      <div class="music-row-actions">
        <span class="field-note">${escapeHtml(track.usage_scope || "")}</span>
        <button type="button" class="text-button" data-preview-music="${escapeHtml(track.id)}">试听</button>
        <button type="button" class="text-button" data-pick-music="${escapeHtml(track.id)}">用这首</button>
      </div>
    </div>`).join("");
}

// 试听共用一个 <audio>，避免一屏 47 个音频元素
let musicPreview = null;
let musicPreviewId = "";

function toggleMusicPreview(trackId) {
  const track = (state.musicLibrary?.tracks || []).find((t) => t.id === trackId);
  if (!track) return;
  if (!musicPreview) {
    musicPreview = new Audio();
    musicPreview.addEventListener("ended", () => { musicPreviewId = ""; syncPreviewButtons(); });
  }
  if (musicPreviewId === trackId && !musicPreview.paused) {
    musicPreview.pause();
    musicPreviewId = "";
    syncPreviewButtons();
    return;
  }
  musicPreviewId = trackId;
  musicPreview.src = `/music/${encodeURIComponent(track.file)}`;
  musicPreview.play().catch((error) => {
    musicPreviewId = "";
    syncPreviewButtons();
    showToast(error?.name === "NotAllowedError"
      ? "浏览器拦截了自动播放，再点一次「试听」即可。"
      : "这段音频浏览器放不了，可以下载后本地试听。");
  });
  syncPreviewButtons();
}

function syncPreviewButtons() {
  document.querySelectorAll("[data-preview-music]").forEach((button) => {
    const playing = button.dataset.previewMusic === musicPreviewId;
    button.textContent = playing ? "停止" : "试听";
  });
}

// 点「用这首」= 把该曲目的第一个情绪填进背景音乐下拉框。
// 生成时后端按「情绪 + 叙事风格」再挑具体曲目，所以这里只能定情绪，不能锁定单曲。
function pickMusicTrack(trackId) {
  const library = state.musicLibrary;
  const track = (library?.tracks || []).find((t) => t.id === trackId);
  if (!track) return;
  const moods = (state.catalog?.music_moods || []).filter((m) => m !== "无音乐");
  const mood = (track.moods || []).find((m) => moods.includes(m));
  if (!mood) {
    showToast("这首曲子的情绪不在当前选项里，换一首试试。");
    return;
  }
  const select = document.querySelector("#create-form [name='music_mood']");
  if (select) {
    select.value = mood;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
  if (musicPreview) { musicPreview.pause(); musicPreviewId = ""; }
  $("#music-modal").hidden = true;
  showToast(`背景音乐已设为「${mood}」，生成时会从该情绪的曲目里挑选。`);
}

async function openMusicLibrary() {
  const modal = $("#music-modal");
  if (!modal) return;
  try {
    const library = await loadMusicLibrary();
    const count = $("#music-count");
    if (count) count.textContent = String(library.total || 0);
    const moodSelect = $("#music-filter-mood");
    if (moodSelect && moodSelect.options.length <= 1) {
      (library.moods || []).forEach((m) => moodSelect.add(new Option(m, m)));
    }
    renderMusicList(library, moodSelect?.value || "");
    modal.hidden = false;
  } catch (error) {
    showToast("曲库加载失败，请确认服务已启动。");
  }
}

function bindMusicLibrary() {
  const browse = $("#browse-music");
  if (browse) browse.addEventListener("click", openMusicLibrary);
  const close = $("#music-modal-close");
  if (close) close.addEventListener("click", () => {
    if (musicPreview) musicPreview.pause();
    musicPreviewId = "";
    $("#music-modal").hidden = true;
  });
  const modal = $("#music-modal");
  if (modal) modal.addEventListener("click", (event) => {
    if (event.target !== modal) return;
    if (musicPreview) musicPreview.pause();
    musicPreviewId = "";
    modal.hidden = true;
  });
  const list = $("#music-list");
  if (list) list.addEventListener("click", (event) => {
    const pick = event.target.closest("[data-pick-music]");
    if (pick) { pickMusicTrack(pick.dataset.pickMusic); return; }
    const preview = event.target.closest("[data-preview-music]");
    if (preview) toggleMusicPreview(preview.dataset.previewMusic);
  });
  const moodFilter = $("#music-filter-mood");
  if (moodFilter) moodFilter.addEventListener("change", async () => {
    const library = await loadMusicLibrary();
    renderMusicList(library, moodFilter.value || "");
  });
  // 首次加载就把曲目数填进按钮文案
  loadMusicLibrary().then((library) => {
    const count = $("#music-count");
    if (count) count.textContent = String(library.total || 0);
  }).catch(() => {});
}

boot();
