#!/usr/bin/env python3
"""
NOEMate question recommendation helper.

Responsibilities:
1. Normalize recommendation context.
2. Build the core generation prompt.
3. Provide a local fallback Top3.
4. Optionally call a configurable OpenAI-compatible model to generate Top3.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from openai import OpenAI
from runtime_config import get_script_config, get_shared_config, load_runtime_config, merge_value


SKILL_PROFILES = {
    "CN_KnowledgeQA": {
        "goal": "从知识解释推进到运维落地。",
        "prefer": [
            "知识点在异常场景中的表现",
            "产品实现、配置查看和定位命令",
            "KPI 或告警映射关系",
            "版本差异和配置示例",
        ],
        "avoid": [
            "与当前知识点无关的泛监控问题",
            "高风险直接执行动作",
        ],
        "fallback": [
            "这个知识点在实际故障场景里最常见的失败点有哪些？",
            "在华为相关网元上，这个功能通常如何查看配置或定位状态？",
            "如果这个流程异常，通常会先看到哪些 KPI 或告警变化？",
        ],
    },
    "CN_CompSpirit": {
        "goal": "从投诉现象推进到影响确认和根因证据。",
        "prefer": [
            "是否扩散到更多用户",
            "更可能发生在哪个网络环节",
            "信令、错误码或日志证据",
            "是否需要继续跟踪",
        ],
        "avoid": [
            "与投诉个体无关的宏观统计问题",
            "容量规划类问题",
        ],
        "fallback": [
            "同时段还有其他用户出现类似问题吗？",
            "这个问题更可能发生在哪个网络环节？",
            "是否有相关信令、错误码或日志可以进一步确认？",
        ],
    },
    "CN_FaultSpirit": {
        "goal": "围绕影响、根因、证据和恢复验证继续深挖。",
        "prefer": [
            "影响范围",
            "根因对象",
            "告警、日志、变更等证据",
            "恢复验证或值守监控",
        ],
        "avoid": [
            "脱离当前故障上下文的泛知识问法",
            "宽泛且不可执行的问题",
        ],
        "fallback": [
            "这次异常影响了多少用户或业务？",
            "哪些区域、网元或接口受影响最明显？",
            "同时段是否有告警、变更或日志异常可以关联？",
        ],
    },
    "CN_NetworkMonitoring": {
        "goal": "从异常发现推进到异常定位、业务影响和趋势对比。",
        "prefer": [
            "异常集中对象",
            "业务影响",
            "同比环比或时序趋势",
            "关联告警和日志",
        ],
        "avoid": [
            "纯知识解释类追问",
            "与当前观察结果无关的泛排障问题",
        ],
        "fallback": [
            "异常主要集中在哪些接口、Peer、区域或网元？",
            "这些异常是否已经影响到业务成功率或用户体验？",
            "和昨天或上周同期相比，这个指标变化趋势如何？",
        ],
    },
    "CN_NetworkPlanning": {
        "goal": "从当前统计推进到趋势、风险和扩容优先级。",
        "prefer": [
            "长周期趋势",
            "区域差异",
            "容量瓶颈和风险",
            "扩容优先级",
        ],
        "avoid": [
            "即时故障处置类问题",
        ],
        "fallback": [
            "过去一个月或一年这个指标的趋势如何？",
            "哪些区域或网元增长最快？",
            "按当前增长速度，哪些对象会最先达到容量瓶颈？",
        ],
    },
}

GENERIC_FILTER_PATTERNS = (
    "还有什么可以看",
    "还有哪些异常",
    "还能看什么",
    "还有什么问题",
    "还可以继续分析什么",
)

GAP_FALLBACKS = {
    "has_impact": [
        "这次异常影响了多少用户或业务？",
        "哪些区域、网元或接口受影响最明显？",
    ],
    "has_root_cause": [
        "更可能是哪个网元、接口、Peer 或网络环节导致了这次异常？",
        "当前有没有更接近根因的对象可以继续下钻？",
    ],
    "has_evidence": [
        "同时段是否有告警、错误码、日志或信令异常？",
        "还有哪些证据可以进一步确认当前判断？",
    ],
    "has_trend": [
        "和昨天或上周同期相比，这个指标变化趋势如何？",
        "过去一周或一个月，这类异常是否持续出现？",
    ],
    "has_action": [
        "下一步更适合先查看哪些配置、KPI、告警或跟踪信息？",
    ],
}

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class Context:
    original_query: str
    rewritten_query: str
    skill: str
    skills: List[str] = field(default_factory=list)
    knowledge_summary: str = ""
    plan_summary: str = ""
    execution_summary: str = ""
    answer_summary: str = ""
    final_answer: str = ""
    memory_questions: List[str] = field(default_factory=list)
    user_features: Dict[str, List[str]] = field(default_factory=dict)
    entities: Dict[str, List[str]] = field(default_factory=dict)
    result_tags: Dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict) -> "Context":
        raw_skill = str(data.get("skill", "")).strip()
        raw_skills = data.get("skills", [])
        skills = normalize_text_list(raw_skills if isinstance(raw_skills, list) else [raw_skills])
        if not skills and raw_skill:
            skills = [raw_skill]

        memory_questions = data.get("memory_questions")
        if memory_questions is None:
            memory_questions = data.get("memory_candidates", [])

        return cls(
            original_query=str(data.get("original_query", "")).strip(),
            rewritten_query=str(data.get("rewritten_query", "")).strip(),
            skill=raw_skill or (skills[0] if skills else ""),
            skills=skills,
            knowledge_summary=str(data.get("knowledge_summary", "")).strip(),
            plan_summary=str(data.get("plan_summary", "")).strip(),
            execution_summary=str(data.get("execution_summary", "")).strip(),
            answer_summary=str(data.get("answer_summary", "")).strip(),
            final_answer=str(data.get("final_answer", "")).strip(),
            memory_questions=normalize_text_list(memory_questions),
            user_features=normalize_entities(data.get("user_features", {})),
            entities=normalize_entities(data.get("entities", {})),
            result_tags={str(k): bool(v) for k, v in data.get("result_tags", {}).items()},
        )


def normalize_text_list(items: Iterable[str]) -> List[str]:
    result: List[str] = []
    for item in items or []:
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
        if items:
            entities[str(key)] = items
    return entities


def normalize_for_match(text: str) -> str:
    lowered = str(text).lower()
    return re.sub(r"[\s\W_]+", "", lowered, flags=re.UNICODE)


def is_near_duplicate(left: str, right: str) -> bool:
    left_norm = normalize_for_match(left)
    right_norm = normalize_for_match(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    ratio = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    return ratio >= 0.82


def is_generic(question: str) -> bool:
    normalized = normalize_for_match(question)
    return any(normalize_for_match(pattern) in normalized for pattern in GENERIC_FILTER_PATTERNS)


def format_mapping(mapping: Dict[str, List[str]]) -> str:
    if not mapping:
        return "- 无"
    lines = []
    for key, values in mapping.items():
        if values:
            lines.append(f"- {key}: {', '.join(values)}")
    return "\n".join(lines) if lines else "- 无"


def format_result_tags(result_tags: Dict[str, bool]) -> str:
    if not result_tags:
        return "- 无"
    return "\n".join(f"- {key}: {value}" for key, value in sorted(result_tags.items()))


def format_memory_questions(memory_questions: List[str]) -> str:
    if not memory_questions:
        return "- 无"
    return "\n".join(f"- {item}" for item in memory_questions[:5])


def resolve_skills(ctx: Context) -> List[str]:
    return ctx.skills or ([ctx.skill] if ctx.skill else [])


def build_skill_preferences(skills: List[str]) -> str:
    if not skills:
        return "- 优先推荐最贴合当前上下文、最能推动下一步任务的问题。"

    lines: List[str] = []
    for skill in skills:
        profile = SKILL_PROFILES.get(skill)
        if not profile:
            continue
        lines.append(f"- {skill}")
        lines.append(f"  - 目标：{profile['goal']}")
        lines.append("  - 优先方向：")
        for item in profile["prefer"]:
            lines.append(f"    - {item}")
        lines.append("  - 避免方向：")
        for item in profile["avoid"]:
            lines.append(f"    - {item}")

    return "\n".join(lines) if lines else "- 优先推荐最贴合当前上下文、最能推动下一步任务的问题。"


@lru_cache(maxsize=None)
def load_prompt_template(template_name: str) -> str:
    template_path = PROMPTS_DIR / template_name
    return template_path.read_text(encoding="utf-8")


def build_prompt_payload(ctx: Context) -> Dict[str, str]:
    skills = resolve_skills(ctx)
    return {
        "original_query": ctx.original_query or "无",
        "rewritten_query": ctx.rewritten_query or "无",
        "skills": ", ".join(skills) if skills else (ctx.skill or "无"),
        "user_features": format_mapping(ctx.user_features),
        "knowledge_summary": ctx.knowledge_summary or "无",
        "plan_summary": ctx.plan_summary or "无",
        "execution_summary": ctx.execution_summary or "无",
        "answer_summary": ctx.answer_summary or "无",
        "final_answer": ctx.final_answer or "无",
        "entities": format_mapping(ctx.entities),
        "result_tags": format_result_tags(ctx.result_tags),
        "skill_preferences": build_skill_preferences(skills),
        "memory_questions": format_memory_questions(ctx.memory_questions),
    }


def build_prompt(ctx: Context) -> str:
    template = load_prompt_template("generation_user_prompt.txt")
    return template.format(**build_prompt_payload(ctx))


def get_generation_system_prompt() -> str:
    return load_prompt_template("generation_system_prompt.txt").strip()


def build_gap_fallbacks(ctx: Context) -> List[str]:
    questions: List[str] = []
    for tag_name, fallback_questions in GAP_FALLBACKS.items():
        if tag_name in ctx.result_tags and not ctx.result_tags[tag_name]:
            questions.extend(fallback_questions)
    return questions


def postprocess_questions(questions: Iterable[str], ctx: Context) -> List[str]:
    result: List[str] = []
    current_questions = [ctx.original_query, ctx.rewritten_query]

    for question in questions:
        text = str(question).strip()
        if not text:
            continue
        if is_generic(text):
            continue
        if any(is_near_duplicate(text, current) for current in current_questions if current):
            continue
        if any(is_near_duplicate(text, existing) for existing in result):
            continue
        result.append(text)
        if len(result) == 3:
            break

    return result


def build_fallback_questions(ctx: Context) -> List[str]:
    skills = resolve_skills(ctx)
    raw_questions: List[str] = []
    raw_questions.extend(ctx.memory_questions[:5])
    raw_questions.extend(build_gap_fallbacks(ctx))
    for skill in skills:
        profile = SKILL_PROFILES.get(skill, {})
        raw_questions.extend(profile.get("fallback", []))
    return postprocess_questions(raw_questions, ctx)


def parse_top3_response(raw_text: str) -> List[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("top3", "questions", "recommendations"):
                value = data.get(key)
                if isinstance(value, list):
                    return normalize_text_list(value)
        if isinstance(data, list):
            return normalize_text_list(data)
    except json.JSONDecodeError:
        pass

    candidates: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^\d+\.\s*", "", stripped)
        stripped = re.sub(r"^[-*]\s*", "", stripped)
        stripped = stripped.strip()
        if stripped:
            candidates.append(stripped)
    return normalize_text_list(candidates)


def create_openai_client(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key_env: str = "OPENAI_API_KEY",
) -> OpenAI:
    resolved_api_key = api_key or os.environ.get(api_key_env)
    if not resolved_api_key:
        raise RuntimeError(f"Missing API key. Set {api_key_env} or pass --api-key.")
    return OpenAI(api_key=resolved_api_key, base_url=base_url)


def request_model_text(
    *,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 600,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key_env: str = "OPENAI_API_KEY",
) -> str:
    client = create_openai_client(api_key=api_key, base_url=base_url, api_key_env=api_key_env)

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if (base_url and "dashscope.aliyuncs.com" in base_url) or model.lower().startswith("qwen3"):
        kwargs["extra_body"] = {"enable_thinking": False}

    try:
        response = client.chat.completions.create(max_completion_tokens=max_tokens, **kwargs)
    except TypeError:
        response = client.chat.completions.create(max_tokens=max_tokens, **kwargs)

    return response.choices[0].message.content or ""


def generate_top3_with_model(
    ctx: Context,
    *,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 600,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key_env: str = "OPENAI_API_KEY",
) -> Dict[str, object]:
    prompt = build_prompt(ctx)
    raw_text = request_model_text(
        model=model,
        messages=[
            {"role": "system", "content": get_generation_system_prompt()},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        base_url=base_url,
        api_key_env=api_key_env,
    )
    parsed = postprocess_questions(parse_top3_response(raw_text), ctx)
    if len(parsed) < 3:
        parsed = postprocess_questions(parsed + build_fallback_questions(ctx), ctx)
    return {
        "model": model,
        "raw_text": raw_text,
        "top3": parsed[:3],
        "prompt": prompt,
    }


def build_final_candidates(
    ctx: Context,
    *,
    use_llm: bool = False,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 600,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key_env: str = "OPENAI_API_KEY",
) -> List[str]:
    if use_llm:
        if not model:
            raise ValueError("model is required when use_llm=True")
        result = generate_top3_with_model(
            ctx,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
            api_key_env=api_key_env,
        )
        return list(result["top3"])
    return build_fallback_questions(ctx)


def load_context(path: Path) -> Context:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return Context.from_dict(data)


def resolve_runtime_options(args: argparse.Namespace) -> Dict[str, object]:
    config = load_runtime_config(args.config)
    shared = get_shared_config(config)
    section = get_script_config(config, "recommend_questions")

    return {
        "model": merge_value(args.model, section.get("model")),
        "api_key": merge_value(args.api_key, section.get("api_key"), shared.get("api_key")),
        "api_key_env": merge_value(
            args.api_key_env,
            section.get("api_key_env"),
            shared.get("api_key_env"),
            default="OPENAI_API_KEY",
        ),
        "base_url": merge_value(args.base_url, section.get("base_url"), shared.get("base_url")),
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
            default=600,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the NOEMate recommendation prompt or Top3.")
    parser.add_argument("--input", required=True, help="Input JSON file path")
    parser.add_argument("--config", help="Runtime JSON config path")
    parser.add_argument(
        "--mode",
        choices=("prompt", "top3", "llm-top3"),
        default="prompt",
        help="prompt = core prompt, top3 = local fallback, llm-top3 = model-generated Top3",
    )
    parser.add_argument("--model", help="Generation model name used in llm-top3 mode")
    parser.add_argument("--api-key", help="API key for the OpenAI-compatible endpoint")
    parser.add_argument("--api-key-env", help="Environment variable name for API key")
    parser.add_argument("--base-url", help="Base URL for an OpenAI-compatible endpoint")
    parser.add_argument("--temperature", type=float, help="Generation temperature")
    parser.add_argument("--max-tokens", type=int, help="Maximum output tokens")
    args = parser.parse_args()

    ctx = load_context(Path(args.input))
    runtime = resolve_runtime_options(args)

    if args.mode == "prompt":
        print(build_prompt(ctx))
        return 0

    if args.mode == "top3":
        print(json.dumps({"top3": build_fallback_questions(ctx)}, ensure_ascii=False, indent=2))
        return 0

    if not runtime["model"]:
        raise SystemExit("--model is required when --mode llm-top3")

    result = generate_top3_with_model(
        ctx,
        model=str(runtime["model"]),
        temperature=float(runtime["temperature"]),
        max_tokens=int(runtime["max_tokens"]),
        api_key=runtime["api_key"],
        base_url=runtime["base_url"],
        api_key_env=str(runtime["api_key_env"]),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
