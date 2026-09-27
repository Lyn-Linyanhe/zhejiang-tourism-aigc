# 假素材包

给 **P4** 用的。P4 拿这套东西就能开工，不需要 P1/P2/P3 的任何产出。

同时也给 **P1** 当 stub 数据源。

---

## 生成

```bash
cd zhejiang-tourism-aigc    # 项目根

python "team/假素材包/make_fixtures.py"                  # 静图版（默认 6 镜头 / 30 秒）
python "team/假素材包/make_fixtures.py" --video          # 素材出 mp4，测渲染器的视频分支
python "team/假素材包/make_fixtures.py" --shot-count 4 --duration 20
python "team/假素材包/make_fixtures.py" --out-dir D:/fx  # 换输出目录
```

**依赖**：纯 Python 标准库。只有 `--video` 需要 ffmpeg 在 PATH 里。

---

## 产出

```
fixtures/
├── shots.json              6 个镜头的完整定义，asset_local_path 已填好
├── audio_result.json       完整的 AudioResult（含 cues），可直接喂给渲染器
├── cues.json               只含 cues，方便单独读
├── fixture-manifest.json   元信息：契约版本、镜头数、总时长、分辨率、fps
├── ffmpeg-samples.txt      几条可直接粘的 ffprobe 校验命令
├── assets/
│   └── shot_1.ppm ... shot_6.ppm     程序化场景图（山、水、日晕）
└── audio/
    ├── voice.wav           占位配音（正弦波，带抖动模拟语调）
    └── music.wav           占位音乐（按情绪选基频）
```

**素材是故意做得丑的**——程序化画的山水，一眼能看出是假的。这样你在调试渲染器时不会把「画面难看」误判成「渲染参数不对」。

---

## 素材长什么样

`shots.json` 里每条：

```json
{
  "id": "shot_1",
  "order": 1,
  "duration_sec": 5.0,
  "title": "城市开场",
  "visual_prompt": "城市意象与地标远景，晨雾、柔和日光、竖屏构图",
  "narration": "如果把一座城市写成一首诗，第一句往往从清晨的水面开始。",
  "subtitle": "如果把一座城市写成一首诗，第一句往往从清晨的水面开始。",
  "transition": "fade",
  "asset_local_path": ".../assets/shot_1.ppm",
  "asset_media_type": "image",
  "asset_source": "stub-asset",
  "asset_seed": 1001
}
```

`audio_result.json` 里 `cues` 是音画同步的关键：

```json
{
  "voice_path": ".../audio/voice.wav",
  "music_path": ".../audio/music.wav",
  "cues": [
    {"shot_id": "shot_1", "start": 0.0, "end": 5.0, "text": "..."},
    {"shot_id": "shot_2", "start": 5.0, "end": 10.0, "text": "..."}
  ],
  "total_duration": 30.0
}
```

---

## 注意：cues 现在是「名义时长」，不是「实测时长」

stub 的 cues 是按镜头名义时长（每镜 5 秒）铺的，和真实的 TTS 时间戳**不一样**。

这对 P4 来说够用——你要验证的是「字幕能不能按 cues 摆放」，不是「cues 准不准」。cues 的准确性是 P3 的责任。

合并 P3 的真实实现之后，cues 会变成真实音频位置，届时再跑一次回归即可。

---

## 校验命令

`ffmpeg-samples.txt` 里已经写好了。核心三条：

```bash
# 总时长必须是 30.000000（转场不许改掉它）
ffprobe -v error -show_entries format=duration -of csv=p=0 final.mp4

# 规格必须是 720,1280,25/1
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -of csv=p=0 final.mp4

# 必须有 video 和 audio 两条流
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 final.mp4
```

---

## 重新生成

改素材不满意就直接重跑，会覆盖。**不要手动改 `fixtures/` 里的文件**——那是生成物，改了下次重跑就没了。

要改内容就改 `make_fixtures.py` 里的 `SHOT_TEMPLATES` 和 `NARRATIONS`。
