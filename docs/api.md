# API Reference

Base URL: `http://127.0.0.1:8790`

All request and response bodies use UTF-8 JSON.

## `GET /api/health`

Returns provider configuration without exposing secrets.

## `GET /api/catalog`

Returns city knowledge, default styles, voice choices, music moods and durations. `entities` lists `{id, name, city, type}` references and `shot_tags` maps `wide`, `detail`, `process`, `performance`, `ambient` to display names. The UI uses these values as suggestions; text fields also accept custom values.

## `GET /api/music/library`

Lists the tracks a user may pick from: only catalog entries with `approved: true` whose file exists on disk. Returns `{tracks, moods, styles, total}`; each track exposes `id`, `file`, `name`, `moods`, `styles`, `usage_scope` and `moods_source`.

`moods_source` is `auto-estimated-tempo-quartile` for tracks whose mood was derived from tempo, so the UI labels mood as an estimate. Every bundled track lists all five narrative styles, meaning style does not currently narrow the choice.

## `GET /music/{file}`

Streams one library track for in-app preview (`audio/mpeg`). The filename must appear in the approved catalog; anything else, including path traversal attempts, returns `404`. Served from `MUSIC_DIR`, which may live outside the repo.

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

If the model returns invalid JSON or a script that fails validation, the server retries once with the exact error and asks for a corrected full JSON response. Validation checks shot count, required fields, positive shot durations, the transition whitelist, and that each shot's `subtitle` matches its `narration` after whitespace/punctuation normalization. A second failure is reported as a task error; it is not silently replaced by offline output.

## `GET /api/tasks/{task_id}`

Returns the complete task, including:

- `status`, `stage` and `progress`;
- editable script and shots;
- asset source, license and local path metadata;
- audio provider, model and URLs;
- render URLs and `has_audio` / `has_burned_subtitles`;
- provider trace and failure events.
- `metrics.cost`: provider-reported amounts with `amount_status` (`reported`, `incomplete`, `not_reported`), reported subtotal, and no implied currency. Missing data is unknown, not zero.
- `metrics.billing`: explicitly recorded billing entries grouped by currency; no mixed-currency grand total.
- `metrics.execution`: measured pipeline run durations, where available.
- `metrics.publication_observations`: manually entered platform-reported metrics with post URL and reporting window.

The same `metrics` object is included by `GET /api/tasks?limit=...` and draft PATCH. Legacy `cost` remains as an alias of `metrics.cost`. Cost summaries report only calls that explicitly mark the provider amount as reported. Existing image/video adapters currently default this flag to false because their `cost_per_call` values default to zero and that zero is a placeholder, not a quote. An explicitly reported zero must set `cost_reported: true`.

## `GET /api/tasks/{task_id}/export?format=md|txt|json`

Exports the editable script and storyboard without calling any provider. `format` defaults to `md`; `txt` and `json` are also supported. The download includes title, summary, CTA, full narration, per-shot duration/visual/narration/subtitle/transition, input metadata, and provider/model/fallback. The export reflects the last saved task; save draft edits before exporting. Unknown formats return `400 INVALID_REQUEST`; a missing task returns `404 TASK_NOT_FOUND`.

## `POST /api/tasks/{task_id}/billing`

Record a manually reviewed billing amount. This does not alter provider-reported cost and does not claim the provider supplied a bill. `amount` must be finite and non-negative; `currency` is an uppercase 3-letter code; `source` and `captured_at` are required.

```json
{"amount": 12.34, "currency": "CNY", "source": "Provider invoice INV-123", "captured_at": "2026-11-21", "note": "November test batch"}
```

Response groups recorded amounts by currency. Do not sum unlike currencies or treat a provider estimate as an invoice.

## `POST /api/tasks/{task_id}/publication-observations`

Record a manually observed public-post metric snapshot. `platform` is one of `douyin`, `xiaohongshu`, `bilibili`, `youtube`, `weibo`, `wechat-channels`, `other`. Same post URL/platform/reporting window updates the current snapshot and appends the previous snapshot to a bounded revision history. Values omitted from `metrics` are unreported, not zero. Allowed keys: `views`, `reach`, `impressions`, `likes`, `comments`, `shares`, `saves`, `clicks`, `watch_time_seconds`, `followers_gained`.

```json
{"platform":"youtube","post_url":"https://youtube.com/watch?v=abc","published_at":"2026-11-20","observed_at":"2026-11-21","window_days":1,"metrics":{"views":0,"likes":3},"source_note":"Creator analytics screenshot"}
```

These are descriptive observations, not causal attribution or ROI. Keep platform and time window visible; do not aggregate unlike platform metrics.

## `PATCH /api/tasks/{task_id}`

Updates draft-level fields and optional shot patches in one atomic save. The task must have status `draft`, `edited` or `failed`:

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
  },
  "shots": [
    {"id": "shot_1", "duration_sec": 4.25},
    {"id": "shot_2", "duration_sec": 3.25}
  ]
}
```

`shots` is an optional array of partial shot patches with unique string IDs; omitted shots are retained. All task and shot changes are validated before one save, so an invalid field or binding leaves the stored task unchanged. If the task target duration or any shot duration changes, the resulting shot total must match `input.duration` within 0.01 seconds. Changed shot durations must be between 1 and 20 seconds. Existing positive finite durations measured from audio can be saved unchanged, including values below one second or a measured total different from the requested container duration.

The task returns to draft status until generation is confirmed. The workbench uses this endpoint to save its script and all shot edits together.

## `PATCH /api/tasks/{task_id}/shots/{shot_id}`

Updates `title`, `visual_prompt`, `narration`, `subtitle`, `transition`, `duration_sec`, `entity_id`, `shot_tag` and `library_asset_id`. Entities must belong to the task city; a bound asset must match the selected entity and shot tag. `library_asset_id: null` clears a library binding and its reusable file. Empty entity/tag values request automatic planning.

When this request changes a duration, the resulting total must match the task duration within 0.01 seconds and the changed value must be between 1 and 20 seconds. Unchanged measured durations remain editable with other fields. Use the batch `shots` array on the task endpoint to balance changes across several shots in one save.

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

The server requires a non-empty `source_url`, author, license and license URL or evidence, plus a valid entity/tag, unexpired evidence and an unchanged file, then records `checked_at`. A source title alone is insufficient. Changing entity/place, source, author, license, usage scope or expiration evidence resets both review states to `pending`, limits use to `preview` and clears `checked_at`, unless the same request explicitly sets `review_status=approved` and passes every approval check. The workbench saves evidence and approves it separately. `review_status=rejected` revokes publication eligibility. Invalid values return `400 INVALID_REQUEST`; a missing asset returns `404 NOT_FOUND`.

## Visual use and provenance

`TaskInput.visual_usage` is `preview` by default. `publication` forces the reviewed entity library for every shot, regardless of the configured fallback source. No pending, expired, unrelated or silently substituted media can enter that path; the server rechecks the current city, binding, review and file immediately before rendering, including `retry` from `rendering`.

Task assets and manifest `shots` retain entity, shot tag, library asset ID and `provenance` (source, author, license, allowed usages, dates and SHA-256). Bound previews also recheck the current rejection state, entity/city/tag and file fingerprint before rendering retries. Pexels is a photo-only supplemental search in this version: it records the actual query and original page as an `external-candidate`; AI providers retain `ai-generated`, provider events and remote task receipts. Neither is publication-cleared automatically. The legacy `/api/assets` remains compatible for preview uploads, while the workbench uses `/api/library/assets`.
