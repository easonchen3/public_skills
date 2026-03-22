#!/usr/bin/env python3
"""
Evaluate NOEMate recommendation quality on the offline dataset.

Primary metric:
- top3_accuracy: semantic hit count in Top3 / 3

Two configurable model roles:
- generation model: produces Top3 from the recommendation prompt
- judge model: evaluates whether each generated item matches the gold results
"""

from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from recommend_questions import (
    Context,
    build_final_candidates,
    create_openai_client,
    is_generic,
    is_near_duplicate,
)
from runtime_config import get_script_config, get_shared_config, load_runtime_config, merge_value


DIMENSION_KEYWORDS = {
    "impact": ("影响", "用户", "区域", "网元", "业务成功率", "受影响", "用户体验", "业务", "小区"),
    "root_cause": ("根因", "哪个网元", "接口", "peer", "网络环节", "变更", "告警", "导致", "更像是", "本端", "对端", "对象"),
    "evidence": ("日志", "错误码", "信令", "跟踪", "证据", "kpi", "告警", "原因值", "状态", "链路"),
    "trend": ("趋势", "昨天", "上周", "一个月", "一年", "同比", "环比", "同期", "增幅", "抬升", "波动", "拐点"),
    "action": ("扩容", "创建", "配置", "命令", "查看", "优先", "先看", "先查", "处理", "检查"),
    "knowledge": ("失败点", "配置示例", "命令", "kpi", "告警", "流程", "机制", "环节", "关键步骤", "配置", "状态"),
}

SKILL_DIMENSIONS = {
    "CN_KnowledgeQA": {"knowledge", "evidence", "action"},
    "CN_CompSpirit": {"impact", "root_cause", "evidence"},
    "CN_FaultSpirit": {"impact", "root_cause", "evidence"},
    "CN_NetworkMonitoring": {"impact", "trend", "evidence", "root_cause"},
    "CN_NetworkPlanning": {"trend", "impact", "action"},
}

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def normalize(text: str) -> str:
    return "".join(str(text).lower().split())


@lru_cache(maxsize=None)
def load_prompt_template(template_name: str) -> str:
    template_path = PROMPTS_DIR / template_name
    return template_path.read_text(encoding="utf-8")


def load_dataset(path: Path) -> List[Dict]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_skills(sample: Dict) -> List[str]:
    skills = sample.get("skills", [])
    if isinstance(skills, list):
        normalized = [str(skill).strip() for skill in skills if str(skill).strip()]
        if normalized:
            return normalized

    skill = str(sample.get("skill", "")).strip()
    return [skill] if skill else []


def infer_expected_dimensions(gold_questions: Iterable[str]) -> set[str]:
    dimensions = set()
    for question in gold_questions:
        dimensions.update(classify_dimensions(question))
    return dimensions


def infer_allowed_dimensions(skills: List[str], expected_dimensions: set[str]) -> set[str]:
    allowed = set()
    for skill in skills:
        allowed.update(SKILL_DIMENSIONS.get(skill, set()))
    return allowed or expected_dimensions


def classify_dimensions(question: str) -> List[str]:
    normalized_question = normalize(question)
    result = []
    for dimension, keywords in DIMENSION_KEYWORDS.items():
        if any(normalize(keyword) in normalized_question for keyword in keywords):
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


def extract_json(text: str) -> Dict:
    text = str(text or "").strip()
    if not text:
        raise ValueError("Empty judge response")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError(f"Unable to parse judge JSON: {text}")


def build_judge_prompt(sample: Dict, predictions: List[str]) -> str:
    template = load_prompt_template("judge_user_prompt.txt")
    payload = {
        "sample_id": sample.get("id", ""),
        "original_query": sample.get("original_query", ""),
        "rewritten_query": sample.get("rewritten_query", ""),
        "skills": json.dumps(resolve_skills(sample), ensure_ascii=False),
        "final_answer": sample.get("final_answer", ""),
        "gold_questions": json.dumps(sample.get("gold_questions", []), ensure_ascii=False, indent=2),
        "predictions": json.dumps(predictions, ensure_ascii=False, indent=2),
        "json_schema": json.dumps(
            {
                "matched_count": 0,
                "top3_accuracy": 0.0,
                "matches": [
                    {
                        "prediction": "string",
                        "is_match": False,
                        "matched_gold": "string or empty",
                        "reason": "short reason",
                    }
                ],
                "summary": "short summary",
            },
            ensure_ascii=False,
            indent=2,
        ),
    }
    return template.format(**payload)


