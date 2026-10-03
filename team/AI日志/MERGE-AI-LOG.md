# 合并 · 负责人 —— AI 使用日志

> 记录规范见 `team/AI日志/README.md`。本文件记录四条产线的合并动作（负责人侧），
> 与 `P1~P4-AI-LOG.md` 并列，不替代各产线自己的日志。

---

## Entry 001 - 2026-10-03

- **Author:** 负责人（合并）
- **AI 工具:** Claude Code (deepseek-flash)
- **Change type:** Refactor / Docs
- **Files changed:**
  `backend/app/providers/registry.py`、`backend/app/services/orchestrator.py`、
  `backend/app/services/renderer.py`、`backend/app/services/cost.py`、
  `backend/app/providers/media_ffmpeg.py`、`backend/app/models.py`、
  `backend/app/config.py`、`backend/app/main.py`、`frontend/app.js`、
  `frontend/index.html`、`frontend/styles.css`、`.gitignore`、`team/CONTRACT.md`
- **AI 参与度:** 主导（合并与冲突裁定），全部改动经真实回归验证
- **Reason:** 四条产线各自交付完成，但交付物分散在四份互不相同的代码树里，没有任何一份
  是完整产出。需要按 `team/合并计划.md` 的顺序把 P1/P4/P3/P2 合到同一棵树，并保证
  `合并计划.md`「三条不能破的指标」不被破坏。
- **What changed:**
  - **择一（不改逻辑，整份取用）**：P1 取 007WS-cry 交付（registry/orchestrator/config/
    models/main/frontend）；P4 取 A 交付（`renderer.py` + `media_ffmpeg.py`，含 5 个段内转场、
    静图推镜、统一调色、cues 字幕）；P3 取 2.0 交付（`tts.py`/`music.py`/`audio_sync.py`/
    `uploads.py`/`audio_runtime*.py`/`contract.py`）；P2 取 A 交付（`image.py`/`video.py`/
    `consistency.py`/`cost.py`）与知识库（`knowledge.py` + `data_zhejiang_kb/`）。
  - **最小接线（6 处）**：
    1. `registry.py` 的 `ai-image`/`ai-video` 原是 P1 预留的空占位（原文「等待 P2 注册具体
       实现」），填入 P2 的 `_CloudAssetAdapter`；并入 P2 的 `asset_chain_for(source)`
       来源→降级链映射与 `AcquireResult` 逐档失败记录。
    2. `orchestrator._audio` 换成 P3 的 `PerShotAudioBuilder`（逐镜配音/逐镜上传、
       ffprobe 实测 cues、镜间 150ms 静音、48kHz 单声道）。
    3. `orchestrator._assets` 接 P2 的 `consistency.grade`（写入 `task.audio["grade"]`）、
       知识库英文检索词与图生视频首帧准备。
    4. `orchestrator._render` 写入 `task.audio["requested_duration"]`，并由 P4 的渲染器
       用 `-frames:v round(duration*fps)` 把成片容器锁回用户选择的秒数。
    5. `models.py` 补 `Shot.audio_local_path`（P3 逐镜音频读取）；`asset_source` 接受 P2
       注册表别名（`cloud-i2v`/`cloud-t2i`/`ai`/`cloud`）。
    6. `config.py` 并入 P3 的音频运行时键；`main.py` 的 `/api/uploads` 改为 P3 的逐镜配音
       + 授权音乐校验；`/api/health` 补 `contract_version`/`tts_provider`/`timing`；
       前端补齐 5 个转场、逐镜配音绑定与音乐授权字段。
  - **修正偏离**：
    - `renderer._burn_subtitles` 的 `-vsync cfr` 在 ffmpeg 9.0 已被移除，字幕烧录直接
      失败（`Unrecognized option 'vsync'`）→ 改为等价的 `-fps_mode cfr`。
    - P3 的 `PerShotAudioBuilder.build()` 不遵守 `target_duration`（其自带
      `VALIDATION-REPORT.md` 实测「名义 15s → 实出 41.72s」），与「总时长 30.000s」
      冲突 → 由接线 4 的容器锁兜住，不修改 P3 的合成逻辑。
- **AI 产出的部分:** 全部接线代码、`cost.py` 的并集（P2 的 `CostLedger`/`sum_costs` +
  P1 的 `summarize_cost`）、`team/CONTRACT.md` 的 v1→v2 变更记录。
