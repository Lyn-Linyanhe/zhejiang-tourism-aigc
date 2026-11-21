# PR #1 本地审查与整改（2026-11-21）

对应 PR：[Add audio pipeline and entity-licensed media pilot](https://github.com/Lyn-Linyanhe/zhejiang-tourism-aigc/pull/1)。两条独立只读审查轴检查的是固定提交 `b89698f9f66ae93698e0bd9c31622f9629171818` 相对 `bf78f3944190f2f17071c3bd71a183fdc6c01918` 的差异；下表记录随后在本地复现和修正的结果。子代理审查不是 GitHub review approval，也没有代用户合并 main。

## 审查发现及整改

| 审查轴 | 发现 | 整改与回归 |
|---|---|---|
| Standards / P1 | 授权选曲失败后，目录回退仍可能取到未批准音乐 | 移除任意目录文件回退；缺少授权音乐明确失败。显式开启的技术预览可使用合成音调，发布模式禁止该回退。`test_music_failure_does_not_use_unapproved_directory_file` |
| Standards / P1 | 任务主题中的更长别名覆盖单镜头明确实体 | 先解析镜头标题、画面和旁白；仅空镜头描述回查主题。`test_shot_entity_name_beats_task_theme` |
| Standards / P2 | 预览绑定素材驳回后，可跳过素材阶段重渲 | 读画面前复验当前库记录、驳回状态、城市、实体、意图和两个文件路径的 SHA-256。`test_revoked_bound_preview_cannot_skip_asset_checks_on_rerender` |
| Standards / P2 | 音频实测时长不足 1 秒或总和不同于请求时长，导致失败草稿无法原样保存 | 未改变的正有限实测值可保留；显式时长修改继续要求单镜范围及总和合法；任务目标时长变更也会触发总和校验。服务层与真实 HTTP 均有回归。 |
| Standards / P2 | `TaskInput` 插入新参数破坏旧位置构造的音量含义 | 新参数放在旧 `voice_volume` / `music_volume` 之后。`test_legacy_positional_task_input_volumes_are_unchanged` |
| Spec / P2 | 使用范围等证据变动后仍保留发布批准 | 证据变动会撤销发布资格并退回授权待审、仅预览；原已驳回素材保留驳回状态，不能因改证据恢复预览。重新批准须明确提交且通过完整校验。另要求真实 `source_url`，标题不能替代。新增三项审核回归。 |
| Spec / P2 | 平衡调整两镜时长仍因逐镜 PATCH 中间态失败，并可能留下任务级修改 | 增加任务 PATCH 的 `shots` 数组，全部校验后一次保存；前端改为一次请求。服务层及 HTTP 验证失败请求不部分落库。 |
| Spec / P2 | `scenario_tags` 未参与实际镜头实体解析 | 镜头解析启用场景标签召回。`test_unambiguous_shot_scenario_tag_resolves_entity` |
| Spec / P2 | AI `AssetResult.event` / `remote_task_id` 在适配器中丢失 | 事件和远端回执进入任务 trace、素材 provenance，供 manifest 继续消费。`test_ai_remote_receipt_survives_adapter_and_task_trace` |

音频工作台与天依代码来自此前用户要求的音频版本合并，超出实体试点本身，但属于既有授权工作；本轮未回退。此次修正针对已复现边界，没有宣称所有历史音乐重渲路径都已完成独立授权审计。

字幕改动单独提交为 `4041f37`。其原 SRT 与时间保留、ASS 安全转义、中文断行和真实 FFmpeg 烧录均有专项验证。

## 最终自动化验证

```text
python -B -m unittest discover -s tests -v
Ran 159 tests in 37.991s
OK
```

包括 14 项审查回归、12 项真实本地 HTTP 素材/草稿回归，以及真实 FFmpeg 渲染与字幕用例。

以下命令均成功退出：

```text
node --check frontend/app.js
node --check frontend/voice-studio.js
python -m compileall -q backend tests scripts/collect_entity_pilot.py scripts/accept_entity_pilot.py
python -I -m py_compile scripts/collect_entity_followup.py
python -I scripts/collect_entity_followup.py --help
git diff --check
```

采集脚本另有真实两文件下载、独立回执/哈希核验与待审登记记录。全量 unittest 不包含公开网络下载测试。

## 页面验收与证据

使用 `runtime/_wire_probe_gui_entity_20261121` 新建独立数据库和媒体目录，从原试点库只读复制 7 张素材，并准备 `gui_entity_review_20261121` 四镜草稿。服务监听 `127.0.0.1:8792`，provider 为 offline；原库、普通运行数据、`.env` 和曲库未覆盖。预置草稿和已有绑定只作为测试入口，不计作 GUI 创建或绑定成功。

正式测试期间未修改被测代码，未注入点击、请求或页面状态。每个下列结论都对应已查看截图和 DOM 读取：

| 测试点 | 结果 | 证据 |
|---|---|---|
| 桌面首页及草稿冷加载 | 首页连接状态为 offline；DOM 有四镜及既有库绑定。截图确认首页布局。 | [桌面首页](../../runtime/_wire_probe_gui_entity_20261121/gui-evidence/t1-desktop-home.png) |
| 多镜时长编辑 | 两个输入分别显示 4.25 / 3.25，其他镜头仍为 3.75；绑定选项保留。点击保存超时，没有成功提示，访问日志未出现任务 PATCH，保存闭环受阻。 | [第一镜编辑](../../runtime/_wire_probe_gui_entity_20261121/gui-evidence/t2-shot1-edited.png)、[第二镜编辑](../../runtime/_wire_probe_gui_entity_20261121/gui-evidence/t2-shot2-edited.png)、[保存超时现场](../../runtime/_wire_probe_gui_entity_20261121/gui-evidence/t2-save-timeout.png) |
| 待审筛选 | 选择“待审核”成功，控件值为 `pending`，DOM 中待审卡片为 2 张。截图确认控件反馈；滚动到卡片和后续截图超时，因此两卡视觉及审核操作未完成。 | [筛选反馈](../../runtime/_wire_probe_gui_entity_20261121/gui-evidence/t3-pending-filter.png) |
| 390×844 响应式分镜区域 | 截图成功，当前可见分镜表单单列排列，实体/意图/既有绑定可见，无明显横向溢出。仅覆盖该区域；不代表完整手机流程。 | [手机分镜区域](../../runtime/_wire_probe_gui_entity_20261121/gui-evidence/t4-mobile-shot.png) |

测试结束恢复 1280×720，保留本地验收页面。完整审核/驳回/重新绑定及保存的 GUI 验收仍待补；浏览器运行时不支持上传，未测试 GUI 文件上传。运行时没有已验证的 console 接口，未收集 console 日志，不能据此宣称没有 console 错误。工具超时不足以判定产品功能失败，也不能计作通过。

## 交付边界

最新隔离采集库有杭州 5 实体、7 张照片：原 5 张满足发布筛选，两张新候选均为待审、仅预览。原五镜样片没有被新候选替换。每实体 2–3 类镜头、宁波授权媒体、Pexels 视频回归和全片动态/听感验收仍待完成。GitHub 没有配置 CI 或远端 review decision，`MERGEABLE` 不能替代这些检查。
