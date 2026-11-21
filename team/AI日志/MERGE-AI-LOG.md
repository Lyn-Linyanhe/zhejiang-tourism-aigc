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

---

## Entry 002 - 2026-11-20

- **Author:** 负责人侧（实体知识与素材试点）
- **AI 工具:** ZCode / GPT-6.1；只读子代理用于公开来源核验与发布链路独立检查。
- **Change type:** Feature / Fix / Tests / Docs
- **Reason:** 执行已批准的实体素材试点计划，将知识实体、镜头意图、库素材、许可审核和成片来源追溯接通。
- **Files changed:** `contract.py`、`models.py`、`config.py`、`storage.py`、`main.py`、`registry.py`、`media.py`、`services/library.py`、`knowledge.py`、`orchestrator.py`、`renderer.py`、`frontend/app.js`、`frontend/styles.css`、杭州知识与元数据、API/操作/契约文档、专项测试与试点采集/验收脚本。工作区原有音频合并等改动不归为本轮新增。
- **What changed:**
  - 新增可选 `AssetProvenance` 合同，含 `allowed_usages`、实体/来源/许可/日期/指纹/技术字段；保留 v2 及旧调用兼容。
  - 图库哈希去重，重复上传保留已有审核；上传固定待审和仅预览，非法任务/镜头/实体绑定在写文件前拒绝。
  - 发布模式只选精确实体/意图且已审核、许可有效、文件未变的素材。重渲在焦点识别和 FFmpeg 输入前再次检查当前城市、镜头/素材记录与文件。
  - 独立核验发现空白凭证通过、改城后重渲旧城素材、预览跨城回退，均先复现后修复；另补素材记录不一致、取消绑定残留旧图与异步下拉绑定丢失。
  - 工作台增加库筛选、预览、来源编辑、独立审核和逐镜绑定，上传/库刷新保留草稿。正式模式锁定本地实体库。
  - Pexels 保留实际查询/原始页，明确为照片候选；AI 保留生成分类。manifest 保存各镜实体与当前许可、哈希，同时保留 provider 技术字段。
  - 核实并下载杭州五实体照片（CC BY 2.0/3.0/4.0 与 CC0），逐文件记录来源/许可回执；补 5 条 fact_records，分别记录官方短事实和照片描述。宁波未核授权的媒体不进入发布库。
- **Verification:**
  - `python -m compileall -q backend tests scripts/collect_entity_pilot.py scripts/accept_entity_pilot.py` → 通过。
  - `node --check frontend/app.js`、`node --check frontend/voice-studio.js` → 通过。
  - `git diff --check` → 通过；修正了既有 `video.py` 末尾多余空行。
  - 最终 `python -B -m unittest discover -s tests -v` → **Ran 134 tests in 40.870s — OK**。包括 10 项真实本地 HTTP 回归和 17 项实体发布/重渲用例。
  - 本地样片 `entity_pilot_20261120_104452` → completed；FFprobe：**30.000000 s / 720×1280 / 25fps / H.264 + AAC 48kHz mono**。本机系统语音 Microsoft Huihui Desktop；无背景音乐；字幕与 AIGC 标识生成。
  - 抽查五镜照片主体、字幕与标识，修正灵隐寺句子中年份换行；未将静态抽帧等同于完整动态/听感验收。
- **GUI verification:** 首页和素材卡片桌面截图已查看并保存；五条实体/来源/许可可读取。首页点击/滚动及窄屏截图出现浏览器工具超时；390×844 视觉验收和完整审核/筛选/保存交互未完成。浏览器运行时不支持文件上传，HTTP 回归不能代替 GUI 上传成功。已恢复桌面视口。详见 `docs/research/entity-assets-pilot.md`。
- **契约影响:** `AssetProvenance`、`VISUAL_USAGES`、`SHOT_TAGS` 与可选字段已在 `contract.py` 和 `team/CONTRACT.md` 登记；现有 dataclass 必填字段/位置参数不改，版本仍为 `zj-team4-v2`。未代表用户向其他协作者发送外部通知。
- **Known limitations / Follow-up:** 当前仅五实体各一张静态照片，每实体 2–3 类镜头目标未完成；宁波红帮等非遗需索权/自摄；拱宸桥候选下载中断未入库；Pexels 视频搜索回归待独立修复；完整桌面/手机 GUI 与全片动态听感验收待补。
- **Deliverables:** `docs/research/entity-assets-pilot.md`、同名 CSV；本地 MP4 目录附 `ATTRIBUTION.md`、`fact-records.json`、`task.json`、`manifest.json`、`ffprobe.json`。
- **Workspace:** 保留当前 `feature/zhejiang-entity-assets`，未提交、未推送、未对外发布。使用独立 `runtime/_wire_probe_entity_assets_20261120`；未覆盖 `.env`、常规媒体/数据库或音乐库，未停止 8787 进程。

---

## Entry 003 - 2026-11-21

- **Task:** 侧聊请求改进字幕字体、字号和排版；不执行父会话的提交/推送。
- **Files:** 新增 `backend/app/providers/subtitles.py`、`tests/test_subtitles.py`；局部修改 `services/renderer.py` 和 `tests/test_rendering.py`。
- **Change:** 保留原 SRT 下载文本和毫秒级时码，生成 `subtitles.ass` 用于烧录；微软雅黑加粗优先，其他系统回退已有中文字体。按实际画布比例设置字号、左右 8% 与竖屏底部 12% 留白，使用细描边和轻阴影。
- **Layout:** 常规长句优先排两行，保护年份/数字/英文词与行首标点；保留手动换行和连续空格。异常长稿保留全文，必要时超过两行；超宽英文单词应急拆分。花括号及反斜杠按字面安全显示。ASS 烧录时码为厘秒精度（量化误差至多约 5ms），原 SRT 不变。
- **Visual QA:** 首版可变思源字体被 DirectWrite/libass 选为 Thin，实际预览过细过小，已弃用该本机字体路径；改为静态雅黑字重，校准 ASS 字号（720 宽画面为 44，非等同字形高度），对照 2s/11s/22s 的实拍帧核验。短句一行、样片长句两行、浅色背景有细描边；AIGC 标识保留。
- **Verification:** 全量 `python -B -m unittest discover -s tests -v` → **Ran 143 tests in 33.899s — OK**；局部 `git diff --check` 通过；真实 FFmpeg 烧录与音画同步测试通过。对比版 FFprobe 为 30.000000s / 720×1280 / 25fps / H.264 + AAC。原版与新版音轨 SHA-256 相同、SRT 字节相同。特殊字符探针确认路径字形不变。
- **Preview:** `runtime/_wire_probe_entity_assets_20261120/media/subtitles_preview_20261121/final.mp4`；本地对比页 `http://127.0.0.1:8811/`，文件位于 `runtime/_probe_assets/subtitle-review-20261121/`，保留原片与独立对比产物。
- **Contract:** 无新增对外必填字段，不改变现有 SRT URL 和烧录开关。已有 MP4 不自动覆盖；后续渲染使用新样式，运行中的服务需重新加载代码。
- **Scope:** 本侧聊未提交、推送、修改 .env 或重启既有应用服务；只启动了 8811 本地静态对比服务。
- **父会话交付复核:** `python -B -m unittest discover -s tests -p test_subtitles.py -v` → 9 项通过；同法运行 `test_rendering.py` → 3 项通过（真实 FFmpeg）；`python -B -m unittest discover -s tests -v` → **Ran 143 tests in 34.819s — OK**。字幕代码与这条验证记录独立提交，不包含素材或本机对比文件。
