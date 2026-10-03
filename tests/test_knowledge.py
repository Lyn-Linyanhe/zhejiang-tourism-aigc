import unittest

from backend.app.providers.llm import _resolve_knowledge_context
from backend.app.services import knowledge


class KnowledgeContextTests(unittest.TestCase):
    """知识注入：检索命中、预算控制与降级行为。"""

    def test_corpus_is_loaded(self):
        stats = knowledge.stats()
        self.assertGreater(stats["entities"], 0)
        self.assertEqual(stats["cities"], ["杭州"])
        self.assertGreater(stats["facts"], 0)

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
        """语料只有杭州时，宁波选题应退回背景资料而不是注入杭州事实。"""
        context = knowledge.build_generation_context(city="宁波", landmark="天一阁", theme="藏书文化")
        self.assertNotIn("西湖", context)
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


if __name__ == "__main__":
    unittest.main()
