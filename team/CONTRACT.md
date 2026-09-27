# 接口契约 v1（冻结版）

**契约版本**：`zj-team4-v1`
**状态**：冻结。任何字段改动需先改 `contract.py` 并在 AI 日志记一条 Entry。

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

**最后一个必须是永远可用的本地兜底**——`OfflineAssetProvider`（`media.py:30-86` 那个程序化生成场景图的实现，丑但能跑）。它是演示永不翻车的保险。

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
