"""生成一套完整的假素材包，供 P4 独立开发渲染器使用。

产出（默认写到 ./fixtures/）：
    shots.json          6 个镜头的完整定义，asset_local_path 已填好
    audio_result.json   完整的 AudioResult（含 cues）
    cues.json           只含 cues，方便 P4 单独读
    assets/shot_N.ppm   6 张程序化场景图
    audio/voice.wav     占位配音
    audio/music.wav     占位音乐
    ffmpeg-samples.txt  几条可直接粘的 ffprobe 校验命令

P4 拿这套东西就能开工，不需要 P1/P2/P3 的任何产出。

用法：
    python make_fixtures.py                     # 静图版
    python make_fixtures.py --video             # 同时产出 mp4 版素材（测视频分支）
    python make_fixtures.py --out-dir D:/fx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
# team/假素材包/ -> team/ -> 项目根
PROJECT_ROOT = HERE.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.providers.contract import CONTRACT_VERSION  # noqa: E402
from backend.app.providers.stubs.stub_assets import StubAssetProvider  # noqa: E402
from backend.app.providers.stubs.stub_audio import (  # noqa: E402
    StubMusicProvider,
    StubTTSProvider,
)


# 与 OfflineLLMProvider 的模板保持一致，便于真实链路对照
SHOT_TEMPLATES = [
    ("城市开场", "城市意象与地标远景，晨雾、柔和日光、竖屏构图"),
    ("地标入画", "地标代表性景观，水面前景，游客剪影，镜头缓慢推进"),
    ("文化细节", "手作、街巷、器物或生活细节，特写，真实质感"),
    ("风物体验", "地方风物与市井烟火，摊铺、茶点、手艺，温暖自然光"),
    ("当季活动", "节庆氛围中的城市公共空间，灯笼、花事，节奏明快"),
    ("收束号召", "山水与城市夜色交叠，留出标题空间，适合结尾"),
]

NARRATIONS = [
    "如果把一座城市写成一首诗，第一句往往从清晨的水面开始。",
    "沿着水脉与山色走进城里，你会遇见时间留下的纹理。",
    "从一杯茶、一味风物，到一段仍在延续的手艺。",
    "市井的烟火气，是这座城市最真实的注脚。",
    "当季的花事与灯火，把整座城点亮。",
    "这个秋天，来走一走，让风景成为记忆。",
]


def build_shots(unit: float, shot_count: int) -> list[dict]:
    """按 shot_count 铺镜头。模板不够时循环取用（8 镜头会复用前两条模板）。"""
    shots = []
    for index in range(1, shot_count + 1):
        label, visual = SHOT_TEMPLATES[(index - 1) % len(SHOT_TEMPLATES)]
        narration = NARRATIONS[(index - 1) % len(NARRATIONS)]
        shots.append(
            {
                "id": f"shot_{index}",
                "order": index,
                "duration_sec": unit,
                "title": label,
                "visual_prompt": visual,
                "narration": narration,
                "subtitle": narration,
                "transition": "fade" if index == 1 else "dissolve",
                "asset_local_path": "",
                "asset_media_type": "",
            }
        )
    return shots


def main() -> int:
    parser = argparse.ArgumentParser(description="生成假素材包")
    parser.add_argument("--out-dir", default=str(HERE.parent / "fixtures"))
    parser.add_argument("--shot-count", type=int, default=6)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--video", action="store_true", help="素材用 mp4 而非静图")
    parser.add_argument("--mood", default="舒缓")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    assets_dir = out_dir / "assets"
    audio_dir = out_dir / "audio"
    unit = round(args.duration / args.shot_count, 3)

    shots = build_shots(unit, args.shot_count)
    provider = StubAssetProvider(assets_dir, as_video=args.video, duration=unit)

    # ---- 画面 ----
    for shot in shots:
        result = provider.generate(shot, assets_dir, {"color": "#24526a", "accent": "#d6ad60"})
        shot["asset_local_path"] = result.path
        shot["asset_media_type"] = result.media_type
        shot["asset_source"] = result.source
        shot["asset_seed"] = result.seed
    print(f"[ok] 画面 {len(shots)} 条 -> {assets_dir}")

    # ---- 音频 ----
    tts = StubTTSProvider(audio_dir)
    music = StubMusicProvider(audio_dir)
    cues = []
    elapsed = 0.0
    for shot in shots:
        cues.append(
            {
                "shot_id": shot["id"],
                "start": round(elapsed, 3),
                "end": round(elapsed + shot["duration_sec"], 3),
                "text": shot["narration"],
            }
        )
        elapsed += shot["duration_sec"]
    total = round(elapsed, 3)

    voice = tts.synthesize("".join(c["text"] for c in cues), audio_dir / "voice.wav", "女声")
    track = music.generate(args.mood, total, audio_dir / "music.wav")
    print(f"[ok] 音频 -> {audio_dir}")

    audio_result = {
        "voice_path": voice["path"],
        "music_path": track["path"],
        "cues": cues,
        "total_duration": total,
        "cost": 0.0,
        "event": {"type": "audio", "provider": "fixture", "ok": True},
    }

    # ---- 落盘 ----
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "shots.json").write_text(
        json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "audio_result.json").write_text(
        json.dumps(audio_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "cues.json").write_text(
        json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "contract_version": CONTRACT_VERSION,
        "shot_count": len(shots),
        "duration": total,
        "resolution": "720x1280",
        "fps": 25,
        "asset_media_type": shots[0]["asset_media_type"],
    }
    (out_dir / "fixture-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ffprobe = (
        "# P4 渲染完成后用这几条校验\n"
        f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{out_dir}/../out/final.mp4"\n'
        "# 期望：30.000000（转场不能把总长改掉）\n"
        f'ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -of csv=p=0 '
        f'"final.mp4"\n'
        "# 期望：720,1280,25/1\n"
    )
    (out_dir / "ffmpeg-samples.txt").write_text(ffprobe, encoding="utf-8")

    print(f"[ok] 清单 -> {out_dir / 'fixture-manifest.json'}")
    print(f"     契约版本 {CONTRACT_VERSION}，总时长 {total}s，{len(shots)} 镜头")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
