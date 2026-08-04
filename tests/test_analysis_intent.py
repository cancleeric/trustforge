from trustforge.analysis_intent import (
    AnalysisIntent,
    IntentOperation,
    IntentValidationError,
    compile_analysis_intent,
    evaluate_answer_coverage,
    validate_intent,
)


QUESTION = "請分析 BTC：比對新聞與社群情緒是否一致，並指出來源時效與可能的操弄風險。"


def test_btc_news_social_question_compiles_to_composed_operations():
    intent = compile_analysis_intent(QUESTION, ["BTC"])

    assert intent.assets == ("BTC",)
    assert intent.matched_official_template == "multi_source"
    assert [operation.type for operation in intent.operations] == [
        "sentiment_analysis",
        "sentiment_analysis",
        "compare",
        "freshness_assessment",
        "manipulation_risk",
    ]
    assert intent.deliverables == (
        "sentiment_news",
        "sentiment_social",
        "alignment",
        "freshness",
        "manipulation_risk",
    )


def test_official_template_is_not_a_support_whitelist():
    intent = compile_analysis_intent("BTC 有哪些值得注意的資料訊號？", ["BTC"])

    assert intent.supported is True
    assert intent.matched_official_template is None
    assert intent.operations[0].type == "market_synthesis"


def test_news_social_comparison_only_requests_explicit_deliverables():
    intent = compile_analysis_intent("比較 BTC 的新聞與社群情緒是否一致", ["BTC"])

    assert intent.deliverables == (
        "sentiment_news",
        "sentiment_social",
        "alignment",
    )
    assert "freshness" not in intent.deliverables
    assert "manipulation_risk" not in intent.deliverables


def test_dual_asset_comparison_compiles_ordered_child_plan():
    intent = compile_analysis_intent("比較 BTC 與 ETH 的正式分析結果", ["BTC", "ETH"])

    assert intent.assets == ("BTC", "ETH")
    assert intent.matched_official_template == "dual_asset_comparison"
    assert [operation.id for operation in intent.operations] == [
        "asset_analysis_a",
        "asset_analysis_b",
        "comparison_synthesis",
    ]
    assert [operation.type for operation in intent.operations] == [
        "asset_analysis",
        "asset_analysis",
        "comparison_synthesis",
    ]
    assert intent.operations[2].depends_on == (
        "asset_analysis_a",
        "asset_analysis_b",
    )
    assert intent.deliverables == (
        "asset_report_a",
        "asset_report_b",
        "comparison_summary",
    )


def test_english_dual_asset_comparison_preserves_asset_order():
    intent = compile_analysis_intent(
        "compare ETH and BTC formal analysis reports",
        ["ETH", "BTC"],
    )

    assert intent.assets == ("ETH", "BTC")
    assert intent.matched_official_template == "dual_asset_comparison"
    assert intent.deliverables[-1] == "comparison_summary"


def test_non_two_asset_comparisons_do_not_compile_dual_asset_plan():
    same_asset = compile_analysis_intent("比較 BTC 與 BTC", ["BTC", "BTC"])
    missing_asset = compile_analysis_intent("比較 BTC 與", ["BTC"])
    too_many_assets = compile_analysis_intent("比較 BTC ETH SOL", ["BTC", "ETH", "SOL"])

    assert same_asset.matched_official_template != "dual_asset_comparison"
    assert missing_asset.matched_official_template != "dual_asset_comparison"
    assert too_many_assets.matched_official_template != "dual_asset_comparison"


def test_targeted_two_asset_comparison_uses_market_synthesis_not_asset_plan():
    price_intent = compile_analysis_intent("比較 BTC 與 ETH 價格差異", ["BTC", "ETH"])
    english_price_intent = compile_analysis_intent("compare BTC and ETH price", ["BTC", "ETH"])
    regulatory_intent = compile_analysis_intent("比較 BTC 與 ETH 監管差異", ["BTC", "ETH"])
    formal_price_intent = compile_analysis_intent(
        "比較 BTC 與 ETH 價格正式分析",
        ["BTC", "ETH"],
    )
    formal_english_price_intent = compile_analysis_intent(
        "compare BTC and ETH price analysis report",
        ["BTC", "ETH"],
    )
    formal_regulatory_intent = compile_analysis_intent(
        "比較 BTC 與 ETH 監管分析結果",
        ["BTC", "ETH"],
    )

    assert price_intent.operations[0].type == "market_synthesis"
    assert price_intent.operations[0].targets == ("price",)
    assert english_price_intent.operations[0].type == "market_synthesis"
    assert english_price_intent.operations[0].targets == ("price",)
    assert regulatory_intent.operations[0].type == "market_synthesis"
    assert regulatory_intent.operations[0].targets == ("regulatory",)
    assert formal_price_intent.operations[0].type == "market_synthesis"
    assert formal_price_intent.operations[0].targets == ("price",)
    assert formal_english_price_intent.operations[0].type == "market_synthesis"
    assert formal_english_price_intent.operations[0].targets == ("price",)
    assert formal_regulatory_intent.operations[0].type == "market_synthesis"
    assert formal_regulatory_intent.operations[0].targets == ("regulatory",)
    assert price_intent.matched_official_template != "dual_asset_comparison"
    assert english_price_intent.matched_official_template != "dual_asset_comparison"
    assert regulatory_intent.matched_official_template != "dual_asset_comparison"
    assert formal_price_intent.matched_official_template != "dual_asset_comparison"
    assert formal_english_price_intent.matched_official_template != "dual_asset_comparison"
    assert formal_regulatory_intent.matched_official_template != "dual_asset_comparison"


