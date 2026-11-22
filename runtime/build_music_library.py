"""扫描 runtime/music，按声学特征估计情绪，生成 catalog.json。

曲目大多没有 ID3 标签也没有随附清单，情绪只能靠声学特征估计：
ffmpeg 取整轨响度（EBU R128）和一段单声道 PCM，再用自相关估节拍，最后按
节拍排序取四分位分档。**这是机器估计，不是人工听审**，catalog 里用
moods_source 标出来，moods_review 也写进 runtime/music_moods_review.json，
换曲或人工复核后直接改 catalog.json 即可。

四分位而不是绝对阈值：曲库实测只有三四个节拍簇，固定阈值会把某一档留空，
用户一选就报错。标签的含义是"本曲库里偏慢的一档"，真实速度在复核清单里。

用法：python runtime/build_music_library.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUSIC_DIR = ROOT / "runtime" / "music"
CATALOG = MUSIC_DIR / "catalog.json"
REVIEW = ROOT / "runtime" / "music_moods_review.json"
HAND_ASSIGNED = MUSIC_DIR / "hand_assigned.json"

AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}
MOOD_BY_QUARTILE = ("舒缓", "雅致", "轻快", "热烈")
# 风格不参与判别，全部允许：判别轴只有情绪，风格写死避免了"情绪对上、风格
# 对不上"导致的选曲失败。
ALL_STYLES = ["诗意纪实", "城市漫游", "国风雅韵", "青春活力", "节庆热烈"]

# 微信下载来的文件名带下载 ID、emoji 和"日推歌单 P1 …"这类前缀，进 UI 和
# manifest 都很难看，还逼近 Windows 的 260 字符路径上限。这里规整成可读曲名，
# 原文件名记进 catalog 的 source_file，源包不动。
RENAMES: dict[str, str] = {
    "apricity .mp3": "apricity.mp3",
    "Love a Little Harder .mp3": "Love a Little Harder.mp3",
    "summer triangle .mp3": "summer triangle.mp3",
    "HeartStrings·曾怡.mp3": "HeartStrings.mp3",
    "广播剧小行星BGM⭐What If I Told You (Lyrics)-Cayson Renshaw番外3：你是我的烟火人间✨仇音✖应宸 P1 Cayson Renshaw - What If I Told You (Lyrics)_550279609.mp3": "What If I Told You.mp3",
    "日推歌单 P1 “好适合睡前单曲循环 慵懒又温柔”《Weather Any Storm》｜日推歌单_1044663293.mp3": "Weather Any Storm.mp3",
    "私人致享 P1 在百万豪装录音棚大声听 徐梦圆《CHINA-春风》【Hi-res】_1429430060.mp3": "CHINA 春风.mp3",
    "美国音乐人Cody Francis - Rare Love_580915769.mp3": "Rare Love.mp3",
    "超好听的纯音乐-For All Eternity_1239258219.mp3": "For All Eternity.mp3",
    "洛天依 - 又见月光光 - 原版伴奏_39363085163.mp3": "又见月光光 伴奏.mp3",
    "胡夏 夏至未至 伴奏 P1 伴奏_35803104701.mp3": "夏至未至 伴奏.mp3",
    "没语季节 钢琴完整版.mp3": "没语季节 钢琴版.mp3",
    "草原の風 (Wind Of The Prairie).mp3": "草原之风 Wind Of The Prairie.mp3",
}


def apply_renames() -> dict[str, str]:
    """按 RENAMES 改名。返回 新文件名 -> 原文件名。"""
    origin: dict[str, str] = {}
    for old, new in RENAMES.items():
        source, target = MUSIC_DIR / old, MUSIC_DIR / new
        if source.is_file():
            if not target.exists():
                source.rename(target)
            origin[new] = old
        elif target.is_file():
            origin[new] = old
    return origin


def integrated_loudness(path: Path) -> float:
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace", check=False,
    )
    for line in reversed((done.stderr or "").splitlines()):
        text = line.strip()
        if text.startswith("I:") and "LUFS" in text:
            try:
                return float(text.split()[1])
            except (IndexError, ValueError):
                break
    return -70.0


def estimate_bpm(path: Path, seconds: float = 60.0) -> float:
    """自相关估节拍。取前 60 秒的 RMS 包络，找 60~200 BPM 区间的主周期。"""
    try:
        import numpy as np
    except ImportError:
        return 0.0
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path),
         "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=False,
    )
    samples = np.frombuffer(done.stdout or b"", dtype=np.int16)
    if samples.size < 8000 * 4:
        return 0.0
    # 包络：128 样本一帧（16ms），帧率 62.5Hz。帧太长会让节拍只能落到整数 lag 上。
    hop = 128
    frames = samples[: samples.size // hop * hop].reshape(-1, hop)
    envelope = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    if envelope.size < 64:
        return 0.0
    envelope -= envelope.mean()
    onset = np.clip(np.diff(envelope), 0, None)   # 只留起音
    onset -= onset.mean()
    if not np.any(onset):
        return 0.0
    envelope_rate = 8000.0 / hop
    min_lag = max(1, int(envelope_rate * 60 / 200))
    max_lag = min(onset.size - 1, int(envelope_rate * 60 / 60))
    if max_lag <= min_lag:
        return 0.0
    correlation = np.correlate(onset, onset, mode="full")[onset.size - 1:]
    window = correlation[min_lag:max_lag + 1]
    if window.size == 0 or not np.any(window > 0):
        return 0.0
    index = int(np.argmax(window))
    lag = float(min_lag + index)
    # 抛物线插值取次 lag 精度，否则相邻 lag 之间会跳好几个 BPM。
    if 0 < index < window.size - 1:
        left, middle, right = window[index - 1], window[index], window[index + 1]
        denominator = left - 2 * middle + right
        if denominator:
            lag += 0.5 * (left - right) / denominator
    bpm = 60.0 * envelope_rate / lag
    while bpm < 60:
        bpm *= 2
    while bpm > 200:
        bpm /= 2
    return round(bpm, 1)


def assign_moods(measured: list[tuple[Path, float, float]]) -> dict[str, str]:
    """按节拍排序取四分位分档。返回 文件名 -> 情绪。"""
    ordered = sorted(measured, key=lambda item: (item[1] <= 0, item[1]))
    total = len(ordered)
    assignment: dict[str, str] = {}
    for index, (path, _bpm, _loudness) in enumerate(ordered):
        quartile = min(len(MOOD_BY_QUARTILE) - 1, index * len(MOOD_BY_QUARTILE) // max(1, total))
        assignment[path.name] = MOOD_BY_QUARTILE[quartile]
    return assignment


def main() -> int:
    if not MUSIC_DIR.is_dir():
        print(f"找不到音乐目录：{MUSIC_DIR}", file=sys.stderr)
        return 2
    origin = apply_renames()

    # 手工定档的曲目不参与自动估计，情绪原样保留。
    hand: dict[str, dict] = {}
    if HAND_ASSIGNED.is_file():
        for record in json.loads(HAND_ASSIGNED.read_text(encoding="utf-8")).get("tracks", []):
            hand[str(record.get("file", ""))] = record

    sources = sorted(
        p for p in MUSIC_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and p.name not in hand
    )
    measured: list[tuple[Path, float, float]] = []
    for source in sources:
        bpm = estimate_bpm(source)
        loudness = integrated_loudness(source)
        measured.append((source, bpm, loudness))
        print(f"  {source.name[:44]:<46} bpm={bpm:>6}  lufs={loudness:>7.2f}", flush=True)

    moods = assign_moods(measured)
    tracks: list[dict] = []
    review: list[dict] = []
    for path, bpm, loudness in measured:
        mood = moods[path.name]
        tracks.append({
            "id": f"bgm-{path.stem}"[:60],
            "file": path.name,
            "source_file": origin.get(path.name, ""),
            "moods": [mood],
            "styles": list(ALL_STYLES),
            "license": "用户自备素材，授权状态未知，公开传播前需自行确认",
            "author": "用户提供",
            "usage_scope": "比赛展示与内部评审；正式对外发布前必须确认授权",
            "approved": True,
            "moods_source": "auto-estimated-tempo-quartile",
        })
        review.append({
            "file": path.name, "bpm": bpm, "lufs": round(loudness, 2),
            "mood": mood, "needs_review": True,
        })

    for name, record in hand.items():
        if (MUSIC_DIR / name).is_file():
            record.setdefault("moods_source", "hand-assigned")
            tracks.insert(0, record)

    CATALOG.write_text(
        json.dumps(
            {
                "version": 3,
                "note": (
                    "Moods are tempo/loudness estimates, not human listening review. "
                    "See runtime/music_moods_review.json; correct 'moods' here if wrong."
                ),
                "tracks": tracks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    review.sort(key=lambda item: item["bpm"])
    REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    for item in review:
        counts[item["mood"]] = counts.get(item["mood"], 0) + 1
    print(f"\n曲目 {len(tracks)} 首 -> {CATALOG}")
    print("情绪分布:", json.dumps(counts, ensure_ascii=False))
    print(f"复核清单: {REVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
