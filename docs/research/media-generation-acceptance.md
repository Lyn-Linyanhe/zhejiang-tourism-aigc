# 图生图 / 图生视频接入与验收（2026-11-22）

## 结论

网关的四个能力全部打通，并接进了项目的素材链路。**完整跑通一条真实链路：脚本 → 首帧生图 → 图生视频 → 渲染成片**，产出 15 秒、720×1280 的成片，4 个镜头全部走图生视频。

## 四个能力的实测结果

| 能力 | 端点 | 模型 | 实测 |
|---|---|---|---|
| 文生图 | `POST /v1/images/generations` | `raccoon/gpt-image-2` | ✅ 返回图片 URL，2.7 MB |
| 图生图 | `POST /v1/images/edits` | 同上 | ✅ 冷调→黄昏暖调，构图保留 |
| 文生视频 | `POST /v1/videos` | `workbuddy/minimax-video-h3` | ✅ 约 190 秒，82.14 credit |
| 图生视频 | `POST /v1/videos` + `image` | 同上 | ✅ 约 95 秒，82.14 credit |

## 三个必须记住的接口约束

这些都是踩过才知道的，写进代码注释和测试里了：

**1. 图生图只接受 JSON，`image` 必须是 data URI**

- multipart 上传 → `400 媒体接口只接受 JSON 请求体`
- http URL → `400 image 解码失败: 暂不支持 URL 形态`
- 正确做法：本地读图 → base64 → `data:image/png;base64,...`

**2. 视频是异步任务，绝不能重复提交**

创建返回 `{"id":"vid_...","status":"queued"}`，需要轮询 `GET /v1/videos/{id}` 到 `completed`。超时或 5xx 时远端可能已计费，**重试等于重复扣费**。代码里创建只发一次，轮询只重试查询。

**3. 视频参数有硬约束**

- `seconds` 必须 4–15
- `prompt` ≤ 2000 字符
- 项目的 `shot_count` 只能 4/6/8，`duration` 只能 15/30/60

## 代码改动

| 文件 | 改动 |
|---|---|
| `backend/app/providers/image.py` | 新增 `build_edit_payload()`、`image_file_to_data_url()`、`CloudImageProvider.edit()` |
| `backend/app/providers/video_gateway.py` | 新建。网关协议适配：`submit` / `poll` / `download` / `generate_video` |
| `backend/app/providers/video.py` | 新增 `_detect_api_style()` 三协议分流；新增 `_generate_gateway()` |
| `tests/test_media_gateway.py` | 新建，13 项离线测试 |

`_detect_api_style` 按 base_url 分流：

```text
http://127.0.0.1:8787/v1                    -> gateway
https://ark.cn-beijing.volces.com/api/v3    -> volcengine
https://x.tokendance.space/v1               -> tokendance
```

## 完整链路实测

任务 `task_ba335141e7a4`（兰亭，15 秒 4 镜）：

| 指标 | 值 |
|---|---|
| 状态 | **completed** |
| 4 个素材来源 | 全部 `cloud-i2v`（图生视频） |
| 每镜首帧 | `ai-image` 生成，4/4 就绪 |
| 单镜成本 | 0.8214（82.14 credit） |
| 单镜时长 | 5.17 秒 |
| 成片 | 15.0 秒 / 720×1280 / h264 + aac / 6.99 MB |

首帧事件与视频事件的对应关系可以从 trace 里核对：

```json
{"type":"first_frame","shot_id":"shot_1","status":"ready","provider":"ai-image"}
{"type":"video","provider":"cloud-i2v","remote_task_id":"vid_9eecd861147223f1",
 "credit":82.14,"seconds":"5","mode":"i2v"}
```

抽帧目视确认：画面是兰亭的曲水流觞、白墙黛瓦，实景质感，不是程序画的占位图。

## 一个踩过的坑