def test_news_social_comparison_is_not_dual_asset_even_with_two_assets():
    intent = compile_analysis_intent(
        "比較 BTC 的 news 與 social 是否一致",
        ["BTC", "ETH"],
    )

    assert intent.matched_official_template == "multi_source"
    assert [operation.type for operation in intent.operations[:3]] == [
        "sentiment_analysis",
        "sentiment_analysis",
        "compare",
    ]


def test_llm_cannot_invent_capability_or_connector():
    def unsafe_parser(_question, _assets):
        return {
            "assets": ["BTC"],
            "operations": [
                {
                    "id": "steal_secret",
                    "type": "call_arbitrary_url",
                    "targets": ["https://attacker.invalid"],
                    "output": "alignment",
                }
            ],
            "deliverables": ["alignment"],
            "parse_confidence": 1,
        }

    intent = compile_analysis_intent(QUESTION, ["BTC"], llm_parser=unsafe_parser)

    assert intent.parse_mode == "deterministic_fallback"
    assert all(operation.type != "call_arbitrary_url" for operation in intent.operations)


def test_llm_cannot_replace_caller_authorized_assets():
    def asset_swapping_parser(_question, _assets):
        return {
            "assets": ["ETH"],
            "operations": [
                {
                    "id": "market",
                    "type": "market_synthesis",
                    "targets": ["price"],
                    "output": "market_summary",
                }
            ],
            "deliverables": ["market_summary"],
        }

    intent = compile_analysis_intent("分析 BTC 價格", ["BTC"], llm_parser=asset_swapping_parser)

    assert intent.assets == ("BTC",)
    assert intent.parse_mode == "deterministic_fallback"


def test_llm_dual_asset_plan_requires_two_caller_authorized_assets():
    def unauthorized_dual_asset_parser(_question, _assets):
        return {
            "assets": ["BTC"],
            "operations": [
                {
                    "id": "asset_analysis_a",
                    "type": "asset_analysis",
                    "targets": ["asset"],
                    "output": "asset_report_a",
                },
                {
                    "id": "asset_analysis_b",
                    "type": "asset_analysis",
                    "targets": ["asset"],
                    "output": "asset_report_b",
                },
                {
                    "id": "comparison_synthesis",
                    "type": "comparison_synthesis",
                    "targets": ["asset_analysis_a", "asset_analysis_b"],
                    "output": "comparison_summary",
                    "depends_on": ["asset_analysis_a", "asset_analysis_b"],
                },
            ],
            "deliverables": ["asset_report_a", "asset_report_b", "comparison_summary"],
            "matched_official_template": "dual_asset_comparison",
            "parse_confidence": 1,
        }

    intent = compile_analysis_intent(
        "比較 BTC 與 ETH 的正式分析結果",
        ["BTC"],
        llm_parser=unauthorized_dual_asset_parser,
    )

    assert intent.assets == ("BTC",)
    assert intent.parse_mode == "deterministic_fallback"
    assert intent.matched_official_template != "dual_asset_comparison"


def test_llm_dual_asset_plan_requires_explicit_formal_analysis_request():
    def dual_asset_parser(_question, _assets):
        return {
            "assets": ["BTC", "ETH"],
            "operations": [
                {
                    "id": "asset_analysis_a",
                    "type": "asset_analysis",
                    "targets": ["asset"],
                    "output": "asset_report_a",
                },
                {
                    "id": "asset_analysis_b",
                    "type": "asset_analysis",
                    "targets": ["asset"],
                    "output": "asset_report_b",
                },
                {
                    "id": "comparison_synthesis",
                    "type": "comparison_synthesis",
                    "targets": ["asset_analysis_a", "asset_analysis_b"],
                    "output": "comparison_summary",
                    "depends_on": ["asset_analysis_a", "asset_analysis_b"],
                },
            ],
            "deliverables": ["asset_report_a", "asset_report_b", "comparison_summary"],
            "matched_official_template": "dual_asset_comparison",
            "parse_confidence": 1,
        }

    formal_intent = compile_analysis_intent(
        "比較 BTC 與 ETH 的正式分析結果",
        ["BTC", "ETH"],
        llm_parser=dual_asset_parser,
    )
    price_intent = compile_analysis_intent(
        "compare BTC and ETH price",
        ["BTC", "ETH"],
        llm_parser=dual_asset_parser,
    )
    regulatory_intent = compile_analysis_intent(
        "比較 BTC 與 ETH 監管差異",
        ["BTC", "ETH"],
        llm_parser=dual_asset_parser,
    )
    formal_price_intent = compile_analysis_intent(
        "compare BTC and ETH price analysis report",
        ["BTC", "ETH"],
        llm_parser=dual_asset_parser,
    )
    formal_regulatory_intent = compile_analysis_intent(
        "比較 BTC 與 ETH 監管分析結果",
        ["BTC", "ETH"],
        llm_parser=dual_asset_parser,
    )

    assert formal_intent.parse_mode == "llm"
    assert formal_intent.matched_official_template == "dual_asset_comparison"
    assert price_intent.parse_mode == "deterministic_fallback"
    assert price_intent.operations[0].type == "market_synthesis"
    assert price_intent.operations[0].targets == ("price",)
    assert regulatory_intent.parse_mode == "deterministic_fallback"
    assert regulatory_intent.operations[0].type == "market_synthesis"
    assert regulatory_intent.operations[0].targets == ("regulatory",)
    assert formal_price_intent.parse_mode == "deterministic_fallback"
    assert formal_price_intent.operations[0].type == "market_synthesis"
    assert formal_price_intent.operations[0].targets == ("price",)
    assert formal_regulatory_intent.parse_mode == "deterministic_fallback"
    assert formal_regulatory_intent.operations[0].type == "market_synthesis"
    assert formal_regulatory_intent.operations[0].targets == ("regulatory",)


