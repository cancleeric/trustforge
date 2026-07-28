"""Bedrock LLM comparative synthesis — CA-04.

用 Bedrock 對四個比較面向產出語意比較（comparative synthesis），
並在失敗/違規時降級回 deterministic fallback（CA-03 備援）。

不可修改 DB schema/migration、不可修改 pipeline.run() 單幣路徑。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .bedrock import BedrockClient, LLMResult
from .comparison_contract import (
    COMPARISON_DIMENSIONS,
    DIMENSION_LABEL_MAP,
    ComparisonReport,
    DimensionResult,
)
from .schema import Evidence

if TYPE_CHECKING:
    from .execlog import ExecutionLog

logger = logging.getLogger(__name__)

# 每個面向的 confidence ceiling（LLM 回傳不得超過此值）
DIMENSION_CONFIDENCE_CEILINGS: dict[str, float] = {
    "價格動能": 0.85,
    "鏈上活動": 0.80,
    "市場情緒": 0.75,
    "生態發展": 0.70,
}

_Snippet = 200  # 每筆 evidence 截取字數，防 token 爆量


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def synthesize_comparison_with_bedrock(
    client: BedrockClient,
    comparison: ComparisonReport,
    evidence_a: list[Evidence],
    evidence_b: list[Evidence],
    log: ExecutionLog | None = None,
    max_retries: int = 1,
) -> ComparisonReport:
    """用 Bedrock 對四個面向產出語意比較，失敗時回傳原始 comparison（降級）。

    流程：
    1. 從 comparison 讀取各面向 a_evidence_refs / b_evidence_refs
    2. 組建 Bedrock prompt（JSON 格式，含四面向 A/B evidence 摘要）
    3. 呼叫 client.complete(system, prompt)（bounded retry，最多 2 次）
    4. 解析 JSON 回應 → 建立新 ComparisonReport
    5. 解析失敗 / timeout → 回傳原始 comparison（CA-03 deterministic fallback）
    6. 驗證每面向 finding 不引用不存在的 evidence ref
    7. 驗證每面向 finding 數字是否出現在 source evidence（overclaim validation）
    8. 記錄 latency / cost / execution-event 到 log
    9. Confidence 套用 dimension ceiling
    """
    system_prompt, user_prompt = _build_synthesis_prompt(comparison, evidence_a, evidence_b)

    for attempt in range(max_retries + 1):
        t_start = time.time()
        try:
            result: LLMResult = client.complete(system_prompt, user_prompt)
        except Exception as exc:
            if attempt < max_retries:
                backoff = 1 if attempt == 0 else 3
                logger.warning(
                    "Bedrock complete() attempt %d/%d failed: %s. Retrying in %ds...",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
                continue
            logger.warning(
                "Bedrock complete() 失敗（%d/%d attempts），降級回原始 comparison: %s",
                max_retries + 1,
                max_retries + 1,
                exc,
            )
            return comparison

        latency = time.time() - t_start

        # 記錄 execution event
        if log is not None:
            log.record(
                "comparison.bedrock.call",
                params={
                    "attempt": attempt + 1,
                    "total_attempts": max_retries + 1,
                    "latency_sec": round(latency, 3),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "model_id": result.model_id,
                },
                summary=f"attempt {attempt + 1}/{max_retries + 1} latency={latency:.3f}s tokens={result.input_tokens}/{result.output_tokens}",
            )

        try:
            parsed = _parse_synthesis_response(result.text)
        except Exception as exc:
            if attempt < max_retries:
                backoff = 1 if attempt == 0 else 3
                logger.warning(
                    "Bedrock 回應解析失敗（attempt %d/%d）: %s. Retrying in %ds...",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
                continue
            logger.warning("Bedrock 回應解析失敗，降級回原始 comparison: %s", exc)
            return comparison

        # 驗證輸出結構
        violations = _validate_synthesis_output(parsed, comparison)
        if violations:
            if attempt < max_retries:
                backoff = 1 if attempt == 0 else 3
                logger.warning(
                    "Bedrock 合成輸出驗證失敗（attempt %d/%d, %s 項）: %s. Retrying in %ds...",
                    attempt + 1,
                    max_retries + 1,
                    len(violations),
                    violations,
                    backoff,
                )
                time.sleep(backoff)
                continue
            logger.warning(
                "Bedrock 合成輸出驗證失敗 (%s 項)，降級回原始 comparison: %s",
                len(violations),
                violations,
            )
            return comparison

        # Overclaim validation：檢查 finding 中的數字是否出現在 evidence 中
        _validate_overclaim(parsed, evidence_a, evidence_b)

        # 套用 confidence ceiling 後建出增強 ComparisonReport
        return _build_enhanced_report(parsed, comparison, evidence_a, evidence_b)

    return comparison


# ---------------------------------------------------------------------------
# Prompt 建構
# ---------------------------------------------------------------------------

def _build_synthesis_prompt(
    comparison: ComparisonReport,
    evidence_a: list[Evidence],
    evidence_b: list[Evidence],
) -> tuple[str, str]:
    """回傳 (system_prompt, user_prompt)。

    每個面向的 A/B evidence 截取首 200 chars，防 token 爆量。
    System prompt 強調：只能用提供的 evidence refs，不可引用不存在證據。
    """
    system = (
        "你是加密市場比較分析師。請根據提供的雙幣證據，產出結構化比較報告。"
        "你**只能引用以下提供的證據**，不可引用不存在或超出範圍的證據。"
        "輸出必須是純 JSON，不含 markdown 代碼塊標記。"
    )

    # 為每個面向組建摘要（帶索引）
    def _snippets(evidence: list[Evidence], refs: list[int]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for idx in refs:
            if 0 <= idx < len(evidence):
                ev = evidence[idx]
                out.append({
                    "idx": idx,
                    "source": ev.source,
                    "kind": ev.kind,
                    "snippet": ev.content_reference[:_Snippet] if ev.content_reference else "",
                })
        return out

    dims_payload: list[dict[str, Any]] = []
    for dim in comparison.dimensions:
        dims_payload.append({
            "dimension": dim.dimension,
            "a_evidence": _snippets(evidence_a, dim.a_evidence_refs),
            "b_evidence": _snippets(evidence_b, dim.b_evidence_refs),
        })

    prompt_payload = {
        "coin_a": comparison.coin_a,
        "coin_b": comparison.coin_b,
        "dimensions": dims_payload,
    }

    user = (
        "請根據以下雙幣證據，產出結構化 JSON 比較報告。\n\n"
        f"```json\n{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "輸出 JSON 格式：\n"
        '{\n'
        '  "conclusion": "綜合比較結論（非空字串）",\n'
        '  "overall_confidence": 0.0-1.0,\n'
        '  "dimensions": [\n'
        '    {\n'
        '      "dimension": "價格動能|鏈上活動|市場情緒|生態發展",\n'
        '      "finding": "比較發現文字",\n'
        '      "confidence": 0.0-1.0,\n'
        '      "decision": "abstain|insufficient|normal",\n'
        '      "a_evidence_refs": [0, 1, ...],\n'
        '      "b_evidence_refs": [0, 1, ...]\n'
        '    },\n'
        '    ...\n'
        '  ]\n'
        '}\n'
        "\n規則：\n"
        "1. conclusion 不可為空。\n"
        "2. 四個面向缺一不可，順序不拘。\n"
        "3. confidence 值不得超過各面向上限：價格動能 0.85、鏈上活動 0.80、市場情緒 0.75、生態發展 0.70。\n"
        "4. evidence_refs 只能引用存在的索引（a 在 0~N-1，b 在 0~M-1）。\n"
        "5. 只使用已提供的證據，不添加外部知識。\n"
    )

    return system, user


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def _parse_synthesis_response(text: str) -> dict:
    """Robust JSON parse：strips ```json ... ``` wrappers，驗證必要欄位。"""
    text = text.strip()

    # 剝掉 markdown 代碼塊
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline:]
        if text.endswith("```"):
            text = text[:-3].strip()
        # 如果第一行是 json 語言標記，也剝掉
        if text.startswith("json"):
            text = text[4:].lstrip()

    # 找 JSON 物件主體（有時模型會加前後廢話）
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("回應中找不到 JSON 物件")
    text = text[start : end + 1]

    data: dict = json.loads(text)

    # 驗證必要頂層欄位
    for key in ("conclusion", "dimensions", "overall_confidence"):
        if key not in data:
            raise ValueError(f"回應缺少必要欄位: {key}")

    dims = data.get("dimensions")
    if not isinstance(dims, list) or len(dims) != 4:
        raise ValueError(f"dimensions 必須是 4 個元素的 list，實際 {type(dims).__name__} / len={len(dims) if isinstance(dims, list) else 'N/A'}")

    return data


# ---------------------------------------------------------------------------
# 輸出驗證
# ---------------------------------------------------------------------------

def _validate_synthesis_output(
    response: dict,
    original: ComparisonReport,
) -> list[str]:
    """回傳違規清單（空 list = 可用）。

    檢查：
    - conclusion 非空
    - 四個面向缺一不可
    - evidence_refs 不越界
    - confidence 在 0-1 範圍
    """
    violations: list[str] = []
    ev_a_count = len(original.supporting_evidence_a)
    ev_b_count = len(original.supporting_evidence_b)

    conclusion = str(response.get("conclusion", "")).strip()
    if not conclusion:
        violations.append("conclusion 為空")

    dims = response.get("dimensions", [])
    if not isinstance(dims, list):
        violations.append("dimensions 不是 list")
        return violations

    if len(dims) != 4:
        violations.append(f"dimensions 數量不為 4: {len(dims)}")

    seen_dims: set[str] = set()
    for i, dim in enumerate(dims):
        if not isinstance(dim, dict):
            violations.append(f"dimensions[{i}] 不是 dict")
            continue
        dim_name = dim.get("dimension", "")
        if dim_name not in COMPARISON_DIMENSIONS:
            violations.append(f"未知面向: {dim_name}")
        if dim_name in seen_dims:
            violations.append(f"重複面向: {dim_name}")
        seen_dims.add(dim_name)

        confidence = dim.get("confidence", 0.0)
        try:
            c = float(confidence)
        except (ValueError, TypeError):
            c = -1.0
        if not (0.0 <= c <= 1.0):
            violations.append(f"面向 '{dim_name}' confidence 超出範圍: {confidence}")

        for ref in dim.get("a_evidence_refs", []):
            try:
                r = int(ref)
            except (ValueError, TypeError):
                violations.append(f"面向 '{dim_name}' a_evidence_refs 含非整數: {ref}")
                continue
            if r < 0 or r >= ev_a_count:
                violations.append(
                    f"面向 '{dim_name}' a_evidence_refs[{r}] 越界 (A 共 {ev_a_count} 筆)"
                )

        for ref in dim.get("b_evidence_refs", []):
            try:
                r = int(ref)
            except (ValueError, TypeError):
                violations.append(f"面向 '{dim_name}' b_evidence_refs 含非整數: {ref}")
                continue
            if r < 0 or r >= ev_b_count:
                violations.append(
                    f"面向 '{dim_name}' b_evidence_refs[{r}] 越界 (B 共 {ev_b_count} 筆)"
                )

    overall_conf = response.get("overall_confidence", 0.0)
    try:
        oc = float(overall_conf)
    except (ValueError, TypeError):
        oc = -1.0
    if not (0.0 <= oc <= 1.0):
        violations.append(f"overall_confidence 超出範圍: {overall_conf}")

    return violations


# ---------------------------------------------------------------------------
# Overclaim validation
# ---------------------------------------------------------------------------

def _validate_overclaim(
    response: dict,
    evidence_a: list[Evidence],
    evidence_b: list[Evidence],
) -> None:
    """檢查每面向 finding 中的數字是否出現在 source evidence 中。

    若 finding 含未在 evidence content_reference 中出現的數字，
    則將該 dimension 降級為 insufficient，conclusion 保留但標註。
    修改 response dict in-place。
    """
    dims = response.get("dimensions", [])
    if not isinstance(dims, list):
        return

    for dim in dims:
        if not isinstance(dim, dict):
            continue
        finding = str(dim.get("finding", ""))
        numbers = re.findall(r'-?\d+\.?\d*', finding)
        if not numbers:
            continue

        # 收集所有被引用的 evidence content_reference 文字
        a_refs: list[int] = []
        b_refs: list[int] = []
        for ref in dim.get("a_evidence_refs", []):
            try:
                a_refs.append(int(ref))
            except (ValueError, TypeError):
                pass
        for ref in dim.get("b_evidence_refs", []):
            try:
                b_refs.append(int(ref))
            except (ValueError, TypeError):
                pass

        evidence_texts: list[str] = []
        for ref in a_refs:
            if 0 <= ref < len(evidence_a):
                cr = evidence_a[ref].content_reference or ""
                evidence_texts.append(cr)
        for ref in b_refs:
            if 0 <= ref < len(evidence_b):
                cr = evidence_b[ref].content_reference or ""
                evidence_texts.append(cr)

        combined = " ".join(evidence_texts)

        for num in numbers:
            if num not in combined:
                dim["decision"] = "insufficient"
                old_finding = str(dim.get("finding", ""))
                dim["finding"] = old_finding + "（部分發現含未驗證數值）"
                break  # 一個未驗證數字即足以降級


# ---------------------------------------------------------------------------
# 建構增強報告
# ---------------------------------------------------------------------------

def _build_enhanced_report(
    parsed: dict,
    original: ComparisonReport,
    evidence_a: list[Evidence],
    evidence_b: list[Evidence],
) -> ComparisonReport:
    """根據 LLM 解析結果與原始骨架，建立增強 ComparisonReport。

    - 套用 confidence ceiling
    - 保留原始 supporting_report / supporting_evidence
    - 未提供的面向保留原始 skeleton
    """
    # 以 dimension 名稱為 key 的快速查詢
    dim_map: dict[str, dict] = {}
    for d in parsed.get("dimensions", []):
        if isinstance(d, dict):
            dim_map[d.get("dimension", "")] = d

    new_dimensions: list[DimensionResult] = []
    for dim in original.dimensions:
        dim_name = dim.dimension
        llm_dim = dim_map.get(dim_name)
        if llm_dim is None:
            # 如果 LLM 沒回這個面向，保留原始 skeleton
            new_dimensions.append(dim)
            continue

        raw_conf = float(llm_dim.get("confidence", 0.0))
        ceiling = DIMENSION_CONFIDENCE_CEILINGS.get(dim_name, 1.0)
        capped_conf = min(raw_conf, ceiling)

        new_dimensions.append(DimensionResult(
            dimension=dim_name,
            label=DIMENSION_LABEL_MAP.get(dim_name, dim_name),
            finding=str(llm_dim.get("finding", dim.finding)).strip() or dim.finding,
            a_evidence_refs=[int(r) for r in llm_dim.get("a_evidence_refs", dim.a_evidence_refs) if isinstance(r, (int, float))],
            b_evidence_refs=[int(r) for r in llm_dim.get("b_evidence_refs", dim.b_evidence_refs) if isinstance(r, (int, float))],
            confidence=capped_conf,
            decision=str(llm_dim.get("decision", dim.decision)).strip() or dim.decision,
        ))

    overall_conf = float(parsed.get("overall_confidence", original.confidence))
    overall_conf = min(overall_conf, 1.0)

    return ComparisonReport(
        coin_a=original.coin_a,
        coin_b=original.coin_b,
        query=original.query,
        conclusion=str(parsed.get("conclusion", original.conclusion)).strip() or original.conclusion,
        dimensions=new_dimensions,
        confidence=overall_conf,
        limits=list(original.limits),
        could_flip=list(original.could_flip),
        generated_at=original.generated_at,
        supporting_report_a=original.supporting_report_a,
        supporting_report_b=original.supporting_report_b,
        supporting_evidence_a=list(evidence_a),
        supporting_evidence_b=list(evidence_b),
    )
