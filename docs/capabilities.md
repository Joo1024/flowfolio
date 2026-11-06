# 能力矩阵（protocol 1.0）

Reference 0.1.0 和 Backtrader adapter 0.1.0 的共同能力如下。
完整机器可读列表：`flowfolio capabilities`。

|能力标识|Reference|Backtrader|范围|
|---|---|---|---|
|single_account|支持|支持|一个现金账户|
|single_currency|支持|支持|单一案例货币，无兑换|
|long_only|支持|支持|持仓/现金非负，不融资|
|fractional_shares|支持|支持|1e-8 股粒度|
|monthly_contributions|支持|支持|显式日期，每月最多一笔非负投入|
|initial_positions|支持|支持|首行价格估值，非负初始仓位|
|fixed_fee|支持|支持|每笔非零成交固定收费|
|proportional_fee|支持|支持|实际成交名义金额×比例|
|budget_transfers|支持|支持|invest/reserve 标签间转移|
|cash_budget_orders|支持|支持|买入含费用预算，卖出股数|
|previous_close_next_open|支持|支持|明确一观察期滞后|
|total_return_adjusted|支持|支持|外部提供已一致调整的 open/close|
|insufficient_reject|支持|支持|预算不足全单拒绝|
|insufficient_clip|支持|支持|按可用现金裁剪、partial反馈|
|deterministic|支持|支持|相同输入、版本及环境|
|multi_currency / withdrawals|拒绝|拒绝|预留命名，不做模拟|
|short_sales / volume_partial|拒绝|拒绝|不做卖空、成交量部分成交|
|slippage / live_trading|拒绝|拒绝|零滑点假设、无实盘|

未知能力默认拒绝；不能因策略忘记声明而忽略案例里的费用/现金流/执行假设。
实验字段未知时 schema 校验失败，合法但未支持的执行选项返回 unsupported。
每个后端可以声明更窄的集合，兼容检查在策略模块导入前完成。

不支持多个账户、利息、税、原始价格的分红拆股处理、限价挂单、异步标的日历、
优化、提款、杠杆、衍生品。不能用 reserve 标签冒充第二个账户。

价格最多4位小数、财务输入最多8位小数、账户值≤1e9；超界报错。计算使用固定
40位 Decimal 上下文，不受调用者设置影响；运行后恢复调用者上下文。
比较货币/股数绝对容差1e-7、收益1e-10；无相对容差，也不按收益好坏验收。
