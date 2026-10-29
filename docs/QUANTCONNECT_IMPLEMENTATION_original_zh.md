# 非线性因子择时：QuantConnect 多文件纯文本版

本版使用两个可读Python文件。历史数据独立存放在CSV中，通过Object Store或用户指定的CSV直链读取。源码保留注释，没有压缩包导入、编码数据载荷、动态执行源码或隐藏数据。

依据：华安证券《非线性世界中的因子择时》（2026-10-21），尤其第5—7、11、15—18页。默认交易策略是SPY股息价格比与CFNAI的市场择时近似版。原文PMI与50个异象组合数据需要另外接入。

## 1. 在QuantConnect准备文件

在同一个Python项目的根目录创建并保存两个文件，文件名保持一致：

| 文件 | 用途 | 放置位置 |
|---|---|---|
| main.py | 算法入口、行情读取、月度交易 | 项目代码 |
| timing_core.py | 回归、VAR、动态组合、时间对齐 | 与main.py同级的项目代码 |
| macro_cfnai_REVISED_DEMO.csv | CFNAI月度发布记录近似数据 | Object Store或HTTPS CSV地址 |
| rf_tb3ms_REVISED_DEMO.csv | TB3MS月度利率近似数据 | Object Store或HTTPS CSV地址 |

main.py通过普通的from timing_core import导入模型。两个代码文件均低于32,000字符，不需要生成或合并单文件。不要把CSV内容粘贴进Python文件。

如果项目里留有旧版embedded_macro.py、build_main.py或qc_adapter.py，删除这些旧文件。本版不使用它们。可以保留项目自带的research.ipynb。

测试、说明、研究脚本不需要全部放进交易项目；下载包只用于解压获取文件。

## 2. 数据接入：选择一种方式

### Object Store

QuantConnect官方文档说明Object Store需要付费组织。已有该权限时：

1. 打开Algorithm Lab的Organization > Object Store。
2. 点击Upload，上传examples目录中的两个CSV。
3. 保持原文件名，并检查上传后的实际key。

默认查找以下key，也会尝试在前面添加项目ID/：

```text
macro_cfnai_REVISED_DEMO.csv
rf_tb3ms_REVISED_DEMO.csv
```

如果上传到其他目录，在项目Parameters中设置macro_key、rf_key为各自完整实际key。无需FRED API key，也不需要算法向FRED下载数据。文件不存在或为空时，错误会指出具体key。

### CSV直链

没有Object Store权限时，将这两个CSV放在你有权使用、QuantConnect可访问的HTTPS数据地址，然后设置：

| 参数 | 值 |
|---|---|
| macro_url | 宏观发布记录CSV的直接HTTPS地址 |
| rf_url | 利率发布记录CSV的直接HTTPS地址 |

地址应直接返回CSV文本，而不是登录页、预览页或下载确认页。本包没有替你公开托管数据，也没有附带虚构的可用地址。

使用URL时，将对应的macro_key或rf_key清空。显式填写的key优先于URL。自有PMI文件读取失败时，不会悄悄改用CFNAI。URL返回空白或HTML时会报出清楚的错误，不会伪造或补零数据。

完成代码和数据接入后再运行回测；单独复制两个Python文件仍不足以产生有效宏观策略回测。

## 3. 默认策略与参数

- 交易标的：SPY；剩余资金配置短期国债ETF BIL，保留小额现金。
- 估值：log(过去12个完整日历月的每股现金股息 / 上月末SPY价格)。
- 状态：上月末可用的CFNAI。
- 预测：下一月市场超额收益。
- 估计：默认240个已完成月度样本对，滚动OLS。
- 交易：每月首个交易日美东10:00，用分钟行情执行。
- 区间：2020-01-01至2025-12-31，初始资金100万美元。
- 风险参数：gamma=3、lambda=300、年贴现率2%。
- SPY权重限制为0%—99%，从现金开始渐进建立仓位。

默认行情从1998年起覆盖；先准备12个月股息和20年训练窗口，再从2020年交易。提前起点可能因样本不足而等待。

收益使用原始价格和现金股息，避免复权价格再加股息的重复计算。代码固定使用SPY；遇到拆股会停止。训练中的无风险收益由此前可用的TB3MS年化报价近似换算，既不是原文精确的无风险收益，也不等于实际BIL收益。

