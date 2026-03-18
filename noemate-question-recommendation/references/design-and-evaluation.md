# NOEMate 问题推荐能力设计与评测方案

## 1. 背景与目标

NOEMate 是华为 iMaster NAIE 27.0 的核心组件，服务对象包括 NOC 运维工程师、规划经理、维护经理、优化经理和运营经理。系统当前已经具备：

- `AICOService`：智能体核心编排与对外服务入口
- 短期上下文：原始问题、改写问题、知识片段、当前 Skill、任务规划与执行结果
- 记忆系统：基于相似度和访问频次返回 TopN 高频相似问题
- `Skill Engine`：负责执行具体 Skill

当前目标是在主回答结束后，给出 3 个最合理的下一步问题，帮助用户顺滑进入深挖、排障、监控、统计或执行动作。

首版设计约束如下：

- 只返回 Top3
- 输出必须是纯问题列表
- 全链路只允许一次最终 LLM 调用
- 推荐必须强绑定当前上下文和当前 Skill
- 候选生成优先使用规则、模板和记忆，不引入重链路排序

## 2. 当前能力边界

当前推荐能力优先覆盖 5 个核心 Skill：

- `CN_KnowledgeQA`
- `CN_CompSpirit`
- `CN_FaultSpirit`
- `CN_NetworkMonitoring`
- `CN_NetworkPlanning`

调研样本里还存在“任务执行、信息收集、日志查询、网络查询”等问法。这些样本应作为后续扩展的输入来源，但首版主指标建议仍围绕上述 5 个核心 Skill 评测。

## 3. 理论依据

该问题本质上不是“相似问题推荐”，而是“下一步问题决策”。可将其建模为一个受约束的候选排序问题：

```text
Top3 = Select(Context, Skill, Candidates_template+memory+gap)
```

理论上，下一步问题的价值取决于三个因素：

1. 当前任务闭环程度
2. 当前 Skill 的自然追问路径
3. 当前回答缺失的信息维度

因此首版采用“规则召回 + 候选粗排 + 单次 LLM 轻融合”的混合方案，而不是直接做开放式生成。

## 4. 总体架构

建议的线上链路如下：

### 4.1 请求入口

由 `AICOService` 在主回答完成后组装推荐上下文，调用推荐模块。

### 4.2 上下文构建层

输入字段：

- 原始问题
- 改写问题
- 当前 Skill
- 知识片段摘要
- 任务规划摘要
- 执行结果摘要
- 回答摘要
- 关键实体
- 结果标签

结果标签建议包含：

- `has_root_cause`
- `has_impact`
- `has_evidence`
- `has_trend`
- `has_action`

### 4.3 候选召回层

三路并行：

1. Skill 模板候选
2. 记忆系统候选
3. 回答缺口候选

候选池建议控制在 8 到 15 条。

### 4.4 候选过滤与粗排层

过滤：

- 重复问题
- 与当前问题同义的问题
- 系统无能力回答的问题
- 过泛问题
- 高风险动作问题

粗排：

```text
Score = IntentMatch + ContextMatch + NextStepValue + MemoryWeight
```

进入最终 LLM 的候选控制在 6 到 10 条。

### 4.5 单次 LLM 输出层

只做“选择 + 轻改写”，不做自由生成，输出严格限制为：

```text
1. ...
2. ...
3. ...
```

## 5. 上下文建模细则

### 5.1 必备字段

- `original_query`
- `rewritten_query`
- `skill`
- `answer_summary`
- `entities`
- `result_tags`

### 5.2 建议抽取的实体

- 时间窗
- 区域
- 网元
- MSISDN
- 接口
- Peer
- KPI
- 告警
- 版本
- 特性

### 5.3 回答缺口判定逻辑

围绕 5 个高价值维度判断缺口：

- 影响范围
- 根因定位
- 证据补强
- 趋势对比
- 下一步动作

推荐模块应优先解决“当前答案还差什么”。

## 6. Skill 分场景策略

### 6.1 CN_KnowledgeQA

目标：从知识理解推进到运维落地。

优先维度：

- 失败点
- KPI/告警映射
- 配置和命令
- 版本差异

### 6.2 CN_CompSpirit

目标：从单用户投诉推进到根因方向和证据确认。

优先维度：

- 同时段同类影响
- 网络环节定位
- 信令证据
- 跟踪建议

### 6.3 CN_FaultSpirit

