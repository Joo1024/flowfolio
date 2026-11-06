# Harbor 研究记录

参考仓库：[harbor-framework/harbor](https://github.com/harbor-framework/harbor)，
研究时 main 提交 `7367e809aeed7cc01060e6d23245fb46c3310089`。

阅读范围：README、`src/harbor/models/task/task.py`、
`src/harbor/models/task/config.py`、`src/harbor/agents/base.py`、
`src/harbor/verifier/base.py` 和 LICENSE（Apache-2.0）。

Task 类将 task.toml、instruction、environment、tests 等组织为可校验的包；
Agent 接口把环境中的执行与任务定义分离；BaseVerifier 独立返回验证结果。
本项目借鉴这些职责划分，没有复制其代码、配置字段或目录接口。

|Harbor 概念|flowfolio 对应|这里的关键约束|
|---|---|---|
|任务包|StrategyPackage|策略代码只依赖公共领域协议|
|任务/环境条件|ExperimentCase|数据、投入、费用与策略独立|
|agent 的执行职责|BacktestRuntime|原生推进时间、执行、记账|
|agent adapter|RuntimeAdapter|对接引擎，不重写每个策略|
|trial|一次 run|包×案例×兼容后端|
|verifier|独立金融/协议验证|重建账本、校验现金流调整收益|

与题目评分不同，金融结果必须区分本金、收益、费用和内部标签转移。
因此协议先规定执行时序和现金预算，再开放后端；验证失败不会靠更高收益掩盖。
