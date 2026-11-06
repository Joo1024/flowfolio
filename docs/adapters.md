# Runtime adapter 开发指南

适配器实现公共 `BacktestRuntime` / `RuntimeAdapter` 结构化协议：

```python
from flowfolio import RuntimeSession

class MyRuntime:
    name = "my-engine"
    version = "0.1.0"                 # 适配器版本
    engine = {"name": "engine-name", "version": "pinned-version"}
    capabilities = frozenset(...)    # 仅声明已实现并测试的能力

    def execute(self, session: RuntimeSession) -> None:
        ...                         # 在引擎的原生事件回调内实现下表
```

传 `run(package, case, runtime=MyRuntime())`，无需注册。分发插件可使用 entry point：

```toml
[project.entry-points."flowfolio.runtimes"]
my-engine = "my_package.adapter:MyRuntime"
```

此后 CLI 接受 `--runtime my-engine`。可选 `availability() -> list[str]`
在运行前报告缺少依赖、版本不符；不能静默 fallback。

## 生命周期

|调用|时机与职责|
|---|---|
|session.case|读取不可变的独立案例、FeeModel 与固定 bars|
|session.initial(cash, positions, equity)|首行 native 账户和 native 初始估值，一次|
|session.flow(index)|返回本期外部投入；由引擎原生现金接口注入|
|session.open(index, cash, positions)|现金注入后调用；创建前缀快照、调用策略、处理内部预算转移并准备订单|
|返回 PreparedOrder|包含 intent、intent_id、quantity、price、fee、allocation、partial；只是协议规格化的请求|
|session.fill(id, actual_quantity, actual_price, actual_fee)|实际 native 成交通知；每个订单一条，保留原始成交原因|
|session.native_reject(id, reason)|原生拒绝，明确报告；已准备订单意外拒绝会使结果 invalid|
|session.close(cash, positions, equity)|该期全部通知完成后，传入 native 账户及收盘估值|

SDK 在执行前兼容检查、实例化策略，在执行后统一计算指标并调用独立 verifier。
它负责最后调用 session.finish；适配器不要重复调用。

Session 只维护预算标签、策略状态、订单预留和报告，不持有可交易账户。
prepare/affordable_quantity 是公共执行语义的确定性规格化，并不成交、修改资金或持仓。
适配器必须将每个请求送到第三方原生订单入口，让原生 broker 扣费、更新持仓/现金；
随后从原生通知读取事实。**禁止调用 ReferenceRuntime.execute 或把已计算流水回放成另一后端结果。**

原生回测引擎需要遵循观察、现金注入、成交和估值顺序；不能把未来价送给策略。
策略可以在 next_open 回调时执行，但 Context 只包含先前收盘前缀，当前执行价
只用于适配器预算到股数的转换。任何新的挂单、成交量、滑点、提款能力都需要先定义
协议语义和 verifier，再声明支持；不可复用名称表达不同语义。

## 验证门槛

先运行三个独立策略的全部固定案例，再测试费用半分舍入、刚好花完预算、初始仓位、
卖出清仓、零初始账户、同日多单、跳空与拒绝。比较原始现金流、意图、逐笔成交、
账户、估值和指标；使用固定绝对容差，金额/股数1e-7、收益1e-10，费用/执行价完全相等。
容差不适用于隐藏不同的订单数量、状态、规则或日期。

`tests/test_conformance.py::test_native_engine_executes_and_accounts` 展示如何监测第三方
真实执行与现金变化，并令 reference runtime 被调用时直接失败。新增适配器必须提供
类似的原生执行证据，以及引擎版本和许可证记录。
