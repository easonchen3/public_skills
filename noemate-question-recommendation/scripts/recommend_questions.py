#!/usr/bin/env python3
"""
NOEMate 问题推荐脚本

用途：
1. 根据上下文生成模板候选、记忆候选、缺口候选
2. 执行去重、过滤和粗排
3. 构建给最终单次 LLM 调用的 Prompt

说明：
- 本脚本不直接调用 LLM
- `top3` 模式输出的是本地粗排后的兜底 Top3
- 真正接入线上时，应使用 `prompt` 模式结果去调用一次 LLM
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List


SKILL_TEMPLATES = {
    "CN_KnowledgeQA": [
        "{topic}流程里最常见的失败点有哪些？",
        "如果{topic}异常，通常会触发哪些告警或KPI波动？",
        "在华为{network_element}上，{topic}相关配置或查看命令是什么？",
        "能否给出基于{version}版本的{topic}配置示例？",
    ],
    "CN_CompSpirit": [
        "同时段是否还有其他用户出现类似问题？",
        "该问题更可能发生在哪个网络环节？",
        "这段时间有哪些异常信令或错误码？",
        "是否需要创建跟踪进一步确认？",
    ],
    "CN_FaultSpirit": [
        "这次异常影响了多少用户？",
        "哪些区域或网元受到影响最大？",
        "同时段是否有关联告警或变更操作？",
        "是否需要进一步拉取日志或创建跟踪？",
    ],
    "CN_NetworkMonitoring": [
        "异常主要集中在哪些接口、Peer或区域？",
        "这些异常是否已经影响业务成功率？",
        "与昨天或上周同期相比变化如何？",
        "是否有相关告警或日志同时异常？",
    ],
    "CN_NetworkPlanning": [
        "过去一个月或一年该指标的趋势如何？",
        "哪些区域增长最快？",
        "如果保持当前增长速度，何时达到容量瓶颈？",
        "哪些网元更适合优先扩容？",
    ],
}

GENERIC_FILTER_WORDS = {"还有什么异常", "还能看什么", "还有哪些问题"}
ACTION_HINTS = ("创建", "执行", "扩容", "变更", "回退", "跟踪")


@dataclass
class Context:
    original_query: str
    rewritten_query: str
    skill: str
    answer_summary: str
    knowledge_summary: str = ""
    execution_summary: str = ""
    entities: Dict[str, List[str]] = field(default_factory=dict)
    result_tags: Dict[str, bool] = field(default_factory=dict)
    memory_candidates: List[str] = field(default_factory=list)
    invalid_candidates: List[str] = field(default_factory=list)
    max_candidates: int = 8

    @classmethod
    def from_dict(cls, data: Dict) -> "Context":
        return cls(
            original_query=data.get("original_query", "").strip(),
            rewritten_query=data.get("rewritten_query", "").strip(),
            skill=data.get("skill", "").strip(),
            answer_summary=data.get("answer_summary", "").strip(),
            knowledge_summary=data.get("knowledge_summary", "").strip(),
            execution_summary=data.get("execution_summary", "").strip(),
            entities=normalize_entities(data.get("entities", {})),
            result_tags={k: bool(v) for k, v in data.get("result_tags", {}).items()},
            memory_candidates=normalize_text_list(data.get("memory_candidates", [])),
            invalid_candidates=normalize_text_list(data.get("invalid_candidates", [])),
            max_candidates=int(data.get("max_candidates", 8) or 8),
        )


def normalize_text_list(items: Iterable[str]) -> List[str]:
    result: List[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def normalize_entities(raw_entities: Dict) -> Dict[str, List[str]]:
    entities: Dict[str, List[str]] = {}
    for key, value in (raw_entities or {}).items():
        if isinstance(value, list):
            items = normalize_text_list(value)
        elif value is None:
            items = []
        else:
            items = normalize_text_list([value])
        entities[str(key)] = items
    return entities


def first_entity(entities: Dict[str, List[str]], key: str, default: str) -> str:
    values = entities.get(key, [])
    return values[0] if values else default


def fill_template(template: str, entities: Dict[str, List[str]]) -> str:
    return template.format(
        topic=first_entity(entities, "feature", "该流程"),
        network_element=first_entity(entities, "network_element", "相关网元"),
        version=first_entity(entities, "version", "当前"),
    )


def generate_template_candidates(ctx: Context) -> List[str]:
    templates = SKILL_TEMPLATES.get(ctx.skill, [])
    return [fill_template(template, ctx.entities) for template in templates]


def generate_gap_candidates(ctx: Context) -> List[str]:
    tags = ctx.result_tags
    candidates: List[str] = []

    if not tags.get("has_impact", False):
        candidates.extend(
            [
                "这次异常影响了多少用户？",
                "哪些区域或网元受到影响最大？",
            ]
        )

    if not tags.get("has_root_cause", False):
        candidates.extend(
            [
                "这次异常更可能由哪个网元、接口或Peer导致？",
                "同时段是否有相关告警或错误码异常？",
            ]
        )

    if not tags.get("has_evidence", False):
        candidates.extend(
            [
                "是否可以拉取该时段日志进一步确认？",
                "是否需要创建相关信令跟踪？",
            ]
        )

    if not tags.get("has_trend", False):
        candidates.extend(
            [
                "过去一周这个指标的趋势如何？",
                "与昨天或上周同期相比变化如何？",
            ]
        )

    if not tags.get("has_action", False) and ctx.skill == "CN_KnowledgeQA":
        network_element = first_entity(ctx.entities, "network_element", "相关网元")
        candidates.extend(
            [
                f"在华为{network_element}上，相关配置如何查看？",
                "该流程异常时应优先关注哪些KPI和告警？",
            ]
        )

    return candidates


def deduplicate(candidates: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for candidate in candidates:
        key = normalize_for_match(candidate)
        if key and key not in seen:
            seen.add(key)
            result.append(candidate.strip())
    return result


def normalize_for_match(text: str) -> str:
    return "".join(text.lower().split())


def is_same_as_current_question(candidate: str, rewritten_query: str, original_query: str) -> bool:
    normalized_candidate = normalize_for_match(candidate)
    return normalized_candidate in {
        normalize_for_match(rewritten_query),
        normalize_for_match(original_query),
    }


def is_too_generic(candidate: str) -> bool:
    normalized = normalize_for_match(candidate)
    return any(normalize_for_match(word) in normalized for word in GENERIC_FILTER_WORDS)


def is_risky_action(candidate: str, ctx: Context) -> bool:
    if ctx.result_tags.get("has_action", False):
        return False
    return any(action in candidate for action in ACTION_HINTS)


def filter_candidates(candidates: Iterable[str], ctx: Context) -> List[str]:
    result: List[str] = []
    invalid_set = {normalize_for_match(item) for item in ctx.invalid_candidates}
    for candidate in candidates:
        normalized = normalize_for_match(candidate)
        if not normalized:
            continue
        if normalized in invalid_set:
            continue
        if is_same_as_current_question(candidate, ctx.rewritten_query, ctx.original_query):
            continue
        if is_too_generic(candidate):
            continue
        if is_risky_action(candidate, ctx):
            continue
        result.append(candidate)
    return result


def score_candidate(candidate: str, ctx: Context) -> int:
    score = 0

    if candidate in ctx.memory_candidates:
        score += 2

    if ctx.skill == "CN_KnowledgeQA" and any(word in candidate for word in ("失败点", "配置", "命令", "告警", "KPI")):
        score += 3
    if ctx.skill == "CN_CompSpirit" and any(word in candidate for word in ("用户", "信令", "错误码", "跟踪")):
        score += 3
    if ctx.skill == "CN_FaultSpirit" and any(word in candidate for word in ("影响", "网元", "告警", "日志", "跟踪")):
        score += 3
    if ctx.skill == "CN_NetworkMonitoring" and any(word in candidate for word in ("接口", "Peer", "业务成功率", "日志", "告警")):
        score += 3
    if ctx.skill == "CN_NetworkPlanning" and any(word in candidate for word in ("趋势", "区域", "容量", "扩容")):
        score += 3

    entity_hits = 0
    for values in ctx.entities.values():
        for value in values:
            if value and value.lower() in candidate.lower():
                entity_hits += 1
    score += min(entity_hits, 2)

    if any(word in candidate for word in ("影响", "根因", "告警", "趋势", "证据", "日志", "跟踪", "配置")):
        score += 1

    return score


def rough_rank(candidates: Iterable[str], ctx: Context) -> List[str]:
    return sorted(candidates, key=lambda item: score_candidate(item, ctx), reverse=True)


def build_final_candidates(ctx: Context) -> List[str]:
    combined = []
    combined.extend(generate_template_candidates(ctx))
    combined.extend(ctx.memory_candidates[:5])
    combined.extend(generate_gap_candidates(ctx))
    combined = deduplicate(combined)
    combined = filter_candidates(combined, ctx)
    combined = rough_rank(combined, ctx)
    max_candidates = max(3, min(ctx.max_candidates, 10))
    return combined[:max_candidates]


def format_entities(entities: Dict[str, List[str]]) -> str:
    lines = []
    for key, values in entities.items():
        if values:
            lines.append(f"- {key}: {', '.join(values)}")
    return "\n".join(lines) if lines else "- 无"


def build_prompt(ctx: Context, candidates: List[str]) -> str:
    candidate_text = "\n".join(f"{index}. {item}" for index, item in enumerate(candidates, start=1))
    answer_summary = ctx.answer_summary or "无"
    return f"""你是 NOEMate 问题推荐模块。你的任务不是自由生成，而是基于当前上下文，从候选问题中选择并在必要时轻微改写，输出最合适的 3 个下一步问题。

