#!/usr/bin/env python3
"""
NOEMate 问题推荐离线评测脚本

功能：
1. 加载评测集
2. 调用 recommend_questions.py 的本地逻辑生成 Top3
3. 计算 Hit@3 / Precision@3 / Recall@3 / 维度覆盖率 / Skill一致性 / 禁推率 / 重复率
4. 输出总体结果、按 Skill 结果和失败样本
"""

from __future__ import annotations

import argparse
import difflib
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from recommend_questions import Context, build_final_candidates


DIMENSION_KEYWORDS = {
    "impact": ("影响", "用户", "区域", "网元", "业务成功率"),
    "root_cause": ("根因", "哪个网元", "接口", "peer", "网络环节", "变更", "告警"),
    "evidence": ("日志", "错误码", "信令", "跟踪", "证据", "kpi", "告警"),
    "trend": ("趋势", "昨天", "上周", "一个月", "一年", "同比", "环比"),
    "action": ("创建", "扩容", "配置", "命令", "查看", "优先"),
    "knowledge": ("失败点", "配置示例", "命令", "kpi", "告警"),
}

SKILL_DIMENSIONS = {
    "CN_KnowledgeQA": {"knowledge", "evidence", "action"},
    "CN_CompSpirit": {"impact", "root_cause", "evidence"},
    "CN_FaultSpirit": {"impact", "root_cause", "evidence"},
    "CN_NetworkMonitoring": {"impact", "trend", "evidence", "root_cause"},
    "CN_NetworkPlanning": {"trend", "impact", "action"},
}


def normalize(text: str) -> str:
    return "".join(str(text).lower().split())


def load_dataset(path: Path) -> List[Dict]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def classify_dimensions(question: str) -> List[str]:
    normalized = normalize(question)
    result = []
    for dimension, keywords in DIMENSION_KEYWORDS.items():
        if any(normalize(keyword) in normalized for keyword in keywords):
            result.append(dimension)
    return result


def is_relaxed_match(prediction: str, gold_question: str) -> bool:
    normalized_prediction = normalize(prediction)
    normalized_gold = normalize(gold_question)

    if normalized_prediction == normalized_gold:
        return True

    ratio = difflib.SequenceMatcher(None, normalized_prediction, normalized_gold).ratio()
    if ratio >= 0.72:
        return True

    prediction_dims = set(classify_dimensions(prediction))
    gold_dims = set(classify_dimensions(gold_question))
    if prediction_dims and gold_dims and prediction_dims == gold_dims:
        shared_keywords = 0
        for keywords in DIMENSION_KEYWORDS.values():
            for keyword in keywords:
                normalized_keyword = normalize(keyword)
                if normalized_keyword in normalized_prediction and normalized_keyword in normalized_gold:
                    shared_keywords += 1
        if shared_keywords >= 1:
            return True

    return False


def calc_overlap(predictions: List[str], gold_questions: Iterable[str]) -> Tuple[int, List[str]]:
    gold_list = list(gold_questions)
    matched_gold_indexes = set()
    matched = []
    hit_count = 0
    for question in predictions:
        for index, gold_question in enumerate(gold_list):
            if index in matched_gold_indexes:
                continue
            if is_relaxed_match(question, gold_question):
                matched_gold_indexes.add(index)
                hit_count += 1
                matched.append(question)
                break
    return hit_count, matched


def evaluate_sample(sample: Dict) -> Dict:
    ctx = Context.from_dict(sample)
    predictions = build_final_candidates(ctx)[:3]
    gold_questions = sample.get("gold_questions", [])
    expected_dimensions = set(sample.get("expected_dimensions", []))
    disallowed = {normalize(item) for item in sample.get("disallowed_questions", [])}

    hit_count, matched = calc_overlap(predictions, gold_questions)
    precision_at_3 = hit_count / 3 if predictions else 0.0
    recall_at_3 = hit_count / len(gold_questions) if gold_questions else 0.0
    hit_at_3 = 1.0 if hit_count > 0 else 0.0

    predicted_dimensions = set()
    duplicate_count = 0
    seen = set()
    skill_consistent = 0
    disallowed_hits = 0

    allowed_dimensions = SKILL_DIMENSIONS.get(sample["skill"], expected_dimensions)

    for question in predictions:
        normalized = normalize(question)
        if normalized in seen:
            duplicate_count += 1
        seen.add(normalized)

        if normalized in disallowed:
            disallowed_hits += 1

        dims = classify_dimensions(question)
        predicted_dimensions.update(dims)
        if not allowed_dimensions or set(dims) & allowed_dimensions:
            skill_consistent += 1

    dimension_coverage = (
        len(predicted_dimensions & expected_dimensions) / len(expected_dimensions)
        if expected_dimensions
        else 0.0
    )
    skill_consistency = skill_consistent / 3 if predictions else 0.0
    duplicate_rate = duplicate_count / 3 if predictions else 0.0
    disallowed_rate = disallowed_hits / 3 if predictions else 0.0

    return {
        "id": sample["id"],
        "skill": sample["skill"],
        "include_in_primary_score": sample.get("include_in_primary_score", True),
        "predictions": predictions,
        "matched_questions": matched,
        "hit_at_3": hit_at_3,
        "precision_at_3": precision_at_3,
        "recall_at_3": recall_at_3,
        "dimension_coverage": dimension_coverage,
        "skill_consistency": skill_consistency,
        "duplicate_rate": duplicate_rate,
        "disallowed_rate": disallowed_rate,
    }


def summarize(results: List[Dict]) -> Dict:
    primary = [item for item in results if item["include_in_primary_score"]]
    if not primary:
        return {}

    summary = {}
    metric_names = [
        "hit_at_3",
        "precision_at_3",
        "recall_at_3",
        "dimension_coverage",
        "skill_consistency",
        "duplicate_rate",
        "disallowed_rate",
    ]

    for metric in metric_names:
        summary[metric] = round(sum(item[metric] for item in primary) / len(primary), 4)

    summary["sample_count"] = len(primary)
    return summary


def summarize_by_skill(results: List[Dict]) -> Dict[str, Dict]:
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for result in results:
        if result["include_in_primary_score"]:
            grouped[result["skill"]].append(result)

    summaries = {}
    for skill, items in grouped.items():
        summaries[skill] = summarize(items)
    return summaries


def select_failures(results: List[Dict]) -> List[Dict]:
    failures = []
    for result in results:
        if not result["include_in_primary_score"]:
            continue
        if (
            result["hit_at_3"] < 1.0
            or result["dimension_coverage"] < 0.67
            or result["disallowed_rate"] > 0
            or result["duplicate_rate"] > 0
        ):
            failures.append(result)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="评测 NOEMate 问题推荐精度")
    parser.add_argument("--dataset", required=True, help="评测集 JSON 文件路径")
    parser.add_argument("--failures-only", action="store_true", help="只输出失败样本")
    args = parser.parse_args()

    dataset = load_dataset(Path(args.dataset))
    results = [evaluate_sample(sample) for sample in dataset]
    overall = summarize(results)
    per_skill = summarize_by_skill(results)
    failures = select_failures(results)

    if args.failures_only:
        print(json.dumps({"failures": failures}, ensure_ascii=False, indent=2))
        return 0

    print(
        json.dumps(
            {
                "overall": overall,
                "per_skill": per_skill,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
