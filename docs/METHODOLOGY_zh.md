# 模型与代码说明

## 四个收益预测模型

令 x 为标准化估值，s 为标准化宏观状态；预测目标是下一月 SPY 超额收益。
标准化参数、回归系数和状态动态都仅从当前训练窗口中估计。

| model | 预测形式 | 作用 |
|---|---|---|
| mean | b0 | 历史均值 |
| linear | b0 + bx*x | 估值预测 |
| bivariate | b0 + bx*x + bs*s | 估值和宏观加法预测 |
| interaction | b0 + bx*x + bs*s + bxs*x*s | 宏观状态改变估值斜率 |

economic_constraints=true 时，非均值模型要求 bx>0；interaction 还要求 bxs<0。
不满足便将该收益方程改为当前训练样本历史均值，而不是保留违反符号的系数。
约束作用在标准化回归表示上。这是预设经济符号筛选，不是参数自动搜索。

## 动态目标组合

状态满足 VAR(1)：`f_next=c+A*f+epsilon`。算法使用完整二次状态
`z=[1,x,s,x²,x*s,s²]`，即便收益方程只选交互项，也保留平方项，因为状态转移会产生
它们。`moment_transition` 构造满足 `E[z_next|f]=T*z(f)` 的矩阵。

`TimingModel` 用二次状态的折现 resolvent 计算长期 aim，再按
`w_new=(1-kappa)*w_previous+kappa*aim` 平滑调仓。默认 gamma=3、lambda=300、
年贴现率 2%，kappa 约 0.09434。并非先算一次短期仓位再简单移动平均。

算法入口使用账户实际 SPY 权重作为 w_previous，将新目标截在 0—99%，BIL 目标为
99.5%-SPY目标，余下留现金。平台还会施加现金缓冲、整股和费用处理。
SPY 为分钟订阅；月初首个交易日美东开盘 30 分钟后（通常 10:00）调仓。

## 训练与预测对齐

历史 SPY 从 1998 年开始准备，先构造 12 个月股息和 240 个完整训练对。形成月份为 t
时，只用已经完成的 `(state_s, return_s+1)` 对；不使用下一月才可获得的观测。
扩展窗口可通过 expanding=true 选择，当前导出实验使用默认滚动窗口。

OOS R² 相对历史均值预测：`1-SSE_model/SSE_mean`，与买入持有收益无关。
当前 QC 评分在下次调仓结算，因此 72 次月度拟合通常只有 71 个已评分预测。

## 成本和研究模块

lambda 是 Bellman 动态策略中的二次调仓惩罚系数，不是 300bp 手续费。
main.py 通过平台券商手续费和 1bp 滑点模型扣除账户成本，没有再次按理论二次成本
扣减资金。paper_research.py 则按研报式二次成本计算研究损益，两个口径不能混为一谈。

paper_research.py 支持 market、hml、pc1、joint；PCA 在初始窗口估计后冻结，采用
同一线性映射构造收益和估值代理。接口还输出 OOS R²、近似 Clark-West HAC 检验、
CER 等。它没有实现研报 ΔCER 的平稳 bootstrap，也不模拟底层股票交易、借券和融资。
现有归档没有为这些未提供数据的目标生成经验结果。

## 代码地图

| 文件 | 功能 |
|---|---|
| main.py | QuantConnect 入口、行情和股息、CSV 接入、调仓、统计 |
| timing_core.py | ReleaseTape、收益面板、OLS/VAR、二次状态、动态权重 |
| paper_research.py | 研究面板、冻结 PCA、HML/PC1/联合模拟、研究指标 |
| tests.py | 26 项本地数值和时间完整性测试，含 QC 服务模拟 |
| scripts/summarize_results.py | 解析原回测导出，生成汇总、序列、订单和图 |
| scripts/verify_archive.py | 检查交付文件、参数与已有回测结果一致性 |

VAR 特征根截断、协方差特征值下限、持仓裁剪都是实际实现调整，不等于原文有约束
组合问题的精确最优解。研报与本项目采用的数据、样本和资产有差异。
