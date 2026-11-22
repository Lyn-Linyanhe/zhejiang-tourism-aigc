# 绍兴文旅知识库小规模试点（2026-11-21）

本轮把知识库从杭州、宁波扩展到绍兴：新增 **10 个实体、29 条事实**，每条事实都有独立的 `fact_records` 来源记录；同时整理了 **9 个实体的 Wikimedia Commons 图片候选**，逐张下载、目视核对并记录作者、许可和画面限制。没有把任何候选标成已获发布授权。

## 交付物

- [绍兴实体与事实](D:/some-project/zhejiang-tourism-aigc/data_zhejiang_kb/shaoxing.json)
- [绍兴实体媒体候选清单](D:/some-project/zhejiang-tourism-aigc/docs/research/shaoxing-entity-media-candidates.csv)
- [知识库元数据](D:/some-project/zhejiang-tourism-aigc/data_zhejiang_kb/_meta.json)：新增绍兴条目、移除待规划城市、补充场景标签与来源。
- 本地核验图：`runtime/_wire_probe_shaoxing_20261121/media/`（运行产物，不提交）。

## 实体覆盖

| 实体 ID | 类型 | 实体 | 事实数 | 主要来源 | 媒体情况 |
|---|---|---|---:|---|---|
| `sx-lanting` | scenic | 兰亭 | 3 | 兰亭景区官网 | 2 张细节（鹅池碑亭、御碑），无全景 |
| `sx-luxun-guli` | scenic | 鲁迅故里·沈园 | 2 | 景区官网 | 1 张入口照壁，已因 Commons 删除请求停用 |
| `sx-dayuling` | scenic | 大禹陵 | 3 | 大禹陵景区官网 | 1 张 1930 年历史照片 |
| `sx-anchang` | scenic | 安昌古镇 | 3 | 人民日报海外版、社科在线 | 1 张傍晚沿河街市 |
| `sx-cangqiao` | scenic | 仓桥直街 | 3 | 新华网 | 2 张巷弄/街角，非河街全景 |
| `sx-huangjiu` | intangible | 绍兴黄酒酿制技艺 | 3 | 中国非物质文化遗产网 | 1 张杯中酒液，非酿造现场 |
| `sx-yueju` | intangible | 越剧 | 3 | 中国非物质文化遗产网 | 1 张舞台演出 |
| `sx-lianhualuo` | intangible | 绍兴莲花落 | 3 | 中国非物质文化遗产网 | 未找到开放许可演出影像 |
| `sx-wupengchuan` | intangible | 乌篷船制作技艺 | 3 | 新华网 | 1 张水巷乌篷船实景 |
| `sx-kuaijishan-fei` | scenic | 会稽山古香榧群 | 3 | FAO GIAHS | 1 张炒制果实细节，非古树 |

覆盖类型仍不完整：绍兴美食、节庆、历史人物、传说等类型尚未采集；这 10 个实体是 schema 与来源流程的扩展样本，不是绍兴全量文旅库。

## 事实与来源口径

- `facts` 继续保留字符串数组，未改成对象数组，避免破坏 prompt 渲染。
- 每条 `facts` 与一条 `fact_records` 一一对应；记录含 `source_url`、`source_title`、`source_publisher`、`source_id`、`evidence_quote`、`fact_type`、`retrieved_at`、`checked_at`、`valid_until`、`license_status`、`reuse_status`。
- 只摘短事实并保留原文短引，不复制官网或媒体的整段叙述。
- 全部 29 条事实的 `reuse_status` 为 `fact-only-pending-license-review`：景区官网、中国非遗网、新华社/人民日报/社科在线、FAO 页面均未发现可直接套用的开放数据再分发许可，不能当作已获授权数据集发布。
- 事实核对日期统一为 2026-11-21；票价、开放时间、活动日期等时效信息没有写入永久事实。

主要来源：

