# Operations Guide

## Start the local service

```powershell
Set-Location <解压后的项目目录>
.\scripts\start.ps1
```

Open `http://127.0.0.1:8787`.

The startup script is ASCII-only because Windows PowerShell 5.1 can misread UTF-8 diagnostic strings.

## Production-like local configuration

Keep these values in `.env` only:

```dotenv
DEFAULT_PROVIDER=deepseek
ALLOW_PROVIDER_FALLBACK=false
ALLOW_SYNTHETIC_AUDIO_FALLBACK=false
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=REPLACE_WITH_NEW_KEY
LLM_MODEL=deepseek-chat
```

The key previously posted in chat must be revoked and replaced.

For real visual media:

```dotenv
ASSET_PROVIDER=pexels
PEXELS_API_KEY=REPLACE_WITH_PEXELS_KEY
```

Or use `asset_source=upload` and bind a licensed file to every shot.

For music, put an authorized file in `runtime/music` or upload it from the task panel. Use `无音乐` when no music is available.

## Technical smoke

The smoke test can run without DeepSeek, but this is not provider acceptance:

```powershell
$env:DEFAULT_PROVIDER = "offline"
$env:ALLOW_PROVIDER_FALLBACK = "false"
$env:ALLOW_SYNTHETIC_AUDIO_FALLBACK = "true"
.\scripts\smoke-test.ps1
```

This mode may use a synthetic tone and synthetic scene image only to verify FFmpeg, audio muxing and subtitle burn-in. Do not publish its media.

## Real-provider acceptance

1. Configure the DeepSeek credentials in the local `.env`.
2. Confirm the network can reach `https://api.deepseek.com`.
3. Verify the configured model and base URL.
4. Start the service.
5. Create a preview in the UI.
6. Check `trace.provider=openai-compatible`, `trace.model=deepseek-chat` and `trace.fallback_used=false`.
7. Edit and save the draft.
8. Upload approved media or configure Pexels.
9. Upload a real narration/music file, configure an approved TTS, or select no music.
10. Confirm generation.
11. Check MP4 with `ffprobe`.


## Artifact checks

```powershell
ffprobe -v error `
  -show_entries format=duration:stream=index,codec_name,codec_type,width,height,channels `
  -of json `
  runtime\media\<task_id>\final.mp4
```

Expected for the default portrait output:

```text
video: h264, 720x1280
audio: aac
runtime/media/<task_id>/subtitles.srt
runtime/media/<task_id>/manifest.json
runtime/media/<task_id>/cover.jpg
```

## Recovery

- Provider failure: inspect `task.error` and `trace.events`.
- Re-render with saved script and assets: `POST /api/tasks/{id}/retry` with `{"from_stage":"rendering"}`.
- Re-run audio: retry from `audio`.
- Regenerate the script: retry from `scripting`.
- Do not delete `runtime/db` or `runtime/media` while a task is running.

## MoneyPrinterTurbo

The sibling checkout is optional for script/keyword generation:

```powershell
Set-Location <MoneyPrinterTurbo 检出目录>   # 可选上游，同级放置即可
uv sync --frozen
Copy-Item config.example.toml config.toml
```

Configure its provider in the local, ignored `config.toml`, then set:

```dotenv
DEFAULT_PROVIDER=moneyprinterturbo
MPT_ROOT=..\MoneyPrinterTurbo
MPT_PYTHON=C:\path\to\MoneyPrinterTurbo\.venv\Scripts\python.exe
```

The adapter uses a subprocess because both projects contain a top-level package named `app`.

## 作品底账与逐镜核验

- 在「作品档案」打开任务，点击「下载任务底账」。JSON 包含创作输入、知识库命中条目、旁白、分镜、素材来源与授权、配音和音乐、渲染信息、核验结果、外部服务费用和调用记录。
- 「逐镜画面核验」展示成片各镜在避开转场后的前段、中段和后段样本，以及对应旁白、画面目标和知识库来源。抽帧同时检查近黑与缺帧。核验结论对应成片 SHA-256，修改结论时保留旧记录。用于地标发布时，请根据素材原始页、实地资料及画面填写具体观察。
- 上传与导入的音乐按用户确认的完整授权口径入账。作者、使用范围和授权凭证可以补填。原始图库许可、AI 服务条款和外部上传图片的授权另以其各自记录为依据。
- 「费用台账」按调用保留可得到的请求号、远端任务号与用量。账单确认金额按币种汇总。收到供应商账单后，可把账单号或凭证链接及金额录入相应条目。没有历史账单或用量的调用保持待对账状态，缓存复用和本地 FFmpeg 步骤记为无新增外部服务费用。
- 知识库覆盖浙江 11 个设区市，使用 data_zhejiang_kb 中的代表性景区、非遗、饮食、节庆、传说和人物条目。新增条目在实体级保留来源。脚本参考和素材检索按选定城市匹配。
