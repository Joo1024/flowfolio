# 策略开发指南

公共入口：`from flowfolio import Context, Decision, OrderIntent, TransferIntent, D`。
框架核心没有第三方运行时依赖。不要导入 `flowfolio.runtimes` 或引擎对象。

## 包格式

```text
my_strategy/
  strategy.json
  strategy.py
  README.md
  tests/test_my_strategy.py
```

`strategy.json` 示例：

```json
{
  "protocol": "1.0",
  "name": "my_strategy",
  "version": "0.1.0",
  "description": "每月投资固定比例的现金预算",
  "entrypoint": "strategy.py:Strategy",
  "requires": ["monthly_contributions", "cash_budget_orders"],
  "parameters": {
    "fraction": {"type": "decimal", "default": "0.5", "minimum": "0", "maximum": "1", "description": "投资比例"}
  }
}
```

```python
from flowfolio import Decision, OrderIntent

class Strategy:
    def __init__(self, parameters):
        self.fraction = parameters["fraction"]

    def decide(self, context):
        month = context.date[:7]
        if context.state.get("month") == month and context.contribution == 0:
            return Decision(state=context.state)
        amount = context.budgets["invest"] * self.fraction
        actions = (OrderIntent("EQUITY", "buy", amount,
                               "Monthly allocation", "monthly.half"),) if amount > 0 else ()
        return Decision(actions, {"month": month})
```

`Context`：date 是执行日期；market.bars 是**仅包含可见前缀**的不可变 tuple；
market.prices 为最近可见收盘价；market.at(date) 对未来日期抛 FutureDataError。
positions/cash/budgets 为本批次交易前、现金流注入后的账户；equity 按观察价
计值，contribution 为本期新投入。feedback 为上一批所有实际成交/拒绝/转移，
state 为上次 Decision 返回的递归只读状态。

状态只允许字符串、整数、布尔、Decimal、None、列表/tuple 和字典；返回列表
会冻结为 tuple。不要用实例可变成员保存跨期业务状态，不读系统时间或外部数据，
否则无法满足确定性。需要随机数的策略应将种子及状态纳入协议扩展，首版示例不用随机。

订单 amount 使用 Decimal，建议 D("100")；买入预算包含全部费用，卖出单位为股。
每条动作必须有非空 reason 和稳定 rule_id；intent_id 由 runtime 按日期/顺序分配。
可用预算标签只有 invest、reserve；TransferIntent 只能在标签间转移现金。
卖出不为同批买单提供新预算，实际费用/成交以反馈为准，不猜测执行价。

参数类型支持 string、decimal，未知参数或超出声明范围拒绝。版本变化与代码变化
都记录在 manifest，整个包内容参与哈希；缓存和隐藏文件不参与。包不允许符号链接，
策略实现是单个 strategy.py；加载不修改 sys.path。元数据、哈希和源码来自同一份
字节快照。执行直接编译该快照，不读取或写入 pyc；每次运行创建新的模块和实例，
模块在执行前注册到 sys.modules，支持 dataclass 等标准库用法，失败时恢复先前注册。
第三方 Python 包属于受信任代码，
只读 Context 保障接口纪律而非防御恶意的反射/磁盘读取。

将包目录交给 `flowfolio compare --strategy my_strategy --case ...` 即可验证两个后端。
独立测试至少包含一个可手算案例和动作原因断言。不要按后端名分支，也不要凭收益优劣
断言正确。包声明 `requires`，框架会额外从实验配置推导必要能力。

示例包在每月首次观察及正向入金时决策，避免月初空账户观察吞掉月中投入。
两者同日发生只调用一次；没有新入金的同月后续日期不重复下单。
现金储备包另行记录 deployment_month，保证同月两次决策不会重复部署储备。