def get_judge_system_prompt() -> str:
    return load_prompt_template("judge_system_prompt.txt").strip()


def judge_with_model(
    *,
    sample: Dict,
    predictions: List[str],
    model: str,
    api_key: str | None,
    base_url: str | None,
    api_key_env: str,
    temperature: float,
    max_tokens: int,
) -> Dict:
    client = create_openai_client(api_key=api_key, base_url=base_url, api_key_env=api_key_env)
    messages = [
        {"role": "system", "content": get_judge_system_prompt()},
        {"role": "user", "content": build_judge_prompt(sample, predictions)},
    ]

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    if (base_url and "dashscope.aliyuncs.com" in base_url) or model.lower().startswith("qwen3"):
        kwargs["extra_body"] = {"enable_thinking": False}

    try:
        response = client.chat.completions.create(max_completion_tokens=max_tokens, **kwargs)
    except TypeError:
        response = client.chat.completions.create(max_tokens=max_tokens, **kwargs)

    raw_text = response.choices[0].message.content or ""
    data = extract_json(raw_text)

    matches = data.get("matches", [])
    matched_count = int(data.get("matched_count", 0))
    if matched_count < 0:
        matched_count = 0
    if matched_count > 3:
        matched_count = 3

    if matched_count == 0 and isinstance(matches, list):
        matched_count = sum(1 for item in matches if isinstance(item, dict) and item.get("is_match"))

    top3_accuracy = round(matched_count / 3, 4)
    matched_questions = [
        str(item.get("prediction", "")).strip()
        for item in matches
        if isinstance(item, dict) and item.get("is_match")
    ]

    return {
        "judge_model": model,
        "judge_raw": raw_text,
        "judge_result": data,
        "matched_count": matched_count,
        "matched_questions": matched_questions,
        "top3_accuracy": top3_accuracy,
    }


def evaluate_prediction_quality(sample: Dict, predictions: List[str]) -> Dict[str, float]:
    gold_questions = sample.get("gold_questions", [])
    resolved_skills = resolve_skills(sample)
    expected_dimensions = infer_expected_dimensions(gold_questions)
    predicted_dimensions = set()
    duplicate_count = 0
    seen = set()
    skill_consistent = 0
    disallowed_hits = 0
    allowed_dimensions = infer_allowed_dimensions(resolved_skills, expected_dimensions)

    for question in predictions:
        normalized_question = normalize(question)
        if normalized_question in seen:
            duplicate_count += 1
        seen.add(normalized_question)

        if (
            is_generic(question)
            or is_near_duplicate(question, sample.get("original_query", ""))
            or is_near_duplicate(question, sample.get("rewritten_query", ""))
        ):
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
        "dimension_coverage": round(dimension_coverage, 4),
        "skill_consistency": round(skill_consistency, 4),
        "duplicate_rate": round(duplicate_rate, 4),
        "disallowed_rate": round(disallowed_rate, 4),
    }


def evaluate_sample(
    sample: Dict,
    *,
    generator_mode: str,
    generator_model: str | None,
    generator_api_key: str | None,
    generator_base_url: str | None,
    generator_api_key_env: str,
    judge_mode: str,
    judge_model: str | None,
    judge_api_key: str | None,
    judge_base_url: str | None,
    judge_api_key_env: str,
    temperature: float,
    max_tokens: int,
) -> Dict:
    ctx = Context.from_dict(sample)
    predictions = build_final_candidates(
        ctx,
        use_llm=(generator_mode == "llm"),
        model=generator_model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=generator_api_key,
        base_url=generator_base_url,
        api_key_env=generator_api_key_env,
    )[:3]

    if judge_mode == "llm":
        if not judge_model:
            raise ValueError("judge_model is required when judge_mode=llm")
        judge_result = judge_with_model(
            sample=sample,
            predictions=predictions,
            model=judge_model,
            api_key=judge_api_key,
            base_url=judge_base_url,
            api_key_env=judge_api_key_env,
            temperature=0,
            max_tokens=max_tokens,
        )
        matched_count = judge_result["matched_count"]
        matched_questions = judge_result["matched_questions"]
        top3_accuracy = judge_result["top3_accuracy"]
        judge_payload = {
            "judge_model": judge_result["judge_model"],
            "judge_result": judge_result["judge_result"],
        }
    else:
        matched_count, matched_questions = calc_overlap(predictions, sample.get("gold_questions", []))
        top3_accuracy = round(matched_count / 3, 4)
        judge_payload = {
            "judge_model": "heuristic",
            "judge_result": {
                "matched_count": matched_count,
                "top3_accuracy": top3_accuracy,
            },
        }

    quality_metrics = evaluate_prediction_quality(sample, predictions)
    hit_at_3 = 1.0 if matched_count > 0 else 0.0
    all_3_hit = 1.0 if matched_count == 3 else 0.0
    recall_at_3 = (
        matched_count / len(sample.get("gold_questions", []))
        if sample.get("gold_questions")
        else 0.0
    )

    return {
        "id": sample["id"],
        "skill": sample.get("skill", ""),
        "skills": resolve_skills(sample),
        "intent_mode": "multi" if len(resolve_skills(sample)) > 1 else "single",
        "include_in_primary_score": sample.get("include_in_primary_score", True),
        "generator_mode": generator_mode,
        "generator_model": generator_model or "fallback",
        "judge_mode": judge_mode,
        "predictions": predictions,
        "matched_questions": matched_questions,
        "matched_count": matched_count,
        "top3_accuracy": top3_accuracy,
        "hit_at_3": hit_at_3,
        "all_3_hit": all_3_hit,
        "recall_at_3": round(recall_at_3, 4),
        **quality_metrics,
        **judge_payload,
    }


