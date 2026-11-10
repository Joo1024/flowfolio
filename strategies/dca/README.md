# 普通定投

每月首次观察及正向现金流到账时，将全部 invest 预算投入 symbol（默认 EQUITY）。
月初观察无现金不会阻止月中入金后的投资；同月无新入金的后续观察不重复下单。
碎股尾差留待下月或下一次入金。只使用公开 StrategyContext、OrderIntent 与 StrategyDecision。
参数在 strategy.json 定义；测试包含独立手算金标准。