| 参数 | 默认值 | 含义 |
|---|---|---|
| model | interaction | 选择预测模型 |
| fixed_weight | 0.55 | model=fixed时的SPY固定目标/渐进目标 |
| fixed_entry | immediate | model=fixed时选择immediate或gradual |
| start / end | 2020-01-01 / 2025-12-31 | 回测区间 |
| window | 240 | 训练样本对月数 |
| expanding | false | true改用扩展窗口 |
| economic_constraints | true | 符号不符时回退历史均值 |
| gamma | 3 | 风险厌恶 |
| lambda | 300 | 动态调仓惩罚，不是手续费率 |
| var_cap | 0.995 | VAR特征根限制；0关闭 |
| max_weight | 0.99 | SPY权重上限，最大0.995 |
| slippage_bps | 1 | 固定比例成交滑点，单位bp |
| macro_key / rf_key | 对应CSV文件名 | Object Store完整key |
| macro_url / rf_url | 空 | 用户指定的HTTPS CSV直链 |

model可以选择mean、linear、bivariate、interaction，依次为历史均值、估值、估值+状态、估值+状态+交互项。另可选择fixed运行固定仓位对照。保持其他参数相同，分别运行比较组合表现。

SPY是净值基准；一步预测OOS R²的基准是历史均值。历史均值模型不是SPY买入持有，R²也不是策略收益率。

旧版macro_source和save_logs不再使用。本版只输出配置、仓位和预测质量汇总，不向源码、图表或CSV日志输出底层行情及宏观数据值。

## 4. 动态组合与成本

```text
r[t+1] = b0 + bx*x[t] + bs*s[t] + bxs*x[t]*s[t] + error
f[t+1] = c + A*f[t] + epsilon
z(f) = [1, f1, ..., fk, f1^2, f1*f2, ..., fk^2]
E[z(f[t+1]) | f[t]] = T*z(f[t])
aim = inv(gamma*Sigma)*C*(1-q)*inv(I-q*T)*z(current_state)
w = (1-kappa)*previous_weight + kappa*aim
```

q=a/(gamma+a)，kappa=a/lambda，a来自研报Bellman方程的正根，默认kappa约为0.09434。完整二次状态保留平方项和交互项，目标组合同时考虑估值及经济状态的持续性。

仓位截取、VAR稳定化、协方差特征值下限是工程调整，不是原文有约束问题的最优解。主程序采用账户实际SPY权重；IB保证金账户模型处理结算，但目标总敞口低于100%。

QuantConnect主程序净值扣除券商手续费和设定滑点。lambda决定调仓速度，不会再按二次惩罚扣一次账户资金。研究模块另按研报二次成本计算净收益；两者成本口径不同。

## 5. 数据格式与复现边界

演示CSV从FRED网站直接获取，获取日2026-10-28，观测期截至2026-09。它们不是从QuantConnect提取的数据。格式为：

```text
period,available_at,value
```

实际列顺序可不同。period为观测月份，available_at为该版本真正可用的时刻，value为发布值；无时区时间按纽约时间解释。

演示文件假设观测月后第2个月1日可用，这不是实际发布时间。两个月延迟不能消除历史修订偏差。严格复现需要真正的PMI或CFNAI历史发布/版本记录；修订应作为同一观测月、不同可用时刻的独立行。

算法按历史月末的信息集选择最新可用观测，不用下月发布数据改写上月信号。估值中的财报披露时点也需要原数据保证。

默认只是SPY/CFNAI近似复现。原文更长市场样本、PMI和50异象原始数据没有随研报提供，不能据本包声称重现原文收益和显著性。

## 6. HML与PC1研究

paper_research.py和timing_core.py可用于本地或QC Research，计算因子损益，不把HML/PC1当成上市证券下单。

月度面板需要date和rf，以及所选目标的收益和估值列：market_excess/log_dp，hml/value_spread，或pc1_return/pc1_log_bm；PCA预处理还可能需要market_log_bm。收益均为月度小数，HML多空收益不要再次减RF。

```bash
python -m pip install -r requirements.txt
python tests.py
python paper_research.py --panel monthly_panel.csv --macro pmi_releases.csv --target hml --out hml_output
```

构建PC还需日期完全匹配的50异象多空收益、对应log-BM面板，列为date,a01,...,a50。不能用50只个股替代，也不能直接对可能为负的多空BM差取对数。

```bash
python paper_research.py --panel monthly_panel.csv --macro pmi_releases.csv --target pc1 --anomaly-returns anomaly_returns.csv --anomaly-logbm anomaly_logbm.csv --out pc1_output
```

PCA只用初始240个月估计Beta、中性化、标准化并提取前4个主成分，之后冻结。相同组合权重映射估值。已有作者预处理估值时使用--bm-preprocessed，或直接提供作者的PC1列。作者原始口径优先于摘要近似实现。

研究目标还可选market或joint，支持扩展窗口、关闭经济约束。输出OOS R²、Clark-West近似HAC检验、毛/净CER等；没有实现原文ΔCER的平稳bootstrap显著性检验。研究模块不模拟券商持仓漂移、融资、借券及底层股票成交。