def summarize(results: List[Dict]) -> Dict:
    primary = [item for item in results if item["include_in_primary_score"]]
    if not primary:
        return {}

    summary = {}
    metric_names = [
        "top3_accuracy",
        "hit_at_3",
        "all_3_hit",
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

    return {skill: summarize(items) for skill, items in grouped.items()}


def summarize_by_intent_mode(results: List[Dict]) -> Dict[str, Dict]:
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for result in results:
        if result["include_in_primary_score"]:
            grouped[result["intent_mode"]].append(result)

    return {intent_mode: summarize(items) for intent_mode, items in grouped.items()}


def select_failures(results: List[Dict]) -> List[Dict]:
    failures = []
    for result in results:
        if not result["include_in_primary_score"]:
            continue
        if (
            result["top3_accuracy"] < 0.67
            or result["dimension_coverage"] < 0.5
            or result["skill_consistency"] < 0.67
            or result["disallowed_rate"] > 0
            or result["duplicate_rate"] > 0
        ):
            failures.append(result)
    return failures


def resolve_runtime_options(args: argparse.Namespace) -> Dict[str, object]:
    config = load_runtime_config(args.config)
    shared = get_shared_config(config)
    section = get_script_config(config, "evaluate_recommendation")

    return {
        "generator_mode": merge_value(args.generator_mode, section.get("generator_mode"), default="fallback"),
        "generator_model": merge_value(args.generator_model, section.get("generator_model")),
        "generator_api_key": merge_value(
            args.generator_api_key,
            section.get("generator_api_key"),
            args.api_key,
            section.get("api_key"),
            shared.get("api_key"),
        ),
        "generator_api_key_env": merge_value(
            args.generator_api_key_env,
            section.get("generator_api_key_env"),
            args.api_key_env,
            section.get("api_key_env"),
            shared.get("api_key_env"),
            default="OPENAI_API_KEY",
        ),
        "generator_base_url": merge_value(
            args.generator_base_url,
            section.get("generator_base_url"),
            args.base_url,
            section.get("base_url"),
            shared.get("base_url"),
        ),
        "judge_mode": merge_value(args.judge_mode, section.get("judge_mode"), default="heuristic"),
        "judge_model": merge_value(args.judge_model, section.get("judge_model")),
        "judge_api_key": merge_value(
            args.judge_api_key,
            section.get("judge_api_key"),
            args.api_key,
            section.get("api_key"),
            shared.get("api_key"),
        ),
        "judge_api_key_env": merge_value(
            args.judge_api_key_env,
            section.get("judge_api_key_env"),
            args.api_key_env,
            section.get("api_key_env"),
            shared.get("api_key_env"),
            default="OPENAI_API_KEY",
        ),
        "judge_base_url": merge_value(
            args.judge_base_url,
            section.get("judge_base_url"),
            args.base_url,
            section.get("base_url"),
            shared.get("base_url"),
        ),
        "temperature": merge_value(
            args.temperature,
            section.get("temperature"),
            shared.get("temperature"),
            default=0.2,
        ),
        "max_tokens": merge_value(
            args.max_tokens,
            section.get("max_tokens"),
            shared.get("max_tokens"),
            default=800,
        ),
        "concurrency": merge_value(
            args.concurrency,
            section.get("concurrency"),
            default=1,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate NOEMate recommendation accuracy")
    parser.add_argument("--dataset", required=True, help="Dataset JSON file path")
    parser.add_argument("--config", help="Runtime JSON config path")
    parser.add_argument("--output", help="Write the evaluation result to a UTF-8 JSON file")
    parser.add_argument("--generator-mode", choices=("fallback", "llm"))
    parser.add_argument("--generator-model", help="Model name used to generate Top3")
    parser.add_argument("--judge-mode", choices=("heuristic", "llm"))
    parser.add_argument("--judge-model", help="Model name used to judge Top3 accuracy")
    parser.add_argument("--api-key", help="Shared API key for generation and judge model")
    parser.add_argument("--api-key-env", help="Shared API key environment variable")
    parser.add_argument("--base-url", help="Shared OpenAI-compatible base URL")
    parser.add_argument("--generator-api-key", help="API key only for generator model")
    parser.add_argument("--generator-api-key-env", help="Environment variable only for generator API key")
    parser.add_argument("--generator-base-url", help="Base URL only for generator model")
    parser.add_argument("--judge-api-key", help="API key only for judge model")
    parser.add_argument("--judge-api-key-env", help="Environment variable only for judge API key")
    parser.add_argument("--judge-base-url", help="Base URL only for judge model")
    parser.add_argument("--temperature", type=float, help="Generation temperature")
    parser.add_argument("--max-tokens", type=int, help="Max output tokens per model call")
    parser.add_argument("--concurrency", type=int, help="Number of concurrent evaluation workers")
    parser.add_argument("--limit", type=int, help="Only evaluate the first N samples")
    parser.add_argument("--failures-only", action="store_true", help="Only output failed samples")
    args = parser.parse_args()
    runtime = resolve_runtime_options(args)

    if runtime["generator_mode"] == "llm" and not runtime["generator_model"]:
        raise SystemExit("--generator-model is required when --generator-mode llm")
    if runtime["judge_mode"] == "llm" and not runtime["judge_model"]:
        raise SystemExit("--judge-model is required when --judge-mode llm")

    dataset = load_dataset(Path(args.dataset))
    if args.limit:
        dataset = dataset[: args.limit]

    worker_kwargs = {
        "generator_mode": str(runtime["generator_mode"]),
        "generator_model": runtime["generator_model"],
        "generator_api_key": runtime["generator_api_key"],
        "generator_base_url": runtime["generator_base_url"],
        "generator_api_key_env": str(runtime["generator_api_key_env"]),
        "judge_mode": str(runtime["judge_mode"]),
        "judge_model": runtime["judge_model"],
        "judge_api_key": runtime["judge_api_key"],
        "judge_base_url": runtime["judge_base_url"],
        "judge_api_key_env": str(runtime["judge_api_key_env"]),
        "temperature": float(runtime["temperature"]),
        "max_tokens": int(runtime["max_tokens"]),
    }

    concurrency = max(1, int(runtime["concurrency"]))
    if concurrency == 1:
        results = [evaluate_sample(sample, **worker_kwargs) for sample in dataset]
    else:
        indexed_results: List[Tuple[int, Dict]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {
                executor.submit(evaluate_sample, sample, **worker_kwargs): index
                for index, sample in enumerate(dataset)
            }
            for future in concurrent.futures.as_completed(future_map):
                index = future_map[future]
                indexed_results.append((index, future.result()))
        indexed_results.sort(key=lambda item: item[0])
        results = [item[1] for item in indexed_results]

    output = {
        "config": {
            "dataset": args.dataset,
            "runtime_config": args.config or "",
            "generator_mode": runtime["generator_mode"],
            "generator_model": runtime["generator_model"] or "fallback",
            "judge_mode": runtime["judge_mode"],
            "judge_model": runtime["judge_model"] or "heuristic",
            "concurrency": concurrency,
            "primary_metric": "top3_accuracy",
            "sample_count": len(dataset),
        },
        "overall": summarize(results),
        "per_skill": summarize_by_skill(results),
        "per_intent_mode": summarize_by_intent_mode(results),
        "failures": select_failures(results),
    }

    result_obj = {"config": output["config"], "failures": output["failures"]} if args.failures_only else output

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    if args.failures_only:
        print(json.dumps(result_obj, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
