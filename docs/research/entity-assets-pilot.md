# 实体素材试点与验收记录（2026-11-20）

本轮完成了“实体—镜头意图—素材—授权—逐镜 manifest”的小规模闭环，并生成了一条 30 秒本地验收片。当前素材覆盖 **杭州 5 个实体、每实体 1 张照片**，包含 4 个全景和 1 个茶汤细节。尚未达到每实体 2–3 类镜头的扩充目标，宁波非遗授权画面与实拍视频仍是缺口。

## 可以直接查看的交付

- [最终试点 MP4](../../runtime/_wire_probe_entity_assets_20261120/media/entity_pilot_20261120_104452/final.mp4)
- [逐镜 manifest](../../runtime/_wire_probe_entity_assets_20261120/media/entity_pilot_20261120_104452/manifest.json)
- [任务与 trace](../../runtime/_wire_probe_entity_assets_20261120/media/entity_pilot_20261120_104452/task.json)
- [事实来源](../../runtime/_wire_probe_entity_assets_20261120/media/entity_pilot_20261120_104452/fact-records.json)
- [随片署名与改动说明](../../runtime/_wire_probe_entity_assets_20261120/media/entity_pilot_20261120_104452/ATTRIBUTION.md)
- [FFprobe 记录](../../runtime/_wire_probe_entity_assets_20261120/media/entity_pilot_20261120_104452/ffprobe.json)
- [素材清单 CSV](entity-assets-pilot.csv)
- [原始采集清单及逐文件元数据回执](../../runtime/_wire_probe_entity_assets_20261120/downloads/index.json)

离线试点服务为 `http://127.0.0.1:8790`，使用 `runtime/_wire_probe_entity_assets_20261120` 独立数据库。审核草稿入口：

<http://127.0.0.1:8790/#task=entity_pilot_20261120_104452_review>

这些链接是本地试点产物，不代表已经对外发布。原有 `.env`、常规运行数据库、媒体和音乐库未被覆盖；8787 上的进程保留。

## 首批素材清单

每个文件均通过 Commons API 核对作者、许可、文件页及修订号，下载后保存 SHA-256，并目视检查画面。上传默认待审，试点的单独验收脚本才将以下五条审核为可用于发布模式；脚本不会批量批准其他候选。

