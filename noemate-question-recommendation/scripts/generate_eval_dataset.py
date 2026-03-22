#!/usr/bin/env python3
"""
生成更贴近真实 NOEMate 场景的离线评测集。

约束：
1. 总样本数 200
2. 单意图 150， 多意图 50
3. memory_candidates 固定 Top5
4. 不包含 entities、result_tags、expected_dimensions、disallowed_questions
5. 包含用户特征，且阈值类与地区类特征 key 不固定
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


FEATURE_SETS = [
    {
        "用户职责": ["云核运维工程师"],
        "网络流量异常": ["波动超过10%阈值"],
        "泰国北区": ["清迈府", "清莱府"],
    },
    {
        "用户职责": ["NOC值班工程师"],
        "Attach成功率异常": ["下降超过5%阈值"],
        "华北大区": ["北京", "天津"],
    },
    {
        "用户职责": ["核心网优化工程师"],
        "CPU利用率异常": ["超过75%阈值"],
        "印尼西区": ["雅加达", "万隆"],
    },
    {
        "用户职责": ["投诉分析工程师"],
        "掉话率异常": ["升高超过3%阈值"],
        "华南片区": ["深圳", "广州"],
    },
    {
        "用户职责": ["网络规划工程师"],
        "License利用率预警": ["超过80%阈值"],
        "中东东区": ["迪拜", "阿布扎比"],
    },
]

REWRITE_PATTERNS = [
    "{feature_context}，{base}",
    "在{feature_context}的前提下，{base}",
    "考虑{feature_context}后，{base}",
    "从{feature_context}出发，{base}",
    "结合{feature_context}，{base}",
]


def clone_feature(feature: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(values) for key, values in feature.items()}


def feature_parts(feature: dict[str, list[str]]) -> tuple[str, str, str, str, list[str], str]:
    role = feature["用户职责"][0]
    threshold_key = ""
    threshold_value = ""
    region_key = ""
    region_values: list[str] = []

    for key, values in feature.items():
        if key == "用户职责":
            continue
        if len(values) == 1 and not threshold_key:
            threshold_key = key
            threshold_value = values[0]
        elif len(values) > 1 and not region_key:
            region_key = key
            region_values = list(values)

    if not threshold_key or not region_key:
        raise ValueError(f"Invalid feature shape: {feature}")

    region_text = f"{region_key}{'、'.join(region_values)}"
    return role, threshold_key, threshold_value, region_key, region_values, region_text


def select_user_features(
    scenario_id: str,
    feature: dict[str, list[str]],
    feature_index: int,
) -> dict[str, list[str]]:
    role, threshold_key, threshold_value, region_key, region_values, _ = feature_parts(feature)
    candidates = [
        ("用户职责", [role]),
        (threshold_key, [threshold_value]),
        (region_key, region_values),
    ]

    digest = hashlib.md5(f"{scenario_id}:{feature_index}".encode("utf-8")).digest()
    subset_size = digest[0] % 4

    if subset_size == 0:
        return {}

    if subset_size == 3:
        selected_indexes = [0, 1, 2]
    elif subset_size == 1:
        selected_indexes = [digest[1] % 3]
    else:
        pair_options = ([0, 1], [0, 2], [1, 2])
        selected_indexes = list(pair_options[digest[1] % len(pair_options)])

    return {candidates[index][0]: list(candidates[index][1]) for index in selected_indexes}


def build_feature_context(selected_features: dict[str, list[str]]) -> str:
    if not selected_features:
        return ""

    descriptions: list[str] = []
    for key, values in selected_features.items():
        if key == "用户职责":
            descriptions.append(f"面向{values[0]}")
        elif len(values) > 1:
            descriptions.append(f"结合{key}{'、'.join(values)}场景")
        else:
            descriptions.append(f"参考{key}{values[0]}这类用户特征")

    return "，".join(descriptions)


def build_rewritten_query(base: str, selected_features: dict[str, list[str]], feature_index: int) -> str:
    feature_context = build_feature_context(selected_features)
    if not feature_context:
        return base

    pattern = REWRITE_PATTERNS[(feature_index - 1) % len(REWRITE_PATTERNS)]
    return pattern.format(feature_context=feature_context, base=base)


def make_sample(scenario: dict, feature: dict[str, list[str]], feature_index: int) -> dict:
    skills = list(scenario.get("skills", [scenario["skill"]]))
    selected_features = select_user_features(scenario["id"], feature, feature_index)
    return {
        "id": f"{scenario['id']}_{feature_index}",
        "source_type": scenario["source_type"],
        "intent_mode": "multi" if len(skills) > 1 else "single",
        "skill": scenario["skill"],
        "skills": skills,
        "original_query": scenario["original_query"],
        "rewritten_query": build_rewritten_query(scenario["rewrite_base"], selected_features, feature_index),
        "user_features": clone_feature(selected_features),
        "knowledge_summary": scenario["knowledge_summary"],
        "plan_summary": scenario["plan_summary"],
        "execution_summary": scenario["execution_summary"],
        "answer_summary": scenario["answer_summary"],
        "final_answer": scenario["final_answer"],
        "memory_candidates": list(scenario["memory_candidates"]),
        "gold_questions": list(scenario["gold_questions"]),
        "include_in_primary_score": True,
    }


SINGLE_SCENARIOS = [
    {
        "id": "knowledge_attach_flow",
        "source_type": "单意图-知识问答",
        "skill": "CN_KnowledgeQA",
        "original_query": "3GPP Attach 流程到底怎么走？",
        "rewrite_base": "解释 3GPP Attach 流程，并突出当前运维场景最需要关注的关键步骤",
        "knowledge_summary": "已检索到 Attach 流程时序、关键网元职责和常见失败位置相关知识。",
        "plan_summary": "当前先完成原理解释，下一步更适合从失败点、KPI 和配置入口继续追问。",
        "execution_summary": "回答已经说明 Attach 的主要交互过程，但还没有落到定位与运维动作。",
        "answer_summary": "当前回答偏知识解释，尚未给出失败点、告警KPI和配置查看入口。",
        "final_answer": "用户已经理解流程主线，但如果要继续在现网使用，还需要补充失败点、异常表现和定位入口。",
        "memory_candidates": [
            "Attach 流程里最容易出问题的环节通常是哪些？",
            "Attach 失败时一般会先看到哪些告警或 KPI 变化？",
            "在华为相关网元上，Attach 相关配置通常从哪里查看？",
            "如果要在现网排查 Attach 异常，第一步该关注哪个网元或接口？",
            "不同版本里 Attach 相关配置有没有明显差异？",
        ],
        "gold_questions": [
            "Attach 流程里最容易出问题的环节通常是哪些？",
            "Attach 失败时一般会先看到哪些告警或 KPI 变化？",
            "在华为相关网元上，Attach 相关配置通常从哪里查看？",
            "如果要在现网排查 Attach 异常，第一步该关注哪个网元或接口？",
            "不同版本里 Attach 相关配置有没有明显差异？",
        ],
    },
    {
        "id": "knowledge_tau_flow",
        "source_type": "单意图-知识问答",
        "skill": "CN_KnowledgeQA",
        "original_query": "TAU 流程什么时候触发，核心步骤有哪些？",
        "rewrite_base": "说明 TAU 流程的触发条件和关键步骤，并补充现网排查视角",
        "knowledge_summary": "已检索到 TAU 触发场景、关键信令和常见失败原因相关知识。",
        "plan_summary": "已经完成基础原理说明，接下来更适合落到异常表现和排查入口。",
        "execution_summary": "回答说明了 TAU 的触发逻辑，但还没有给出故障场景中的典型失败点。",
        "answer_summary": "当前回答缺少 TAU 失败表现、错误码、KPI 和配置排查入口。",
        "final_answer": "如果用户接下来要继续追问，最有价值的是围绕 TAU 异常如何表现、如何定位展开。",
        "memory_candidates": [
            "TAU 失败时最常见的原因一般落在哪些环节？",
            "现网里 TAU 异常通常会对应哪些告警、错误码或 KPI 波动？",
            "如果要排查 TAU 异常，优先看哪个网元或接口状态？",
            "TAU 相关配置在华为设备上通常怎么查？",
            "TAU 和附着、寻呼等流程的关系在现网定位里该怎么理解？",
        ],
        "gold_questions": [
            "TAU 失败时最常见的原因一般落在哪些环节？",
            "现网里 TAU 异常通常会对应哪些告警、错误码或 KPI 波动？",
            "如果要排查 TAU 异常，优先看哪个网元或接口状态？",
            "TAU 相关配置在华为设备上通常怎么查？",
            "TAU 和附着、寻呼等流程的关系在现网定位里该怎么理解？",
        ],
    },
    {
        "id": "knowledge_volte_register",
        "source_type": "单意图-知识问答",
        "skill": "CN_KnowledgeQA",
        "original_query": "VoLTE 注册流程涉及哪些关键网元？",
        "rewrite_base": "解释 VoLTE 注册流程和关键网元职责，并突出后续运维排查思路",
        "knowledge_summary": "已检索到 IMS 注册、SIP 流程和核心网网元职责相关知识。",
        "plan_summary": "当前回答偏原理说明，下一步适合补充失败点、告警和网元定位入口。",
        "execution_summary": "回答已经说明主要交互网元，但没有继续解释异常时怎么落到现网。",
        "answer_summary": "当前回答缺少 VoLTE 注册失败点、典型告警和配置查看入口。",
        "final_answer": "若继续追问，更有价值的是把注册流程映射到现网失败场景、KPI 和网元配置上。",
        "memory_candidates": [
            "VoLTE 注册失败最常见是卡在哪个环节？",
            "IMS 或核心网侧通常会先出现哪些告警、错误码或 KPI 变化？",
            "如果用户注册不上，优先应该看哪几个网元的状态？",
            "VoLTE 注册相关配置在华为设备上一般从哪里查看？",
            "不同版本下 VoLTE 注册排查思路会不会有差别？",
        ],
        "gold_questions": [
            "VoLTE 注册失败最常见是卡在哪个环节？",
            "IMS 或核心网侧通常会先出现哪些告警、错误码或 KPI 变化？",
            "如果用户注册不上，优先应该看哪几个网元的状态？",
            "VoLTE 注册相关配置在华为设备上一般从哪里查看？",
            "不同版本下 VoLTE 注册排查思路会不会有差别？",
        ],
    },
    {
        "id": "knowledge_eps_bearer",
        "source_type": "单意图-知识问答",
        "skill": "CN_KnowledgeQA",
        "original_query": "EPS 承载建立流程关键点有哪些？",
        "rewrite_base": "解释 EPS 承载建立流程，并补充承载建立失败时的定位思路",
        "knowledge_summary": "已检索到 EPS 承载建立流程、QCI 和常见失败原因相关知识。",
        "plan_summary": "下一步更适合从失败点、KPI 和接口定位角度继续追问。",
        "execution_summary": "回答目前只覆盖了承载建立主线，没有展开现网异常表现。",
        "answer_summary": "当前回答没有说明承载失败时的关键网元、接口和指标入口。",
        "final_answer": "要让这段知识对运维更有用，后续更应追问失败场景、KPI 映射和配置检查点。",
        "memory_candidates": [
            "EPS 承载建立失败一般容易卡在哪些步骤？",
            "承载建立异常时最先波动的 KPI 或错误码通常是什么？",
            "如果用户上网业务异常，优先看哪个接口或网元最有效？",
            "EPS 承载相关配置或状态在设备上通常怎么查看？",
            "承载建立问题和策略、鉴权、会话建立之间怎么关联排查？",
        ],
        "gold_questions": [
            "EPS 承载建立失败一般容易卡在哪些步骤？",
            "承载建立异常时最先波动的 KPI 或错误码通常是什么？",
            "如果用户上网业务异常，优先看哪个接口或网元最有效？",
            "EPS 承载相关配置或状态在设备上通常怎么查看？",
            "承载建立问题和策略、鉴权、会话建立之间怎么关联排查？",
        ],
    },
    {
        "id": "knowledge_s1ap_paging",
        "source_type": "单意图-知识问答",
        "skill": "CN_KnowledgeQA",
        "original_query": "S1AP 寻呼流程是怎么触发的？",
        "rewrite_base": "说明 S1AP 寻呼流程的触发条件和关键环节，并补充异常时的运维入口",
        "knowledge_summary": "已检索到寻呼触发机制、S1AP 消息和常见异常场景相关知识。",
        "plan_summary": "已完成基础流程说明，下一步适合切到寻呼失败的故障表现和排查入口。",
        "execution_summary": "回答说明了寻呼的消息链路，但还没有给出故障场景中的常见失败原因。",
        "answer_summary": "当前回答缺少寻呼失败时的告警、指标、接口和网元排查信息。",
        "final_answer": "如果用户要继续追问，更像是希望知道寻呼失败时该看什么、怎么查。",
        "memory_candidates": [
            "寻呼失败最常见是卡在哪个网元或环节？",
            "现网里寻呼异常通常会对应哪些 KPI、告警或错误码？",
            "如果用户被叫接不通，优先该查哪个接口或网元状态？",
            "S1AP 寻呼相关的配置和状态一般在哪里查看？",
            "寻呼异常和 TAU、空闲态用户管理之间怎么关联判断？",
        ],
        "gold_questions": [
            "寻呼失败最常见是卡在哪个网元或环节？",
            "现网里寻呼异常通常会对应哪些 KPI、告警或错误码？",
            "如果用户被叫接不通，优先该查哪个接口或网元状态？",
            "S1AP 寻呼相关的配置和状态一般在哪里查看？",
            "寻呼异常和 TAU、空闲态用户管理之间怎么关联判断？",
        ],
    },
    {
        "id": "knowledge_diameter_routing",
        "source_type": "单意图-知识问答",
        "skill": "CN_KnowledgeQA",
        "original_query": "Diameter 路由机制在核心网里是怎么工作的？",
        "rewrite_base": "解释 Diameter 路由机制和相关节点职责，并补充实际定位思路",
        "knowledge_summary": "已检索到 Diameter 路由、Peer 管理和常见转发表异常相关知识。",
        "plan_summary": "当前已完成机制说明，下一步更适合转向 Peer 异常、错误码和配置查看。",
        "execution_summary": "回答解释了路由选择逻辑，但没有继续落到现网路由异常怎么查。",
        "answer_summary": "当前回答缺少 Diameter 路由异常时的关键告警、错误码和检查点。",
        "final_answer": "若继续追问，更高价值的是围绕 Peer 异常、路由配置和错误码定位展开。",
        "memory_candidates": [
            "Diameter 路由异常最常见会体现在哪些 Peer 或错误码上？",
            "如果某个 Peer 出现问题，先看哪些告警或统计最有效？",
            "路由配置和 Peer 状态在设备上通常从哪里检查？",
            "哪些现网现象往往和 Diameter 路由问题直接相关？",
            "当 Diameter 路由异常时，如何快速判断是本端还是对端问题？",
        ],
        "gold_questions": [
            "Diameter 路由异常最常见会体现在哪些 Peer 或错误码上？",
            "如果某个 Peer 出现问题，先看哪些告警或统计最有效？",
            "路由配置和 Peer 状态在设备上通常从哪里检查？",
            "哪些现网现象往往和 Diameter 路由问题直接相关？",
            "当 Diameter 路由异常时，如何快速判断是本端还是对端问题？",
        ],
    },
    {
        "id": "comp_volte_drop",
        "source_type": "单意图-投诉分析",
        "skill": "CN_CompSpirit",
        "original_query": "今天早上有个号码一直投诉 VoLTE 掉话，这种情况先怎么查？",
        "rewrite_base": "分析单用户 VoLTE 掉话投诉，判断是否扩散到同区域用户并定位最可疑网络环节",
        "knowledge_summary": "已检索到 VoLTE 掉话投诉分析、信令流程和常见错误码知识。",
        "plan_summary": "已识别投诉现象，下一步更适合确认群体性影响并补齐证据链。",
        "execution_summary": "回答只说明了可能与注册、切换或无线侧有关，还没有进入用户群和网络环节判断。",
        "answer_summary": "当前回答没有说明是否是群体性问题，也缺少信令、错误码和跟踪建议。",
        "final_answer": "继续追问时，用户更可能想知道还有多少用户受影响、问题落在哪个环节、要不要继续跟踪。",
        "memory_candidates": [
            "同时段还有其他用户出现类似掉话吗？",
            "这类 VoLTE 掉话更像是出在接入、IMS 还是核心网环节？",
            "有没有对应的 SIP、信令错误码或掉话原因值可以确认？",
            "是否需要针对该号码或同小区用户继续拉取跟踪？",
            "这种投诉和当前的告警或 KPI 波动能不能对上？",
        ],
        "gold_questions": [
            "同时段还有其他用户出现类似掉话吗？",
            "这类 VoLTE 掉话更像是出在接入、IMS 还是核心网环节？",
            "有没有对应的 SIP、信令错误码或掉话原因值可以确认？",
            "是否需要针对该号码或同小区用户继续拉取跟踪？",
            "这种投诉和当前的告警或 KPI 波动能不能对上？",
        ],
    },
    {
        "id": "comp_no_data",
        "source_type": "单意图-投诉分析",
        "skill": "CN_CompSpirit",
        "original_query": "用户说 4G 满格但上不了网，这类投诉怎么定位？",
        "rewrite_base": "分析单用户无法上网投诉，判断是否存在同类用户扩散并锁定最可疑网络环节",
        "knowledge_summary": "已检索到上网投诉分析、会话建立失败和承载异常知识。",
        "plan_summary": "下一步更适合确认是否仅个体受影响，以及问题落在哪个会话或承载环节。",
        "execution_summary": "回答目前只给了笼统方向，没有说明更像个体问题还是群体问题。",
        "answer_summary": "当前回答缺少影响范围、会话失败证据和跟踪建议。",
        "final_answer": "用户下一步更可能追问同类用户范围、错误码、信令和应不应该拉取日志。",
        "memory_candidates": [
            "同时段还有其他用户也反馈上不了网吗？",
            "这个问题更像卡在鉴权、承载建立还是路由环节？",
            "有没有对应的错误码、信令或会话失败原因值？",
            "如果继续定位，优先要不要拉取该号码或同区域用户的跟踪？",
            "这类投诉和 PGW、MME 或 PCRF 侧告警是否有关联？",
        ],
        "gold_questions": [
            "同时段还有其他用户也反馈上不了网吗？",
            "这个问题更像卡在鉴权、承载建立还是路由环节？",
            "有没有对应的错误码、信令或会话失败原因值？",
            "如果继续定位，优先要不要拉取该号码或同区域用户的跟踪？",
            "这类投诉和 PGW、MME 或 PCRF 侧告警是否有关联？",
        ],
    },
    {
        "id": "comp_handover_fail",
        "source_type": "单意图-投诉分析",
        "skill": "CN_CompSpirit",
        "original_query": "高铁沿线用户投诉切换失败，这种投诉怎么查更快？",
        "rewrite_base": "分析切换失败投诉，判断是否集中在特定区域并定位最可疑环节",
        "knowledge_summary": "已检索到切换失败投诉分析、切换原因值和邻区相关知识。",
        "plan_summary": "接下来更适合看是否为区域性集中问题，以及是否有切换失败证据。",
        "execution_summary": "回答目前没有说明问题是否集中在固定区域或某条切换链路。",
        "answer_summary": "当前回答缺少区域集中性、错误码和跟踪建议。",
        "final_answer": "继续追问时，用户更需要知道问题是不是集中爆发、切换原因值是什么、是否要继续跟踪。",
        "memory_candidates": [
            "这类切换失败是不是集中在某几个站点或区域？",
            "同时段是否还有更多用户出现类似切换问题？",
            "有没有切换失败原因值、错误码或信令可以直接确认？",
            "问题更像邻区参数、接口异常还是核心网侧过程导致？",
            "这种场景是否需要继续做跟踪或抓包确认？",
        ],
        "gold_questions": [
            "这类切换失败是不是集中在某几个站点或区域？",
            "同时段是否还有更多用户出现类似切换问题？",
            "有没有切换失败原因值、错误码或信令可以直接确认？",
            "问题更像邻区参数、接口异常还是核心网侧过程导致？",
            "这种场景是否需要继续做跟踪或抓包确认？",
        ],
    },
    {
        "id": "comp_roaming_register",
        "source_type": "单意图-投诉分析",
        "skill": "CN_CompSpirit",
        "original_query": "国际漫游用户注册不上，这类投诉一般要怎么查？",
        "rewrite_base": "分析国际漫游注册失败投诉，判断是否为群体性问题并定位最可疑网络环节",
        "knowledge_summary": "已检索到漫游注册投诉分析、S6a/S6d 交互和错误码知识。",
        "plan_summary": "下一步更适合确认是否只影响个体用户，以及问题更像本端还是对端链路。",
        "execution_summary": "回答目前没有说明群体范围，也缺少本端/对端判断证据。",
        "answer_summary": "当前回答缺少同类用户范围、错误码和对端交互证据。",
        "final_answer": "后续更自然的追问会集中在是否群体受影响、S6a/对端链路异常和跟踪建议上。",
        "memory_candidates": [
            "同一国家或同一运营商的漫游用户还有没有类似问题？",
            "这类注册失败更像是本端配置、S6a 交互还是对端问题？",
            "有没有对应的 Diameter 错误码或注册拒绝原因值？",
            "当前要不要进一步看对端链路、Peer 状态或跟踪日志？",
            "这类投诉和当前漫游相关告警有没有直接关联？",
        ],
        "gold_questions": [
            "同一国家或同一运营商的漫游用户还有没有类似问题？",
            "这类注册失败更像是本端配置、S6a 交互还是对端问题？",
            "有没有对应的 Diameter 错误码或注册拒绝原因值？",
            "当前要不要进一步看对端链路、Peer 状态或跟踪日志？",
            "这类投诉和当前漫游相关告警有没有直接关联？",
        ],
    },
    {
        "id": "comp_sms_fail",
        "source_type": "单意图-投诉分析",
        "skill": "CN_CompSpirit",
        "original_query": "用户反馈短信一直发不出去，这种投诉怎么判断原因？",
        "rewrite_base": "分析短信发送失败投诉，判断是否影响更多用户并定位最可疑环节",
        "knowledge_summary": "已检索到短信业务流程、核心网短信路径和失败原因相关知识。",
        "plan_summary": "下一步应先判断是否为群体性问题，再看环节和错误证据。",
        "execution_summary": "回答只给出宽泛方向，没有说明更像个体、区域还是系统性问题。",
        "answer_summary": "当前回答缺少群体范围、错误码和具体环节判断。",
        "final_answer": "更自然的下一问通常是还有多少用户受影响、短信流程卡在哪、是否有错误码证据。",
        "memory_candidates": [
            "同时段还有其他用户也发不出短信吗？",
            "问题更像出在短信中心、接入环节还是核心网流程上？",
            "有没有对应的错误码、日志或信令可以直接确认？",
            "这种投诉和当前告警、接口异常是否有关联？",
            "如果继续定位，是否需要针对用户或区域做跟踪？",
        ],
        "gold_questions": [
            "同时段还有其他用户也发不出短信吗？",
            "问题更像出在短信中心、接入环节还是核心网流程上？",
            "有没有对应的错误码、日志或信令可以直接确认？",
            "这种投诉和当前告警、接口异常是否有关联？",
            "如果继续定位，是否需要针对用户或区域做跟踪？",
        ],
    },
    {
        "id": "comp_slow_call_setup",
        "source_type": "单意图-投诉分析",
        "skill": "CN_CompSpirit",
        "original_query": "最近有用户反映语音接通特别慢，这类投诉要怎么分析？",
        "rewrite_base": "分析语音接通慢投诉，判断是否扩散到更多用户并定位最可疑环节",
        "knowledge_summary": "已检索到呼叫建立流程、SIP/CSFB 相关知识和常见问题点。",
        "plan_summary": "下一步更适合看是否为群体性慢接通，并补足网络环节和错误证据。",
        "execution_summary": "回答目前没有判断慢接通是否集中在特定区域、时间段或业务类型。",
        "answer_summary": "当前回答缺少影响范围、呼叫建立环节判断和日志证据。",
        "final_answer": "对用户来说，更自然的下一问通常是还有多少用户受影响、问题出在呼叫建立哪个环节、要不要继续跟踪。",
        "memory_candidates": [
            "同时段还有多少用户也出现语音接通慢？",
            "这种慢接通更像卡在接入、IMS 还是核心网环节？",
            "有没有对应的呼叫建立时延指标、错误码或信令可以确认？",
            "问题是不是集中在某个区域、网元或特定时段？",
            "要不要继续拉取跟踪看看建立时延具体卡在哪里？",
        ],
        "gold_questions": [
            "同时段还有多少用户也出现语音接通慢？",
            "这种慢接通更像卡在接入、IMS 还是核心网环节？",
            "有没有对应的呼叫建立时延指标、错误码或信令可以确认？",
            "问题是不是集中在某个区域、网元或特定时段？",
            "要不要继续拉取跟踪看看建立时延具体卡在哪里？",
        ],
    },
    {
        "id": "fault_attach_drop",
        "source_type": "单意图-故障诊断",
        "skill": "CN_FaultSpirit",
        "original_query": "凌晨 2 点 Attach 成功率突然掉下来了，下一步先看什么？",
        "rewrite_base": "定位 Attach 成功率下降故障，优先判断影响范围、根因方向和证据",
        "knowledge_summary": "已检索到 Attach 异常、相关告警、失败原因和网元定位知识。",
        "plan_summary": "当前已确认存在异常，下一步适合先看影响范围，再看根因对象和证据。",
        "execution_summary": "回答目前只确认了指标下降，还没给出受影响区域、网元或错误证据。",
        "answer_summary": "当前回答缺少影响范围、根因对象和日志告警证据。",
        "final_answer": "后续最自然的追问会是影响了谁、集中在哪、有没有告警和日志能支撑根因判断。",
        "memory_candidates": [
            "这次 Attach 成功率下降影响了多少用户或业务？",
            "异常主要集中在哪些区域、网元或接口？",
            "同时段有没有相关告警、错误码或变更异常？",
            "更像是哪个网元、接口或 Peer 导致了这次下降？",
            "恢复后还需要重点盯哪些指标确认风险消除？",
        ],
        "gold_questions": [
            "这次 Attach 成功率下降影响了多少用户或业务？",
            "异常主要集中在哪些区域、网元或接口？",
            "同时段有没有相关告警、错误码或变更异常？",
            "更像是哪个网元、接口或 Peer 导致了这次下降？",
            "恢复后还需要重点盯哪些指标确认风险消除？",
        ],
    },
    {
        "id": "fault_s1_alarm_spike",
        "source_type": "单意图-故障诊断",
        "skill": "CN_FaultSpirit",
        "original_query": "S1 接口告警突然暴增，这种情况怎么继续排查？",
        "rewrite_base": "定位 S1 接口告警突增故障，优先判断影响范围、根因方向和证据",
        "knowledge_summary": "已检索到 S1 接口异常、链路告警和性能影响相关知识。",
        "plan_summary": "已经确认告警异常，下一步应补充影响对象、相关业务影响和故障证据。",
        "execution_summary": "回答只停留在现象确认阶段，没有收敛告警集中对象和根因方向。",
        "answer_summary": "当前回答缺少受影响区域、业务影响和日志或变更证据。",
        "final_answer": "更自然的下一步问题会围绕影响了哪些网元和业务、告警是否与变更相关、要不要拉日志。",
        "memory_candidates": [
            "告警暴增主要集中在哪些区域、站点或网元？",
            "这批 S1 告警有没有已经影响到附着、寻呼或业务成功率？",
            "同时段有没有链路变更、配置调整或设备重启记录？",
            "更可能是传输、接口板卡还是对端问题导致的？",
            "当前要不要继续看接口日志或链路跟踪确认？",
        ],
        "gold_questions": [
            "告警暴增主要集中在哪些区域、站点或网元？",
            "这批 S1 告警有没有已经影响到附着、寻呼或业务成功率？",
            "同时段有没有链路变更、配置调整或设备重启记录？",
            "更可能是传输、接口板卡还是对端问题导致的？",
            "当前要不要继续看接口日志或链路跟踪确认？",
        ],
    },
    {
        "id": "fault_paging_drop",
        "source_type": "单意图-故障诊断",
        "skill": "CN_FaultSpirit",
        "original_query": "分页成功率这几个小时一直在掉，下一步该怎么追？",
        "rewrite_base": "定位分页成功率下降故障，优先分析影响范围、根因方向和证据",
        "knowledge_summary": "已检索到分页成功率异常、寻呼流程和相关网元定位知识。",
        "plan_summary": "下一步应先看异常集中对象，再判断是否与告警、负荷或配置有关。",
        "execution_summary": "回答只确认了指标下降，没有判断受影响用户和寻呼链路对象。",
        "answer_summary": "当前回答缺少影响对象、趋势对比和证据链。",
        "final_answer": "用户后续更自然的追问通常是影响了哪些用户和区域、哪个环节异常、有没有告警或日志支撑。",
        "memory_candidates": [
            "分页成功率下降主要集中在哪些区域、网元或接口？",
            "这次异常已经影响到用户被叫接通或呼叫建立了吗？",
            "和昨天或上周同期相比，这次下降幅度是不是更明显？",
            "同时段有没有相关告警、错误码或负荷异常？",
            "更像是寻呼链路、接口异常还是配置问题导致的？",
        ],
        "gold_questions": [
            "分页成功率下降主要集中在哪些区域、网元或接口？",
            "这次异常已经影响到用户被叫接通或呼叫建立了吗？",
            "和昨天或上周同期相比，这次下降幅度是不是更明显？",
            "同时段有没有相关告警、错误码或负荷异常？",
            "更像是寻呼链路、接口异常还是配置问题导致的？",
        ],
    },
    {
        "id": "fault_diameter_auth_fail",
        "source_type": "单意图-故障诊断",
        "skill": "CN_FaultSpirit",
        "original_query": "Diameter 鉴权失败一下子涨上来了，应该先从哪边看？",
        "rewrite_base": "定位 Diameter 鉴权失败增加故障，优先判断影响范围、根因方向和证据",
        "knowledge_summary": "已检索到 Diameter 鉴权失败、Peer 状态和错误码分析知识。",
        "plan_summary": "已确认鉴权失败上升，下一步适合判断影响范围和本端/对端方向。",
        "execution_summary": "回答没有说明问题集中在哪些 Peer、区域或业务场景。",
        "answer_summary": "当前回答缺少影响范围、Peer 维度和错误码证据。",
        "final_answer": "继续追问时，更可能聚焦在影响对象、异常 Peer、错误码和本端/对端定位上。",
        "memory_candidates": [
            "这波鉴权失败主要集中在哪些 Peer、区域或业务？",
            "它已经影响到哪些用户或核心业务流程？",
            "同时段有没有明显的 Diameter 错误码或对端异常？",
            "更像是本端配置、Peer 状态还是对端返回异常导致的？",
            "当前要不要继续看 Diameter 日志或链路跟踪确认？",
        ],
        "gold_questions": [
            "这波鉴权失败主要集中在哪些 Peer、区域或业务？",
            "它已经影响到哪些用户或核心业务流程？",
            "同时段有没有明显的 Diameter 错误码或对端异常？",
            "更像是本端配置、Peer 状态还是对端返回异常导致的？",
            "当前要不要继续看 Diameter 日志或链路跟踪确认？",
        ],
    },
    {
        "id": "fault_amf_cpu_spike",
        "source_type": "单意图-故障诊断",
        "skill": "CN_FaultSpirit",
        "original_query": "AMF CPU 突然打满了，这种情况怎么继续收敛原因？",
        "rewrite_base": "定位 AMF CPU 突增故障，优先判断影响范围、根因方向和证据",
        "knowledge_summary": "已检索到 AMF 负荷异常、性能指标和关联告警分析知识。",
        "plan_summary": "接下来应判断是否已影响业务，并识别最异常的实例和时间点。",
        "execution_summary": "回答只说明 CPU 异常存在，没有继续判断最可疑的触发对象。",
        "answer_summary": "当前回答缺少业务影响、实例分布、告警和日志证据。",
        "final_answer": "用户下一步更关心的是哪些实例最异常、业务有没有受影响、根因像不像流量、告警或任务触发。",
        "memory_candidates": [
            "CPU 异常主要集中在哪些 AMF 实例或时间段？",
            "这次 CPU 突增有没有已经影响到注册、寻呼或切换业务？",
            "同时段有没有告警、批量任务或流量突增与它相关？",
            "更像是流量冲击、异常消息还是内部进程问题导致的？",
            "当前要不要继续看 AMF 日志或热点接口统计？",
        ],
        "gold_questions": [
            "CPU 异常主要集中在哪些 AMF 实例或时间段？",
            "这次 CPU 突增有没有已经影响到注册、寻呼或切换业务？",
            "同时段有没有告警、批量任务或流量突增与它相关？",
            "更像是流量冲击、异常消息还是内部进程问题导致的？",
            "当前要不要继续看 AMF 日志或热点接口统计？",
        ],
    },
    {
        "id": "fault_pgw_session_fail",
        "source_type": "单意图-故障诊断",
        "skill": "CN_FaultSpirit",
        "original_query": "PGW 会话建立失败率上来了，接下来怎么追最有效？",
        "rewrite_base": "定位 PGW 会话建立失败故障，优先分析影响范围、根因方向和证据",
        "knowledge_summary": "已检索到 PGW 会话建立失败、承载问题和策略路由相关知识。",
        "plan_summary": "已确认会话异常，下一步应看影响用户、区域、错误码和告警。",
        "execution_summary": "回答只确认了故障现象，还没有判断与策略、路由还是资源相关。",
        "answer_summary": "当前回答缺少影响范围、错误码、日志和根因对象。",
        "final_answer": "更自然的后续问题会集中在影响了哪些用户、失败主要落在哪类错误、和哪类告警或配置有关。",
        "memory_candidates": [
            "PGW 会话建立失败主要影响了哪些用户、区域或 APN？",
            "失败最集中的是哪些错误码、接口或会话场景？",
            "同时段有没有策略、路由或资源侧告警异常？",
            "更像是配置问题、资源瓶颈还是对端交互异常导致的？",
            "当前要不要继续看 PGW 日志或会话建立跟踪？",
        ],
        "gold_questions": [
            "PGW 会话建立失败主要影响了哪些用户、区域或 APN？",
            "失败最集中的是哪些错误码、接口或会话场景？",
            "同时段有没有策略、路由或资源侧告警异常？",
            "更像是配置问题、资源瓶颈还是对端交互异常导致的？",
            "当前要不要继续看 PGW 日志或会话建立跟踪？",
        ],
    },
    {
        "id": "monitor_signal_spike",
        "source_type": "单意图-网络监控",
        "skill": "CN_NetworkMonitoring",
        "original_query": "过去 24 小时接口信令流量突然涨了一截，怎么继续下钻？",
        "rewrite_base": "下钻分析接口信令流量突增，定位异常对象、业务影响和趋势变化",
        "knowledge_summary": "已检索到接口信令流量监控、Peer 统计和告警关联知识。",
        "plan_summary": "接下来应先看异常集中对象，再看业务影响和与历史趋势差异。",
        "execution_summary": "回答只确认了流量增幅，没有指出集中在哪些接口、Peer 或区域。",
        "answer_summary": "当前回答缺少异常对象、业务影响和趋势对比。",
        "final_answer": "用户后续更自然的追问通常是涨幅集中在哪、有没有影响业务、和历史相比是否异常。",
        "memory_candidates": [
            "流量突增主要集中在哪些接口、Peer 或区域？",
            "这波流量变化有没有已经影响到业务成功率或用户体验？",
            "和昨天或上周同期相比，这次增幅是不是异常？",
            "同时段有没有相关告警、日志或链路异常一起出现？",
            "当前最值得继续下钻的对象是哪个接口或 Peer？",
        ],
        "gold_questions": [
            "流量突增主要集中在哪些接口、Peer 或区域？",
            "这波流量变化有没有已经影响到业务成功率或用户体验？",
            "和昨天或上周同期相比，这次增幅是不是异常？",
            "同时段有没有相关告警、日志或链路异常一起出现？",
            "当前最值得继续下钻的对象是哪个接口或 Peer？",
        ],
    },
    {
        "id": "monitor_service_success_fluctuation",
        "source_type": "单意图-网络监控",
        "skill": "CN_NetworkMonitoring",
        "original_query": "某省 VoLTE 业务成功率一会高一会低，这种波动怎么继续看？",
        "rewrite_base": "下钻分析业务成功率波动，定位异常对象、业务影响和趋势变化",
        "knowledge_summary": "已检索到 VoLTE 成功率监控、分区域统计和告警关联知识。",
        "plan_summary": "下一步应先定位波动集中对象，再看趋势和告警关联。",
        "execution_summary": "回答只指出成功率在波动，没有说明集中区域、时段和对象。",
        "answer_summary": "当前回答缺少波动对象、业务影响和关联异常。",
        "final_answer": "更自然的下一问通常是波动集中在哪里、是否影响用户、与历史趋势和告警是否能对应上。",
        "memory_candidates": [
            "这次成功率波动主要集中在哪些区域、网元或时段？",
            "它是不是已经明显影响到用户体验或通话建立成功率？",
            "和昨天或上周同期相比，这种波动是不是更异常？",
            "同时段有没有相关告警、日志或错误码同步出现？",
            "当前最值得继续下钻的对象是哪个区域或网元？",
        ],
        "gold_questions": [
            "这次成功率波动主要集中在哪些区域、网元或时段？",
            "它是不是已经明显影响到用户体验或通话建立成功率？",
            "和昨天或上周同期相比，这种波动是不是更异常？",
            "同时段有没有相关告警、日志或错误码同步出现？",
            "当前最值得继续下钻的对象是哪个区域或网元？",
        ],
    },
    {
        "id": "monitor_error_code_trend",
        "source_type": "单意图-网络监控",
        "skill": "CN_NetworkMonitoring",
        "original_query": "最近某个错误码趋势不太对，下一步应该怎么下钻？",
        "rewrite_base": "下钻分析错误码异常趋势，定位异常对象、业务影响和趋势变化",
        "knowledge_summary": "已检索到错误码趋势监控、协议流程映射和告警关联知识。",
        "plan_summary": "下一步适合看错误码集中对象、影响范围和与历史趋势差异。",
        "execution_summary": "回答只确认错误码趋势有异常，还没有说明集中对象和可能影响。",
        "answer_summary": "当前回答缺少错误码对象分布、业务影响和关联告警。",
        "final_answer": "用户更自然的下一问会是异常集中在哪些对象、影响了什么业务、和历史相比是不是突发。",
        "memory_candidates": [
            "这个错误码异常主要集中在哪些区域、接口或网元？",
            "它已经影响到哪些业务流程或用户体验？",
            "和昨天或上周同期相比，这个错误码增幅有多大？",
            "同时段有没有相关告警、日志或对端异常一起出现？",
            "当前最值得继续下钻的是哪个对象或时段？",
        ],
        "gold_questions": [
            "这个错误码异常主要集中在哪些区域、接口或网元？",
            "它已经影响到哪些业务流程或用户体验？",
            "和昨天或上周同期相比，这个错误码增幅有多大？",
            "同时段有没有相关告警、日志或对端异常一起出现？",
            "当前最值得继续下钻的是哪个对象或时段？",
        ],
    },
    {
        "id": "monitor_peer_latency",
        "source_type": "单意图-网络监控",
        "skill": "CN_NetworkMonitoring",
        "original_query": "某个 Peer 的时延这几个小时都偏高，这种监控异常怎么看？",
        "rewrite_base": "下钻分析 Peer 时延升高异常，定位异常对象、业务影响和趋势变化",
        "knowledge_summary": "已检索到 Peer 监控、时延统计和对端链路告警相关知识。",
        "plan_summary": "已确认时延升高，下一步适合看异常时段、影响业务和关联对象。",
        "execution_summary": "回答只说明了 Peer 时延偏高，还没有指出对业务的真实影响。",
        "answer_summary": "当前回答缺少影响范围、历史趋势和关联异常对象。",
        "final_answer": "对用户来说，更自然的下一问会是哪些 Peer 最异常、影响了哪些业务、有没有对端或链路告警支撑。",
        "memory_candidates": [
            "时延升高主要集中在哪些 Peer、链路或区域？",
            "这波时延异常有没有已经影响到业务成功率或用户体验？",
            "和历史同期相比，这次时延抬升是不是更明显？",
            "同时段有没有相关链路、接口或对端告警异常？",
            "当前最值得继续下钻的 Peer 或时间点是哪一个？",
        ],
        "gold_questions": [
            "时延升高主要集中在哪些 Peer、链路或区域？",
            "这波时延异常有没有已经影响到业务成功率或用户体验？",
            "和历史同期相比，这次时延抬升是不是更明显？",
            "同时段有没有相关链路、接口或对端告警异常？",
            "当前最值得继续下钻的 Peer 或时间点是哪一个？",
        ],
    },
    {
        "id": "monitor_alarm_surge",
        "source_type": "单意图-网络监控",
        "skill": "CN_NetworkMonitoring",
        "original_query": "告警数量这两小时突然飙起来了，后面怎么继续看？",
        "rewrite_base": "下钻分析告警数量激增异常，定位异常对象、业务影响和趋势变化",
        "knowledge_summary": "已检索到告警监控、告警分布和性能告警关联知识。",
        "plan_summary": "当前应优先看告警集中对象、是否伴随业务影响以及与历史趋势差异。",
        "execution_summary": "回答只确认告警激增，没有判断集中区域、网元或告警类型。",
        "answer_summary": "当前回答缺少告警分布、业务影响和与其他异常的关联。",
        "final_answer": "用户后续更自然的追问通常是告警集中在哪些对象、有没有影响业务、是不是与其他指标异常一起出现。",
        "memory_candidates": [
            "告警激增主要集中在哪些区域、网元或告警类型？",
            "这些告警是否已经对应到业务成功率或用户体验异常？",
            "和昨天或上周同期相比，当前告警量是否明显异常？",
            "同时段有没有性能、日志或接口异常一起出现？",
            "当前最值得继续下钻的是哪类告警或哪个网元？",
        ],
        "gold_questions": [
            "告警激增主要集中在哪些区域、网元或告警类型？",
            "这些告警是否已经对应到业务成功率或用户体验异常？",
            "和昨天或上周同期相比，当前告警量是否明显异常？",
            "同时段有没有性能、日志或接口异常一起出现？",
            "当前最值得继续下钻的是哪类告警或哪个网元？",
        ],
    },
    {
        "id": "monitor_register_fail_fluctuation",
        "source_type": "单意图-网络监控",
        "skill": "CN_NetworkMonitoring",
        "original_query": "注册失败率最近老是波动，这种监控结果下一步怎么分析？",
        "rewrite_base": "下钻分析注册失败率波动异常，定位异常对象、业务影响和趋势变化",
        "knowledge_summary": "已检索到注册失败率监控、区域分布和错误码分析相关知识。",
        "plan_summary": "下一步更适合先判断波动集中对象，再看业务影响和历史对比。",
        "execution_summary": "回答确认了失败率波动，但没有继续判断集中区域和具体对象。",
        "answer_summary": "当前回答缺少影响对象、趋势对比和关联证据。",
        "final_answer": "更自然的后续问题会落在异常集中区域、是否影响注册业务、有没有错误码和告警支撑上。",
        "memory_candidates": [
            "注册失败率波动主要集中在哪些区域、网元或时段？",
            "这次波动有没有已经影响到用户注册成功率或投诉量？",
            "和昨天或上周同期相比，这个波动是不是异常放大？",
            "同时段有没有相关错误码、日志或告警异常？",
            "当前最值得继续下钻的对象是哪个区域、网元或错误码？",
        ],
        "gold_questions": [
            "注册失败率波动主要集中在哪些区域、网元或时段？",
            "这次波动有没有已经影响到用户注册成功率或投诉量？",
            "和昨天或上周同期相比，这个波动是不是异常放大？",
            "同时段有没有相关错误码、日志或告警异常？",
            "当前最值得继续下钻的对象是哪个区域、网元或错误码？",
        ],
    },
    {
        "id": "planning_vonr_trend",
        "source_type": "单意图-网络规划",
        "skill": "CN_NetworkPlanning",
        "original_query": "过去一年 VoNR 相关 KPI 的趋势怎么看比较有价值？",
        "rewrite_base": "分析 VoNR KPI 长周期趋势，并给出容量风险和规划方向",
        "knowledge_summary": "已检索到 VoNR KPI、区域分布和长周期趋势分析相关知识。",
        "plan_summary": "已经有现状视图，下一步应展开趋势、区域差异和容量风险判断。",
        "execution_summary": "回答目前只给出了当前值，没有展开一年趋势和区域差异。",
        "answer_summary": "当前回答缺少长期趋势、增长最快对象和瓶颈判断。",
        "final_answer": "用户后续更自然的追问会是趋势怎么变、哪些区域涨得最快、哪里会先出现瓶颈。",
        "memory_candidates": [
            "过去一个月或一年这个 KPI 的变化趋势到底怎样？",
            "哪些区域或网元增长最快，值得优先关注？",
            "按当前增长速度，哪些对象会先触到容量瓶颈？",
            "不同区域之间的容量利用率差异有多大？",
            "如果要提前做规划，哪些资源最适合优先扩容？",
        ],
        "gold_questions": [
            "过去一个月或一年这个 KPI 的变化趋势到底怎样？",
            "哪些区域或网元增长最快，值得优先关注？",
            "按当前增长速度，哪些对象会先触到容量瓶颈？",
            "不同区域之间的容量利用率差异有多大？",
            "如果要提前做规划，哪些资源最适合优先扩容？",
        ],
    },
    {
        "id": "planning_license_forecast",
        "source_type": "单意图-网络规划",
        "skill": "CN_NetworkPlanning",
        "original_query": "License 利用率如果继续涨，后面什么时候会出风险？",
        "rewrite_base": "分析 License 利用率变化趋势，判断容量风险和扩容优先级",
        "knowledge_summary": "已检索到 License 利用率、预测方法和容量告警相关知识。",
        "plan_summary": "下一步应从趋势、区域差异和瓶颈时间点继续分析。",
        "execution_summary": "回答现在只说明了利用率现状，还没有给出趋势和风险窗口。",
        "answer_summary": "当前回答缺少长期趋势、瓶颈时点和优先扩容对象。",
        "final_answer": "更自然的后续问题会集中在增长趋势、最先达阈值的对象和扩容优先顺序上。",
        "memory_candidates": [
            "过去一段时间 License 利用率是持续上升还是阶段性波动？",
            "哪些区域或网元的 License 增长最快？",
            "按当前速度，哪些对象最先会碰到容量红线？",
            "不同区域之间的 License 利用率差异有多大？",
            "如果现在就做规划，哪些资源最值得优先扩容？",
        ],
        "gold_questions": [
            "过去一段时间 License 利用率是持续上升还是阶段性波动？",
            "哪些区域或网元的 License 增长最快？",
            "按当前速度，哪些对象最先会碰到容量红线？",
            "不同区域之间的 License 利用率差异有多大？",
            "如果现在就做规划，哪些资源最值得优先扩容？",
        ],
    },
    {
        "id": "planning_user_growth_curve",
        "source_type": "单意图-网络规划",
        "skill": "CN_NetworkPlanning",
        "original_query": "区域用户增长曲线出来了，后面怎么把它用到规划里？",
        "rewrite_base": "分析区域用户增长曲线，判断容量风险和规划优先级",
        "knowledge_summary": "已检索到区域用户增长、容量模型和扩容规划相关知识。",
        "plan_summary": "接下来更适合从增长趋势、区域差异和瓶颈对象三个方向推进。",
        "execution_summary": "回答只说明了用户量变化，没有转成容量风险或扩容优先级判断。",
        "answer_summary": "当前回答缺少增长最快区域、瓶颈对象和扩容优先级。",
        "final_answer": "用户后续更自然的追问一般会落在增长最快对象、风险时间点和扩容优先区域上。",
        "memory_candidates": [
            "哪些区域或网元的用户增长最快？",
            "过去一段时间用户增长趋势有没有明显拐点？",
            "按当前增长速度，哪些对象会最先达到容量瓶颈？",
            "不同区域之间的容量利用率差异有多大？",
            "如果要排扩容优先级，先盯哪几个区域最合适？",
        ],
        "gold_questions": [
            "哪些区域或网元的用户增长最快？",
            "过去一段时间用户增长趋势有没有明显拐点？",
            "按当前增长速度，哪些对象会最先达到容量瓶颈？",
            "不同区域之间的容量利用率差异有多大？",
            "如果要排扩容优先级，先盯哪几个区域最合适？",
        ],
    },
    {
        "id": "planning_cloud_core_capacity",
        "source_type": "单意图-网络规划",
        "skill": "CN_NetworkPlanning",
        "original_query": "云核资源这块后续扩容怎么做会更稳？",
        "rewrite_base": "分析云核资源容量规划，判断趋势、风险和扩容优先级",
        "knowledge_summary": "已检索到云核 CPU、内存、存储和业务增长关系相关知识。",
        "plan_summary": "当前先做现状评估，下一步更适合看趋势、热点区域和扩容顺序。",
        "execution_summary": "回答只概述了资源现状，没有形成趋势和优先级建议。",
        "answer_summary": "当前回答缺少长周期趋势、热点对象和扩容优先级。",
        "final_answer": "更自然的下一问会围绕哪些资源涨得最快、哪个区域风险最高、应该先扩什么。",
        "memory_candidates": [
            "过去一段时间哪些资源项增长最快？",
            "哪些区域、集群或网元的容量压力最突出？",
            "按当前趋势，哪些对象会最先到达瓶颈？",
            "不同区域之间的资源利用率差异有多大？",
            "如果先做一轮扩容，最应该优先处理哪些资源？",
        ],
        "gold_questions": [
            "过去一段时间哪些资源项增长最快？",
            "哪些区域、集群或网元的容量压力最突出？",
            "按当前趋势，哪些对象会最先到达瓶颈？",
            "不同区域之间的资源利用率差异有多大？",
            "如果先做一轮扩容，最应该优先处理哪些资源？",
        ],
    },
    {
        "id": "planning_core_cpu_risk",
        "source_type": "单意图-网络规划",
        "skill": "CN_NetworkPlanning",
        "original_query": "核心网 CPU 利用率有点高，后面容量风险怎么判断？",
        "rewrite_base": "分析核心网 CPU 容量风险，判断趋势、风险对象和扩容优先级",
        "knowledge_summary": "已检索到核心网 CPU 趋势、热点实例和容量预测相关知识。",
        "plan_summary": "下一步适合把当前现状转成趋势、风险和优先级判断。",
        "execution_summary": "回答只提到 CPU 偏高，还没有判断热点实例和未来风险窗口。",
        "answer_summary": "当前回答缺少趋势、热点对象和容量瓶颈时间点。",
        "final_answer": "用户更自然的追问会围绕哪些实例最热、什么时候会打满、先扩哪一批展开。",
        "memory_candidates": [
            "过去一段时间 CPU 利用率是持续抬升还是波动上升？",
            "哪些实例、网元或区域的 CPU 压力最大？",
            "按当前增长速度，哪些对象会最先打到瓶颈？",
            "不同区域之间的 CPU 利用率差异有多大？",
            "如果要做扩容或迁移，先处理哪些对象最合适？",
        ],
        "gold_questions": [
            "过去一段时间 CPU 利用率是持续抬升还是波动上升？",
            "哪些实例、网元或区域的 CPU 压力最大？",
            "按当前增长速度，哪些对象会最先打到瓶颈？",
            "不同区域之间的 CPU 利用率差异有多大？",
            "如果要做扩容或迁移，先处理哪些对象最合适？",
        ],
    },
    {
        "id": "planning_storage_expand_priority",
        "source_type": "单意图-网络规划",
        "skill": "CN_NetworkPlanning",
        "original_query": "存储利用率已经不低了，扩容优先级应该怎么排？",
        "rewrite_base": "分析存储利用率与扩容优先级，判断趋势、风险和规划方向",
        "knowledge_summary": "已检索到存储利用率、趋势分析和扩容策略相关知识。",
        "plan_summary": "下一步更适合看增长趋势、风险对象和区域差异。",
        "execution_summary": "回答只确认了当前利用率偏高，没有给出后续风险和排序建议。",
        "answer_summary": "当前回答缺少长期趋势、瓶颈对象和扩容优先级。",
        "final_answer": "更自然的后续问题通常是哪里涨最快、哪批对象最先到瓶颈、先扩哪几个最划算。",
        "memory_candidates": [
            "过去一个月或一年的存储利用率趋势是怎样的？",
            "哪些区域、集群或网元的存储增长最快？",
            "按当前速度，哪些对象最先会碰到容量瓶颈？",
            "不同区域之间的存储利用率差异有多大？",
            "如果只能先扩一批，哪些对象最值得优先处理？",
        ],
        "gold_questions": [
            "过去一个月或一年的存储利用率趋势是怎样的？",
            "哪些区域、集群或网元的存储增长最快？",
            "按当前速度，哪些对象最先会碰到容量瓶颈？",
            "不同区域之间的存储利用率差异有多大？",
            "如果只能先扩一批，哪些对象最值得优先处理？",
        ],
    },
]


MULTI_SCENARIOS = [
    {
        "id": "multi_attach_drop_monitor",
        "source_type": "多意图-故障+监控",
        "skill": "CN_FaultSpirit",
        "skills": ["CN_FaultSpirit", "CN_NetworkMonitoring"],
        "original_query": "Attach 成功率掉了，同时监控上接口流量和告警也不正常，这种情况下一步该问什么？",
        "rewrite_base": "结合 Attach 成功率下降和监控异常，生成最有价值的下一步排查问题",
        "knowledge_summary": "已检索到 Attach 异常、接口流量监控和相关告警知识。",
        "plan_summary": "当前需要同时判断业务影响、异常对象和根因证据。",
        "execution_summary": "回答已经确认故障现象和监控异常共现，但没有收敛到具体对象。",
        "answer_summary": "当前缺少影响范围、异常集中对象、趋势对比和证据链。",
        "final_answer": "用户更可能继续追问影响了谁、集中在哪些对象、与哪些告警或变更相关。",
        "memory_candidates": [
            "异常主要集中在哪些区域、网元或接口？",
            "这次异常有没有已经影响到业务成功率或用户体验？",
            "和昨天或上周同期相比，这类异常趋势是不是更明显？",
            "同时段是否有相关告警、日志或变更异常？",
            "当前最可疑的根因对象是什么？",
        ],
        "gold_questions": [
            "异常主要集中在哪些区域、网元或接口？",
            "这次异常有没有已经影响到业务成功率或用户体验？",
            "和昨天或上周同期相比，这类异常趋势是不是更明显？",
            "同时段是否有相关告警、日志或变更异常？",
            "当前最可疑的根因对象是什么？",
        ],
    },
    {
        "id": "multi_peer_latency_fault",
        "source_type": "多意图-监控+故障",
        "skill": "CN_NetworkMonitoring",
        "skills": ["CN_NetworkMonitoring", "CN_FaultSpirit"],
        "original_query": "某个 Peer 时延在升，业务失败率也在涨，这种监控和故障混在一起时下一步怎么问？",
        "rewrite_base": "结合 Peer 时延升高和业务失败率上升，生成最有价值的下一步问题",
        "knowledge_summary": "已检索到 Peer 监控、时延异常和核心网故障定位知识。",
        "plan_summary": "需要同时判断异常对象、业务影响、趋势和根因方向。",
        "execution_summary": "回答已经确认时延异常与业务波动同步出现，但没有说明集中对象。",
        "answer_summary": "当前缺少异常对象、影响范围和告警日志证据。",
        "final_answer": "用户接下来更像是想继续问异常集中在哪、影响到哪些业务、根因更像哪一类对象。",
        "memory_candidates": [
            "异常主要集中在哪些 Peer、接口、区域或网元？",
            "这些异常已经影响到哪些业务成功率或用户体验？",
            "和历史同期相比，这次时延抬升是不是更异常？",
            "同时段有没有相关链路、日志或对端告警？",
            "更可能是哪个网元、接口或对端对象导致了这次问题？",
        ],
        "gold_questions": [
            "异常主要集中在哪些 Peer、接口、区域或网元？",
            "这些异常已经影响到哪些业务成功率或用户体验？",
            "和历史同期相比，这次时延抬升是不是更异常？",
            "同时段有没有相关链路、日志或对端告警？",
            "更可能是哪个网元、接口或对端对象导致了这次问题？",
        ],
    },
    {
        "id": "multi_volte_complaint_fault",
        "source_type": "多意图-投诉+故障",
        "skill": "CN_CompSpirit",
        "skills": ["CN_CompSpirit", "CN_FaultSpirit"],
        "original_query": "用户投诉 VoLTE 掉话，同时同片区也有异常告警，这种场景下一步适合追问什么？",
        "rewrite_base": "结合 VoLTE 掉话投诉和同片区故障异常，生成更自然的下一步问题",
        "knowledge_summary": "已检索到 VoLTE 掉话投诉分析、告警关联和故障定位知识。",
        "plan_summary": "当前需要同时判断群体性影响、网络环节和故障证据。",
        "execution_summary": "回答确认了投诉现象与片区异常共现，但还没有判断是否扩散。",
        "answer_summary": "当前缺少影响范围、最可疑环节和信令告警证据。",
        "final_answer": "用户继续追问时，更像是想确认还有多少用户受影响、问题落在哪个环节、要不要继续跟踪。",
        "memory_candidates": [
            "同时段还有多少用户也出现类似掉话？",
            "问题主要集中在哪些区域、网元或小区？",
            "更像是接入、IMS 还是核心网环节导致的？",
            "有没有对应的 SIP、告警或日志证据可以确认？",
            "当前要不要继续针对该区域做跟踪或日志采集？",
        ],
        "gold_questions": [
            "同时段还有多少用户也出现类似掉话？",
            "问题主要集中在哪些区域、网元或小区？",
            "更像是接入、IMS 还是核心网环节导致的？",
            "有没有对应的 SIP、告警或日志证据可以确认？",
            "当前要不要继续针对该区域做跟踪或日志采集？",
        ],
    },
    {
        "id": "multi_attach_knowledge_fault",
        "source_type": "多意图-知识+故障",
        "skill": "CN_KnowledgeQA",
        "skills": ["CN_KnowledgeQA", "CN_FaultSpirit"],
        "original_query": "我一边想搞懂 Attach 流程，一边又在排查 Attach 异常，这种场景更适合推荐什么问题？",
        "rewrite_base": "结合 Attach 流程知识理解和当前故障定位，生成更有价值的下一步问题",
        "knowledge_summary": "已检索到 Attach 流程知识、常见失败点和故障定位线索。",
        "plan_summary": "需要把知识解释推进到具体故障场景中的失败点和定位入口。",
        "execution_summary": "回答同时覆盖了流程原理和异常现象，但缺少故障落地信息。",
        "answer_summary": "当前缺少流程失败点、现网告警 KPI 和根因证据。",
        "final_answer": "用户更可能继续追问哪个流程环节最容易失败、该看哪些配置和证据来确认根因。",
        "memory_candidates": [
            "这个流程在当前故障场景里最容易出问题的环节有哪些？",
            "和当前异常最相关的网元、接口或配置点是什么？",
            "如果 Attach 异常，通常先会出现哪些告警或 KPI 波动？",
            "在华为相关网元上，应该先查看哪些配置或状态？",
            "还需要哪些日志或信令证据才能更接近根因？",
        ],
        "gold_questions": [
            "这个流程在当前故障场景里最容易出问题的环节有哪些？",
            "和当前异常最相关的网元、接口或配置点是什么？",
            "如果 Attach 异常，通常先会出现哪些告警或 KPI 波动？",
            "在华为相关网元上，应该先查看哪些配置或状态？",
            "还需要哪些日志或信令证据才能更接近根因？",
        ],
    },
    {
        "id": "multi_license_knowledge_planning",
        "source_type": "多意图-知识+规划",
        "skill": "CN_NetworkPlanning",
        "skills": ["CN_NetworkPlanning", "CN_KnowledgeQA"],
        "original_query": "我既想知道 License 机制本身，也想判断后面会不会有容量风险，这种场景下一步问什么最好？",
        "rewrite_base": "结合 License 机制理解与容量预测诉求，生成最合适的下一步问题",
        "knowledge_summary": "已检索到 License 机制、容量指标和规划分析方法。",
        "plan_summary": "需要把知识解释推进到趋势、风险和扩容优先级判断。",
        "execution_summary": "回答解释了概念，但没有落到容量指标、趋势和瓶颈对象。",
        "answer_summary": "当前缺少趋势、区域差异和优先扩容方向。",
        "final_answer": "用户更自然的后续问题会是关键指标怎么看、哪些对象涨得快、哪里会先到瓶颈。",
        "memory_candidates": [
            "这个机制对应的关键容量指标到底该怎么看？",
            "过去一个月或一年相关指标的趋势如何？",
            "哪些区域或网元增长最快？",
            "按当前增长速度，哪些对象会最先达到容量瓶颈？",
            "如果要做规划，哪些资源最应该优先扩容？",
        ],
        "gold_questions": [
            "这个机制对应的关键容量指标到底该怎么看？",
            "过去一个月或一年相关指标的趋势如何？",
            "哪些区域或网元增长最快？",
            "按当前增长速度，哪些对象会最先达到容量瓶颈？",
            "如果要做规划，哪些资源最应该优先扩容？",
        ],
    },
    {
        "id": "multi_traffic_monitor_planning",
        "source_type": "多意图-监控+规划",
        "skill": "CN_NetworkPlanning",
        "skills": ["CN_NetworkPlanning", "CN_NetworkMonitoring"],
        "original_query": "最近流量涨得很快，监控也持续有异常，这种情况下是先看异常还是直接看容量规划？",
        "rewrite_base": "结合流量监控异常和容量规划诉求，生成最有价值的下一步问题",
        "knowledge_summary": "已检索到流量监控、趋势分析和扩容规划知识。",
        "plan_summary": "当前需要同时判断短期异常影响和中长期容量风险。",
        "execution_summary": "回答确认了流量波动和监控异常，但没有形成短期与长期的联动判断。",
        "answer_summary": "当前缺少趋势、异常集中对象、业务影响和扩容优先级。",
        "final_answer": "用户更像是想继续追问趋势怎么变、哪里最突出、是否已影响业务、先扩哪批资源。",
        "memory_candidates": [
            "过去一个月或一年这个指标的趋势到底怎样？",
            "哪些区域或网元增长最快，同时异常也最突出？",
            "这些异常有没有已经影响到业务成功率或用户体验？",
            "按当前增长和异常幅度，哪些对象会最先达到容量瓶颈？",
            "如果只能先扩一批，哪些资源最值得优先处理？",
        ],
        "gold_questions": [
            "过去一个月或一年这个指标的趋势到底怎样？",
            "哪些区域或网元增长最快，同时异常也最突出？",
            "这些异常有没有已经影响到业务成功率或用户体验？",
            "按当前增长和异常幅度，哪些对象会最先达到容量瓶颈？",
            "如果只能先扩一批，哪些资源最值得优先处理？",
        ],
    },
    {
        "id": "multi_errorcode_monitor_knowledge",
        "source_type": "多意图-监控+知识",
        "skill": "CN_NetworkMonitoring",
        "skills": ["CN_NetworkMonitoring", "CN_KnowledgeQA"],
        "original_query": "我看到错误码趋势异常，还想知道它在协议流程里对应哪个环节，这种场景下一步该怎么问？",
        "rewrite_base": "结合错误码监控异常和协议流程理解诉求，生成最自然的下一步问题",
        "knowledge_summary": "已检索到错误码映射、协议流程和监控指标关联知识。",
        "plan_summary": "当前需要把监控异常与协议环节知识对应起来，再继续下钻异常对象。",
        "execution_summary": "回答确认了错误码趋势异常，但没有明确流程环节和后续对象。",
        "answer_summary": "当前缺少流程映射、异常对象、历史趋势和配置入口。",
        "final_answer": "用户更可能继续追问这个错误码对应哪个环节、集中在哪些对象、是否有告警或配置可以佐证。",
        "memory_candidates": [
            "这个异常错误码在协议流程里通常对应哪个环节？",
            "当前最值得继续下钻的对象是哪个区域、网元或接口？",
            "和昨天或上周同期相比，这个错误码的增幅有多大？",
            "如果该环节异常，通常会先出现哪些告警或 KPI 变化？",
            "在相关网元上应该先查看哪些配置或状态？",
        ],
        "gold_questions": [
            "这个异常错误码在协议流程里通常对应哪个环节？",
            "当前最值得继续下钻的对象是哪个区域、网元或接口？",
            "和昨天或上周同期相比，这个错误码的增幅有多大？",
            "如果该环节异常，通常会先出现哪些告警或 KPI 变化？",
            "在相关网元上应该先查看哪些配置或状态？",
        ],
    },
    {
        "id": "multi_complaint_monitor_kpi",
        "source_type": "多意图-投诉+监控",
        "skill": "CN_CompSpirit",
        "skills": ["CN_CompSpirit", "CN_NetworkMonitoring"],
        "original_query": "最近投诉量在涨，监控 KPI 也开始抖，这种场景更适合推荐哪些下一问？",
        "rewrite_base": "结合投诉集中现象和监控 KPI 波动，生成更贴合实际的下一步问题",
        "knowledge_summary": "已检索到投诉分析、监控 KPI 下钻和业务影响判断知识。",
        "plan_summary": "当前需要同时判断群体性影响、异常集中对象和趋势差异。",
        "execution_summary": "回答确认了投诉量和 KPI 波动同步出现，但没有判断是不是同一问题链路。",
        "answer_summary": "当前缺少投诉扩散范围、异常对象和关联证据。",
        "final_answer": "更自然的后续问题会是还有多少用户受影响、异常集中在哪、和历史趋势及告警能否对应上。",
        "memory_candidates": [
            "同时段还有其他用户出现类似投诉吗？",
            "异常主要集中在哪些区域、网元或接口？",
            "和昨天或上周同期相比，这类异常是不是更明显？",
            "这类投诉有没有已经影响到业务成功率或用户体验？",
            "同时段有没有相关告警、错误码或日志异常？",
        ],
        "gold_questions": [
            "同时段还有其他用户出现类似投诉吗？",
            "异常主要集中在哪些区域、网元或接口？",
            "和昨天或上周同期相比，这类异常是不是更明显？",
            "这类投诉有没有已经影响到业务成功率或用户体验？",
            "同时段有没有相关告警、错误码或日志异常？",
        ],
    },
    {
        "id": "multi_capacity_fault_risk",
        "source_type": "多意图-规划+故障",
        "skill": "CN_NetworkPlanning",
        "skills": ["CN_NetworkPlanning", "CN_FaultSpirit"],
        "original_query": "我怀疑现在的容量压力已经开始引发故障了，这种场景下一步该怎么问更合理？",
        "rewrite_base": "结合容量瓶颈判断和故障风险分析，生成更贴合现场的下一步问题",
        "knowledge_summary": "已检索到容量规划、性能告警和故障风险关联分析知识。",
        "plan_summary": "当前需要同时判断容量风险对象、业务影响和故障证据。",
        "execution_summary": "回答确认容量压力和异常现象可能有关，但没有说明最突出的风险对象。",
        "answer_summary": "当前缺少容量风险对象、业务影响和告警证据。",
        "final_answer": "用户更自然的后续问题会落在容量风险集中在哪、是否已影响业务、哪些对象会先触发故障和先扩哪批资源。",
        "memory_candidates": [
            "当前容量风险最突出的区域或网元有哪些？",
            "这些容量问题是不是已经影响到业务或用户？",
            "按当前趋势，哪些对象最可能先触发故障？",
            "同时段有没有相关告警、日志或性能异常一起出现？",
            "如果现在就做调整，哪些资源最适合优先扩容？",
        ],
        "gold_questions": [
            "当前容量风险最突出的区域或网元有哪些？",
            "这些容量问题是不是已经影响到业务或用户？",
            "按当前趋势，哪些对象最可能先触发故障？",
            "同时段有没有相关告警、日志或性能异常一起出现？",
            "如果现在就做调整，哪些资源最适合优先扩容？",
        ],
    },
    {
        "id": "multi_roaming_monitor_fault",
        "source_type": "多意图-投诉+监控+故障",
        "skill": "CN_CompSpirit",
        "skills": ["CN_CompSpirit", "CN_NetworkMonitoring", "CN_FaultSpirit"],
        "original_query": "今天国际漫游投诉在增，S6a 监控也有异常，这种跨场景问题下一步该追什么？",
        "rewrite_base": "结合国际漫游投诉、S6a 监控异常和故障定位诉求，生成更自然的下一步问题",
        "knowledge_summary": "已检索到国际漫游投诉分析、S6a 监控和 Diameter 故障定位知识。",
        "plan_summary": "需要同时判断群体影响、异常 Peer 对象、业务影响和证据链。",
        "execution_summary": "回答确认漫游投诉和 S6a 异常共现，但还没有说明影响范围和本端/对端方向。",
        "answer_summary": "当前缺少群体受影响范围、异常对象、业务影响和错误码证据。",
        "final_answer": "用户更自然的后续问题会是还有多少漫游用户受影响、集中在哪些 Peer 或对端、有没有错误码和告警支撑。",
        "memory_candidates": [
            "同一国家或同一运营商的漫游用户还有多少受影响？",
            "S6a 异常主要集中在哪些 Peer、区域或对端对象？",
            "这些异常有没有已经影响到漫游注册或业务成功率？",
            "同时段有没有明显的 Diameter 错误码、告警或日志异常？",
            "更像是本端配置、链路还是对端问题导致的？",
        ],
        "gold_questions": [
            "同一国家或同一运营商的漫游用户还有多少受影响？",
            "S6a 异常主要集中在哪些 Peer、区域或对端对象？",
            "这些异常有没有已经影响到漫游注册或业务成功率？",
            "同时段有没有明显的 Diameter 错误码、告警或日志异常？",
            "更像是本端配置、链路还是对端问题导致的？",
        ],
    },
]


def build_dataset() -> list[dict]:
    samples: list[dict] = []

    for scenario in SINGLE_SCENARIOS:
        for feature_index, feature in enumerate(FEATURE_SETS, start=1):
            samples.append(make_sample(scenario, feature, feature_index))

    for scenario in MULTI_SCENARIOS:
        for feature_index, feature in enumerate(FEATURE_SETS, start=1):
            samples.append(make_sample(scenario, feature, feature_index))

    return samples


def main() -> int:
    samples = build_dataset()

    assert len(SINGLE_SCENARIOS) == 30
    assert len(MULTI_SCENARIOS) == 10
    assert len(samples) == 200
    assert sum(1 for item in samples if item["intent_mode"] == "single") == 150
    assert sum(1 for item in samples if item["intent_mode"] == "multi") == 50
    assert set(len(item["user_features"]) for item in samples) == {0, 1, 2, 3}

    for item in samples:
        assert len(item["memory_candidates"]) == 5
        assert len(item["gold_questions"]) == 5
        for removed_key in ("entities", "result_tags", "expected_dimensions", "disallowed_questions"):
            assert removed_key not in item

    output_path = Path(__file__).resolve().parents[1] / "eval" / "recommendation_eval_dataset.json"
    output_path.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output_path} with {len(samples)} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
