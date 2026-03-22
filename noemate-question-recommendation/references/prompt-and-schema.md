# Prompt 与数据结构

本文件定义当前推荐算法的上下文契约和核心 Prompt。

当前版本主算法不是“候选召回 + LLM 选择”，而是：

> 完整上下文 + 当前 Skill 约束 + 记忆增强 + 单次最终 LLM 直接生成 Top3

## 上下文字段结构

以下字段中，前 8 项建议作为主输入，结构化字段为可选增强。

```json
{
  "original_query": "string",
  "rewritten_query": "string",
  "skill": "CN_KnowledgeQA | CN_CompSpirit | CN_FaultSpirit | CN_NetworkMonitoring | CN_NetworkPlanning",
  "skills": ["string"],
  "user_features": {
    "用户职责": ["string"],
    "任意阈值类key": ["string"],
    "任意地区类key": ["string", "string"]
  },
  "knowledge_summary": "string",
  "plan_summary": "string",
  "execution_summary": "string",
  "answer_summary": "string",
  "final_answer": "string",
  "memory_questions": ["string"],
  "entities": {
    "time_window": ["string"],
    "network_element": ["string"],
    "interface": ["string"],
    "peer": ["string"],
    "kpi": ["string"],
    "alarm": ["string"],
    "feature": ["string"]
  }
}
```

## 设计说明

- `original_query`、`rewritten_query`、`skill/skills` 是核心锚点。
- `user_features` 用于表达改写阶段已融合的用户职责、阈值偏好和地区偏好，其中阈值类和地区类 key 不固定。
- `knowledge_summary`、`plan_summary`、`execution_summary`、`answer_summary`、`final_answer` 用来帮助模型判断当前任务阶段和回答缺口。
- `memory_questions` 只作为参考，不直接等价于输出。
- `entities` 是增强项，不应成为链路前提。

## 核心 Prompt

```text
你是 NOEMate 的问题推荐模块。

你的任务是在主回答结束后，基于当前完整会话上下文、当前 Skill 的语义边界以及类似场景的高频追问参考，直接生成 3 个最合适的下一步问题。

这不是相似问句扩写任务。你的目标是判断：
在当前回答之后，用户最可能继续问、且最能推动任务向前推进的 3 个问题是什么。

你需要在内部完成四件事：
1. 判断当前任务阶段，例如知识解释、数据查询、异常发现、根因定位、结果验证或规划分析。
2. 判断当前回答已经覆盖了什么、还缺什么，例如影响范围、根因、证据、趋势、运维动作。
3. 结合当前 Skill，选择最符合该 Skill 语义边界的下一步问题。
4. 参考高频追问的语言风格，但不要机械照抄。

输入上下文：
原始问题：
{original_query}

改写问题：
{rewritten_query}

当前 Skill：
{skills}

用户特征：
{user_features}

知识摘要：
{knowledge_summary}

任务规划摘要：
{plan_summary}

执行结果摘要：
{execution_summary}

回答摘要：
{answer_summary}

最终回答：
{final_answer}

可选结构化信息：
实体：
{entities}

当前 Skill 的推荐偏好：
{skill_preferences}

类似场景高频追问，仅供参考，不得机械照抄：
{memory_questions}

输出规则：
- 只输出 3 个问题
- 使用如下格式：
1. ...
2. ...
3. ...
- 每一条都必须是完整、自然、可直接点击追问的问题句
- 不要输出解释、标题、分类、原因、前后缀说明
- 不要重复当前问题，不要复述主回答
- 不要输出“还有什么可以看”“还有哪些异常”这类泛问题
- 如果某个维度已经被当前回答覆盖，优先补足尚未覆盖的维度
- 优先在当前 Skill 内推进，不要无故跳到其他 Skill
- 与当前会话语言保持一致
```

## 输出契约

最终输出必须可直接解析为：

```text
1. ...
2. ...
3. ...
```

额外要求：

- 每条都是问题句
- 不得夹带说明文字
- 不得输出空行之外的额外内容

## 后处理建议

最终输出返回前建议执行以下校验：

1. 过滤与 `original_query` 或 `rewritten_query` 高度重复的问题。
2. 过滤 Top3 内部重复或近似重复的问题。
3. 过滤明显过泛、无执行价值的问题。
4. 如不足 3 条，用当前 Skill 的兜底示例补齐。
## 评测 Prompt

离线评测不再只依赖规则字符串匹配，增加统一的裁判 Prompt 来判断语义是否命中推荐结果。裁判模型输入为：

- 当前样本上下文
- `skills`
- `final_answer`
- `gold_questions`
- 待评测模型生成的 `predictions`

推荐裁判 Prompt 骨架：

```text
你是 NOEMate 问题推荐评测裁判。
你只负责判断预测 Top3 是否语义命中了标准推荐问题，不负责重写问题。
评测原则：
1. 语义等价即可命中，不要求字面完全一致。
2. 允许不同表达方式，但必须是同一个“下一步追问意图”。
3. 同一条 gold_question 最多只能被一个 prediction 命中。
4. 如果 prediction 过于泛化、与当前 Skill 不符、或只是复述原问题，则判定为未命中。
5. 重点判断推荐是否符合 NOEMate 当前上下文下的下一步追问价值。

请输出 JSON：
{
  "matched_count": 0,
  "top3_accuracy": 0.0,
  "matches": [
    {
      "prediction": "string",
      "is_match": false,
      "matched_gold": "string or empty",
      "reason": "short reason"
    }
  ],
  "summary": "short summary"
}
```

## 评测指标

当前主指标统一为：

```text
Top3Accuracy = matched_count / 3
```

说明：

- `matched_count` 由裁判模型基于评测 Prompt 给出
- 生成模型和裁判模型都支持独立配置
- 规则评测保留为无模型场景下的兜底模式，不作为主评测方式

## 运行时配置文件

模型相关配置已从命令行参数中抽取到 JSON 配置文件，默认示例路径：

- `config/runtime_config.json`

推荐结构：

```json
{
  "shared": {
    "api_key": "",
    "api_key_env": "OPENAI_API_KEY",
    "base_url": "",
    "temperature": 0.2,
    "max_tokens": 800
  },
  "recommend_questions": {
    "model": "gpt-4.1-mini"
  },
  "evaluate_recommendation": {
    "generator_mode": "llm",
    "generator_model": "gpt-4.1-mini",
    "judge_mode": "llm",
    "judge_model": "gpt-4.1-mini"
  }
}
```

脚本支持 `--config` 读取该文件，且命令行参数优先级更高。