- 兰亭景区官网：<https://www.sxlanting.com/>
- 鲁迅故里·沈园景区官网：<http://sxlxmuseum.com/>
- 大禹陵景区官网：<https://www.sxdayuling.com/>
- 中国非物质文化遗产网：<https://www.ihchina.cn/>
- 新华网仓桥直街报道：<https://www.news.cn/culture/20240426/b0122356585a4a7ab4aa2bc73a23c93f/c.html>
- 新华网乌篷船报道：<http://www.news.cn/photo/20250612/425b807578bf40a3b14f0c0649269839/c.html>
- 人民日报海外版安昌报道：<http://paper.people.com.cn/rmrbhwb/html/2024-08/28/content_26077325.htm>
- 社科在线安昌镇条目：<https://skzx.zjol.com.cn/ch133/system/2022/08/22/033795209.shtml>
- FAO 会稽山古香榧群：<https://www.fao.org/giahs/around-the-world/detail/china-kuaijishan/zh>

## 媒体候选与限制

所有候选都通过 Commons API 核对文件页、作者、许可、尺寸和修订信息，并下载 1280px 缩略图目视检查。候选状态统一为 `pending`、仅 `preview`；CC BY-SA 候选另需评估相同方式共享义务。

**一处例外**：`File:Luxun native place.JPG` 已被 Commons 列入 2026 年 11 月删除请求，理由是中国没有 2D 全景自由，而照壁上的画作作者李以泰仍在世——CC BY 3.0 只覆盖摄影者，不覆盖被摄的平面作品。该图在 CSV 中标为 `deletion-requested-fop-risk` / `blocked`，**连预览也不建议再用**，改用团队自摄或其他可授权素材。

| 实体 | 文件 | 许可 | 画面与限制 |
|---|---|---|---|
| 兰亭 | `Lanting, 2021-04-17 02.jpg` | CC BY-SA 4.0 | 实际是鹅池碑亭/题刻，不是兰亭全景；需署名并评估 SA |
| 兰亭 | `Lanting, 2021-04-17 07.jpg` | CC BY-SA 4.0 | 御碑亭内碑刻特写，人物和木栅栏入镜 |
| 兰亭 | `Lanting tablet.JPG` | CC BY 3.0 | 康熙御碑；下载被远端重置，未取得本地文件 |
| 鲁迅故里·沈园 | `Luxun native place.JPG` | CC BY 3.0 | **已停用**：2026-11-12 Commons 删除请求，中国无 2D 全景自由，照壁画作作者李以泰在世 |
| 大禹陵 | `Yu Mausoleum in Shaoxing (Tomb of Yu the Great).jpg` | Public domain | 1930 年历史照片，不可当现状 |
| 安昌古镇 | `Anchang Town (61724297).jpeg` | CC BY 3.0 | 傍晚沿河街市、红灯笼、摊位蒸汽；现代摊位可见 |
| 仓桥直街 | `Cangqiao Straight Street 03 2012-07.jpg` | CC BY-SA 4.0 | 巷弄，现代空调外机和招牌明显 |
| 仓桥直街 | `Cangqiao Straight Street 08 2012-07.jpg` | CC BY-SA 4.0 | 状元楼街角，现代车辆和限重标志入镜 |
| 绍兴黄酒 | `Shaoxing wine in a glass by udono in Tokyo.jpg` | CC BY-SA 2.0 | 杯中酒液特写，拍摄于东京，不是绍兴酿造现场 |
| 越剧 | `Romance of the Western Chamber Shaoxing opera 06.jpg` | CC BY 2.0 | 舞台演出；演员肖像和剧目权属需另行确认 |
| 乌篷船制作技艺 | `Black-Awning (Wupeng) Boat.jpg` | CC BY-SA 4.0 | 绍兴水巷实景；人物可辨识，发布前需确认肖像使用 |
| 会稽山古香榧群 | `Roasted Torreya grandis fruit placed in the palm of your hand.jpg` | CC0 | 炒制果实细节，不是古树或林下环境 |

明确缺口：

1. 兰亭没有可复核的开放许可全景或曲水流觞照片。
2. 大禹陵只有 1930 年历史照片，不能充作当代实景。
3. 仓桥直街没有河街全景，现有图偏巷弄/街角，且现代设施明显。
4. 香榧只有果实细节，缺少古树与林下环境。
5. 绍兴莲花落没有开放许可演出影像；不得用越剧或其他曲艺画面冒充。
6. 所有 CC BY-SA 候选只允许预览；正式发布需要逐项评估相同方式共享义务并保留署名。
7. 鲁迅故里的入口照壁图已进入删除流程，当前**没有任何可用的鲁迅故里开放许可图片**。

## 注入行为修正

