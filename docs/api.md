# API Reference

Base URL: `http://127.0.0.1:8790`

All request and response bodies use UTF-8 JSON.

## `GET /api/health`

Returns provider configuration without exposing secrets.

## `GET /api/catalog`

Returns city knowledge, default styles, voice choices, music moods and durations. `entities` lists `{id, name, city, type}` references and `shot_tags` maps `wide`, `detail`, `process`, `performance`, `ambient` to display names. The UI uses these values as suggestions; text fields also accept custom values.

## `POST /api/previews`

Creates a draft and generates only the script/storyboard.

```json
{
  "city": "杭州",
  "landmark": "西湖",
  "theme": "春日城市漫游",
  "culture": "宋韵文化",
  "festival": "春季文旅推广",
  "audience": "年轻游客",
  "duration": 30,
  "shot_count": 6,
  "style": "诗意纪实",
  "voice": "女声",
  "music_mood": "无音乐",
  "include_ai_label": true,
  "include_subtitles": true,
  "custom_brief": "",
  "visual_usage": "preview",
  "asset_source": "configured",
  "output_resolution": "720x1280",
  "fps": 25,
  "voice_rate": 1.0,
  "voice_volume": 1.0,
  "music_volume": 0.18
}
```

The response has `status: "draft"` and contains `title`, `summary`, `cta`, `narration` and `shots`. No `final.mp4` is created by this endpoint.

## `GET /api/tasks/{task_id}`

Returns the complete task, including:

- `status`, `stage` and `progress`;
- editable script and shots;
- asset source, license and local path metadata;
- audio provider, model and URLs;
- render URLs and `has_audio` / `has_burned_subtitles`;
- provider trace and failure events.

## `PATCH /api/tasks/{task_id}`

Updates draft-level fields:

```json
{
  "title": "新的标题",
  "summary": "新的摘要",
  "cta": "新的行动号召",
  "narration": "完整旁白",
  "input": {
    "asset_source": "upload",
    "output_resolution": "1080x1920",
    "include_subtitles": true
  }
}
```

The task remains a draft until generation is confirmed.

## `PATCH /api/tasks/{task_id}/shots/{shot_id}`

Updates `title`, `visual_prompt`, `narration`, `subtitle`, `transition`, `duration_sec`, `entity_id`, `shot_tag` and `library_asset_id`. Entities must belong to the task city; a bound asset must match the selected entity and shot tag. `library_asset_id: null` clears a library binding and its reusable file. Empty entity/tag values request automatic planning.

All shot durations must add up to the task duration. The server rejects inconsistent edits.

## `POST /api/tasks/{task_id}/generate`

Starts actual generation from the saved draft:

```text
audio → assets → rendering → review
```

This endpoint must not be called until script, storyboard, assets and audio choices have been reviewed.

## `POST /api/assets`

Uploads and binds an image or video to a shot.

```json
{
  "task_id": "draft_xxx",
  "shot_id": "shot_1",
  "name": "west-lake.jpg",
  "data_base64": "data:image/jpeg;base64,...",
  "license": "团队自有拍摄，比赛展示授权",
  "author": "团队成员",
  "usage_scope": "比赛展示和内部审核"
}
```

## `POST /api/uploads`

Uploads task-level audio. `kind` is `voice` or `music`.

```json
{
  "task_id": "draft_xxx",
  "kind": "voice",
  "name": "narration.wav",
  "data_base64": "data:audio/wav;base64,..."
}
```

## Static media

Generated files are served below:

```text
/media/{task_id}/final.mp4
/media/{task_id}/subtitles.srt
/media/{task_id}/cover.jpg
/media/{task_id}/voice.wav
/media/{task_id}/music.wav
```

## `GET /api/library/assets`

Lists reusable local library media. Filters: `type=image|video|audio`, `q`, `city`, `entity_id`, `shot_tag`, `review_status=pending|approved|rejected`, and `usage=preview|publication` (default `preview`). City names accept a trailing `市`.

`publication` checks both review states, allowed usages, non-empty source/author/license/evidence, expiration, file existence and SHA-256. A missing match returns an empty list. Ordinary previews can include pending assets; rejected bindings are refused by the asset provider. Entity searches stay within the selected entity/city and do not silently use the most recent unrelated file.

## `POST /api/library/assets`

Uploads an image or short video, deduplicated by SHA-256. Supported extensions: JPEG, PNG, WebP, BMP, MP4, MOV, M4V and WebM; maximum decoded size is 20 MiB. Uploaded files are candidates, not proof of media identity or license.

```json
{
  "name": "leifeng.jpg",
  "data_base64": "data:image/jpeg;base64,...",
  "city": "杭州",
  "entity_id": "hz-leifengta",
  "shot_tags": ["wide"],
  "source_url": "https://example.org/original-file-page",
  "source_title": "Original file title",
  "source_publisher": "Original publisher",
  "author": "Photographer",
  "license": "CC BY 4.0",
  "license_url": "https://creativecommons.org/licenses/by/4.0/",
  "license_evidence": "License receipt and verification reference",
  "usage_scope": "Attribution and permitted channels",
  "valid_until": null
}
```

Returns `201` and the asset record with `id`, `local_path`, `/library-media/...` URL and `sha256`. New uploads always start with `review_status=pending`, `license_status=pending`, `allowed_usages=["preview"]`; upload fields cannot self-approve. The city is inferred from a valid entity when omitted. A duplicate preserves existing reviewed metadata; conflicting entity/tags or changed stored content are rejected.

Optional `task_id` and `shot_id` must appear together. The task must be editable, and its city/entity/tag must match before any file or library record is written. Valid binding updates the task's shot and asset record. Without these fields, the upload only registers a library candidate.

## `PATCH /api/library/assets/{id}`

Updates entity/place metadata and source/license/review fields. File paths, hashes, IDs and URLs of stored media are immutable. To approve publication:

```json
{
  "review_status": "approved",
  "license_status": "approved",
  "allowed_usages": ["preview", "publication"]
}
```

The server checks entity/tag, complete unexpired license evidence and an unchanged file, then records `checked_at`. `review_status=rejected` revokes publication eligibility. Invalid values return `400 INVALID_REQUEST`; a missing asset returns `404 NOT_FOUND`.

## Visual use and provenance

`TaskInput.visual_usage` is `preview` by default. `publication` forces the reviewed entity library for every shot, regardless of the configured fallback source. No pending, expired, unrelated or silently substituted media can enter that path; the server rechecks the current city, binding, review and file immediately before rendering, including `retry` from `rendering`.

Task assets and manifest `shots` retain entity, shot tag, library asset ID and `provenance` (source, author, license, allowed usages, dates and SHA-256). Pexels is a photo-only supplemental search in this version: it records the actual query and original page as an `external-candidate`; AI providers record `ai-generated`. Neither is publication-cleared automatically. The legacy `/api/assets` remains compatible for preview uploads, while the workbench uses `/api/library/assets`.
