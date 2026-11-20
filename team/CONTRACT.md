# 接口契约 v2（向后兼容扩展）

**契约版本**：`zj-team4-v2`（2026-10-03 合并时由 `zj-team4-v1` 升级）
**状态**：冻结。任何字段改动需先改 `contract.py` 并在 AI 日志记一条 Entry。

> **v1 → v2 变更记录（合并时补记）**
>
> 变更内容：`AudioResult` 新增 `target_duration` / `timing_source` / `provider_trace` /
> `music_match` 四个字段；新增 `MusicResult` 数据类与 `MusicProviderProtocol`。
> `AssetResult` / `Cue` / `RenderRequest` / `RenderResult` / `FallbackChain` 保持不变。
>
> 变更理由：接入 P3 声音线的逐镜实测 cues（需要 `timing_source` 区分
> `measured-shot-audio` 与 `nominal-no-voice`）与带授权字段的授权音乐契约
> （`MusicResult` 强制 `license/author/usage_scope/approved`）。
>
> 新增字段均有缺省值，v1 调用方按位置/关键字构造 `AudioResult` 不受影响。
> 详见 `team/AI日志/MERGE-AI-LOG.md`。按契约流程：已改 `contract.py` → 已在日志标注 →
> 需通知全员 → 各自跟进。

---

## 2026-11-20：实体素材与发布审核扩展

`contract.py` 增加 `AssetProvenance` 可选字段合同，`TaskInput.visual_usage` 默认 `preview`，`Shot` 增加 `entity_id`、`shot_tag`、`library_asset_id`，`AssetRecord.provenance` 默认空对象。保留 `zj-team4-v2` 版本及既有 dataclass 位置参数，旧任务缺字段时继续可读。

素材证据含实体/城市/地点、镜头标签、来源页/标题/发布方/作者、许可/凭证/状态、`allowed_usages`、核验日期/有效期、SHA-256 与媒体尺寸/时长。`allowed_usages` 仅允许 `preview`、`publication`。`classification` 区分 `entity-approved`、`external-candidate`、`ai-generated`。

- `preview`：待审候选可进入内部预览；驳回绑定不可用。实体检索不跨城、不悄悄取最近的无关素材。
- `publication`：每镜精确绑定实体和镜头意图，且审核与许可都通过、证据完整、未过期、文件指纹不变。强制走本地已审核实体库，无符合项就失败。
- 渲染重试仍在读取画面/焦点识别之前检查当前任务城市、实体、素材记录与文件。
- 新上传固定为待审和仅预览；人工审核独立调用。重复文件不覆盖原审核信息；任务/镜头无效时不写库。
- manifest 每镜保留实体、素材 ID、意图与证据；Pexels 照片记录实际查询和来源页，AI 保留生成语义。

此扩展已记录于 `MERGE-AI-LOG.md` Entry 002；本轮未向其他协作者发送外部通知。后续接入方按以上可选字段消费，缺失字段不得解释为已获发布许可。

---

## 为什么要有这份东西

四个人改的是同一个项目，但**只在这一个点上耦合**。契约一冻结：

- 谁都不用等谁
- 合并时只有「把 stub 换成真实现」一个动作
- 出问题时能一眼定位是哪条产线违约

**唯一的硬约束：不许私自往返回值里加字段。** 需要新字段就走「先改 contract.py → 通知全员 → 各自跟进」的流程。

---

## 三条产线的交付接口

### ① P2 画面 → 产出 `AssetResult`

```python
AssetResult(
    path: str,               # 图片(.png/.jpg) 或 短视频(.mp4) 的本地绝对路径
    media_type: str,         # "image" | "video"
    source: str,             # "cloud-t2i" / "cloud-i2v" / "local-library" ...
    model: str,              # 模型 ID，用于追溯
    seed: int,               # 必填。这是画面可控性的根
    prompt: str,             # 实际提交的 prompt（含前缀和负向词）
    first_frame_path: str,   # 图生视频时必填，指向首帧；文生图留空
    cost: float,             # 本次调用成本，供成本台账汇总
    event: dict,             # 写进 trace 的事件
)
```

**硬性要求**

