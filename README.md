# flowfolio

**面向持续现金流与长期资产配置的可移植策略协议和回测执行框架。**

策略作者只实现公共接口，独立策略包可在 reference 和 Backtrader 两个后端
执行。实验案例独立固定市场数据、投入、初始账户、费用和执行假设。

```text
StrategyPackage × Scenario × ExecutionEngine → Run → RunResult
```

首版聚焦定投、现金储备、回撤加仓和再平衡；不是通用量化交易平台。
Python 3.11+，核心仅标准库，第三方后端通过 extra 安装。

## 快速运行

```bash
git clone --branch v0.1.1 https://github.com/Joo1024/flowfolio.git
cd flowfolio
# 依赖、输出和测试临时目录均放在仓库外
python -m venv ../flowfolio-work/venv
source ../flowfolio-work/venv/bin/activate
pip install --no-cache-dir '.[backtrader]'

flowfolio check --strategy strategies/dca --scenario cases/hand_fees.json
flowfolio run --strategy strategies/dca --scenario cases/hand_fees.json \
  --engine reference --output ../flowfolio-work/dca-reference
flowfolio compare --strategy strategies/threshold_rebalance \
  --scenario cases/allocation_cycle.json \
  --engines reference backtrader \
  --output ../flowfolio-work/rebalance-comparison
flowfolio verify --result ../flowfolio-work/dca-reference --scenario cases/hand_fees.json
```

输出目录必须不存在，防止覆盖已有实验。省略 `--output` 输出规范 JSON。
`python -m flowfolio` 与 `flowfolio` 等价。退出码：0 成功，1 验证/比较失败，
2 无效输入、缺失依赖或 unsupported。未知能力明确拒绝，不降级运行。

## 三个独立策略包

|包|行为|主要参数|
|---|---|---|
|[dca](strategies/dca)|每月投入可用现金预算|symbol|
|[reserve_drawdown](strategies/reserve_drawdown)|留存现金，按已观察回撤分批加仓|reserve_fraction、drawdown、deploy_fraction|
|[threshold_rebalance](strategies/threshold_rebalance)|双资产配置，超阈值时卖出超配资产|equity_weight、threshold|

包内含 `strategy.json`、实现、说明和测试。拷贝整个目录即可用已安装的
SDK/CLI 加载；无需修改后端。参数用 `--param drawdown=0.2` 传入。
wheel 提供框架，策略与案例随仓库及源码发行包交付。
三个示例包均在每月首次观察和正向入金时决策，支持月中到账；储备加仓每月最多一次。

## SDK

```python
from flowfolio import Scenario, run
from flowfolio.verify import compare

scenario = Scenario.load("cases/allocation_cycle.json")
reference = run("strategies/reserve_drawdown", scenario,
                parameters={"drawdown": "0.2"})
backtrader = run("strategies/reserve_drawdown", scenario,
                 engine="backtrader", parameters={"drawdown": "0.2"})
assert reference.manifest["status"] == backtrader.manifest["status"] == "completed"
assert compare(reference, backtrader) == []
```

策略只接收只读 `StrategyContext`，返回 `StrategyDecision`（有序 `OrderIntent`/
`TransferIntent` 和新状态）。账户修改由执行引擎处理。实际成交、费用、
拒绝、剩余预算和动作原因分别记录，并在下一观察点反馈给策略。
参见 [策略开发指南](docs/strategies.md) 和 [完整 SDK 示例](examples/compare_engines.py)。

`flowfolio.scenario_generation.ScenarioGenerator` 定义生成场景的接口
`generate() -> Iterable[Scenario]`。拿到一个 `Scenario` 后，可以直接交给 `run()`。

## 执行语义与结果

第一行是 warmup；后续每期先注入现金，观察截至上期收盘的数据，本期开盘
成交，本期收盘估值。买入 amount 是**含费用的现金预算**，卖出 amount 是
股数。股数粒度 1e-8；固定费+比例费按每笔订单合并、四舍五入至分、扣一次。
现金不足按案例声明 reject 或 clip，卖出所得下批次可用。