| 实体 ID | 本地文件 / 素材 ID | 镜头意图 | 作者与许可 | 来源与使用限制 |
|---|---|---|---|---|
| `hz-xihu` | `hz-xihu.jpg` / `library_6b733b177a51e27f` | `wide` | Wanderingchina / CC BY 4.0 | [2023年12月西湖航拍全景](https://commons.wikimedia.org/wiki/File:Aerial_panorama_of_West_Lake_and_its_surrounding_mountains._December_2023.jpg)。静态航拍照片，不能称实拍航拍视频。 |
| `hz-duanqiao` | `hz-duanqiao.jpg` / `library_669e3632ed816282` | `wide` | Suicasmo / CC0 | [断桥 20250505](https://commons.wikimedia.org/wiki/File:Broken_Bridge_(Hangzhou)_20250505.jpg)。雨中行人与桥体，没有雪；不能描述为雪景。 |
| `hz-leifengta` | `hz-leifengta.jpg` / `library_282be9ea777ab904` | `wide` | lienyuan lee / CC BY 3.0 | [雷峰塔](https://commons.wikimedia.org/wiki/File:%E9%9B%B7%E5%B3%B0%E5%A1%94_Leifeng_Pagoda_-_panoramio.jpg)。湖面、塔与远山，塔身细节仍需另采。 |
| `hz-lingyinsi` | `hz-lingyinsi.jpg` / `library_d732aba57838740e` | `wide` | Yiwa / CC BY 3.0 | [灵隐寺大雄宝殿](https://commons.wikimedia.org/wiki/File:Mahavira_Hall_of_Lingyin_Temple_20061010.jpg)。展示殿宇，不是飞来峰造像。文件另列 GFDL，本试点选择 CC BY 3.0。 |
| `hz-longjing` | `hz-longjing.jpg` / `library_09d37c103444f566` | `detail` | Gary Stevens / CC BY 2.0 | [梅家坞龙井茶特写](https://commons.wikimedia.org/wiki/File:Longjing_tea_2.jpg)。茶汤/茶叶细节，不是炒茶、采茶过程。 |

CC BY 素材应随片保留作者、原始页、许可链接和改动说明。本片改动包括竖屏裁剪、尺寸调整、调色、缓慢运镜、转场、字幕与合成配音。五条许可并不替代人物、音乐或其他内容的权利核查；本片未使用背景音乐。

原图 API 有时将请求的缩略尺寸映射到另一种实际尺寸。最终技术字段以 provider 解码得到的 `provenance.width/height` 为准，采集索引中的 `width/height` 是 API 报告值。

## 事实和画面分别追溯

杭州五个试点实体增加了 `fact_records`，保持原有 `facts` 字符串不变：

- 西湖、断桥：本镜仅描述核对过的照片，不引用本轮未取到官方正文的 UNESCO 年份或断桥位置信息。
- 雷峰塔新塔 2002 年落成：[景区官网](http://en.leifengta.com/about.aspx?c_kind=2&c_kind2=10)，支持原文 `The new pagoda is completed on Oct.25th,2002.`。
- 灵隐寺始建于 326 年：[寺院官网](https://en.lingyinsi.org/intro/)，支持原文 `Lingyin Temple, founded in 326AD`。
- 西湖龙井与 2022 年中国传统制茶非遗项目的关系：[杭州市政府英文网](https://www.ehangzhou.gov.cn/2025-03/21/c_293024.htm)，支持原文 `In late 2022, China's traditional tea-making, of which West Lake Longjing tea is an important component, was inscribed on the UNESCO intangible cultural heritage list.`。

短事实来源记录不授予其网页叙述或图片许可；图片许可在独立素材记录中保存。

## 实现后的行为

- 新上传固定为待审核、授权待审核、仅预览；上传参数不能自行通过审核。
- 上传的任务/镜头、城市、实体与标签校验先于写文件和登记；不合法绑定不会留下库记录。
- 重复文件按 SHA-256 去重，保留已有人工审核；不能用重新上传覆盖审核凭证。
- 前端提供实体/意图/审核筛选、图片/视频预览、许可字段、独立审核和逐镜绑定；刷新库不重建脚本编辑区。异步加载会保留已保存的库绑定。
- 正式模式只从已审核实体库选材，要求精确实体/意图、授权证据完整、未过期且文件指纹不变。预览实体检索也不跨城取最近素材。
- 正式重渲在焦点检测和 FFmpeg 读取之前复验任务城市、实体、当前库审核、镜头/素材记录与文件 SHA-256；不因跳过素材阶段而绕过门槛。
- manifest/trace 逐镜保存来源、实体、许可、指纹、查询词和媒体尺寸。
- Pexels 当前提供照片候选，记录实际查询与原始页；AI 记录 `ai-generated`。Pexels 视频搜索回归仍单独待修。

## 自动化和成片验收

最终后端回归为 **134 tests，OK**。覆盖上传 HTTP、错误绑定无副作用、审核值域、空白凭证、重复上传、取消绑定、文件变化、授权撤销、跨城预览/重渲、素材记录不一致和 manifest 来源。

通过的命令：

```text
python -m compileall -q backend tests scripts/collect_entity_pilot.py scripts/accept_entity_pilot.py
node --check frontend/app.js
node --check frontend/voice-studio.js
git diff --check
python -B -m unittest discover -s tests -v
```

最终样片：H.264、720×1280、25 fps、30.000000 秒；AAC、48 kHz、单声道；配音为 Windows 本地 Microsoft Huihui Desktop，无背景音乐。SRT、烧录字幕及右上 AIGC 标识均生成。逐镜许可与实体通过断言核对，旁白在成片结束前结束。

抽查五镜画面，目视核对照片主体、字幕和 AIGC 标识；静态抽帧不替代全片运动与听感验收。

## 页面验收结果与限制

准备：以独立试点数据库启动离线服务，用采集脚本登记候选、单独验收脚本审核五条并创建草稿。草稿由脚本准备；不计作从首页提交预览的 GUI 成功。

- 首页状态可读为 offline，桌面布局正常：[截图](../../runtime/_wire_probe_entity_assets_20261120/gui-evidence/t1-offline-home.png)。
- 审核草稿可加载，五条素材卡片、作者/许可/有效期和发布状态存在：[桌面截图](../../runtime/_wire_probe_entity_assets_20261120/gui-evidence/t2-library-desktop.png)。DOM 读取能列出每镜的实体、镜头意图和匹配库素材。
- 检查发现冷加载时绑定下拉会显示“自动选材”，已修复初始占位保留；[修复后卡片截图](../../runtime/_wire_probe_entity_assets_20261120/gui-evidence/t3-library-after-fix.png)。完整交互复验仍未完成。
- 浏览器的首页提交、滚动和后续截图出现超时，未以注入点击或改页面状态强行继续；审核/筛选/保存的完整 GUI 闭环不能标为通过。
- 390×844 的视口已设置，但截图连续超时，窄屏视觉验收未完成；已恢复 1280×720。
- 当前浏览器运行时不支持文件上传；相应端到端 GUI 操作未执行。HTTP 上传回归通过，但不等同于 GUI 上传验收。
- 浏览器未提供已验证的 console 日志接口，本轮未收集 console；未宣称没有 console 错误。

## 剩余素材缺口和下一批任务

1. 五个实体目前只有 1 类照片：补环境、建筑/器物细节、人物动作或工艺过程，每实体最终达到 2–3 类；缺少合法素材的类型明确留空。
2. 拱宸桥有已核 CC0 的 1919 年历史照片线索：[文件页](https://commons.wikimedia.org/wiki/File:Three-arch_Bridge_Konztu_Chiao.jpg)。下载请求被远端重置，未入库；即便取得也须标注历史年份，不能充作当代运河实景。
3. 宁波红帮官方入口：[市非遗中心](https://www.ihningbo.cn/directory/show/129)，咨询电话 0574-87195071、非遗馆 0574-87191860。[浙江非遗项目页](https://www.zjich.cn/xiangmu/xiangmushow.html?id=6715)有 `All Rights Reserved`；[宁波文旅局生产性保护基地报道](https://wglyj.ningbo.gov.cn/art/2024/4/1/art_1229057571_58928476.html)含照片但未见开放许可。均只作联系索权线索；本轮未联系机构。
4. 宁波金银彩绣、宁海平调、象山竹根雕未确认开放媒体文件，继续按实体索权或自摄，不用无关图片补齐数量。
5. 重做完整桌面/窄屏 GUI 审核与绑定验收；修复 Pexels 视频回归；再扩城市。
6. 2026-11-21 首次下载断桥雪景和雷峰塔顶棚时连接中断，后从 Commons 核验文件修订及许可，使用其 1280px 缩略版本重试成功。两张 JPEG 均为 1280×960，经目视及 SHA-256 复核后登记到同一隔离试点库，审核和授权状态均为 `pending`，仅限 `preview`；发布查询不返回它们。见[第二批候选清单](entity-assets-followup.csv)和[采集索引及许可证据](../../runtime/_wire_probe_entity_assets_20261121/downloads-retry/index.json)。原五镜成片未替换素材。

当前隔离库覆盖杭州 5 个实体、7 张照片（5 张满足发布筛选条件、2 张待审）。雷峰塔已有全景与细节两类候选；断桥新增雪景，但两张都属于全景。飞来峰笑佛、龙井炒茶的 CC BY-SA 候选以及 `PD-CN` 采茶史料仅作复用待评估线索，不计作入库。西湖环境视频尚无已核可用文件。下载照片、试点数据库和成片均留在本地。
