#!/usr/bin/env python3
"""
根据种子评测集自动扩展 NOEMate 问题推荐评测样本。
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Dict, Iterable, List


PRIMARY_VARIANTS = 8
FUTURE_VARIANTS = 3

ENTITY_POOLS: Dict[str, List[str]] = {
    "feature": ["Attach", "TAU", "VoLTE", "VoNR", "注册流程", "S1连接建立", "Diameter消息处理", "用户面会话建立"],
    "network_element": ["MME", "AMF", "SMF", "PGW", "SGW", "IMS", "SBC", "DRA", "USC", "eNodeB", "gNodeB"],
    "version": ["27.0", "27.1", "28.0", "28.1", "29.0"],
    "kpi": ["attach success rate", "VoNR注册成功率", "呼叫建立成功率", "掉话率", "呼叫建立时长", "S1信令流量", "Diameter消息量", "license利用率", "用户呼增"],
    "alarm": ["错误码异常趋势", "高优先级告警", "链路抖动告警", "接口超时告警", "注册失败异常告警"],
    "region": ["华东区域", "华北区域", "核心城区", "XX区域", "Bangkok区域", "大区A"],
    "peer": ["xxx Peer", "USC/DRA", "SBC-Peer-A", "ENUM-Peer-B", "IMS-Peer-C"],
    "interface": ["S1-MME", "S6a", "Cx", "Rx", "N2", "N11", "Gx"],
    "time_window": ["今天", "过去24小时", "昨天", "过去一周", "过去一个月", "XX Nov 2024 02:00", "XX Nov 2024 12:20-12:35", "重大操作后30分钟"],
    "msisdn": ["66********", "66xxxxxxxxx", "86138******01", "86139******88", "85266******77"],
}

ZH_PREFIXES = ["", "请帮我", "帮我", "麻烦分析一下", "请看一下"]
ZH_SUFFIXES = ["", "，请给出下一步建议。", "，并说明后续应该怎么查。", "，希望继续深挖。"]
EN_PREFIXES = ["", "Please help to ", "Can you help to "]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text_list(values: Iterable[str]) -> List[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def pick(pool_name: str, offset: int) -> str:
    pool = ENTITY_POOLS[pool_name]
    return pool[offset % len(pool)]


def replace_text(text: str, replacements: Dict[str, str]) -> str:
    result = text
    for old, new in replacements.items():
        if old and new and old in result:
            result = result.replace(old, new)
    return result


def update_entities(sample: Dict, variant_index: int) -> Dict[str, List[str]]:
    entities = copy.deepcopy(sample.get("entities", {}))
    updated: Dict[str, List[str]] = {}
    for key, values in entities.items():
        normalized_values = normalize_text_list(values)
        if not normalized_values:
            updated[key] = []
            continue
        if key in ENTITY_POOLS:
            updated[key] = [pick(key, variant_index + idx) for idx, _ in enumerate(normalized_values)]
        else:
            updated[key] = normalized_values
    return updated


def build_replacements(old_entities: Dict[str, List[str]], new_entities: Dict[str, List[str]]) -> Dict[str, str]:
    replacements: Dict[str, str] = {}
    for key, old_values in old_entities.items():
        old_values = normalize_text_list(old_values)
        new_values = normalize_text_list(new_entities.get(key, []))
        for idx, old_value in enumerate(old_values):
            if idx < len(new_values):
                replacements[old_value] = new_values[idx]
    return replacements


def apply_style(text: str, variant_index: int) -> str:
    stripped = text.strip()
    if not stripped:
        return stripped
    if stripped.isascii():
        prefix = EN_PREFIXES[variant_index % len(EN_PREFIXES)]
        if prefix and not stripped.lower().startswith(prefix.lower()):
            return prefix + stripped[0].lower() + stripped[1:]
        return stripped
    prefix = ZH_PREFIXES[variant_index % len(ZH_PREFIXES)]
    suffix = ZH_SUFFIXES[variant_index % len(ZH_SUFFIXES)]
    result = stripped
    if prefix and not result.startswith(prefix):
        result = prefix + result
    if suffix and not result.endswith(suffix):
        result = result + suffix
    return result


def variant_result_tags(sample: Dict, variant_index: int) -> Dict[str, bool]:
    tags = dict(sample.get("result_tags", {}))
    skill = sample.get("skill")
    if skill == "CN_KnowledgeQA":
        tags["has_action"] = variant_index % 4 == 0
        tags["has_evidence"] = variant_index % 5 == 0
    elif skill in {"CN_CompSpirit", "CN_FaultSpirit"}:
        tags["has_impact"] = variant_index % 6 == 0
        tags["has_evidence"] = variant_index % 7 == 0
    elif skill == "CN_NetworkMonitoring":
        tags["has_trend"] = variant_index % 3 == 0
        tags["has_evidence"] = variant_index % 5 == 0
    elif skill == "CN_NetworkPlanning":
        tags["has_action"] = variant_index % 4 == 0
        tags["has_trend"] = True
    return tags


def expand_sample(sample: Dict, variant_count: int) -> List[Dict]:
    expanded: List[Dict] = []
    old_entities = sample.get("entities", {})
    for variant_index in range(variant_count):
        new_sample = copy.deepcopy(sample)
        new_sample["id"] = f"{sample['id']}_v{variant_index + 1:02d}"
        new_sample["seed_id"] = sample["id"]
        new_entities = update_entities(sample, variant_index)
        replacements = build_replacements(old_entities, new_entities)

        new_sample["original_query"] = apply_style(replace_text(sample.get("original_query", ""), replacements), variant_index)
        new_sample["rewritten_query"] = replace_text(sample.get("rewritten_query", ""), replacements)
        new_sample["answer_summary"] = replace_text(sample.get("answer_summary", ""), replacements)
        new_sample["entities"] = new_entities
        new_sample["result_tags"] = variant_result_tags(sample, variant_index)
        new_sample["memory_candidates"] = [replace_text(item, replacements) for item in sample.get("memory_candidates", [])]
        new_sample["gold_questions"] = [replace_text(item, replacements) for item in sample.get("gold_questions", [])]
        new_sample["disallowed_questions"] = [replace_text(item, replacements) for item in sample.get("disallowed_questions", [])]
        expanded.append(new_sample)
    return expanded


def build_dataset(seed_dataset: List[Dict]) -> List[Dict]:
    dataset: List[Dict] = []
    for sample in seed_dataset:
        variant_count = PRIMARY_VARIANTS if sample.get("include_in_primary_score", True) else FUTURE_VARIANTS
        dataset.extend(expand_sample(sample, variant_count))
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="扩展 NOEMate 问题推荐评测集")
    parser.add_argument("--seed", required=True, help="种子评测集路径")
    parser.add_argument("--output", required=True, help="输出评测集路径")
    args = parser.parse_args()

    seed_dataset = read_json(Path(args.seed))
    expanded_dataset = build_dataset(seed_dataset)
    write_json(Path(args.output), expanded_dataset)
    print(
        json.dumps(
            {
                "seed_count": len(seed_dataset),
                "expanded_count": len(expanded_dataset),
                "primary_count": len([item for item in expanded_dataset if item.get("include_in_primary_score", True)]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