`AssetResult.validate()` 要求视频素材必须能说清"这一镜为什么长这样"：图生视频回填 `first_frame_path`，文生视频把 `event.mode` 标成 `t2v`。第一次实现时我漏了这个字段，直接被校验器拦住。这个约束是对的——一条来路不明的视频不该进成片档案。

## 配置

`.env`（已 gitignore）新增/修改：

```text
IMAGE_BASE_URL=http://127.0.0.1:8787/v1
IMAGE_MODEL=raccoon/gpt-image-2
IMAGE_SIZE=1024x1536
VIDEO_BASE_URL=http://127.0.0.1:8787/v1
VIDEO_MODEL=workbuddy/minimax-video-h3
ASSET_PROVIDER=ai-video
```

**注意**：机器上残留的系统环境变量会盖住 `.env`（`config.py` 的逻辑是"环境变量已存在就不覆盖"）。启动服务时需显式传参覆盖，否则会拿到旧 key 报 401。

## 测试

```text
python -B -m unittest tests.test_media_gateway -v
Ran 13 tests
OK

python -B -m unittest discover -s tests
Ran 234 tests
OK
```

新增 13 项覆盖：data URI 强制、空参考图拒绝、视频参数边界、轮询状态机（完成/失败/超时不重提）、空文件下载算失败、三协议识别。

## 成本记录

| 项 | 单价 |
|---|---|
| 图生图 | 未返回计费字段 |
| 图生视频（5 秒） | 82.14 credit = 0.8214 |

4 镜全链路约 4 × 0.8214 ≈ **3.29**（视频部分），首帧生图的成本网关没有返回。

## 参考生图已接线（2026-11-22 补完）

原本 `edit()` 写了但零调用，链路实际是"文生图出首帧 → 图生视频"，参考图完全没用上。现已接通：

| 环节 | 改动 |
|---|---|
| `services/reference_images.py` | 新建。按 `城市 + 点位 + shot_tag` 从 322 张小红书图里挑参考，同 seed 稳定、不同 seed 分散，跨城市绝不串 |
| `providers/registry.py` | `_CloudAssetAdapter` 补 `edit()` 转发；`create_or_find` 与 `generate_asset` 都支持参考图 |
| `services/orchestrator.py` | 首帧生成前用 `entity_id` 反查点位名，挑参考图并写进 payload |

**踩的两个坑**

1. adapter 没有 `edit` 方法 → `hasattr(provider, "edit")` 永远为假，参考图被静默忽略。补了转发才生效。
2. 参考图被同时传给视频 provider → 报"provider 不支持参考图生图"把整条链打断。改成只有 `kind == "image"` 才走 edit。

**实测结果**（任务 `task_50425754f8a3`，兰亭 15 秒 4 镜）：

```text
assets/shot_1_edit.png   assets/shot_2_edit.png
assets/shot_3_edit.png   assets/shot_4_edit.png
assets/shot_2.mp4        assets/shot_3.mp4      assets/shot_4.mp4
```

4 个首帧全部是 `_edit.png`（图生图产物），参考图事件 4 条，来源分别是 `68285a9d_2.webp`、`6a2bfab5_4.webp` 等不同图片——每镜挑到不同参考，不是同一张反复用。

**许可边界**：小红书图是 `reference-only`，只作为图生图的输入，生成结果是一张新图，原图不会被输出、不进素材库、不进成片。trace 里每条参考图事件都带 `note: "reference-only：仅作图生图输入，不输出原图"`。

## 还没做的

1. ~~图生图未接进编排~~：已接线，见上。
2. **参考图质量未做自动评分**：现在只按城市/点位/tag 匹配，没有判断"这张图适不适合这个镜头"（比如构图太挤、主体不清）。
2. **画面—旁白匹配仍是粗判**：现在能生成真视频了，可以抽帧做真正的匹配评分，但还没做。
3. **成本只覆盖视频**：图片接口不返回计费字段。
