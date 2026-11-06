# 现金储备与回撤加仓

每月将 invest 的 reserve_fraction 转入 reserve，剩余部分定投。
观察价较历史最高收盘价回撤达到 drawdown 时，将储备的 deploy_fraction
转回 invest 并单独下单。基础定投与加仓保留独立原因、规则和费用。
持续低于阈值时每月可以再次部署，未承诺抄底或收益优势。
