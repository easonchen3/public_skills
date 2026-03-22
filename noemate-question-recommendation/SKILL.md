---
name: noemate-question-recommendation
description: 设计、评审或实现 NOEMate 风格的问题推荐能力，在主回答结束后只返回 Top3 下一步问题。适用于基于完整上下文、当前 Skill 约束、记忆增强和单次最终 LLM 直生成链路来构建推荐能力的场景。
---

# NOEMate 问题推荐

## 概述

这个 Skill 用于设计或落地一个真实可上线的 NOEMate 问题推荐能力。

推荐的不是“相似问题”，而是“用户在当前回答之后最值得继续追问的 3 个下一步问题”。

请按下面这条主线理解方案：

- 完整上下文输入
- 当前 Skill 约束
- 记忆高频问题增强
- 单次最终 LLM 直接生成 Top3
- 轻量后处理保证结果质量

如果现有方案仍以“候选池召回 + 粗排 + LLM 只做选择”为主，请优先按本 Skill 重构为“受约束的直接生成式推荐”。

## 代码资源

本 Skill 自带两个可直接复用的脚本：

- `scripts/recommend_questions.py`：组装推荐上下文、生成核心 Prompt，并提供本地兜底 Top3
- `scripts/evaluate_recommendation.py`：基于评测集快速回归本地兜底逻辑的覆盖和质量

核心生成 Prompt 已从脚本中拆出，位于：

- `prompts/generation_system_prompt.txt`
- `prompts/generation_user_prompt.txt`

其中 `generation_user_prompt.txt` 使用占位符方式组织，由脚本在运行时替换：

- `{original_query}`
- `{rewritten_query}`
- `{skills}`
- `{user_features}`
- `{knowledge_summary}`
- `{plan_summary}`
- `{execution_summary}`
- `{answer_summary}`
- `{final_answer}`
- `{entities}`
- `{result_tags}`
- `{skill_preferences}`
- `{memory_questions}`

评测裁判 Prompt 也已从脚本中拆出，位于：

- `prompts/judge_system_prompt.txt`
- `prompts/judge_user_prompt.txt`

其中 `judge_user_prompt.txt` 使用占位符方式组织，由评测脚本在运行时替换：

- `{sample_id}`
- `{original_query}`
- `{rewritten_query}`
- `{skills}`
- `{final_answer}`
- `{gold_questions}`
- `{predictions}`
- `{json_schema}`

推荐使用方式：

```bash
python scripts/recommend_questions.py --input scripts/example_context.json --mode prompt
python scripts/recommend_questions.py --input scripts/example_context.json --mode top3
python scripts/evaluate_recommendation.py --dataset eval/recommendation_eval_dataset.json
```

其中：

- `prompt`：输出给最终单次 LLM 的核心推荐 Prompt
- `top3`：在没有接入真实 LLM 时，输出本地规则兜底的 Top3

## 核心约束

除非用户明确要求修改，否则始终遵守以下约束：

- 只返回 Top3。
- 输出必须是纯问题列表，不带解释。
- 全链路只能调用一次最终 LLM。
- 推荐必须强绑定当前上下文和当前 Skill。
- 推荐必须优先服务于“下一步任务推进”，而不是复述当前问题。
- 不强依赖前置实体抽取和结果标签；若已有结构化字段，可作为增强输入。
- 记忆系统只作为风格与高频追问参考，不直接等价于最终结果。
- 后处理只做轻量去重、近似过滤和数量补齐，不引入新的多阶段模型链路。

## 工作流

按下面顺序执行。

### 1. 组装完整上下文

首版本优先复用系统已具备的上下文，不新增重型前置抽取链路。

推荐输入字段：

- `original_query`
- `rewritten_query`
- `skill`
- `skills`
- `user_features`
- `knowledge_summary`
- `plan_summary`
- `execution_summary`
- `answer_summary`
- `final_answer`
- `memory_questions`

其中：

- `skills` 用于表示单意图或多意图场景
- `user_features` 用于承载在改写阶段已融合的用户职责、阈值偏好和地区偏好
- 阈值类和地区类特征的 key 不固定

可选增强字段：

- `entities`
- `result_tags`

如果没有 `entities` 或 `result_tags`，不要阻塞流程，直接依赖模型对完整上下文做隐式理解。

### 2. 注入 Skill 推荐偏好

Skill 的作用不是穷举模板，而是告诉模型：

- 当前 Skill 下什么问题属于“自然的下一步”
- 哪些方向优先
- 哪些方向不应优先

默认优先方向：

- `CN_KnowledgeQA`：知识点落地、产品实现、配置查看、命令、KPI/告警映射、版本差异
- `CN_CompSpirit`：同类用户是否扩散、定位网络环节、信令/错误码证据、是否需要继续跟踪
- `CN_FaultSpirit`：影响范围、根因对象、告警/日志/变更证据、恢复验证
- `CN_NetworkMonitoring`：异常集中对象、业务影响、趋势对比、关联告警/日志
- `CN_NetworkPlanning`：长周期趋势、区域差异、容量风险、扩容优先级

### 3. 接入记忆增强

记忆问题只作为类似场景下的高频追问参考。

使用原则：

- 保留 Top3 到 Top5 即可
- 明确告诉模型“仅供参考，不要机械照抄”
- 优先借用真实用户追问风格，而不是直接复用原句

### 4. 执行单次最终 LLM 调用

最终 LLM 的任务是直接生成 Top3，而不是执行复杂多阶段推理链。

模型在内部应完成四件事：

