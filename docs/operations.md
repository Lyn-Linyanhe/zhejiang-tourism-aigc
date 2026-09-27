# Operations Guide

## Start the local service

```powershell
Set-Location C:\Users\ev041\Desktop\人工智能\zhejiang-tourism-aigc
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

1. Revoke and replace the exposed DeepSeek key.
2. Put the replacement only in `.env`.
3. Confirm the current network can reach `https://api.deepseek.com`.
4. Start the service.
5. Create a preview in the UI.
6. Check `trace.provider=openai-compatible`, `trace.model=deepseek-chat` and `trace.fallback_used=false`.
7. Edit and save the draft.
8. Upload approved media or configure Pexels.
9. Upload a real narration/music file, configure an approved TTS, or select no music.
10. Confirm generation.
11. Check MP4 with `ffprobe`.

The current environment previously failed the DeepSeek TLS handshake with `SSL: UNEXPECTED_EOF_WHILE_READING`; that is a network/proxy issue until a replacement key and a clean request have been tested.

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
Set-Location C:\Users\ev041\Desktop\人工智能\MoneyPrinterTurbo
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
