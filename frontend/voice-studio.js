// One narration source per shot; plans/previews use the same unsaved snapshot.
let voiceStudioRevision = 0;
let voiceStudioTimer;
let voiceStudioBusy = false;
let voiceStudioSelection = "";

function voiceStyleOptions(value, inherit = false) {
  const styles = state.catalog?.voice_styles || {};
  return `${inherit ? `<option value="inherit" ${value === "inherit" ? "selected" : ""}>跟随全局</option>` : ""}${Object.entries(styles).map(([key, item]) => `<option value="${key}" ${value === key ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}`;
}

function voiceStudioMarkup(task) {
  const input = task.input;
  return `<section class="voice-studio" aria-label="配音工作台">
    <div class="voice-studio-heading"><div><span class="section-index">VOICE STUDIO</span><h5>把文字放进声音轨道</h5></div><span class="voice-badge">整句连读 · 轻量调校</span></div>
    <div class="voice-library">${(state.catalog?.voice_library || []).map(item => `<button type="button" class="voice-card" data-voice-preset="${escapeHtml(item.voice)}" data-voice-style="${item.style}"><strong>${escapeHtml(item.voice)}</strong><small>${escapeHtml(item.description)}</small><span>联网神经音色</span></button>`).join("")}
      <button type="button" class="voice-card" data-tianyi><strong>天依 · 本地声库</strong><small>文本直接合成 · 无需导入音频</small><span>${state.catalog?.tianyi?.available ? "已安装 · 社区音色模型" : "待安装 · 社区音色模型"}</span></button>
      <button type="button" class="voice-card voice-external" data-voice-upload><strong>上传配音音频</strong><small>使用录音或已制作的配音</small><span>按镜头绑定音频</span></button>
    </div>
    <div class="review-settings-grid">
      <label>声音来源<select data-input-field="voice_engine">${[["configured","跟随服务端配置"],["tianyi","天依本地社区声库"],["edge","神经音色（联网）"],["windows","Windows 系统语音（本地）"],["upload","上传配音音频"]].map(([key,label])=>`<option value="${key}" ${input.voice_engine===key ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label>全局语气<select data-input-field="voice_style">${voiceStyleOptions(input.voice_style || "natural")}</select></label>
      <label>自动语气强度<input data-input-field="voice_expressiveness" type="number" min="0" max="1" step="0.1" value="${input.voice_expressiveness ?? 0.5}" /></label>
    </div>
    <p class="section-note">晓晓等神经音色需要联网；天依声库在本机合成。自动语气规划随引擎能力应用，句内声调由语音模型处理。0 为关闭自动变化，局部手动设置仍生效。</p>
    <div class="voice-source-note" id="voice-source-note"></div>
    <div class="voice-toolbar"><button type="button" data-voice-preview="all">合成整轨试听</button><button type="button" data-voice-preview="selected">试听选中片段</button><button type="button" data-voice-script>下载轨道文本</button><span id="voice-timing">时间轴：分镜预估</span></div>
    <div class="voice-timeline" id="voice-timeline" aria-label="旁白片段时间轴">${task.shots.map(shot=>`<button type="button" data-select-voice="${escapeHtml(shot.id)}" style="flex:${Number(shot.duration_sec)||1}"><small>镜头 ${shot.order}</small><span>${escapeHtml(shot.narration)}</span></button>`).join("")}</div>
    <div id="voice-parameter-lane" class="voice-parameter-lane" aria-live="polite"></div>
    ${task.shots.map(shot=>`<div class="voice-clip-editor" data-voice-clip="${escapeHtml(shot.id)}" hidden>
      <div class="voice-clip-heading"><strong>片段 ${shot.order} · ${escapeHtml(shot.title)}</strong><small>${shot.audio_local_path ? "已绑定导入音频；修改文字不会改变原录音" : "文字自动来自本镜头旁白"}</small>${shot.audio_local_path ? `<button type="button" class="text-button" data-clear-voice="${escapeHtml(shot.id)}">解除音频绑定</button>` : ""}</div>
      <label>旁白文本<textarea data-shot-field="narration" data-shot-id="${escapeHtml(shot.id)}">${escapeHtml(shot.narration)}</textarea></label>
      <div class="review-settings-grid">
        <label>片段语气<select data-shot-field="voice_style" data-shot-id="${escapeHtml(shot.id)}">${voiceStyleOptions(shot.voice_style || "inherit", true)}</select></label>
        <label>局部语速倍率<input type="number" min="0.75" max="1.25" step="0.01" data-shot-field="voice_rate_scale" data-shot-id="${escapeHtml(shot.id)}" value="${shot.voice_rate_scale ?? 1}" /></label>
        <label>局部音高偏移 (Hz)<input type="number" min="-20" max="20" step="1" data-shot-field="voice_pitch_offset" data-shot-id="${escapeHtml(shot.id)}" value="${shot.voice_pitch_offset ?? 0}" /></label>
        <label>片段后停顿（秒）<input type="number" min="0" max="1.5" step="0.05" placeholder="自动：0.15" data-shot-field="voice_pause_after" data-shot-id="${escapeHtml(shot.id)}" value="${shot.voice_pause_after ?? ""}" /></label>
      </div>
    </div>`).join("")}
    <div class="voice-preview-status" id="voice-preview-status" role="status">试听仅生成配音，不生成画面，也不会保存草稿改动。</div>
    <audio id="voice-preview-player" controls preload="none" hidden></audio>
    <a id="voice-download" hidden download>下载本次试听 WAV</a>
  </section>`;
}

function collectVoiceSnapshot() {
  const input = {};
  $$("[data-input-field]").forEach(field => {
    const value = field.type === "checkbox" ? field.checked : field.type === "number" ? Number(field.value) : field.value;
    input[field.dataset.inputField] = value;
  });
  const shots = {};
  $$("[data-shot-field]").forEach(field => {
    const key = field.dataset.shotField;
    if (!["narration", "subtitle", "voice_style", "voice_rate_scale", "voice_pitch_offset", "voice_pause_after"].includes(key)) return;
    const id = field.dataset.shotId;
    shots[id] ||= {id};
    shots[id][key] = key === "voice_pause_after" && field.value === "" ? null : field.type === "number" ? Number(field.value) : field.value;
  });
  return {input, shots:Object.values(shots)};
}

function selectVoiceClip(id) {
  voiceStudioSelection = id;
  $$("[data-voice-clip]").forEach(el => { el.hidden = el.dataset.voiceClip !== id; });
  $$("[data-select-voice]").forEach(el => {
    el.classList.toggle("selected", el.dataset.selectVoice === id);
    el.setAttribute("aria-pressed", String(el.dataset.selectVoice === id));
  });
  $$("[data-parameter-clip]").forEach(el => { el.hidden = el.dataset.parameterClip !== id; });
}

function refreshVoiceSource() {
  const engine = draftFieldValue("voice_engine");
  const note = $("#voice-source-note");
  if (!note) return;
  note.textContent = engine === "tianyi"
    ? `天依社区音色模型：${state.catalog?.tianyi?.available ? "已安装，可直接输入旁白并试听。首次加载较慢，连续片段会复用模型，空闲后自动释放模型资源。" : "运行 scripts/setup-tianyi.ps1 完成首次安装后，刷新页面。"} 这是社区训练的洛天依音色模型，非官方声库。支持语速，音高由模型自然生成，不应用手动 Hz 偏移。已绑定的导入音频仍优先使用，可在片段中解绑。`
    : engine === "upload"
    ? "上传配音模式：在下方选择镜头并上传音频，每镜都需绑定音频；缺失时会提示补齐。上传音频保留原有音高与语速。也可以选择天依等合成来源，只用上传音频覆盖个别镜头。"
    : engine === "windows" ? "本地模式使用已安装的 Windows 声音，不使用上方神经音色；支持语速，不支持这里的音高调校。"
    : engine === "edge" ? "将使用所选神经音色；连接失败会明确报错，可自行切换本地语音。"
    : "实际音源由服务端配置决定；试听后会显示实际引擎和音色。";
  $$(".voice-card").forEach(el => el.classList.toggle("active", el.hasAttribute("data-tianyi") ? engine === "tianyi" : el.hasAttribute("data-voice-upload") ? engine === "upload" : !["upload","tianyi"].includes(engine) && el.dataset.voicePreset === draftFieldValue("voice")));
  $$("[data-input-field='voice_pitch'], [data-shot-field='voice_pitch_offset']").forEach(el => {
    el.disabled = ["tianyi", "windows", "upload"].includes(engine);
    el.title = el.disabled ? "当前声音来源不应用 Hz 音高偏移" : "";
  });
}

function renderVoicePlan(plan) {
  const naturalPitch = draftFieldValue("voice_engine") === "tianyi";
  $("#voice-timing").textContent = `${plan.timing === "measured" ? "本次试听实测" : "分镜预估（合成后更新）"} · ${Number(plan.duration).toFixed(2)} s`;
  for (const clip of plan.clips) {
    const button = $$("[data-select-voice]").find(el => el.dataset.selectVoice === clip.shot_id);
    if (button) {
      button.style.flex = Math.max(0.1, clip.end - clip.start);
      button.querySelector("small").textContent = `镜头 ${clip.order} · ${clip.start.toFixed(1)}–${clip.end.toFixed(1)}s`;
      button.querySelector("span").textContent = clip.phrases.map(p=>p.text).join("");
    }
  }
  $("#voice-parameter-lane").innerHTML = plan.clips.map(clip=>`<div data-parameter-clip="${escapeHtml(clip.shot_id)}"><small>${clip.uploaded ? "导入音频 · 以下调校不作用于原录音" : "整句参数规划（非实测基频曲线）"}</small><div class="voice-phrase-list">${clip.phrases.map(p=>`<div><span>${escapeHtml(p.text)}</span><b>${p.rate.toFixed(2)}× · ${naturalPitch ? "模型自然语调" : `${p.pitch_hz >= 0 ? "+" : ""}${p.pitch_hz} Hz`}</b><small>${escapeHtml(p.reason)}</small></div>`).join("")}</div></div>`).join("");
  selectVoiceClip(voiceStudioSelection);
}

function invalidateVoicePreview() {
  voiceStudioRevision++;
  const player = $("#voice-preview-player");
  if (player) { player.pause(); player.hidden = true; player.removeAttribute("src"); player.load(); }
  if ($("#voice-download")) $("#voice-download").hidden = true;
  if ($("#voice-preview-status")) $("#voice-preview-status").textContent = "参数或文字已更改，请重新合成试听。";
}

async function requestVoicePlan(taskId) {
  const revision = ++voiceStudioRevision;
  try {
    const plan = await api(`/api/tasks/${taskId}/voice-plan`, {method:"POST", body:JSON.stringify(collectVoiceSnapshot())});
    if (revision === voiceStudioRevision && state.latestTaskId === taskId && $("#voice-timeline")) renderVoicePlan(plan);
  } catch (error) {
    if (revision === voiceStudioRevision && $("#voice-preview-status")) $("#voice-preview-status").textContent = error.message;
  }
}

async function previewVoice(taskId, selected) {
  if (voiceStudioBusy) return;
  clearTimeout(voiceStudioTimer);
  const snapshot = collectVoiceSnapshot();
  if (selected) snapshot.shot_id = voiceStudioSelection;
  const revision = ++voiceStudioRevision;
  voiceStudioBusy = true;
  $$("[data-voice-preview]").forEach(el => { el.disabled = true; });
  $("#voice-preview-status").textContent = snapshot.input.voice_engine === "tianyi" ? "正在本地合成天依配音，首次加载模型可能需要一些时间…" : "正在合成真实配音，首次连接可能需要一些时间…";
  try {
    const result = await api(`/api/tasks/${taskId}/voice-preview`, {method:"POST", body:JSON.stringify(snapshot)});
    if (revision !== voiceStudioRevision || state.latestTaskId !== taskId || !$("#voice-preview-player")) return;
    if (!selected) renderVoicePlan(result);
    const player = $("#voice-preview-player");
    player.src = result.audio_url;
    player.hidden = false;
    const download = $("#voice-download");
    download.href = result.audio_url;
    download.hidden = false;
    const sourceLabel = result.provider === "tianyi-local" ? "天依·本地社区声库" : `${result.provider} / ${result.model}`;
    $("#voice-preview-status").textContent = `已生成 ${result.duration.toFixed(2)} 秒 · ${sourceLabel}${result.rate_scale > 1 ? ` · 时长适配提速 ${result.rate_scale}×` : ""}。${result.warnings.join(" ")}`;
    player.ontimeupdate = () => {
      const active = result.traces.find(t => player.currentTime >= t.start && player.currentTime < t.end);
      $$("[data-select-voice]").forEach(el => el.classList.toggle("playing", el.dataset.selectVoice === active?.shot_id));
    };
    player.onended = () => $$("[data-select-voice]").forEach(el => el.classList.remove("playing"));
  } catch (error) {
    if (revision === voiceStudioRevision && $("#voice-preview-status")) $("#voice-preview-status").textContent = `试听失败：${error.message}`;
  } finally {
    voiceStudioBusy = false;
    $$("[data-voice-preview]").forEach(el => { el.disabled = false; });
  }
}

function bindVoiceStudio(task) {
  voiceStudioRevision++;
  clearTimeout(voiceStudioTimer);
  if (!$(".voice-studio")) return;
  if (!task.shots.some(s => s.id === voiceStudioSelection)) voiceStudioSelection = task.shots[0]?.id || "";
  selectVoiceClip(voiceStudioSelection);
  $$("[data-select-voice]").forEach(el => el.addEventListener("click", ()=>selectVoiceClip(el.dataset.selectVoice)));
  $$("[data-voice-preview]").forEach(el => { el.disabled = voiceStudioBusy; el.addEventListener("click", ()=>previewVoice(task.id, el.dataset.voicePreview === "selected")); });
  const changed = () => {
    invalidateVoicePreview(); refreshVoiceSource(); syncAttachPanels();
    clearTimeout(voiceStudioTimer);
    voiceStudioTimer = setTimeout(()=>requestVoicePlan(task.id), 350);
  };
  $(".draft-editor").addEventListener("input", event => {
    if (event.target.dataset.inputField === "voice") {
      $("[data-input-field='voice_engine']").value = event.target.selectedOptions[0]?.dataset.engine || "configured";
    }
    if (event.target.dataset.inputField === "voice_engine") {
      const voice = $("[data-input-field='voice']");
      const engine = event.target.value;
      const match = Array.from(voice.options).find(option => option.dataset.engine === engine);
      if (match && voice.selectedOptions[0]?.dataset.engine !== engine) voice.value = match.value;
    }
    if(event.target.matches("[data-input-field], [data-shot-field]")) changed();
  });
  $$("[data-voice-preset]").forEach(el => el.addEventListener("click", ()=>{
    $("[data-input-field='voice']").value = el.dataset.voicePreset;
    $("[data-input-field='voice_engine']").value = "edge";
    $("[data-input-field='voice_style']").value = el.dataset.voiceStyle;
    changed();
  }));
  $("[data-voice-upload]").addEventListener("click", ()=>{
    $("[data-input-field='voice_engine']").value = "upload";
    $("[data-input-field='voice']").value = "上传配音音频";
    changed();
    $("[data-when='voice-upload']").scrollIntoView({behavior:"smooth", block:"center"});
    $("#voice-file").focus({preventScroll:true});
  });
  $("[data-tianyi]").addEventListener("click", ()=>{
    $("[data-input-field='voice_engine']").value = "tianyi";
    $("[data-input-field='voice']").value = "天依·本地社区音色";
    $("[data-input-field='voice_style']").value = "natural";
    changed();
  });
  $("[data-voice-script]").addEventListener("click", ()=>{
    const snapshot = collectVoiceSnapshot();
    const text = snapshot.shots.map((shot,i)=>`镜头 ${i+1} / ${shot.id}\n${shot.narration}`).join("\n\n");
    const url = URL.createObjectURL(new Blob([text], {type:"text/plain;charset=utf-8"}));
    const link = document.createElement("a"); link.href = url; link.download = `${task.id}-旁白轨道.txt`; link.click();
    setTimeout(()=>URL.revokeObjectURL(url), 1000);
  });
  $$("[data-clear-voice]").forEach(el => el.addEventListener("click", async ()=>{
    try {
      if (!await saveDraft(task.id)) return;
      const updated = await api(`/api/tasks/${task.id}/shots/${el.dataset.clearVoice}`, {method:"PATCH",body:JSON.stringify({clear_audio:true})});
      renderTask(updated);
      showToast("已解除片段的音频绑定，可选择神经音色重新合成。");
    } catch(error) { showToast(error.message); }
  }));
  refreshVoiceSource();
  requestVoicePlan(task.id);
}
