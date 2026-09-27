# API Reference

Base URL: `http://127.0.0.1:8787`

All request and response bodies use UTF-8 JSON.

## `GET /api/health`

Returns provider configuration without exposing secrets.

## `GET /api/catalog`

Returns city knowledge, default styles, voice choices, music moods and durations. The UI uses these values as suggestions; text fields also accept custom values.

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

Updates `title`, `visual_prompt`, `narration`, `subtitle`, `transition` and `duration_sec`.

All shot durations must add up to the task duration. The server rejects inconsistent edits.

## `POST /api/tasks/{task_id}/generate`

Starts actual generation from the saved draft:

```text
assets → audio → rendering → review
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

Lists reusable local library media. Optional query parameters are `type=image|video|audio` and `q` for metadata search. Assets are content-hash deduplicated and include `license_status` and `usage_scope`; scanned validation artifacts remain `pending-review` and must not be treated as publication-cleared.
