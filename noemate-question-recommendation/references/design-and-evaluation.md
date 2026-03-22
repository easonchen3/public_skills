# NOEMate 问题推荐能力设计与评测方案

## 1. 设计结论

当前版本推荐能力建议采用下面这条主链路：

> 基于当前完整上下文 + 当前 Skill 约束 + 记忆高频问题增强 + 单次最终 LLM 直接生成 Top3 推荐问题

这条链路适合当前版本的原因：

- 只调用一次最终 LLM
- 不依赖模板穷举
- 不依赖前置实体和结果标签稳定可得
- 对长尾场景和新 Skill 扩展更稳
- 可以直接复用现有 AICOService、记忆系统和 Skill Engine

## 2. 算法主线

该问题本质上不是“相似问题推荐”，而是“下一步问题决策”。

因此首版更适合做受约束的直接生成，而不是复杂的多阶段召回排序。

模型需要同时理解三件事：

1. 当前任务处于什么阶段。
2. 当前回答还缺什么。
3. 当前 Skill 下什么样的追问最自然、最有价值。

记忆系统的作用不是直接输出问题，而是为模型提供：

- 真实用户高频追问方向
- 更自然的语言风格
- 类似场景下常见的下一步模式

## 3. 输入上下文设计

推荐主输入：

- 原始问题
- 改写问题
- 当前 Skill
- 知识摘要
- 任务规划摘要
- 执行结果摘要
- 回答摘要
- 最终回答
- 记忆问题 Top3 到 Top5

可选增强输入：

- 关键实体
- 结果标签

首版原则：

- 不新增重型抽取链路
- 允许模型对完整上下文做隐式理解
- 若已有结构化字段，则作为增强而不是前提

## 4. Skill 约束设计

Skill 约束的职责不是穷举模板，而是限制推荐语义边界。

### 4.1 `CN_KnowledgeQA`

优先方向：

- 知识点如何落到运维场景
- 对应产品实现、配置、命令
- KPI 或告警映射
- 版本差异和配置示例

避免优先：

- 与当前知识点无关的泛监控追问
- 高风险直接执行动作

### 4.2 `CN_CompSpirit`

优先方向：

- 是否扩散到更多用户
- 问题更可能落在哪个网络环节
- 是否有信令、错误码、日志证据
- 是否需要继续跟踪

避免优先：

- 与投诉个体无关的宏观统计问题
- 容量规划类问题

### 4.3 `CN_FaultSpirit`

优先方向：

- 影响范围
- 根因对象
- 告警、日志、变更等证据
- 恢复验证或值守监控

避免优先：

- 脱离当前故障上下文的泛知识问法
- 与排障无关的宽泛扩展

### 4.4 `CN_NetworkMonitoring`

优先方向：

- 异常集中对象
- 业务影响
- 同比环比或时序趋势
- 关联告警和日志

避免优先：

- 纯知识解释类追问
- 与当前观测结果无关的泛排障问题

### 4.5 `CN_NetworkPlanning`

优先方向：

- 长周期趋势
- 区域差异
- 容量瓶颈和风险
- 扩容优先级

避免优先：

- 即时故障处置类问题

## 5. 后处理设计

推荐返回前只做轻量后处理：

1. 去掉与当前问题高度重复的问题。
2. 去掉 Top3 内部近似重复的问题。
3. 去掉明显过泛、无执行价值的问题。
4. 统一语言风格。
5. 若过滤后不足 3 条，用当前 Skill 的兜底示例补齐。

## 6. 评测建议

离线评测建议同时看：

- `Hit@3`
- `Precision@3`
- `Recall@3`
- `DimensionCoverage@3`
- `SkillConsistency@3`
- `DisallowedRate`
- `DuplicateRate`

人工评测建议重点打分：

- 相关性
- 下一步价值
- Skill 一致性
- 自然性
- 多样性

线上建议监控：

- 推荐点击率
- 推荐采纳率
- 采纳后的有效追问转化率
- 用户手改率
- 链路时延

## 7. 演进路线

### Phase 1

- 直接生成式推荐
- Skill 偏好约束
- 记忆增强
- 轻量后处理

### Phase 2

- 逐步补充高价值结构化字段
- 引入轻量 rerank 规则
- 优化过滤质量和可解释性

### Phase 3

- 基于点击和采纳日志优化推荐
- 引入更细粒度的多样性控制
- 做跨 Skill 个性化优化
## 8. 基于裁判 Prompt 的准确率评测

为了让该评测集真正评测“当前算法的准确率”，当前版本新增两层可配置模型：

- 参测模型：基于核心推荐 Prompt 生成 Top3
- 评测模型：基于统一评测 Prompt 判断 Top3 是否命中 `gold_questions`

主指标定义为：

```text
Top3Accuracy = 语义命中条数 / 3
```

评测链路约定：

1. 对数据集每个样本，使用参测模型生成 Top3。
2. 将 `gold_questions` 与模型生成结果一并送入评测 Prompt。
3. 由评测模型输出 `matched_count` 和 `top3_accuracy`。
4. 按样本、Skill、单意图/多意图分别汇总平均 `top3_accuracy`。

命令行示例：

```bash
python scripts/evaluate_recommendation.py --dataset eval/recommendation_eval_dataset.json --config config/runtime_config.json
```

如果暂时没有在线模型，也支持兜底模式：

```bash
python scripts/evaluate_recommendation.py --dataset eval/recommendation_eval_dataset.json --generator-mode fallback --judge-mode heuristic
```

推荐与评测共用一份运行时 JSON 配置：

- `config/runtime_config.json`

其中可以集中配置：

- 生成模型名称
- 裁判模型名称
- API Key / API Key 环境变量
- Base URL
- 温度和最大输出 token

辅助指标仍保留用于诊断：

- `Hit@3`
- `Recall@3`
- `DimensionCoverage`
- `SkillConsistency`
- `DuplicateRate`
- `DisallowedRate`