- **人工修改的部分:** 无（本轮为合并，未做人工代码改动）。
- **Verification:**（真实命令 + 真实输出）
  - `python -m compileall -q backend tests scripts` → 通过
  - `node --check frontend/app.js` → 通过
  - `python -m unittest discover -s tests -v` → **Ran 35 tests ... OK**（基线 11 项全保留且
    全部通过：`test_domain` 4 + `test_mpt_adapter` 2 + `test_rendering` 2 + `test_storage` 3；
    另并入 P2 的 4 个与 P3 的 5 个测试文件）
  - `powershell -File scripts/smoke-test.ps1`（`DEFAULT_PROVIDER=offline`，
    `ALLOW_SYNTHETIC_AUDIO_FALLBACK=true`，duration=15）→
    **SMOKE TEST PASSED**，产物 `runtime-smoke/media/task_8097afff1743/final.mp4`
  - `ffprobe` 该成片 → `15.000000` / `720,1280,25/1` / `video`+`audio` / `aac,48000,1`
  - 另跑一个 **duration=30** 的完整任务（`/api/tasks` → 轮询到 completed）：
    `manifest.total_duration=30.0`、`timing_source=measured-shot-audio`；
    六镜实测时长 `[7.127, 7.897, 5.632, 6.682, 7.747, 6.807]`（**合计 41.892s**）；
    `ffprobe` 成片 → **`30.000000`** / `720,1280,25/1` / `video`+`audio` / `aac,48000,1`。
    即 **P1 任务3（以 cues 反推每镜头真实时长）与「总时长 30.000s」同时成立**：
    镜头时长按配音实测，成片容器按用户选择的秒数锁定。
- **Known limitations:**
  - 本机无 `py` 启动器，未安装 `.venv-audio`（Python 3.11，约 8GB），因此 `edge-tts` 与
    `whisperx` 强制对齐**未接线**：默认 `TTS_PROVIDER=auto` 走系统语音，离线 smoke 用
    `TTS_PROVIDER=windows`。`test_audio_runtime` 因此不覆盖运行时调用。
  - 离线 provider 的旁白长度与时长无关（固定文案），6 镜实测合计 41.892s；成片锁回 30s
    后**尾段被裁掉**。画面与配音在 30s 窗口内保持对齐（cues 未被缩放），未采用 A 的
    `apply_audio_durations`（它按比例缩放全部镜头以凑总长，会让画面比配音走得快、字幕错位）。
    真实链路（DeepSeek）会按所选时长生成旁白，不会出现该现象。
  - **附带现象**：旁白超出所选时长时，`create_srt`（P4 原有实现）会把落在画面之外的 cue
    收进画面结尾，形成 `14.950 --> 15.000` 这类 50ms 的堆叠字幕（离线 smoke 的 duration=15
    实测可见）。这是 P4 的既有裁剪行为，不是本次合并引入，也未在 `20260925` 要求范围内，
    故按「只合并」原则未改动；旁白合身时不触发。
  - 静图运镜保留 A 的 `scale(eval=frame)`，**未实现 P4 任务书字面的 `zoompan`**。理由见
    A 的实现注释：zoompan 按整像素跳、在 `-loop 1` 循环输入上逐帧重置会卡顿；A 的写法
    同样「先放大再裁回」且不拉长时长。P4 的验收（有运镜/调色/5 转场 + 30.000s/720×1280/25fps）
    已满足，A 的 `tests/test_render_request.py` 亦以断言锁定该实现。
  - C（`zhejiang-tourism-aigc.zip`）独有的更细画面配置键未并入：P2 任务书要求的参数
    A 的 `video.py` 已全部覆盖，符合「不新增未要求内容」。
  - 授权音乐目录 `runtime/music/catalog.json` 未随包提供，选曲需自备曲库或上传带授权
    字段的音乐；离线 smoke 使用「无音乐」。
- **Follow-up:** 需要 edge-tts / whisperx 时执行 `scripts/setup-audio-runtime.ps1`
  （需先装 Python 3.11）；提供一份最小 `runtime/music/catalog.json` 让选曲可演示。
- **契约影响:** **改了** —— `zj-team4-v1` → `zj-team4-v2`。`AudioResult` 新增
  `target_duration`/`timing_source`/`provider_trace`/`music_match`，新增 `MusicResult`
  与 `MusicProviderProtocol`；其余结构不变。已按流程更新 `team/CONTRACT.md` 并在此标注，
  需通知全员。
