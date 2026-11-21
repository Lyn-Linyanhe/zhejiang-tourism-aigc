# Operations Guide

## Start the local service

```powershell
Set-Location <解压后的项目目录>
.\scripts\start.ps1
```

Open `http://127.0.0.1:8790`.

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

Or use `asset_source=upload` for previews and bind a file to every shot. Formal visual publication uses `visual_usage=publication` and the reviewed entity library.

## Entity media review and publication

1. Generate a draft and select a knowledge entity and shot intent for each shot. The entity list is limited to the task city.
2. Upload an image or video through the entity library panel. An optional, independent target-shot selector binds it; otherwise it only enters the library. The older shot upload panel also registers a candidate in this library.
3. Check the original file page, author, license, attribution conditions and expiration. The image of a tea cup does not demonstrate tea-making; a summer bridge photo does not demonstrate a snow scene. Record those restrictions in `usage_scope`.
4. Save source/license information, then separately approve publication. New uploads cannot approve themselves. Review checks a real source and license receipt, entity/shot tags, valid dates and an unchanged SHA-256. Rejected, expired or changed files are not publication candidates.
5. Select `正式发布` in the draft. The workbench sets `local-library`; the backend independently enforces the reviewed library and refuses an unmatched shot. There is no unrelated-file fallback in publication mode. Entity-specific previews also stay on that entity.
6. Generate and retain `manifest.json`, task trace, per-shot source/license metadata and attribution alongside MP4. Re-rendering from `rendering` checks the current task city, review and file again. A rejected/changed file must be reselected; updating metadata cannot forge a new SHA-256.

The workbench keeps script edits while refreshing the library after review/upload. Saving a draft sends its task fields and shot patches together; an invalid edit leaves the saved task unchanged. Balance duration changes across shots in the same save. Unchanged durations measured from audio can be saved on a failed draft, even when they differ from the requested container duration. For an upload bound to a task, draft edits are saved first; invalid city/entity/shot ownership fails before the file is written.

Changing entity/place, source, author, license, usage scope or expiration evidence returns a previously approved asset to pending review and preview-only use, unless the same request explicitly reapproves it and passes all publication checks. Save revised evidence first, inspect it, then approve separately. A title alone cannot replace the original source URL. Rejecting a bound preview asset also blocks rendering retries before focus detection or FFmpeg reads it.

A file license does not grant rights to unrelated music, people, trademarks or copied descriptions. Keep fact-source records separate from image-source records. CC BY requires author/source/license and an indication of edits; do not treat CC BY-SA as CC BY.

## Reproduce the small local pilot

The 2026-11-20 pilot uses an isolated data directory. It does not overwrite the normal database, existing media, `.env` or music library.

```powershell
$env:DATA_DIR = "runtime/_wire_probe_entity_assets_20261120"
$env:DEFAULT_PROVIDER = "offline"
$env:ASSET_PROVIDER = "local-library"
$env:IMAGE_PROVIDER = "off"
$env:VIDEO_PROVIDER = "off"
python -I scripts/collect_entity_pilot.py --direct --output runtime/_wire_probe_entity_assets_20261120/downloads-new
```

Use a new output directory for every collection. The script saves Commons metadata receipts and only registers pending candidates. `--direct` changes proxy handling for these public requests only. It does not alter system proxy settings. Before `accept_entity_pilot.py`, inspect each downloaded image and receipt and verify the `VISUAL_REVIEW` choices; that separate acceptance script approves the five inspected pilot records and renders with local Windows speech. If local speech fails, it fails visibly instead of silently substituting a tone.

```powershell
python -I scripts/accept_entity_pilot.py --index runtime/_wire_probe_entity_assets_20261120/downloads-new/index.json
```

Keep `ATTRIBUTION.md`, `fact-records.json`, `task.json`, `manifest.json` and `ffprobe.json` with the sample. The input photographs are still images with slow crop/motion, not newly acquired video footage. See `docs/research/entity-assets-pilot.md` for coverage and remaining gaps. Pexels currently supplies photo candidates only; its video-search regression remains a separate task.


For music, register an authorized file in `runtime/music/catalog.json` with complete license fields and `approved=true`, or upload it with explicit authorization from the task panel. Placing a file in the music directory alone does not approve it. If no approved match is available, choose `无音乐`; the pipeline fails rather than selecting an arbitrary directory file. Synthetic music fallback requires the explicit technical-preview switch and is refused in publication mode.

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
