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
- FFmpeg 和 FFprobe 已加入 PATH
- Chrome、Edge 或 Firefox
- DeepSeek API key（真实脚本生成）
- 若不使用上传素材，需要配置 `PEXELS_API_KEY` 或接受技术测试场景图
- 若需要背景音乐，需要上传音乐或把授权音乐放入 `MUSIC_DIR`

## 安装和启动

```powershell
Set-Location <解压后的项目目录>
Copy-Item .env.example .env
notepad .env          # 填 LLM_API_KEY / IMAGE_API_KEY / VIDEO_API_KEY
.\scripts\start.ps1
```

在 `.env` 中填写新的 DeepSeek key：

```dotenv
DEFAULT_PROVIDER=deepseek
ALLOW_PROVIDER_FALLBACK=false
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=REPLACE_WITH_NEW_KEY
LLM_MODEL=deepseek-chat
```

不要把 `.env` 提交到 Git。用户之前在聊天中发送的旧 key 已经暴露，必须撤销并重新生成。

浏览器访问 `http://127.0.0.1:8787`。

## 使用流程

1. 在创作工作台填写城市、地标、主题、文化线索、受众、时长、镜头数、风格、配音、音乐和输出规格。
2. 点击“生成脚本预览”。此时只调用文本 provider，不渲染视频。
3. 修改标题、摘要、CTA、完整旁白、分镜画面描述、每镜头旁白、字幕、时长和转场。
4. 选择素材来源：
   - `使用服务端配置`：使用 `.env` 的 `ASSET_PROVIDER`；
   - `每个镜头使用上传素材`：每个镜头都必须绑定图片或视频；
   - `Pexels 在线素材`：需要 `PEXELS_API_KEY`；
   - `技术测试场景图`：只用于技术验收，不应直接用于正式发布。
5. 可为任意任务上传配音和音乐。上传音频会覆盖对应 provider。
6. 点击“保存脚本、分镜和参数修改”。
7. 点击“确认并生成实际视频”。只有这一步才会执行素材、音频和 FFmpeg 渲染。
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

系统按以下顺序尝试：

1. 任务上传的配音；
2. `.env` 配置的 OpenAI 兼容 TTS；
3. Edge TTS；
4. Windows 系统语音。

如果都不可用且 `ALLOW_SYNTHETIC_AUDIO_FALLBACK=false`，任务失败并记录错误。合成音调只允许在明确打开该开关时用于技术 smoke，不会默认伪装成正式配音。

建配音运行时（只装 edge-tts，几 MB，任意 Python 3.9+）：

```powershell
.\scripts\setup-edge-tts.ps1
```

> 换机器后必须重跑：`.venv-audio` 不能跨机器拷贝——venv 会把建立时的 base python 绝对路径写进 `pyvenv.cfg`，换台机器就起不来。要连字级对齐（whisperx）一起装才用 `setup-audio-runtime.ps1`，那个需要 Python 3.11 且要下数 GB。

**Edge TTS 是联网的免费通道**（微软 Edge 朗读接口），不是本地合成；真正的本地兜底是第 4 项 Windows 系统语音，只在 Edge 失败时才用。可选中文音色共 8 个（晓晓/晓伊/小北/小妮/云希/云健/云扬/云夏），旧的"女声""男声"仍作为别名可用。

旁白会**按标点分句合成**，句间插入停顿（逗号 0.2s、句号 0.34s），字幕因此按句跟随，而不是整镜一条字幕从头挂到尾。

### 音乐

上传音乐，或者把已获授权的音频文件放到 `runtime/music`。音乐选项不是“无音乐”时，如果没有真实音乐且未明确打开合成 fallback，任务会失败。

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

只放文件不写 catalog 时，任何非“无音乐”的选择都会在音频阶段失败，错误信息会点名 mood 和 style。

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
