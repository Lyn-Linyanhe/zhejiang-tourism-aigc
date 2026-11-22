# 提交前状态快照（2026-11-22 21:00）

等另外两个会话（7 城知识库采集、音频）完成后统一提交。此文件记录当时的改动边界，避免合并时漏项或混错归属。

## 三个并行会话的改动边界

### A. 本会话：素材生成与知识库核查

**新增文件**

| 文件 | 作用 |
|---|---|
| `backend/app/providers/video_gateway.py` | 网关视频协议适配（`/v1/videos` 异步轮询，创建只发一次防重复计费） |
| `backend/app/services/script_export.py` | 脚本导出 md/txt/json |
| `backend/app/services/metrics.py` | 计费记录、传播观测、执行运行校验 |
| `tests/test_media_gateway.py` | 13 项：data URI 强制、视频参数边界、轮询状态机 |
| `tests/test_script_export.py` | 6 项：三种格式导出 |
| `tests/test_script_generation.py` | 9 项：JSON 修复、镜头校验、字幕一致性 |
| `tests/test_metrics.py` | 成本状态、计费校验、观测校验 |
| `scripts/eval_script_cloud.py` | 10 条固定 brief 云端验收 |
| `scripts/verify_kb_quotes.py` | 引文联网核验（带页面缓存与退避） |
| `scripts/xhs_*.py`（7 个） | 小红书采集、聚合、取图、镜头映射 |

**修改文件**

| 文件 | 改动 |
|---|---|
| `backend/app/providers/image.py` | 新增 `build_edit_payload` / `image_file_to_data_url` / `CloudImageProvider.edit()` |
| `backend/app/providers/video.py` | 新增 `_detect_api_style` 三协议分流、`_generate_gateway` |
| `backend/app/models.py` | 时长放宽到 5–180、镜头数 2–12（0=自动）、单镜下限校验 |
| `backend/app/config.py` | 默认素材源改 `ai-video` |
| `backend/app/providers/llm.py` | 自动镜头数按每镜 5 秒切，带下限保护 |
| `backend/app/services/knowledge.py` | `_evidence_status` 支持 `fact_index` 绑定、许可判定修正 |
| `backend/app/services/catalog.py` | 时长范围/镜头范围/画幅标签 |
| `frontend/index.html`、`app.js` | 时长改输入框+快捷档、镜头数 2–12、素材源默认 AI、画幅标注 |
| `data_zhejiang_kb/hangzhou.json` | 46 实体全部补来源（65 条记录） |
| `data_zhejiang_kb/ningbo.json` | 补 3 个景区实体 |
| `data_zhejiang_kb/huzhou.json` | 新建，27 实体 / 108 记录 |
| `tests/test_knowledge.py` | 覆盖统计、引文一致性、删除请求图片、规则分档 |

**文档**

`docs/research/` 下：`shaoxing-pilot.md`、`shaoxing-shot-intents.csv`、`shaoxing-entity-media-candidates.csv`、`ningbo-scenic-pilot.md`、`hangzhou-huzhou-kb-deepening.md`、`kb-quote-audit-2026-11-22.md`、`script-generation-cloud-acceptance.md`、`media-generation-acceptance.md`、`evaluation-protocol.md` 及三个 evaluation CSV、`xhs-*` 系列、`zhejiang-shot-intents.csv`。

### B. 音频会话（不要动）

`backend/app/providers/tts.py`、`backend/app/providers/audio_runtime.py`、`backend/app/services/audio_sync.py`、`backend/app/services/renderer.py`、`tests/test_mix_loudness.py`、`tests/test_tts_naturalness.py`、`tests/test_music_coverage.py`。

改动内容：edge-tts 进程内兜底、TTS 首尾静音裁剪、响度归一化到 -16 LUFS、曲库覆盖。

### C. 7 城知识库会话（不要动）

`data_zhejiang_kb/{jiaxing,wenzhou,jinhua,quzhou}.json` 与 `_meta.json` 的城市登记。

**快照时状态**

| 城市 | 实体 | 记录 | meta 已登记 |
|---|---:|---:|---|
| 嘉兴 | 12 | 41 | ✅ |
| 温州 | 13 | 52 | ✅ |
| 金华 | 12 | 44 | ✅ |
| 衢州 | 13 | 44 | ❌（文件已生成，meta 未登记） |

剩余待采：舟山、台州、丽水。

## 提交时的注意事项

1. **不要提交 `runtime/` 下的任何东西**：已 gitignore，含 cookies、采集缓存、生成产物。
2. **`.env` 与 `cookies.json` 已忽略**：确认 `git check-ignore` 通过再提交。
3. **`_meta.json` 会冲突**：A 组和 C 组都改它，提交前先确认 C 组已完成。
4. **测试基线**：快照时 239 项，其中 1 项失败（`test_city_filter_excludes_other_cities`），原因是衢州已入库但 meta 未登记，导致测试把已覆盖城市当成未覆盖。C 组完成后该项应恢复。
5. **建议拆成三个 commit**：素材生成 / 音频 / 知识库，便于队长 review。

## 已验证的验收结果

| 项 | 结果 |
|---|---|
| 10 条脚本云端验收 | 10/10 成功，平均 26.8 秒 |
| 引文核验（4 城） | 215 条全通过 |
| 引文核验（嘉兴/温州/金华） | 137 条全通过 |
| 完整链路（脚本→图生图→图生视频→渲染） | 15 秒成片，4 镜全部 `cloud-i2v` |
