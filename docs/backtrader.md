# 为什么选择 Backtrader

采用 `backtrader==1.9.78.123`。它具有成熟的事件循环、原生市价订单、现金账户、
持仓、入金和可定制佣金接口，纯 Python 依赖轻，适合先验证共同执行语义。
GPLv3+ 与本项目选择的 GPL-3.0-or-later 相容。它的发布节奏较慢，所以固定版本，
不宣称适配器已兼容其他版本或完整支持其所有功能。

与之相比，向量化研究库更容易产出收益曲线，但不利于验证逐笔原因/费用/拒绝；
支持更复杂市场模型的引擎会为首版引入额外日历和数据依赖。这里优先选择可检查
原生成交与记账的共同子集，而非功能覆盖率。

## 原生执行路径

`Cerebro → Bridge.next_open → Strategy.buy/sell → BackBroker → OrderExecutionBit → Bridge.notify_order`。
收盘现金、仓位和权益读取原生 broker。原生 `_execute`、Position.update 和账本更新
未被重写；reference runtime 从未被导入。CommissionInfo 负责协议费用报价和货币
运算边界。原生引擎可能多次询价；报价无副作用，只有原生成交会扣费用。

现金和订单数量使用与浮点常量互操作的 Decimal 标量；这改变数值表示，不替代
原生成交或账户算法。数据线仍是 Backtrader 原生浮点价格，输入精度限定4位小数。
通过 CommissionInfo 的 getvaluesize/profitandloss/getoperationcost 边界计算货币，
避免原生 float 在“成本+费用恰好等于现金”时产生负尾差并误拒绝。

实际价格读取单个原生 OrderExecutionBit，避免 executed.price 即使单次成交也做
加权平均乘除引入尾差。每个订单必须只有一个执行 bit；出现成交量部分成交直接
报错而不丢弃明细。所有真实订单各自保留原因和费用。

## 两个明确的版本适配点

1. Backtrader 的 add_cash 延迟到 `_get_value()` 才入账。适配器调用其该原生方法
   刷新待入金队列，确保原生保证金检查之前可用。此方法是私有 API，因此版本固定。
2. 空账户导致未使用的 fund-share 分析器除零。CashBroker 只初始化其辅助份额，并在
   首次非零入金时重新设定 fund 单位；不注入虚拟现金，不改变现金/持仓/权益。
   框架收益来自独立现金流调整序列，不使用该分析器指标。

原生 broker 的所有真实现金扣减、持仓变动、订单拒绝、成交通知仍由 Backtrader 执行。
已有回归测试覆盖空账户、精确预算拒绝问题、手算费用、真实 broker 调用与账户变动。

## 限制

不提供成交量 filler、滑点、挂单、复权器或真实分红处理；案例必须满足协议。
首版不可用 Backtrader 的其它功能绕过能力声明。它在 Python 3.12 的事件循环中会
发出 datetime.utcnow 弃用提示；这是上游时钟 API 提示，不参与固定案例的日期或收益。
所有现金流/价格来自案例，不调用网络或当前时间决定交易。
