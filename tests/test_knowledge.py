import unittest

from backend.app.providers.llm import _resolve_knowledge_context
from backend.app.services import knowledge


class KnowledgeContextTests(unittest.TestCase):
    """知识注入：检索命中、预算控制与降级行为。"""

    def test_corpus_is_loaded(self):
        stats = knowledge.stats()
        self.assertGreater(stats["entities"], 0)
        self.assertEqual(stats["cities"], ["宁波", "杭州"])
        self.assertEqual(stats["entities"], 55)
        self.assertEqual(stats["facts"], 219)
        self.assertGreater(stats["facts"], 0)

    def test_ningbo_pilot_injects_only_ningbo_facts(self):
        context = knowledge.build_generation_context(
            city="宁波", landmark="宁海十里红妆婚俗", theme="非遗体验"
        )
        self.assertIn("宁海十里红妆婚俗", context)
        self.assertIn("国家级", context)
        self.assertNotIn("西湖", context)

    def test_pilot_facts_have_source_records_and_pending_license(self):
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "ningbo.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["entities"]), 9)
        for entity in payload["entities"]:
            facts = entity.get("facts") or []
            records = entity.get("fact_records") or []
            self.assertEqual(len(facts), len(records))
            self.assertTrue(records[0]["source_url"].startswith("https://www.ihningbo.cn/"))
            self.assertEqual(records[0]["reuse_status"], "fact-only-pending-license-review")
            self.assertNotEqual(records[0]["license_status"], "approved")

    def test_landmark_match_injects_verified_facts(self):
        context = knowledge.build_generation_context(
            city="杭州", landmark="西湖", theme="春日城市漫游"
        )
        self.assertIn("西湖", context)
        # 必须是可核实的具体事实，而不是通用套话
        self.assertIn("2011", context)
        self.assertTrue(0 < len(context) <= knowledge.MAX_CONTEXT_CHARS + 400)

    def test_more_specific_alias_wins_over_broad_name(self):
        """虎跑公园比西湖更具体，不应被宽泛条目抢走。"""
        context = knowledge.build_generation_context(
            city="杭州", landmark="虎跑公园", theme="泉水与禅意"
        )
        self.assertIn("虎跑", context)

    def test_context_stays_within_budget(self):
        context = knowledge.build_generation_context(
            city="杭州",
            landmark="西湖",
            theme="龙井茶文化",
            culture="宋韵文化",
            festival="非遗主题周",
        )
        self.assertTrue(len(context) <= knowledge.MAX_CONTEXT_CHARS + 600)

    def test_city_filter_excludes_other_cities(self):
        """未覆盖城市仍不能注入杭州或宁波事实。"""
        context = knowledge.build_generation_context(city="嘉兴", landmark="南湖", theme="红色文化")
        self.assertNotIn("西湖", context)
        self.assertNotIn("宁海十里红妆", context)
        self.assertNotIn("### ", context)

    def test_empty_corpus_lookup_returns_fallback_not_error(self):
        context = knowledge.build_generation_context(city="杭州", theme="城市夜景")
        self.assertTrue(context)
        self.assertIn("使用规则", context)

    def test_provider_helper_survives_missing_fields(self):
        """字段缺失/为 None 是常见调用形态，必须能给出可用参考块而非抛异常。"""
        self.assertIn("使用规则", _resolve_knowledge_context({}))
        self.assertIn("使用规则", _resolve_knowledge_context({"city": None, "theme": None}))

    def test_provider_helper_returns_empty_when_corpus_is_missing(self):
        """语料目录不存在时静默降级为空串，不能阻断脚本生成。"""
        original = knowledge._DATA_DIR
        try:
            knowledge._DATA_DIR = original + "-does-not-exist"
            knowledge.reset_cache()
            self.assertEqual(_resolve_knowledge_context({"city": "杭州", "theme": "测试"}), "")
        finally:
            knowledge._DATA_DIR = original
            knowledge.reset_cache()

    def test_provider_helper_returns_context_for_known_city(self):
        context = _resolve_knowledge_context({"city": "杭州", "landmark": "西湖", "theme": "龙井茶"})
        self.assertIn("西湖", context)


class EnglishQueryTests(unittest.TestCase):
    """素材检索词：知识库的另一半消费方向，回归保护。"""

    def test_entity_alias_maps_to_english_keywords(self):
        query = knowledge.english_query("清晨的西湖湖面", city="杭州", landmark="西湖")
        self.assertIn("West Lake", query)

    def test_unknown_text_returns_empty_for_caller_fallback(self):
        self.assertEqual(knowledge.english_query("完全无关的文本"), "")

    def test_assets_do_not_get_keywords_from_another_city(self):
        self.assertEqual(knowledge.english_query("西湖日落", city="宁波"), "")
        self.assertIn("West Lake", knowledge.english_query("西湖日落", city="杭州市"))

    def test_entity_resolution_prefers_a_name_to_scene_tags(self):
        entity = knowledge.resolve_entity("余姚土布", "宁海传统技艺", city="宁波")
        self.assertEqual(entity["id"], "nb-intangible-yuyao-homespun")

    def test_generation_context_can_find_a_city_entity_by_scene_tag(self):
        context = knowledge.build_generation_context(city="宁波", theme="传统美术")
        self.assertIn("象山竹根雕", context)
        self.assertNotIn("西湖", context)


if __name__ == "__main__":
    unittest.main()