数据使用一致的总收益调整价格，不额外计分红/拆股。每期收益为
`V_end / (V_previous + contribution_at_start) - 1`，从该序列计算 TWR
和回撤。新增投入不会直接变成投资收益。完整定义见 [协议](docs/protocol.md)。

结果文件包括 `manifest.json`、`cashflows.json`、`decisions.json`、`intents.json`、
`fills.json`、`transfers.json`、`valuations.json` 和 `diagnostics.json`。
manifest 的 `scenario` 标识场景，`execution_engine` 记录 Flowfolio 执行引擎的名称和版本，
`engine` 记录底层实现的名称和版本。manifest 还记录有效参数、费用及执行语义。
每个决策点记录更新后的策略记忆和有序动作编号。

## 评价已保存的回测

```python
from flowfolio import EvaluationSpec, RunResult, evaluate

result = RunResult.load("../flowfolio-work/dca-reference")
spec = EvaluationSpec(metrics=("time_weighted_return", "max_drawdown", "total_fees"))
report = evaluate(result, spec)
print(report.metrics)
```

可用 `baseline=` 传入同一场景下另一个策略的 `RunResult`。评价按相同的估值日期和入金计算，
返回两套指标及逐项差值。更换指标或基准时可以直接重用已保存的轨迹。

## 能力与真实第三方执行

|能力|Reference|Backtrader 1.9.78.123|
|---|:---:|:---:|
|单账户、单币种、只做多、碎股|✓|✓|
|每月现金投入、初始持仓|✓|✓|
|固定费+比例费、独立逐笔费用|✓|✓|
|内部预算转移、买入现金预算|✓|✓|
|前收盘观察→本期开盘成交|✓|✓|
|现金不足拒绝/预算裁剪|✓|✓|
|提款、多币种、卖空、杠杆|unsupported|unsupported|
|成交量部分成交、滑点、限价/挂单|unsupported|unsupported|

Backtrader 由 Cerebro 推进时间、原生 Market 订单成交、BackBroker 维护
现金和持仓；适配器没有调用 reference engine。使用其费用扩展接口和
十进制数值边界避免“刚好付得起却被浮点误判拒绝”，不增加现金、不改变容差。
详见 [完整能力矩阵](docs/capabilities.md)、[采用理由与限制](docs/backtrader.md)
及 [适配器开发指南](docs/adapters.md)。

## 测试与固定案例

```bash
pip install --no-cache-dir '.[dev,backtrader]'
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  --basetemp=../flowfolio-work/pytest \
  --junitxml=../flowfolio-work/junit.xml
python examples/compare_engines.py
python -m build --outdir ../flowfolio-work/dist
```

固定案例包括恒价无费用、可手算费用、包含跳空/回撤/卖出的资产配置周期。
符合性测试还使用固定随机种子组合不同价格、投入和费用；不是参数优化。
覆盖未来数据隔离、不可变快照、订单拒绝无副作用、预算守恒、费用舍入、
超额卖出、多笔成交、复现、原生执行证据和跨后端逐字段比较。
手算过程见 [手算金标准](docs/hand-calculations.md)。
本地完整套件 **171 项测试通过**，详见 [验证记录](docs/validation.md)。

正确性验证不以收益高低为判断标准。研究案例为合成固定数据，不代表投资建议
或真实市场表现。首版不包含 Web、机器学习、自动优化、实盘、衍生品或集群。

## 设计来源与许可证

借鉴 Harbor 的统一包、可替换执行方和独立 verifier 职责划分；这是全新实现，
没有复制 Harbor 代码或依赖其应用接口。具体研究记录见 [设计来源](docs/harbor.md)。
项目及策略使用 [GPL-3.0-or-later](LICENSE)，与 Backtrader 的 GPLv3+ 相容。
许可证、依赖和再分发说明见 [NOTICE](NOTICE) 与 [许可证说明](docs/licenses.md)。
