# 许可证说明

项目、示例策略、固定合成案例和文档使用 **GPL-3.0-or-later**；完整 GPL v3
文本见根目录 LICENSE。后续版本选择权在 NOTICE 和包元数据中明确。

|组件|作用|许可证|分发方式|
|---|---|---|---|
|flowfolio|领域协议、SDK/CLI、reference、adapter|GPL-3.0-or-later|本仓库|
|Backtrader 1.9.78.123|可选真实执行后端|GPLv3+|`[backtrader]` extra，单独安装|
|pytest|开发测试|MIT|`[dev]` extra|
|build / hatchling|构建工具|MIT|开发/构建依赖|
|Harbor|概念参考|Apache-2.0|不包含源码，不作为依赖|

核心运行不依赖 Backtrader。选择 GPL 项目许可证是为了使包含该适配器的整体发布
具有一致、明确的开源义务；不要将项目或可选组合宣称为 MIT/Apache 的宽松许可产品。
分发修改版或组合软件时应保留版权与许可证，并遵守 GPL 对相应源码的要求。
仓库没有随附第三方下载产物；各依赖的原版权信息保留在其自身安装包中。
