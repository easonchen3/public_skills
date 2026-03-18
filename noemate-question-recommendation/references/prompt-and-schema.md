# Prompt 与数据结构

在实现最终 LLM 调用或定义上下文契约时使用本文件。

## 上下文字段结构

```json
{
  "original_query": "string",
  "rewritten_query": "string",
  "skill": "CN_KnowledgeQA | CN_CompSpirit | CN_FaultSpirit | CN_NetworkMonitoring | CN_NetworkPlanning",
  "answer_summary": "string",
  "knowledge_summary": "string",
  "execution_summary": "string",
  "entities": {
    "time_window": "string",
    "region": ["string"],
    "network_element": ["string"],
    "msisdn": ["string"],
    "interface": ["string"],
    "peer": ["string"],
    "kpi": ["string"],
    "alarm": ["string"],
    "version": ["string"],
    "feature": ["string"]
  },
  "result_tags": {
    "has_root_cause": true,
    "has_impact": false,
    "has_evidence": false,
    "has_trend": false,
    "has_action": true
  },
  "candidates": ["string"]
}
```

## 最终 LLM Prompt 骨架

```text
你是 NOEMate 问题推荐模块。你的任务不是自由生成，而是基于当前上下文，从候选问题中选择并在必要时轻微改写，输出最合适的 3 个下一步问题。

约束：
- 只能输出 3 个问题
- 输出必须是纯问题列表
- 不要解释原因
- 不要重复
- 问题必须贴合当前 Skill 和当前上下文
- 优先选择最自然、最有下一步价值的问题
- 尽量从候选池中选择，不要偏离候选语义太远

当前原始问题：
{original_query}

当前改写问题：
{rewritten_query}

当前 Skill：
{skill}

当前回答摘要：
{answer_summary}

关键实体：
{entities}

候选问题：
1. {candidate_1}
2. {candidate_2}
...

请输出：
1. ...
2. ...
3. ...
```

## 进入最终 Prompt 前的过滤清单

- 候选是否与当前 Skill 一致
- 候选是否贴合当前对象、实体或指标
- 候选是否在当前系统能力范围内可回答
- 候选是否只是当前问题的同义改写
- 候选是否过于泛化
- 候选是否真的具备下一步价值

## 评测清单

离线评测至少覆盖：

- Top3 命中率
- 下一步价值准确率
- 意图一致性
- 可回答性
- 去重质量
- 端到端时延


