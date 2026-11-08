# 执行器与适配器开发指南

`Simulator` 通过 `execute(run)` 推进回测。Reference 是原生执行器，位于
`flowfolio.simulators.reference`。Backtrader 的数据和订单转换位于
`flowfolio.adapters.backtrader`，通过同名执行器模块接入。

## 接入执行器

在 `flowfolio.simulators` 下新增 `foo.py`，在模块内定义一个可无参构造的具体
`Simulator` 子类。执行器提供 `name`、`version`、`engine`、`capabilities` 和
`execute(run)`。SDK 的 `simulator="foo"` 和 CLI 的 `--simulator foo` 按需加载该模块。
存在模块但缺少依赖，或具体执行器数量不为一时，加载器报告对应错误。

SDK 也接受 `run(package, scenario, simulator=MySimulator())`。外部发行包可以声明：

```toml
[project.entry-points."flowfolio.simulators"]
my-engine = "my_package.adapter:MySimulator"
```

CLI 可用 `--simulator my-engine` 加载该插件。预检先确认协议支持场景和策略所需的能力，
再与执行器声明对照。执行器可实现 `availability() -> list[str]` 报告依赖或版本问题。
预检在加载策略代码之前运行。

## 生命周期

|调用|时机与职责|
|---|---|
|run.scenario|读取初始账户、行情、入金、费用和执行配置|
|run.initial(cash, positions, equity)|报告首行的原生初始账户和估值|
|run.flow(index)|读取下一期的外部投入，由执行器通过原生接口入账|
|run.open(index, cash, positions)|入账后传入账户快照，触发策略并获得 PreparedOrder 列表|
|run.record_fill(Fill(...))|从原生引擎读取实际成交股数、价格和费用，按原意图编号报告|
|run.native_reject(id, reason)|报告原生订单拒绝，已准备订单意外被拒时结果为 invalid|
|run.close(cash, positions, equity)|完成当期订单反馈后，提交原生账户和收盘估值|

`Run` 持有本次执行的进度、预算、策略记忆、待处理订单、反馈和流水。
`protocol` 校验回调顺序、行情可见性、预算转移、订单资格、费用和反馈。
执行器退出后，`Run` 检查最终估值，调用独立 verifier 并返回 `RunResult`。

每条 PreparedOrder 包含原始意图、编号、股数、执行价格、费用、预留金额和裁剪标记。
适配器将请求送到第三方原生订单入口，由原生 broker 成交、扣费并更新现金和持仓。
当期开盘价用于计算可成交的股数，策略的行情快照只含此前可见的行情。

## 验证门槛

用三个独立策略运行固定案例，覆盖费用半分舍入、刚好花完预算、初始仓位、卖出清仓、
零初始账户、同日多单、跳空和拒绝。逐笔比较现金流、决策、意图、实际成交、账户和估值。
金额和股数的绝对容差为 1e-7，收益为 1e-10，费用和执行价必须完全相等。

`tests/test_conformance.py::test_native_engine_executes_and_accounts` 直接监测第三方引擎
成交时的现金扣减和持仓变化。新增适配器需提供同类测试，以及引擎版本和许可证记录。