| 要求 | 原因 |
|---|---|
| `seed` 必须回填 | 不回填就无法复现，一致性无从谈起 |
| 图生视频必须回填 `first_frame_path` | 它是「这一镜为什么长这样」的唯一证据，也是失败重试的起点 |
| `media_type` 必须准确 | 渲染器据此决定用「静图运镜」还是「视频铺时长」 |

**好消息**：`path` 不管是图还是视频，都能直接进现有渲染管线——`renderer.py:47-48` 的 `_is_video_asset()` 已经按扩展名分流了，P4 不需要写新分支。

---

### ② P3 声音 → 产出 `AudioResult`

```python
AudioResult(
    voice_path: str,         # 配音 wav 路径，无配音留空
    music_path: str,         # 背景音乐路径，无音乐留空
    cues: list[Cue],         # 每句旁白的真实时间位置 —— 核心交付物
    total_duration: float,   # 音频总时长（秒）
    cost: float,
    event: dict,
)

Cue(
    shot_id: str,            # 对应 TaskInput 里的镜头 id
    start: float,            # 在最终音轨上的起始秒
    end: float,
    text: str,
)
```

**硬性要求**

> **整段音频不算交付，带时间戳才算。**

`cues` 是音画同步的唯一基础。没有它，字幕只能按镜头名义时长均分，配音和画面会渐进错位——这是当前版本最粗糙的一环（`media.py:290-305` 的 `create_srt()` 就是名义时长均分）。

`AudioResult.validate()` 会自动检查 cues 单调递增、不重叠、区间合法。

---

### ③ P4 渲染 → 消费 `RenderRequest`，产出 `RenderResult`

```python
RenderRequest(
    task_id, shots, audio,
    resolution, fps,
    include_subtitles, include_ai_label,
    voice_volume, music_volume,
    grade: dict,             # P2 的 consistency 模块产出的统一调色参数，P1 透传
)

RenderResult(...)            # 字段与现有 manifest.json 保持兼容
```

`shots` 里每条已经带上了 `asset_local_path` 和 `asset_media_type`。

---

## 降级链

P1 的 Registry 按 `FallbackChain` 声明回退顺序：

```python
FallbackChain(
    slot="asset",
    providers=["cloud-i2v", "cloud-t2i", "local-library", "offline"],
)
```

**技术预览**可按声明的链回退到本地兜底，并记录原因。显式的严格动态来源失败即报错。

**正式发布**绕过该降级链，只接受已审核且精确匹配实体/镜头意图的库素材；无符合项必须失败，不能回退到离线图或最近素材。

断网 / 限流 / 欠费时，Registry 自动往下走一格，并往 trace 里记一条 `fallback_used=true` 加原因。

---

## 阶段顺序（v1 变更）

```
旧：scripting → storyboard → assets → audio → rendering
新：scripting → storyboard → audio → assets → rendering
                             ↑        ↑
                        先出配音   按音频真实时长生成画面
```

**为什么改**：画面是云 API 按秒计费的。旧顺序下，配音生成后一旦发现语速不对或文案要改，分镜时长就变了，**已经花钱生成的视频全部作废**。

**谁受影响**

| 人 | 要做什么 |
|---|---|
| P1 | 改 `orchestrator.py:194-224` 的阶段机；重写 `_normalize_shots()` L282-307（现在强制分镜时长之和 == `input.duration`，引入配音真实时长后会打架） |
| P3 | 先跑，产出 cues |
| P2 | 后跑，按 cues 算出的镜头真实时长生成 |
| P4 | 时长归一化要以 cues 为准，守住 30.000s |

---

## 转场名

`TRANSITIONS = ("fade", "dissolve", "slideleft", "slideright", "zoom")`

`models.py:119` 早就声明了 `transition: str = "fade"` 字段，但 `renderer.py` 从未使用——用的是 `concat` 硬切。这是「声明的能力与实现不符」，评委翻代码就能发现。

**P4 必须把上面这五个全部实现。**

---

## 自检

契约自带校验，每条产线交付前跑一遍：

```bash
python -c "from contract import *; print(CONTRACT_VERSION)"
```

`AssetResult.validate()` 和 `AudioResult.validate()` 会在字段不合规时直接抛错，不用等到合并才发现。
