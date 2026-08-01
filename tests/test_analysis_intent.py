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


def test_forward_dependency_fails_closed():
    intent = AnalysisIntent(
        assets=("BTC",),
        operations=(
            IntentOperation(
                "alignment",
                "compare",
                ("news_sentiment", "social_sentiment"),
                "alignment",
                ("news", "social"),
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
