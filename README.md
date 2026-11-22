# 浙江文旅 AIGC 短视频系统

这是一个可实际运行的单机 Web 应用，不是静态页面或演示数据。完整流程是：

```text
用户输入 → DeepSeek 生成脚本/分镜 → 用户编辑并确认
→ 素材 provider / 用户上传素材 → TTS / 用户上传音频
→ FFmpeg 合成字幕、音频和 MP4 → 任务档案与可追溯元数据
```

## 运行要求

- Windows 10/11
- Python 3.11 或更高版本。Python 3.12 可以直接使用本项目。
- FFmpeg 和 FFprobe 已加入 PATH（首次启动会自动补下载；离线包已随包提供）
- Chrome、Edge 或 Firefox
- DeepSeek API key（真实脚本生成）
- 若不使用上传素材，需要配置 `PEXELS_API_KEY` 或接受技术测试场景图
- 若需要背景音乐，需要上传音乐或把授权音乐放入 `MUSIC_DIR`

## 安装和启动

1. 安装 Python 3.11 或 3.12，并将其加入 PATH。
2. 双击 `START.cmd`。首次启动会联网安装 Edge TTS。若系统和项目目录均未提供 FFmpeg 与 FFprobe，启动流程会下载所需工具。
3. 服务就绪后打开 [浙里成片](http://127.0.0.1:8790/)。默认配置可用来验证本地创作流程。离线验证时可选 Windows 系统语音或上传配音，并选择「无音乐」。

使用联网服务时，将 `.env.example` 复制为本地 `.env`，按所选文本、素材、图库和音频服务填写配置。曲库初始为空，可在系统页导入已获授权的音乐。

[11 月 21 日更新说明](docs/2026-11-21.md)

## 真实创作与素材

- 本包的 `.env` 不含任何 API key，预设 `DEFAULT_PROVIDER=offline` 和 `ASSET_PROVIDER=offline`。该模式用于验收流程，离线场景图不等同于真实旅游照片。

## AI 画面与跨镜一致性

- Pexels 现在优先查询 `/videos/search`，返回真实 MP4 时标记 `media_type=video`；只有在允许降级时才回退照片，并明确标记为 `image`。
- `subject_lock` 会作为统一的身份/形制条件写入每个镜头；启用主体锁定时必须同时提供 `subject_reference_path`。参考图会随请求发送，并用 SHA-256 校验所有镜头引用的是同一张图。只写主体描述会被验收标为 `prompt-only`，不会冒充真正的图像条件锁定；风格词、seed 和调色也不再被当作证明。
- 素材阶段会写出 `visual_qa.json`，其中包含真实视频镜头数、解码后的对比度/清晰度/熵/裁剪比例、跨镜亮度与色彩漂移。最终 `manifest.json` 会嵌入该报告。
- 独立验收命令：`py -3.12 scripts/verify_visual_acceptance.py --task-id task_xxx --data-dir runtime`。默认要求至少 4 个可解码视频镜头；纯静图离线 smoke 请显式传 `--min-video-shots 0`。

## 使用流程

1. 在创作工作台填写城市、地标、主题、文化线索、受众、时长、镜头数、风格、配音、音乐和输出规格。
2. 点击“生成脚本预览”。此时只调用文本 provider，不渲染视频。
3. 修改标题、摘要、CTA、完整旁白、分镜画面描述、每镜头旁白、字幕、时长、转场和静图运动。焦点 X/Y 留空时自动分析图像细节；需要指定主体时可填 0–1 的归一化坐标。
4. 选择素材来源：
   - `使用服务端配置`：使用 `.env` 的 `ASSET_PROVIDER`；
   - `每个镜头使用上传素材`：每个镜头都必须绑定图片或视频；
   - `Pexels 在线素材`：需要 `PEXELS_API_KEY`；
   - `技术测试场景图`：只用于技术验收，不应直接用于正式发布。
5. 可为任意任务上传配音和音乐。上传音频会覆盖对应 provider。
6. 点击“保存脚本、分镜和参数修改”。
7. 点击“确认并生成实际视频”。只有这一步才会执行素材、音频和 FFmpeg 渲染。

转场作用在相邻镜头的交界处，以原镜头边界为中心，时长约 0.5 秒；第二个及之后镜头的“转场”选择决定它如何进入。静图按自动识别或手动指定的焦点平滑推近/拉远，视频素材保留原有运动。自动焦点依据画面局部对比度估算，无法识别“塔”“人”等语义，重要主体可手动指定焦点坐标。实际焦点与转场记录在成片目录的 `manifest.json` 中。
8. 在任务面板检查 MP4、SRT、配音、音乐、素材来源和 trace。

## 生产素材和音频

### Pexels 图片

```dotenv
ASSET_PROVIDER=pexels
PEXELS_API_KEY=REPLACE_WITH_PEXELS_KEY
PEXELS_BASE_URL=https://api.pexels.com/v1
```

### 上传素材

选择 `每个镜头使用上传素材`，生成预览后逐个选择镜头上传图片或视频。系统会保存 source、license、author 和 usage_scope。

### 配音

配音可选择以下来源：

1. 任务上传的配音；
2. `.env` 配置的 OpenAI 兼容 TTS；
3. Edge TTS；
4. Windows 系统语音。

配音不可用时，任务会记录服务错误。合成音调适用于明确开启的技术自检。

建配音运行时（只装 edge-tts，几 MB，任意 Python 3.9+）：

```powershell
.\scripts\setup-edge-tts.ps1
```

> 换机器后必须重跑：`.venv-audio` 不能跨机器拷贝——venv 会把建立时的 base python 绝对路径写进 `pyvenv.cfg`，换台机器就起不来。要连字级对齐（whisperx）一起装才用 `setup-audio-runtime.ps1`，那个需要 Python 3.11 且要下数 GB。

**Edge TTS 是联网的免费通道**（微软 Edge 朗读接口），不是本地合成；真正的本地兜底是第 4 项 Windows 系统语音，只在 Edge 失败时才用。可选中文音色共 8 个（晓晓/晓伊/小北/小妮/云希/云健/云扬/云夏），旧的"女声""男声"仍作为别名可用。

旁白现在按**完整句子连读**，保留逗号和顿号上下文，由语音引擎生成句内停顿，不再重复插入固定静音。字幕按实测句段时长同步；镜间停顿默认 0.15s，可在片段中调整。

脚本审核页的配音工作台支持服务端声库、Edge、Windows 系统语音和逐镜上传，支持整轨／单段试听。服务端音色使用真实 voice ID，配置见 [配音工作台说明](docs/voice-studio.md)。

素材来源“在线实拍素材”检索 Pixabay 和 Pexels，优先视频并保存出处。服务端可配置 `PIXABAY_API_KEY` 或 `PEXELS_API_KEY`。图生视频可在分镜填相同主体组名，并给组中一个镜头上传参考图；生成结果提供首、中、尾帧对照。运行 `python scripts/accept_offline_30s.py --online` 可通过主流程验收杭州西湖样片。

联网依赖已更新为 `edge-tts==7.3.1`，修复本机旧版本的接口 403 问题；明确选择联网音源时，失败会报错，不静默更换声音。

### 音乐

上传音乐，或者把已获授权的音频文件放到 `runtime/music`。找不到匹配的授权曲目时，成片保留旁白，作品档案显示提示。

**光把文件放进目录还不够。** 情绪匹配走的是 `MUSIC_CATALOG_PATH`（默认 `runtime/music/catalog.json`），一条曲目要同时满足才会被选中：

1. 文件在 `MUSIC_DIR` 下且扩展名受支持；
2. catalog 里该条 `approved: true`；
3. 该条的 `moods` 含用户选的音乐情绪，`styles` 含用户选的叙事风格。

```json
{
  "tracks": [
    {
      "file": "曲子.mp3",
      "moods": ["舒缓", "雅致"],
      "styles": ["诗意纪实", "国风雅韵"],
      "license": "已授权 / 待确认",
      "author": "作者",
      "usage_scope": "使用范围",
      "approved": true
    }
  ]
}
```

可在作品档案查看实际使用曲目与授权记录。

## 验证

```powershell
python -m compileall -q backend tests
node --check frontend/app.js
python -m unittest discover -s tests -v
```

技术 smoke：

```powershell
$env:DEFAULT_PROVIDER = "offline"
$env:ALLOW_SYNTHETIC_AUDIO_FALLBACK = "true"
.\scripts\smoke-test.ps1
```

检查成片：

```powershell
ffprobe -v error `
  -show_entries format=duration:stream=index,codec_name,codec_type,width,height,channels `
  -of json `
  runtime\media\<task_id>\final.mp4
```

正式验收必须使用 `DEFAULT_PROVIDER=deepseek`，并确认：

```text
trace.provider = openai-compatible
trace.model = deepseek-chat
trace.fallback_used = false
render.has_audio = true
render.has_burned_subtitles = true
```

## 关键目录

- `backend/app/models.py`：输入、任务、镜头、素材和 trace 合约
- `backend/app/main.py`：HTTP API 和静态文件服务
- `backend/app/services/orchestrator.py`：阶段调度和 provider 组合
- `backend/app/providers/llm.py`：DeepSeek/OpenAI 兼容文本 provider
- `backend/app/providers/contract.py`：跨产线接口契约（素材、音频、渲染）
- `backend/app/providers/media.py`：素材 provider（离线场景图、Pexels、上传）
- `backend/app/providers/media_ffmpeg.py`：FFmpeg 调用、字幕、音频校验
- `backend/app/providers/tts.py`：TTS provider 与无凭据兜底实现
- `backend/app/providers/music.py`：背景音乐匹配与生成
- `backend/app/providers/stubs/`：契约 stub，供无外部依赖时跑通全链路
- `backend/app/services/renderer.py`：图片/视频素材、字幕、音频和 MP4 合成
- `frontend/`：完整创作和审核工作台
- `runtime/db/app.sqlite3`：任务持久化
- `runtime/media/<task_id>/`：任务媒体和 manifest

MoneyPrinterTurbo 是可选上游。它和本项目都使用顶层 `app` 包，因此通过子进程隔离调用；详细说明见 `docs/upstream-integration.md`。
