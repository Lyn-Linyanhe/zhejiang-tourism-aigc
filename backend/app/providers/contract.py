"""浙里成片 · 四人协作接口契约 v1（冻结版）

这份文件是四条产线之间唯一的耦合点。契约冻结后：

  - P1 用 stub 跑通全链路骨架；
  - P2 / P3 / P4 各自写自己的实现，互不阻塞；
  - 合并时只有「把 stub 换成真实现」这一个动作。

修改规则
--------
任何字段的增删改都必须先改这份文件，并在 AI 日志里记一条 Entry。
不允许某条产线私自在返回值里加字段——那会让其他三人的代码在合并时炸掉。
需要新字段时：先改 contract.py，再通知全员，最后各自跟进。

对应关系
--------
  契约           实现方    调用方
  ------------   -------   -----------------------------
  AssetResult    P2 画面   P1 编排（写进 Shot.asset_*）
  AudioResult    P3 声音   P1 编排（写进 task.audio）
  RenderRequest  P4 渲染   P1 编排（调 VideoRenderer.render）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


CONTRACT_VERSION = "zj-team4-v1"

# 画面素材类型。渲染器按这个字段决定用「静图运镜」还是「视频铺时长」。
MEDIA_TYPE_IMAGE = "image"
MEDIA_TYPE_VIDEO = "video"
MEDIA_TYPES = (MEDIA_TYPE_IMAGE, MEDIA_TYPE_VIDEO)

# 允许的转场名。P4 必须实现这里的每一个，P2/P3 不参与。
TRANSITIONS = ("fade", "dissolve", "slideleft", "slideright", "zoom")


# --------------------------------------------------------------------------
# ① 画面：P2 实现，P1 调用
# --------------------------------------------------------------------------
@dataclass
class AssetResult:
    """一个镜头的画面产物。

    path 有两种可能：静图（.png/.jpg/.ppm）或短视频（.mp4）。
    两者都可以直接进现有渲染管线——renderer._is_video_asset() 已经
    按扩展名分流，P4 不需要为视频素材写新分支。
    """

    path: str
    media_type: str
    source: str
    model: str
    seed: int
    prompt: str
    first_frame_path: str = ""
    cost: float = 0.0
    event: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.path:
            raise ValueError("AssetResult.path 不能为空")
        if self.media_type not in MEDIA_TYPES:
            raise ValueError(
                f"AssetResult.media_type 必须是 {MEDIA_TYPES} 之一，"
                f"实际为 {self.media_type!r}"
            )
        if not self.source:
            raise ValueError("AssetResult.source 不能为空（用于追溯素材来源）")
        if self.cost < 0:
            raise ValueError("AssetResult.cost 不能为负")
        if self.media_type == MEDIA_TYPE_VIDEO and not self.first_frame_path:
            raise ValueError(
                "图生视频必须回填 first_frame_path——"
                "它是「这一镜为什么长这样」的唯一证据，也是失败重试的起点"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


# --------------------------------------------------------------------------
# ② 声音：P3 实现，P1 调用
# --------------------------------------------------------------------------
@dataclass
class Cue:
    """一句旁白在最终音轨上的真实时间位置（秒）。

    这是音画同步的唯一基础。没有 cues，字幕只能按镜头名义时长均分，
    配音和画面就会渐进错位。
    """

    shot_id: str
    start: float
    end: float
    text: str

    def validate(self) -> None:
        if not self.shot_id:
            raise ValueError("Cue.shot_id 不能为空")
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"Cue 时间区间非法：start={self.start}, end={self.end}")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class AudioResult:
    """一条任务的完整音频产物。"""

    voice_path: str
    music_path: str
    cues: list[Cue]
    total_duration: float
    cost: float = 0.0
    event: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.voice_path and not self.cues:
            raise ValueError(
                "有配音却没有 cues——整段音频不算交付，带时间戳才算。"
                "P4 的字幕和音画同步都依赖它"
            )
        if self.total_duration <= 0:
            raise ValueError("AudioResult.total_duration 必须大于 0")
        if self.cost < 0:
            raise ValueError("AudioResult.cost 不能为负")
        previous_end = 0.0
        for cue in self.cues:
            cue.validate()
            if cue.start < previous_end - 0.05:
                raise ValueError(
                    f"cues 必须按时间单调递增，{cue.shot_id} 与前一句重叠"
                )
            previous_end = cue.end

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AudioResult":
        cues = [
            item if isinstance(item, Cue) else Cue(**item)
            for item in raw.get("cues", [])
        ]
        return cls(
            voice_path=str(raw.get("voice_path", "")),
            music_path=str(raw.get("music_path", "")),
            cues=cues,
            total_duration=float(raw.get("total_duration", 0)),
            cost=float(raw.get("cost", 0)),
            event=dict(raw.get("event", {})),
        )


# --------------------------------------------------------------------------
# ③ 渲染：P4 实现，P1 调用
# --------------------------------------------------------------------------
@dataclass
class RenderRequest:
    """一次渲染请求。P1 组装，P4 消费。"""

    task_id: str
    shots: list[dict[str, Any]]
    audio: dict[str, Any]
    resolution: str = "720x1280"
    fps: int = 25
    include_subtitles: bool = True
    include_ai_label: bool = True
    voice_volume: float = 1.0
    music_volume: float = 0.18
    # 统一调色参数由 P2 的 consistency 模块产出，经 P1 透传给 P4。
    # 为空时 P4 使用内置默认值。
    grade: dict[str, Any] = field(default_factory=dict)


@dataclass
class RenderResult:
    """渲染产物。字段与现有 manifest.json 保持兼容。"""

    video_url: str
    subtitle_url: str
    cover_url: str
    video_local_path: str
    format: str
    aspect_ratio: str
    resolution: str
    has_audio: bool
    has_burned_subtitles: bool
    total_duration: float
    render_provider: str = "ffmpeg"
    aigc_label: str = ""


# --------------------------------------------------------------------------
# Provider 协议：P1 的 Registry 按这些形状查找实现
# --------------------------------------------------------------------------
@runtime_checkable
class AssetProviderProtocol(Protocol):
    """P2 的 image.py / video.py 都实现这个形状。"""

    name: str
    cost_per_call: float

    def generate(
        self,
        shot: dict[str, Any],
        output_dir: Any,
        style_ctx: dict[str, Any],
    ) -> AssetResult:
        ...


@runtime_checkable
class AudioProviderProtocol(Protocol):
    """P3 的 tts.py / music.py 都实现这个形状。"""

    name: str
    cost_per_call: float

    def synthesize(
        self,
        text: str,
        output_path: Any,
        voice: str,
        rate: float = 1.0,
    ) -> dict[str, Any]:
        ...


# --------------------------------------------------------------------------
# 降级链：P1 的 Registry 按这个结构声明回退顺序
# --------------------------------------------------------------------------
@dataclass
class FallbackChain:
    """画面/音频的降级顺序。

    P2 的云生成失败时，Registry 依次尝试后面的 provider，
    最后一个必须是永远可用的本地兜底（如 OfflineAssetProvider）。
    """

    slot: str
    providers: list[str]

    def validate(self) -> None:
        if not self.providers:
            raise ValueError(f"降级链 {self.slot!r} 不能为空")
        if len(self.providers) != len(set(self.providers)):
            raise ValueError(f"降级链 {self.slot!r} 存在重复项")
