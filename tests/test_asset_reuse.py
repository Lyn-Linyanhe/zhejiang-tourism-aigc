"""素材复用的画面描述比对（IMPROVEMENTS 已知限制：复用曾是 prompt-blind 的）。

提交素材时记录画面描述快照（visual_fingerprint）；重跑时快照存在且与当前
描述不一致才拦截复用。历史素材没有快照时照常复用，保证「重试不重付」。
"""

import hashlib
import unittest

from backend.app.models import AssetRecord, Shot
from backend.app.services.orchestrator import Orchestrator


def _record(visual_prompt_at_commit: str) -> AssetRecord:
    fingerprint = hashlib.sha256(visual_prompt_at_commit.encode("utf-8")).hexdigest() if visual_prompt_at_commit else ""
    return AssetRecord(
        id="a1", task_id="t", shot_id="shot_1", name="n", url="/u", source="ai-video",
        license="internal", author="team", usage_scope="preview", is_ai_generated=True,
        prompt="compose 后的分层提示词", local_path="media/a.mp4", media_type="video",
        visual_fingerprint=fingerprint,
    )


def _shot(visual: str) -> Shot:
    return Shot("shot_1", 1, 3.0, "标题", visual, "旁白", "字幕")


class PromptMatchTests(unittest.TestCase):
    def test_changed_visual_prompt_blocks_reuse(self):
        existing = _record("断桥石栏还留着一层薄霜")
        self.assertFalse(Orchestrator._prompt_matches(existing, _shot("断桥映雪新构图，塔更近")))

    def test_unchanged_visual_prompt_reuses(self):
        existing = _record("断桥石栏还留着一层薄霜")
        self.assertTrue(Orchestrator._prompt_matches(existing, _shot("断桥石栏还留着一层薄霜")))

    def test_legacy_record_without_fingerprint_reuses(self):
        # 历史任务素材没有快照：按可复用处理，不把付过费的素材作废
        self.assertTrue(Orchestrator._prompt_matches(_record(""), _shot("任意新描述")))

    def test_blank_visual_prompt_matches_any_fingerprint(self):
        # 用户把画面描述清空：视为内容未定，沿用旧素材比重新付费更安全
        self.assertTrue(Orchestrator._prompt_matches(_record("有快照"), _shot("")))


if __name__ == "__main__":
    unittest.main()
