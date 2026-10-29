# 在 QuantConnect 重新运行

本说明针对已归档的云端项目结构。把源码和 CSV 分开保存；不要把数据压缩、编码或
粘贴进 Python。完整交付 ZIP 用于下载和 GitHub 保存，不是算法导入方式。

## 1. 准备两个代码文件

在同一个 Python 算法项目根目录中保存 `main.py` 和 `timing_core.py`，保留文件名。
主程序用普通 import 载入第二个文件。其他研究脚本和归档结果无需放入交易项目。

## 2. 接入两个 CSV

选择项目有权访问的一种方式：

- Object Store：上传 examples 中两个 DEMO CSV，设置 macro_key、rf_key 为实际 key；默认寻找原文件名，也尝试项目 ID 前缀。
- HTTPS 直链：设置 macro_url、rf_url 为直接返回 CSV 文本的地址。对应 macro_key、rf_key 清空，因为显式 key 优先。

现有导出记录的直链指向用户之前的公开 qc-macro-data 仓库；没有在本次整理中检查
或依赖其今后可用性。复现时可以使用这两个归档 CSV 上传到自己的数据位置。
固定 A/B 对照完全不读取这两个文件。

## 3. 设置实验参数

`configs/parameters/*.json` 每个文件都是当次导出的参数键值。可照其逐项填写项目
Parameters；这不是上传后会被 main.py 自动读取的本地配置。main.py 使用平台
get_parameter 读取设置。每次实验只保留该配置所需参数，避免前一轮额外参数残留。

建议按记录重建：

| 记录 | model | 额外关键参数 |
|---|---|---|
| model_mean | mean | economic_constraints=true |
| model_linear | linear | economic_constraints=true |
| model_bivariate | bivariate | economic_constraints=true |
| model_interaction / C | interaction | economic_constraints=true |
| A | fixed | fixed_weight=0.55, fixed_entry=immediate |
| B | fixed | fixed_weight=0.55, fixed_entry=gradual |
| 最后一次消融 | interaction | economic_constraints=false |

全部设 start=2020-01-01、end=2025-12-31。其他主要默认参数记录在
`configs/experiments.json`。若想复现有约束模型，尤其要清掉最后一次留下的 false。
非 fixed 模型忽略 fixed_entry/fixed_weight；所以 C 虽保留 gradual 字样，仍使用自己的
动态策略，而不是固定 55% 的建仓规则。

## 4. 检查输出

先看运行状态是否完成，再对照累计收益、CAGR、Sharpe、Drawdown、Total Fees。
有诊断的版本还应有 Avg SPY target、Cap touches、Mean fallback、Forecast n 和
OOS R²。完整 2020—2025 设置通常应有 72 次调仓、71 个已评分预测。

源码默认 model=interaction、economic_constraints=true，最后一次消融不会自动成为
默认策略。若开始日期过早或缺历史训练对，程序会等待，不会凭空产生训练样本。

## 5. 本地研究接口

先安装 requirements.txt。自备完整月度样本后可以运行：

```bash
python paper_research.py --panel monthly_panel.csv --macro pmi_releases.csv --target hml --out hml_output
python paper_research.py --panel monthly_panel.csv --macro pmi_releases.csv --target pc1 --anomaly-returns anomaly_returns.csv --anomaly-logbm anomaly_logbm.csv --out pc1_output
```

这里的输入名指自备真实数据，不是 examples 中只有表头的 schema 文件。
仅查看已归档结果不需要 QuantConnect 账户或联网，详见根目录 README。
