---
name: noemate-question-recommendation
description: 设计、评审或实现 NOEMate 风格的问题推荐能力，在主回答结束后只返回 Top3 下一步问题。用于需要基于当前意图或 Skill、关键实体、回答缺口、记忆召回和单次最终 LLM 调用来构建上下文感知推荐链路的场景。
---

# NOEMate 问题推荐

## 概述

构建一个适合快速上线的问题推荐流程，在主回答结束后推荐 3 个最自然的下一步问题。保持方案具备上下文感知、Skill 感知、记忆增强，并严格限制为单次最终 LLM 调用。

## 代码资源

本 Skill 自带一个可执行脚本：

- `scripts/recommend_questions.py`：根据上下文生成候选问题、执行过滤和粗排，并构建单次 LLM Prompt
- `scripts/evaluate_recommendation.py`：加载评测集，快速评测本地推荐逻辑的精度和覆盖情况

推荐使用方式：

```bash
python scripts/recommend_questions.py --input context.json --mode candidates
python scripts/recommend_questions.py --input context.json --mode prompt
python scripts/recommend_questions.py --input context.json --mode top3
python scripts/evaluate_recommendation.py --dataset eval/recommendation_eval_dataset.json
```

其中：

- `candidates`：输出过滤和粗排后的候选问题
- `prompt`：输出给最终 LLM 的完整 Prompt
- `top3`：在没有接入真实 LLM 时，输出本地粗排后的 Top3 兜底结果

脚本只做前处理和最终 Prompt 构建，不做多次模型调用。

评测集和设计文档位置：

- `eval/recommendation_eval_dataset.json`：基于真实调研问题整理的离线评测集
- `references/design-and-evaluation.md`：完整设计方案、评测口径和调优建议

## 核心约束

除非用户明确要求修改，否则始终遵守以下约束：

- 只返回 Top3。
- 输出必须是纯问题列表，不带解释。
- 全链路只能调用一次 LLM。
- LLM 调用前的候选生成尽量使用轻量规则。
- 推荐必须绑定当前意图或 Skill。
- 优先推荐“当前回答之后最合理的下一步问题”，而不是泛相关问题。
- 记忆系统只用于扩充候选，不直接作为最终结果。

把目标理解为“用户接下来最应该问什么”，而不是“还有哪些内容语义相近”。

## 工作流

按下面的顺序执行。

### 1. 构建上下文输入

提取或定义这些字段：

- `original_query`
- `rewritten_query`
- `skill`
- `answer_summary`
- `knowledge_summary`
- `execution_summary`
- `entities`
- `result_tags`

优先使用结构化实体，而不是大段自由文本。至少考虑：

- 时间窗
- 区域
- 网元
- MSISDN
- 接口
- Peer
- KPI
- 告警
- 版本
- 特性名

用 `result_tags` 表达回答缺口，推荐使用布尔标签：

- `has_root_cause`
- `has_impact`
- `has_evidence`
- `has_trend`
- `has_action`

### 2. 生成候选问题

从三路生成候选池：

1. 基于 Skill 模板的候选
2. 基于记忆召回的高频候选
3. 基于回答缺口的规则候选

原始候选池控制在 8 到 15 条。

把模板候选作为质量下限，把记忆候选作为覆盖增强，把缺口候选作为高价值补充。

需要具体模板时，读取 [references/template-catalog.md](./references/template-catalog.md)。

### 3. 过滤与粗排

在最终 LLM 调用前完成这些处理：

- 去掉完全重复和近似重复问题
- 去掉和当前用户问题同义复述的问题
- 去掉系统当前无法回答或无法执行的问题
- 去掉过于泛化的问题
- 除非当前上下文已经进入执行阶段，否则避免高风险动作建议
- 在影响范围、根因、证据、趋势、下一步动作之间保留多样性

可以使用下面这种轻量打分：

```text
score = intent_match + context_match + next_step_value + memory_weight
```

其中：

- `intent_match`：是否与当前 Skill 一致
- `context_match`：是否贴合当前实体、对象或指标
- `next_step_value`：是否能推动会话进入更有价值的下一步
- `memory_weight`：是否得到历史高频问题支持

最终传给 LLM 的候选控制在 6 到 10 条。

### 4. 执行单次最终 LLM 选择

LLM 只负责“从候选池中选择并轻微改写”，不要让它自由生成一大批问题。

Prompt 中应包含：

- 原始问题
- 改写问题
- 当前 Skill
- 简短回答摘要
- 关键实体
- 候选问题列表
- 输出规则

输出必须可直接解析为纯问题列表：

```text
1. ...
2. ...
3. ...
```