原有注入块把资料统一写成“已核查，可放心引用”。这对杭州多数未逐条登记来源的事实、以及宁波/绍兴许可待核的事实是过度声明。本轮改为：

- 每个实体增加 `来源状态` 行，按实际 `fact_records` 匹配结果生成：
  - `逐条来源已登记（3/3），许可待核`
  - `部分事实有来源记录（1/3），其余仅作背景`
  - `尚未逐条登记来源，仅作背景参考`
- 注入头不再写“已核查，可放心引用”，改为“来源登记状态见条目；未逐条登记来源的内容仅作背景，不要当作已核实事实引用”。
- 使用规则 1 按证据质量分档，不再无条件要求“挑 3 条事实写进旁白”：
  - 全部逐条登记 → 挑 N 条写进旁白（N 按实际注入事实数封顶，最多 3）；
  - 部分有来源 → 只把逐条登记的事实写进旁白，其余仅提供氛围；
  - 全部无来源 → 仅作背景与用词参考，不要写进旁白。
- 使用规则新增：来源或许可状态不明确的内容不得改写成“已核实”“已批准”或具体荣誉。

这是“声明与证据一致”的最小修正；没有改变事实选择、城市隔离或 1500 字预算。

## 2026-11-22 审核修正

初版审核发现三处“看起来有出处、实际对不上”的问题，已全部修正：

| 问题 | 修正 |
|---|---|
| 鲁迅故里/沈园两条事实引了不含该文本的 `cgzn/*.htm` | 改引 `jqjs.htm` 原文；沈园地址按官网写“鲁迅路和延安路之间”，删掉官网没有的“318 号”“东侧” |
| `Luxun native place.JPG` 只标 `attribution-required` | 已确认 2026-11-12 Commons 删除请求（中国无 2D 全景自由、照壁画作作者李以泰在世），改标 `deletion-requested-fop-risk` / `blocked` |
| 头部说“仅作背景”、规则 1 却强制挑 3 条 | 规则 1 按证据分档，条数按实际注入事实数封顶 |

同时修的 P2：

- 事实与记录改为“规范化后比对 + 可选 `fact_index` 显式绑定”，转述型记录不再永远匹配不上；
- 许可待核判断不再用子串匹配（`preview` 含 `review` 会误判），只认显式放行；
- 被预算丢弃的实体不再影响 header 判断；
- 时效表述锚定日期（“至 2026 年已从业 50 余年”），并给记录加 `valid_until`；
- 产地/发源地的当代描述改现行区划（柯桥区、上虞区、嵊州市）；非遗名录“申报地区”是档案原文，正文不改但记录加 `notes` 说明沿革。

## 验证

```text
python -B -m unittest discover -s tests
Ran 200 tests in 35.351s
OK
```

其中知识库专项 29 项通过，覆盖：

- 三城统计（65 实体、248 事实）；
- 绍兴城市过滤、兰亭事实注入、英文检索词；
- 绍兴 10 个实体 facts 与 fact_records 一一对应、来源 URL、待核许可；
- 引文与所引页面一致、删除请求图片不得标 preview；
- 规则 1 与证据质量匹配、事实条数封顶、`preview` 不误判待核；
- 规范化匹配与 `fact_index` 绑定；
- 现行行政区名与时效表述锚定；
- 未知城市仍不串入杭州/宁波/绍兴事实。

媒体侧另行完成：Commons 元数据核对、9 张 1280px 缩略图下载、SHA-256 记录、逐张目视核对。未把任何候选审核为发布可用。

## 下一步

1. 补齐绍兴美食、节庆、人物、传说类型，仍按每类 5–10 个实体小步扩展。
2. 为杭州高价值实体补逐条 `fact_records`；当前杭州 46 个实体中只有 5 个有来源记录。转述型记录请用 `fact_index` 绑定对应事实，不要依赖逐字相等。
3. 为兰亭全景、大禹陵现状、仓桥河街、香榧古树、莲花落演出寻找可授权素材或安排自摄/索权。
4. 在生成侧增加事实使用/忠实度检查；当前只做到“来源状态可见”，还不能保证模型一定采用或保持事实。
5. 其余 8 个待规划城市（温州、嘉兴、湖州、金华、衢州、舟山、台州、丽水）继续按小试点推进。