def test_forward_dependency_fails_closed():
    intent = AnalysisIntent(
        assets=("BTC",),
        operations=(
            IntentOperation("news_sentiment", "sentiment_analysis", ("news",), "sentiment_news"),
            IntentOperation(
                "social_sentiment",
                "sentiment_analysis",
                ("social",),
                "sentiment_social",
            ),
            IntentOperation(
                "alignment",
                "compare",
                ("news_sentiment", "social_sentiment"),
                "alignment",
                ("future_dependency",),
            ),
        ),
        deliverables=("alignment",),
    )

    try:
        validate_intent(intent)
    except IntentValidationError as exc:
        assert "dependency" in str(exc)
    else:
        raise AssertionError("forward dependency must be rejected")


def test_llm_compare_targets_must_have_upstream_producers():
    def unbound_compare_parser(_question, _assets):
        return {
            "assets": ["BTC"],
            "operations": [
                {
                    "id": "sentiment_alignment",
                    "type": "compare",
                    "targets": ["news_sentiment", "social_sentiment"],
                    "output": "alignment",
                }
            ],
            "deliverables": ["alignment"],
            "parse_confidence": 1,
        }

    intent = compile_analysis_intent(
        "比較 BTC 的新聞與社群情緒是否一致",
        ["BTC"],
        llm_parser=unbound_compare_parser,
    )

    assert intent.parse_mode == "deterministic_fallback"
    assert [operation.id for operation in intent.operations[:2]] == [
        "news_sentiment",
        "social_sentiment",
    ]


def test_llm_compare_targets_must_bind_expected_producer_type_and_output():
    def spoofed_producer_parser(_question, _assets):
        return {
            "assets": ["BTC"],
            "operations": [
                {
                    "id": "news_sentiment",
                    "type": "market_synthesis",
                    "targets": ["price"],
                    "output": "market_summary",
                },
                {
                    "id": "social_sentiment",
                    "type": "sentiment_analysis",
                    "targets": ["social"],
                    "output": "sentiment_social",
                },
                {
                    "id": "sentiment_alignment",
                    "type": "compare",
                    "targets": ["news_sentiment", "social_sentiment"],
                    "output": "alignment",
                },
            ],
            "deliverables": ["alignment"],
            "parse_confidence": 1,
        }

    intent = compile_analysis_intent(
        "比較 BTC 的新聞與社群情緒是否一致",
        ["BTC"],
        llm_parser=spoofed_producer_parser,
    )

    assert intent.parse_mode == "deterministic_fallback"
    assert intent.operations[0].type == "sentiment_analysis"


def test_answer_coverage_never_treats_missing_or_unbound_answer_as_complete():
    intent = compile_analysis_intent(QUESTION, ["BTC"])
    coverage = evaluate_answer_coverage(
        intent,
        {
            "sentiment_news": {
                "status": "answered",
                "evidence_claim_ids": ["news-1"],
            },
            "sentiment_social": {
                "status": "insufficient_data",
                "reason": "social_sample_too_small",
            },
            "alignment": {"status": "answered", "evidence_claim_ids": []},
            "freshness": {
                "status": "answered",
                "evidence_claim_ids": ["news-1", "social-1"],
            },
        },
    )

    assert coverage.status == "failed"
    by_name = {item.deliverable: item for item in coverage.items}
    assert by_name["sentiment_social"].status == "insufficient_data"
    assert by_name["alignment"].status == "failed"
    assert by_name["alignment"].reason == "answered_without_evidence"
    assert by_name["manipulation_risk"].reason == "missing_result"


def test_hypothesis_question_remains_a_fixture_not_the_only_supported_shape():
    intent = compile_analysis_intent(
        "市場上有聲音認為 BTC 短期內將維持盤整，請蒐集支持與反對證據。",
        ["BTC"],
    )

    assert intent.matched_official_template == "hypothesis"
    assert intent.operations[0].type == "hypothesis_test"
