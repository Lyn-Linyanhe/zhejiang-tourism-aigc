# 浙里成片 · 四人分工

**契约版本**：`zj-team4-v1`
**项目目录**：`zhejiang-tourism-aigc`

---

## 这是什么

一份**让四个人同时开工、全程不用互等**的分工方案。

核心机制一句话：**链路从第一天起就是通的，只是内容是假的。** 之后每合入一条真实产线，成片就升级一格。

---

## 怎么开工

### 第一步：拿到项目

```bash
cd zhejiang-tourism-aigc
cp .env.example .env
notepad .env          # 填自己的 DeepSeek key 和其它凭据
```

> 每人用**自己的** key，不要共用。配额和账单混在一起，出问题查不出是谁。

### 第二步：确认环境能跑

```bash
python -m unittest discover -s tests     # 期望 11/11
```

跑不过先别开工，找负责人。

### 第三步：读你那份分工文档

| 人 | 先读 | 然后 |
|---|---|---|
| **P1** 编排骨架 | `team/分工/P1_编排骨架.md` | 用 stub 跑通全链路 |
| **P2** 画面线 | `team/分工/P2_画面线.md` | **第一天就去申请 API key** |
| **P3** 声音线 | `team/分工/P3_声音线.md` | 先确认 cues 形状 |
| **P4** 渲染线 | `team/分工/P4_渲染线.md` | 先跑 `team/假素材包/make_fixtures.py` |

**四条线都不需要等别人。** 每个人的输入要么是契约，要么是假素材包。

---

## 目录

```
zhejiang-tourism-aigc/
├── backend/app/providers/
│   ├── contract.py          ← 接口契约（代码版，可直接 import）
│   └── stubs/               ← 契约 stub，顶替 P2/P3
│
└── team/
    ├── README.md            ← 你在这
    ├── CONTRACT.md          ← 契约说明（人读版）
    ├── 假素材包/
    │   ├── README.md
    │   ├── make_fixtures.py ← 一键生成测试素材
    │   └── fixtures/        ← 已生成好（6 图 / 音频 / cues）
    ├── 分工/
    │   ├── P1_编排骨架.md
    │   ├── P2_画面线.md
    │   ├── P3_声音线.md
    │   └── P4_渲染线.md
    ├── AI日志/
    │   ├── README.md        ← 日志规范（必读）
    │   └── P1~P4-AI-LOG.md
    └── 合并计划.md          ← 负责人用
```

---

## 四条产线

| | 职责 | 拥有文件 | 独立验收 |
|---|---|---|---|
| **P1** | 契约落地、编排、配置、API、前端 | `registry.py` `orchestrator.py` `config.py` `models.py` `main.py` `frontend/` | stub 全链路出片 |
| **P2** | 云文生图 + 图生视频 + 一致性 | `image.py` `video.py` `consistency.py` `cost.py` | 命令行单独出图出视频 |
| **P3** | TTS + 时间戳 + 音乐 + 混音 | `tts.py` `music.py` | 命令行单独出带 cues 的配音 |
| **P4** | 运镜 / 转场 / 调色 / 字幕 / 音画同步 | `renderer.py` `media_ffmpeg.py` | 假素材单独出成片 |

---

## 三条纪律

### 1. 契约不许私自改

契约是四条产线**唯一的耦合点**。加字段、改字段、删字段，都必须：

```
改 backend/app/providers/contract.py → 在 AI 日志标注 → 通知全员 → 各自跟进 → 再合并
```

负责人合并前会 grep 日志，只要出现非「无」的契约影响就停下同步。

### 2. 文件所有权是硬边界

**每人只改自己名下的文件，别人的只读。** 详见各自的 `team/分工/P*.md`。

本项目不上远程仓库、没有版本控制，负责人只能靠文件清单判断你有没有越界。**一改别人的文件就会在 diff 里显形。**

### 3. AI 日志必须如实

> **AI 说「能跑」不算数，`Verification` 里必须贴真实命令和真实输出。**

没验证就写「未验证」——**写「未验证」比写一个假的好结果有价值得多**。

---

## 交付方式

各自完工后，把成果打包发回给负责人合并。

**只发你名下的文件 + 你的 AI 日志**：

```
P4-交付-<日期>/
├── backend/app/services/renderer.py
├── backend/app/providers/media_ffmpeg.py
├── P4-AI-LOG.md
└── 说明.md          ← 新增环境变量、新依赖、验证命令与结果
```

详见你那份分工文档末尾的「交付方式」章节。

---

## 已知的坑

| 坑 | 谁 | 位置 |
|---|---|---|
| API key 要第一天申请，是唯一真阻塞 | P2 | `team/分工/P2_画面线.md` |
| 图生视频字段名必须查方舟文档，不能凭记忆 | P2 | 同上 |
| `zoompan` 必须先放大 2 倍否则抖动 | P4 | `team/分工/P4_渲染线.md` |
| xfade 会打破 30.000 秒指标 | P4 | 同上 |
| `siliconflow` 看着像本地其实是云 | P3 | `team/分工/P3_声音线.md` |
| 阶段顺序改为 audio 先于 assets | 全员 | `team/CONTRACT.md` |

---

## 验证这套东西能用

```bash
cd zhejiang-tourism-aigc

# 1. 契约自检
python -c "
from backend.app.providers.contract import CONTRACT_VERSION
from backend.app.providers.stubs import StubAssetProvider
print('[ok]', CONTRACT_VERSION)
"

# 2. 生成假素材包
python "team/假素材包/make_fixtures.py"

# 3. 底线测试
python -m unittest discover -s tests     # 期望 11/11
```