目标：围绕影响、根因、证据、动作进行深挖。

优先维度：

- 影响用户
- 影响区域/网元
- 根因对象
- 告警/变更/日志证据

### 6.4 CN_NetworkMonitoring

目标：从异常发现推进到异常归因与业务影响判断。

优先维度：

- 异常集中对象
- 业务影响
- 同比环比
- 关联告警/日志

### 6.5 CN_NetworkPlanning

目标：从现状统计推进到趋势、瓶颈和扩容建议。

优先维度：

- 长周期趋势
- 区域差异
- 容量风险
- 扩容优先级

## 7. 可扩展设计

为支持后续新增 Skill，推荐模块应保持这三层抽象：

1. `SkillProfile`
   - 定义该 Skill 的优先维度、模板和高风险动作边界
2. `ContextAdapter`
   - 将 AICOService 的运行态信息映射成统一上下文字段
3. `CandidateStrategy`
   - 定义模板召回、记忆过滤、缺口补全和粗排策略

新增 Skill 时，只需补齐：

- Skill 描述与优先维度
- 模板库
- 特有过滤规则
- 对应评测样本

## 8. 评测集设计原则

评测集必须来自真实运维场景，并覆盖多 Skill、多维度、多语言和多对象类型。

每条样本建议包含：

- `id`
- `skill`
- `source_type`
- `original_query`
- `rewritten_query`
- `answer_summary`
- `entities`
- `result_tags`
- `memory_candidates`
- `gold_questions`
- `expected_dimensions`
- `disallowed_questions`

其中：

- `gold_questions`：3 到 5 条理想下一问
- `expected_dimensions`：该场景下应该优先覆盖的追问维度
- `disallowed_questions`：不应推荐的问题

## 9. 评测维度与口径

首版建议同时看精度、质量和安全性。

### 9.1 核心离线指标

- `Hit@3`
  至少一个预测命中金标
- `Precision@3`
  Top3 中命中金标的比例
- `Recall@3`
  Top3 对金标集合的覆盖率
- `DimensionCoverage@3`
  Top3 是否覆盖了期望维度
- `SkillConsistency@3`
  推荐问题是否落在当前 Skill 的优先维度范围内
- `DisallowedRate`
  Top3 是否命中禁推问题
- `DuplicateRate`
  Top3 是否出现重复表达

### 9.2 人工评测维度

- 相关性
- 下一步价值
- 可执行性
- 自然性
- 多样性

### 9.3 线上指标

- 推荐点击率
- 推荐采纳率
- 采纳后的有效追问率
- 推荐后用户手改率
- 推荐时延

## 10. 快速评测方法

本 Skill 自带的 `scripts/evaluate_recommendation.py` 提供一个快评模式：

1. 读取评测集
2. 调用本地 `recommend_questions.py`
3. 输出总体指标
4. 输出按 Skill 的拆分结果
5. 输出失败样本清单

该方式适合：

- 模板调优回归
- 过滤规则回归
- 记忆候选接入前后对比
- Prompt 改写前后的候选质量对比

## 11. 精度优化路径

优先级建议如下：

### 第一阶段

- 打磨 5 个核心 Skill 模板
- 提升 `result_tags` 识别准确率
- 做好记忆候选过滤

### 第二阶段

- 基于离线评测错误样本补模板
- 基于线上点击/采纳日志调权重
- 拆分更细的维度标签和实体槽位

### 第三阶段

- 引入轻量学习排序
- 引入更细粒度的跨 Skill 推荐
- 引入用户角色或场景画像

## 12. 与现有系统的接口建议

### 12.1 AICOService -> 推荐模块

输入建议：

- 会话 ID
- 主问题 ID
- 当前 Skill
- 上下文结构体
- 回答摘要
- 记忆候选

输出建议：

- `top3_questions`
- `candidate_trace`
- `decision_trace`

### 12.2 Skill Engine -> 推荐模块

推荐 Skill Engine 返回额外标签，帮助推荐模块判断缺口：

- 是否已有根因
- 是否已有影响范围
- 是否已有证据
- 是否已有趋势
- 是否已有动作建议

## 13. 当前仓库内配套资产

本仓库已提供：

- `scripts/recommend_questions.py`
- `scripts/evaluate_recommendation.py`
- `eval/recommendation_eval_dataset.json`

可以直接用于离线快评与迭代。