需要可复用的提示词骨架和数据结构时，读取 [references/prompt-and-schema.md](./references/prompt-and-schema.md)。

## 决策规则

默认按下面的优先级做推荐：

- 发现异常但没给影响范围时，优先推荐影响类问题。
- 描述了现象但没给根因时，优先推荐根因类问题。
- 给了根因判断但没给证据时，优先推荐证据类问题。
- 给了当前值但没给时间对比时，优先推荐趋势类问题。
- 解释了知识但没有落到运维操作时，优先推荐配置、KPI、告警或排障入口类问题。

优先在当前 Skill 内找到最自然的下一步，再考虑跨 Skill。

## 按 Skill 的优先方向

默认使用下面这套优先方向：

- `CN_KnowledgeQA`：运维落地、失败点、命令、配置、KPI 或告警映射、版本差异
- `CN_CompSpirit`：同时段同类影响、根因方向、信令证据、跟踪建议
- `CN_FaultSpirit`：影响用户、影响区域或网元、关联告警或变更、日志或跟踪证据
- `CN_NetworkMonitoring`：异常集中对象、业务影响、环比同比对比、关联告警或日志
- `CN_NetworkPlanning`：长周期趋势、区域差异、容量风险、扩容优先级

不要每次都从零发明模式，优先使用模板目录。

## 实现建议

在设计或落地实现时：

- 尽量把前处理做成规则化、确定性的逻辑
- 用结构化字段承载上下文
- 用 Skill 和实体约束记忆召回
- 用显式过滤替代隐式模型行为
- 把最终 LLM 限定为选择器和轻改写器
- 让服务端输出解析保持简单

常见可交付物包括：

- 候选生成函数
- 过滤和粗排逻辑
- Prompt 构建器
- 评测数据集
- 线上与离线指标方案

## 脚本输入约定

`scripts/recommend_questions.py` 默认读取一个 JSON 文件，字段结构参考：

```json
{
  "original_query": "Why attach success rate dropped at XX Nov 2024, 02:00?",
  "rewritten_query": "查询 2024-11-XX 02:00 attach success rate 下降原因",
  "skill": "CN_FaultSpirit",
  "answer_summary": "已发现 attach success rate 在目标时段下降，但尚未明确影响范围和根因证据。",
  "entities": {
    "network_element": ["MME"],
    "feature": ["Attach"],
    "kpi": ["attach success rate"]
  },
  "result_tags": {
    "has_root_cause": false,
    "has_impact": false,
    "has_evidence": false,
    "has_trend": false,
    "has_action": false
  },
  "memory_candidates": [
    "这次异常影响了多少用户？",
    "哪些区域或网元受到影响最大？",
    "同时段是否有相关告警或错误码异常？"
  ],
  "invalid_candidates": [],
  "max_candidates": 8
}
```

如果需要接入真实 LLM，直接复用脚本输出的 `prompt` 即可。

## 评测建议

优先用 `scripts/evaluate_recommendation.py` 做快速回归。该脚本默认评估：

- `Hit@3`
- `Precision@3`
- `Recall@3`
- 维度覆盖率
- Skill 一致性
- 禁推命中率
- 重复率

建议每次修改模板、过滤规则、粗排逻辑或 Prompt 之前后，都跑一次同一份评测集做对比。

## 伪代码

```python
def recommend_questions(context):
    original_query = context.original_query
    rewritten_query = context.rewritten_query
    skill = context.intent
    answer_summary = build_answer_summary(context)
    entities = extract_entities(context)
    result_tags = extract_result_tags(context)

    template_candidates = generate_from_skill_templates(skill, entities, result_tags)
    memory_candidates = recall_from_memory(rewritten_query, skill, entities, topk=5)
    gap_candidates = generate_from_result_gap(skill, entities, result_tags)

    candidates = template_candidates + memory_candidates + gap_candidates
    candidates = deduplicate(candidates)
    candidates = filter_invalid(candidates, skill, context)
    candidates = filter_same_as_current_question(candidates, rewritten_query)
    candidates = rough_rank(candidates, skill, entities, result_tags)

    final_candidates = candidates[:8]
    prompt = build_prompt(
        original_query=original_query,
        rewritten_query=rewritten_query,
        skill=skill,
        answer_summary=answer_summary,
        entities=entities,
        candidates=final_candidates,
    )
    return call_llm_once(prompt)
```

## 输出风格

当用户要求提供可复用方案时，优先输出：

- 架构摘要
- 上下文字段设计
- 候选生成规则
- 过滤与排序规则
- 单次 LLM Prompt 契约
- 评测方案

当用户要求直接实现时，生成满足“只调用一次 LLM”约束的生产级代码与测试。