约束：
- 只能输出 3 个问题
- 输出必须是纯问题列表
- 不要解释原因
- 不要重复
- 问题必须贴合当前 Skill 和当前上下文
- 优先选择最自然、最有下一步价值的问题
- 尽量从候选池中选择，不要偏离候选语义太远

当前原始问题：
{ctx.original_query or "无"}

当前改写问题：
{ctx.rewritten_query or "无"}

当前 Skill：
{ctx.skill or "无"}

当前回答摘要：
{answer_summary}

关键实体：
{format_entities(ctx.entities)}

候选问题：
{candidate_text}

请输出：
1. ...
2. ...
3. ...
"""


def load_context(path: Path) -> Context:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return Context.from_dict(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NOEMate 问题推荐候选和 Prompt")
    parser.add_argument("--input", required=True, help="输入 JSON 文件路径")
    parser.add_argument(
        "--mode",
        choices=("candidates", "prompt", "top3"),
        default="candidates",
        help="输出模式：候选列表、Prompt 或兜底 Top3",
    )
    args = parser.parse_args()

    ctx = load_context(Path(args.input))
    candidates = build_final_candidates(ctx)

    if args.mode == "candidates":
        print(json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2))
        return 0

    if args.mode == "prompt":
        print(build_prompt(ctx, candidates))
        return 0

    print(json.dumps({"top3": candidates[:3]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