1. 判断当前任务处于什么阶段。
2. 判断当前回答已经覆盖了什么、还缺什么。
3. 结合当前 Skill 决定什么是最自然、最有价值的下一步。
4. 参考记忆高频追问，生成更像真实用户会说的话。

### 5. 轻量后处理

对最终结果只做这些处理：

- 去掉与当前问题高度重复的问题
- 去掉 Top3 内部重复问题
- 去掉明显过泛的问题
- 保持与当前会话语言一致
- 如过滤后不足 3 条，可用本 Skill 下的兜底示例补齐

## 核心推荐算法 Prompt

当用户要求“给出核心算法 Prompt”时，优先提供下面这版可直接复用的提示词骨架。

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

## Skill 内容

当用户要求“给出 Skill 内容”时，默认输出以下几个部分：

- 能力目标：推荐的是下一步问题，不是相似问题
- 版本约束：Top3、纯问题列表、单次 LLM
- 输入契约：完整上下文必填，结构化字段可选增强
- Skill 偏好：不同 Skill 下的推荐方向和禁推方向
- 核心 Prompt：单次 LLM 直接生成版
- 后处理规则：去重、近似过滤、语言一致性、兜底补齐
- 评测建议：离线回归 + 人工评测 + 线上点击与采纳

如果用户要求你直接产出一版可落地的 Skill 文档，可以按这个结构组织：

1. 背景与目标
2. 核心约束
3. 输入上下文设计
4. Skill 偏好设计
5. 核心推荐算法 Prompt
6. 后处理与兜底策略
7. 评测方案
8. 演进路线

## 兜底示例

兜底示例只用于以下场景：

- 本地评测
- 没有真实 LLM 时的演示
- 最终结果被后处理过滤后不足 3 条

不要把兜底示例误当成主算法本体。

需要 Skill 级偏好与兜底问题示例时，读取 [references/template-catalog.md](./references/template-catalog.md)。

## 脚本输入约定

`scripts/recommend_questions.py` 默认读取一个 JSON 文件，字段结构参考：

```json
{
  "original_query": "Why attach success rate dropped at XX Nov 2024, 02:00?",
  "rewritten_query": "查询 2024-11-XX 02:00 attach success rate 下降原因",
  "skill": "CN_FaultSpirit",
  "skills": ["CN_FaultSpirit"],
  "user_features": {
    "用户职责": ["云核运维工程师"],
    "网络流量异常": ["波动超过10%阈值"],
    "泰国北区": ["清迈府", "清莱府"]
  },
  "knowledge_summary": "系统已检索到与 attach 流程和常见失败点相关的知识片段。",
  "plan_summary": "已完成异常识别，下一步更适合分析影响范围与证据。",
  "execution_summary": "目标时段 attach success rate 明显下降，暂未发现直接恢复动作。",
  "answer_summary": "已发现指标下降，但尚未明确影响用户范围、根因对象与证据。",
  "final_answer": "attach success rate 在目标时段出现明显下降，当前只能确认现象，尚不能直接确认根因。",
  "memory_questions": [
    "这次异常影响了多少用户？",
    "哪些区域或网元受到影响最大？",
    "同时段是否有相关告警或错误码异常？"
  ],
  "entities": {
    "network_element": ["MME"],
    "feature": ["Attach"],
    "kpi": ["attach success rate"],
    "time_window": ["2024-11-XX 02:00"]
  }
}
```

如果线上已接入真实 LLM，优先使用脚本输出的 `prompt` 去执行最终单次模型调用。

## 评测建议

优先用 `scripts/evaluate_recommendation.py` 做快速回归。默认重点看：

- `Hit@3`
- `Precision@3`
- `Recall@3`
- `DimensionCoverage@3`
- `SkillConsistency@3`
- `DisallowedRate`
- `DuplicateRate`

建议每次修改 Prompt、Skill 偏好、兜底策略或后处理规则前后，都跑同一份评测集对比。

## 输出风格

当用户要求提供可复用方案时，优先输出：

- 架构摘要
- 输入上下文契约
- Skill 偏好规则
- 核心推荐算法 Prompt
- 后处理与兜底策略
- 评测方案

当用户要求直接实现时，优先落地：

- Prompt 构建器
- 推荐上下文结构
- 轻量后处理
- 本地兜底 Top3
- 离线评测脚本

## 模型化评测约定

当前版本评测默认支持两类可配置模型角色：

- `generation model`：基于核心推荐 Prompt 直接生成 Top3
- `judge model`：基于统一评测 Prompt 判断 Top3 是否语义命中 `gold_questions`

主评测指标统一为：

- `Top3Accuracy = 语义命中条数 / 3`

其中：

- 生成模型和裁判模型都支持通过命令行独立配置
- 裁判模型只负责判断是否命中，不负责改写推荐结果
- `Hit@3`、`Recall@3`、`DimensionCoverage`、`SkillConsistency` 作为辅助诊断指标保留

推荐评测命令：

```bash
python scripts/evaluate_recommendation.py --dataset eval/recommendation_eval_dataset.json --config config/runtime_config.json
```

如果只想验证脚本链路，也可以继续使用本地兜底模式：

```bash
python scripts/evaluate_recommendation.py --dataset eval/recommendation_eval_dataset.json --generator-mode fallback --judge-mode heuristic
```

默认配置文件路径：

- `config/runtime_config.json`

配置文件中可统一维护：

- 推荐模型 `model`
- 评测参测模型 `generator_model`
- 评测裁判模型 `judge_model`
- `api_key`
- `api_key_env`
- `base_url`
- `temperature`
- `max_tokens`

命令行参数优先级高于配置文件，适合做临时覆盖实验。