本地CSV输出用于你自行提供的数据；使用QuantConnect数据时在云端分析，遵守平台数据使用规则，不移出云端。

## 7. 下一轮：固定仓位对照与诊断（2026-10-29）

本轮问题是：linear的较好夏普是否来自择时，还是主要来自较低股票仓位和空仓启动。不要用同一段2020—2025样本反复挑参数来声称发现了新规律。

只替换QuantConnect中的main.py，保留已有timing_core.py和两个CSV链接。起止日期仍为2020-01-01、2025-12-31，其余模型参数使用上一轮设置。先按下表运行三次：

| 次序 | model | fixed_weight | fixed_entry | 目的 |
|---|---|---|---|---|
| 1 | fixed | 0.55 | immediate | 首月配置SPY目标55%，以后每月再平衡到相同比例 |
| 2 | fixed | 0.55 | gradual | 以原动态策略相同的调仓速度，从空仓向55%目标靠拢 |
| 3 | interaction | 0.55 | immediate | 检查经济符号约束回退及仓位上限影响；fixed参数在此模式不参与交易 |

两个fixed对照都使用同一SPY/BIL、初始资金、首个交易日10:00调仓、IB手续费和滑点模型。BIL目标=99.5%-SPY目标，目标之外的资金留在现金中；实际成交仓位还受平台现金缓冲、费用和整股取整影响。

immediate与gradual首月配置不同是有意设计的。gradual每个月都按(1-kappa)*实际SPY权重+kappa*0.55计算，默认kappa约为0.09434；后续也继续这样调整，并非最终切换成另一个规则。两个fixed模式不拟合收益模型，也不读取宏观CSV。原四种模型的预测和交易规则没有改动。

55%是参考已看过的linear平均目标仓位选择的近似对照，不是事先锁定的独立样本外检验，也不是严格波动率或Beta匹配。即使linear战胜这两个对照，也只能先视为当前样本中的择时证据。

回测后读取：累计收益、年化收益、Sharpe Ratio、最大回撤、Total Fees，以及新增运行统计：

- Avg SPY target：月度SPY调仓目标的简单平均。
- Cap touches：未截取目标达到或超过max_weight的次数/调仓次数；默认上限99%。
- Mean fallback：所选预测模型因经济符号约束而回退历史均值的次数/拟合次数。
- OOS R2 linear/bivariate/interaction、Forecast n：三个模型共同评分样本上的预测表现。

日志中的Fit diagnostics会在任意一次非fixed回测里，分别汇总三个模型的mean fallback及VAR stabilized次数。前者说明实际有多少次没有使用该回归信号，后者说明VAR特征根限制介入了多少次。这些计数包括最后一次尚未评分的预测，通常为72次拟合；OOS R2仍在下一月调仓时结算，2020—2025完整回测通常为71个月，与上一轮口径相同。

如果linear与固定或渐进对照接近，就尚未分离出明显择时贡献。若interaction大量回退均值，先检查模型系数符号与宏观代理含义；若大量触及上限，说明最终持仓受约束影响明显。统计显著性需要额外检验，不能从一点收益差直接推断。

这一轮结束后，严格复现的优先事项是补齐真正的PMI历史发布版本及原始因子数据。修订值CFNAI代理结果只能作为方法演示与代码检验。扩大回测区间前必须准备足够训练历史；仅把start改到2008年，不能凭空获得240个月训练对。

## 8. 验证与官方参考

26项本地测试通过，覆盖动态模型、时间对齐、股息、PCA、Object Store默认/显式/项目目录key、缺失文件提示、CSV直链、空响应检查、固定仓位与渐进建仓对照、诊断统计，以及项目代码的大小与纯文本检查。

另将本次改动前后的算法入口放在相同模拟输入下比较：四种原模型共24次月度调仓的目标权重、预测值及评分记录一致。这项检查验证入口改动未改变原策略计算，不代表真实行情或成交已经验证。

没有连接你的QuantConnect账户，未提交云端回测。本地测试不代表平台保存、数据权限和LEAN集成运行已经全部验证。

官方文档（2026-10-28至29核对）：

- https://www.quantconnect.com/docs/v2/cloud-platform/projects/files
- https://www.quantconnect.com/docs/v2/cloud-platform/object-store
- https://www.quantconnect.com/docs/v2/cloud-platform/security-and-ip
- https://www.quantconnect.com/docs/v2/writing-algorithms/trading-and-orders/position-sizing
- https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/asset-classes/us-equities
- https://doi.org/10.1016/j.jempfin.2026.101767
