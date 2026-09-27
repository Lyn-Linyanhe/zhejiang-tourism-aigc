"""契约 stub —— 让全链路在第一天就能端到端跑通。

三个 stub 分别顶替三条产线：

    StubAssetProvider   → 顶替 P2 的云文生图 / 图生视频
    StubTTSProvider     → 顶替 P3 的 TTS
    StubMusicProvider   → 顶替 P3 的音乐生成

它们产出的对象与 contract.py 里的定义**完全同形**，
所以「把 stub 换成真实现」是一个纯粹的替换动作，不需要改调用方。
"""

from .stub_assets import StubAssetProvider
from .stub_audio import StubMusicProvider, StubTTSProvider, build_audio_result

__all__ = [
    "StubAssetProvider",
    "StubTTSProvider",
    "StubMusicProvider",
    "build_audio_result",
]
