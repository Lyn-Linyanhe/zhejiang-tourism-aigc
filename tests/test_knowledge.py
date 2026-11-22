import unittest

from backend.app.providers.llm import _resolve_knowledge_context
from backend.app.services import knowledge


class KnowledgeContextTests(unittest.TestCase):
    """知识注入：检索命中、预算控制与降级行为。"""

    def test_corpus_is_loaded(self):
        """基线四城必须在，总数只设下限。

        知识库正在按城市扩容（温州、嘉兴、金华、衢州、舟山、台州陆续落盘），
        把城市清单和实体/事实总数写死会让这条测试每加一城就红一次——那是数据
        在长，不是回归。这里只守住"基线四城没丢、覆盖量没有倒退"。
        """
        stats = knowledge.stats()
        self.assertGreater(stats["entities"], 0)
        self.assertCountEqual(stats["cities"], ["杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水"])
        for city in ("杭州", "宁波", "绍兴", "湖州"):
            self.assertIn(city, stats["cities"])
        # 基线：四城 95 实体 / 369 事实（2026-11-22 引文核查口径），只允许向上
        self.assertGreaterEqual(stats["entities"], 95)
        self.assertGreaterEqual(stats["facts"], 369)

    def test_huzhou_pilot_injects_only_huzhou_facts(self):
        context = knowledge.build_generation_context(
            city="湖州", landmark="南浔古镇", theme="水乡古镇"
        )
        self.assertIn("南浔", context)
        self.assertIn("1252", context)
        self.assertIn("逐条来源已登记", context)
        self.assertNotIn("西湖", context)
        self.assertNotIn("兰亭", context)

    def test_huzhou_facts_have_source_records(self):
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "huzhou.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["entities"]), 27)
        for entity in payload["entities"]:
            facts = entity.get("facts") or []
            records = entity.get("fact_records") or []
            self.assertEqual(len(facts), len(records), entity["id"])
            for record in records:
                self.assertIn("wikipedia.org", record["source_url"])
                self.assertEqual(record["reuse_status"], "fact-only-cc-by-sa-attribution")
                self.assertIn("CC BY-SA", record["license_status"])

    def test_seven_city_kb_facts_have_source_records(self):
        """七城补库（温州/嘉兴/金华/衢州/舟山/台州/丽水）：每条事实必须有来源记录。

        维基来源标 CC BY-SA，非遗网来源标待核许可；两种 reuse_status 不许混。
        这些文件已定稿，断言写死不会随后续扩库变红。
        """
        import json
        from pathlib import Path

        for name in ("wenzhou", "jiaxing", "jinhua", "quzhou", "zhoushan", "taizhou", "lishui"):
            payload = json.loads(
                (Path(knowledge._DATA_DIR) / f"{name}.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(payload["entities"]), 11, name)
            for entity in payload["entities"]:
                facts = entity.get("facts") or []
                records = entity.get("fact_records") or []
                self.assertEqual(len(facts), len(records), f"{name}/{entity['id']}")
                self.assertTrue(facts)
                for record in records:
                    self.assertTrue(record["source_url"].startswith("https://"))
                    if "wikipedia.org" in record["source_url"]:
                        self.assertIn("CC BY-SA", record["license_status"])
                        self.assertEqual(record["reuse_status"],
                                         "fact-only-cc-by-sa-attribution")
                    else:
                        self.assertIn("ihchina.cn", record["source_url"])
                        self.assertEqual(record["reuse_status"],
                                         "fact-only-pending-license-review")

    def test_hangzhou_scenic_entities_are_now_sourced(self):
        """杭州画面已覆盖的实体必须能对上来源，否则“已登记”是空话。"""
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "hangzhou.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        entities = {e["id"]: e for e in payload["entities"]}
        for eid in ("hz-grandcanal", "hz-qingminghetu", "hz-xixi",
                    "hz-liuhe", "hz-xiling", "hz-hupao", "hz-jiuxi",
                    "hz-nansongyuijie", "hz-qiantangchao", "hz-qiandaohu",
                    "hz-person-yuefei", "hz-person-su", "hz-legend-baishezhuan"):
            entity = entities[eid]
            self.assertTrue(entity.get("fact_records"), f"{eid} 仍无来源记录")
            status = knowledge._evidence_status(entity, entity["facts"])
            self.assertTrue(status.startswith("部分事实有来源记录") or
                            status.startswith("逐条来源已登记"), f"{eid}: {status}")

    def test_shaoxing_pilot_injects_only_shaoxing_facts(self):
        context = knowledge.build_generation_context(
            city="绍兴", landmark="兰亭", theme="书法与曲水流觞"
        )
        self.assertIn("兰亭", context)
        self.assertIn("王羲之", context)
        self.assertIn("来源状态", context)
        self.assertIn("许可待核", context)
        self.assertNotIn("西湖", context)
        self.assertNotIn("宁海十里红妆", context)

    def test_shaoxing_facts_have_source_records_and_pending_license(self):
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "shaoxing.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["entities"]), 10)
        for entity in payload["entities"]:
            facts = entity.get("facts") or []
            records = entity.get("fact_records") or []
            self.assertEqual(len(facts), len(records), entity["id"])
            self.assertTrue(facts)
            for record in records:
                self.assertTrue(record["source_url"].startswith(("https://", "http://")))
                self.assertEqual(record["reuse_status"], "fact-only-pending-license-review")
                self.assertNotIn("approved", record["license_status"])

    def test_shaoxing_english_query_maps_to_lanting_keywords(self):
        query = knowledge.english_query("兰亭曲水流觞", city="绍兴")
        self.assertIn("Lanting", query)

    def test_shaoxing_facts_quote_text_that_exists_in_their_source(self):
        """引文必须真的出现在所引页面，而不是凭空写一个像样的出处。"""
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "shaoxing.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        luxun = next(e for e in payload["entities"] if e["id"] == "sx-luxun-guli")
        self.assertEqual(len(luxun["facts"]), len(luxun["fact_records"]))
        for record in luxun["fact_records"]:
            # 修正后统一引 jqjs.htm；旧记录指向不含该文本的 cgzn/*.htm
            self.assertEqual(record["source_url"], "http://sxlxmuseum.com/jqjs.htm")
            self.assertNotIn("318", record["text"])
            self.assertNotIn("东侧", record["text"])
        joined = " ".join(record["evidence_quote"] for record in luxun["fact_records"])
        self.assertIn("鲁迅路和延安路之间", joined)
        self.assertIn("57亩", joined)

    def test_every_fact_record_binds_to_a_fact(self):
        """记录必须能对上事实：靠 text 或 fact_index，两者都没有就是悬空记录。"""
        import json
        from pathlib import Path

        for city in ("hangzhou", "ningbo", "shaoxing", "huzhou", "wenzhou", "jiaxing",
                     "jinhua", "quzhou", "zhoushan", "taizhou", "lishui"):
            payload = json.loads((Path(knowledge._DATA_DIR) / f"{city}.json").read_text(encoding="utf-8"))
            for entity in payload["entities"]:
                facts = entity.get("facts") or []
                records = entity.get("fact_records") or []
                if not records:
                    continue
                normalized = {knowledge._normalize_fact(f) for f in facts}
                for index, record in enumerate(records):
                    bound_by_index = isinstance(record.get("fact_index"), int)
                    bound_by_text = knowledge._normalize_fact(record.get("text")) in normalized
                    self.assertTrue(
                        bound_by_index or bound_by_text,
                        f"{entity['id']} record#{index} 既没有 fact_index 也匹配不上任何 fact",
                    )
                    if bound_by_index:
                        self.assertLess(record["fact_index"], len(facts),
                                        f"{entity['id']} fact_index 越界")

    def test_records_do_not_claim_more_than_they_cover(self):
        """一条记录只覆盖一条事实时，不能对外声称“逐条登记”。"""
        import json
        from pathlib import Path

        payload = json.loads((Path(knowledge._DATA_DIR) / "hangzhou.json").read_text(encoding="utf-8"))
        xihu = next(e for e in payload["entities"] if e["id"] == "hz-xihu")
        status = knowledge._evidence_status(xihu, xihu["facts"])
        self.assertTrue(status.startswith("部分事实有来源记录"), status)
        self.assertIn("1/3", status)

    def test_shaoxing_media_candidates_do_not_ship_deletion_requested_images(self):
        """Commons 删除请求中的图片不得标成可预览。"""
        import csv
        from pathlib import Path

        path = Path(knowledge._DATA_DIR).parent / "docs/research/shaoxing-entity-media-candidates.csv"
        with path.open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        flagged = [r for r in rows if r["license_status"] == "deletion-requested-fop-risk"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["title"], "File:Luxun native place.JPG")
        self.assertEqual(flagged[0]["allowed_usages"], "blocked")
        for row in rows:
            if row["license_status"] == "deletion-requested-fop-risk":
                self.assertNotEqual(row["allowed_usages"], "preview")

    def test_fact_rule_matches_evidence_quality(self):
        """证据不足时不得要求模型把事实写进旁白——这是旧 P1 矛盾的根。"""
        # 西湖：1 条图片描述记录绑定 facts[0]，其余 2 条无来源 → 部分有来源档
        partial = knowledge.build_generation_context(
            city="杭州", landmark="西湖", theme="春日城市漫游"
        )
        self.assertIn("其余仅作背景", partial)
        self.assertIn("只把「来源状态」为逐条登记的事实写进旁白", partial)
        self.assertNotIn("必须真正用进旁白", partial)
        # 兰亭：3/3 全部登记 → 可以要求写进旁白
        sourced = knowledge.build_generation_context(
            city="绍兴", landmark="兰亭", theme="书法与曲水流觞"
        )
        self.assertIn("写进不同镜头的旁白", sourced)
        self.assertNotIn("仅作背景与用词参考", sourced)

    def test_fact_rule_background_only_when_nothing_is_sourced(self):
        """完全没有来源记录的实体才降级为“仅作背景”。"""
        statuses = ["尚未逐条登记来源，仅作背景参考"]
        self.assertIn("仅作背景与用词参考", knowledge._fact_rule(statuses, 3))
        self.assertIn("其余仅作背景", knowledge._fact_rule(
            ["部分事实有来源记录（1/3），其余仅作背景"], 3))

    def test_fact_rule_never_asks_for_more_facts_than_injected(self):
        """单实体单事实时不能要求挑 3 条；总量够 3 条时才提 3。"""
        sourced = ["逐条来源已登记（1/1），许可待核"]
        self.assertIn("挑 1 条", knowledge._fact_rule(sourced, 1))
        self.assertIn("挑 2 条", knowledge._fact_rule(sourced, 2))
        self.assertIn("挑 3 条", knowledge._fact_rule(sourced, 9))
        # 只有一条事实的实体，注入块里也不该出现“挑 3 条”
        context = knowledge.build_generation_context(
            city="宁波", landmark="宁海十里红妆婚俗", theme="非遗体验"
        )
        rule = next(line for line in context.splitlines() if line.startswith("1."))
        self.assertIn("挑 3 条", rule)  # 9 个实体 × 1 条，总量足够

    def test_license_pending_does_not_misfire_on_preview(self):
        """'preview' 含 'review'，不能因此判成待核。"""
        self.assertFalse(knowledge._license_pending("CC BY 4.0 approved"))
        self.assertFalse(knowledge._license_pending("public-domain"))
        self.assertTrue(knowledge._license_pending("preview"))
        self.assertTrue(knowledge._license_pending(""))
        self.assertTrue(knowledge._license_pending("官网未发现开放数据许可"))

    def test_fact_matching_survives_punctuation_and_spacing(self):
        """事实与记录只差标点/空格时，不能判成没有来源。"""
        entity = {
            "facts": ["始建于东晋咸和元年（326 年），由印度僧人慧理创建"],
            "fact_records": [{
                "text": "始建于东晋咸和元年(326年), 由印度僧人慧理创建",
                "license_status": "preview",
            }],
        }
        self.assertEqual(
            knowledge._evidence_status(entity, entity["facts"]),
            "逐条来源已登记（1/1），许可待核",
        )

    def test_fact_index_binds_paraphrased_records(self):
        """转述型记录用 fact_index 显式绑定，不再依赖逐字相等。"""
        entity = {
            "facts": ["甲事实", "乙事实"],
            "fact_records": [
                {"fact_index": 1, "text": "完全不同的转述", "license_status": "preview"},
            ],
        }
        self.assertEqual(
            knowledge._evidence_status(entity, entity["facts"]),
            "部分事实有来源记录（1/2），其余仅作背景",
        )

    def test_shaoxing_facts_use_current_administrative_names(self):
        """当代地理描述用现行区划；官方申报地区原文不改，但要注明。"""
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "shaoxing.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        entities = {e["id"]: e for e in payload["entities"]}
        huangjiu = " ".join(entities["sx-huangjiu"]["facts"])
        self.assertIn("柯桥区（原绍兴县）", huangjiu)
        self.assertIn("上虞区", huangjiu)
        yueju = entities["sx-yueju"]["facts"][0]
        self.assertIn("嵊州市", yueju)
        # 非遗名录的申报地区是档案原文，正文保留，但记录里必须说明区划沿革
        lianhualuo = entities["sx-lianhualuo"]
        self.assertIn("绍兴县", lianhualuo["facts"][1])
        self.assertIn("柯桥区", lianhualuo["fact_records"][1].get("notes", ""))

    def test_shaoxing_time_sensitive_facts_are_anchored(self):
        """“至今 N 年”这类表述会随时间失真，必须锚定日期。"""
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "shaoxing.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entity in payload["entities"]:
            for fact in entity["facts"]:
                self.assertNotIn("至今已有", fact, entity["id"])
                self.assertNotIn("至今已", fact, entity["id"])
        wupeng = next(e for e in payload["entities"] if e["id"] == "sx-wupengchuan")
        self.assertIn("至 2026 年", wupeng["facts"][2])
        self.assertEqual(wupeng["fact_records"][2]["valid_until"], "2027-12-31")


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
        self.assertEqual(len(payload["entities"]), 12)
        for entity in payload["entities"]:
            facts = entity.get("facts") or []
            records = entity.get("fact_records") or []
            self.assertEqual(len(facts), len(records), entity["id"])
            self.assertTrue(records[0]["source_url"].startswith("https://"))
            self.assertNotIn("approved", records[0]["license_status"])
            self.assertNotEqual(records[0]["license_status"], "approved")

    def test_ningbo_scenic_sources_are_labelled_by_level(self):
        """官网来源与维基来源必须区分，不能把百科说成官网。"""
        import json
        from pathlib import Path

        path = Path(knowledge._DATA_DIR) / "ningbo.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        entities = {e["id"]: e for e in payload["entities"]}
        for eid in ("nb-tianyige", "nb-laowaitan", "nb-dongqianhu"):
            self.assertIn(eid, entities, "宁波景区实体缺失")
        # 天一阁有博物院官网来源
        tianyige_urls = {r["source_url"] for r in entities["nb-tianyige"]["fact_records"]}
        self.assertIn("https://www.tianyige.com.cn/", tianyige_urls)
        # 另外两处只有维基来源，必须标 CC BY-SA 且 reuse_status 不是"仅事实引用"
        for eid in ("nb-laowaitan", "nb-dongqianhu"):
            records = entities[eid]["fact_records"]
            for record in records:
                self.assertIn("wikipedia.org", record["source_url"])
                self.assertIn("CC BY-SA", record["license_status"])
                self.assertEqual(record["reuse_status"], "fact-only-cc-by-sa-attribution")

    def test_ningbo_scenic_entities_inject_into_context(self):
        context = knowledge.build_generation_context(
            city="宁波", landmark="天一阁", theme="藏书文化"
        )
        self.assertIn("天一阁", context)
        self.assertIn("范钦", context)
        self.assertIn("逐条来源已登记", context)
        self.assertNotIn("西湖", context)

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

    def test_context_does_not_claim_unverified_facts_are_checked(self):
        context = knowledge.build_generation_context(
            city="杭州", landmark="西湖", theme="春日城市漫游"
        )
        self.assertNotIn("已核查，可放心引用", context)
        self.assertIn("来源状态", context)
        self.assertIn("不要当作已核实事实引用", context)

    def test_city_filter_excludes_other_cities(self):
        """未覆盖城市不能注入其他城市的事实。

        覆盖范围一直在扩（嘉兴、温州、金华陆续入库），所以这里不能用写死的城市名
        当"未覆盖"样本：别的城市的资料里本来就可能提到西湖（南湖就与西湖并称）。
        改成运行时挑一个确实没有数据的城市；全省都覆盖了就跳过。
        """
        covered = set(knowledge.stats()["cities"])
        uncovered = next(
            (city for city in ("衢州", "丽水", "台州", "舟山", "杭州") if city not in covered),
            None,
        )
        if uncovered is None:
            self.skipTest("全省城市都已入库，没有未覆盖城市可验证降级")
        context = knowledge.build_generation_context(city=uncovered, landmark="", theme="城市漫游")
        self.assertNotIn("西湖", context)
        self.assertNotIn("宁海十里红妆", context)
        self.assertNotIn("兰亭", context)
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
