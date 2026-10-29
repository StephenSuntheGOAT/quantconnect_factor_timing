# 非线性因子择时：代码、实验与回测归档

[English](README.md) · [完整结果表](results/summary.md) · [调参/实验过程](docs/EXPERIMENT_LOG_zh.md)

本项目基于华安证券 2026-10-21《非线性世界中的因子择时》，用 SPY 股息价格比与
CFNAI 做市场择时代理实验，剩余资金主要配置 BIL。实现了均值、线性、双变量和交互
四个模型，再加入固定仓位对照与经济符号约束消融。此阶段已在消融实验后停止。

这是方法实现、代理回测与诊断项目。原文的 PMI 历史发布版本和 50 个异象面板缺失，
因此不能把本项目描述为原论文结果的精确复现。所有已拿到的结果，包括表现较弱的
模型和重复下载，都保留并标清来源。

## 1. 主要结果

统一区间：2020-01-01 至 2025-12-31；初始资金 100 万美元；240 个完整月度训练对。
平台统计直接来自原始 JSON，没有为 README 重新选择 Sharpe 的计算口径。

| 实验 | 累计收益 | 年化收益 | Sharpe | 最大回撤 | 平均 SPY 目标 |
|---|---:|---:|---:|---:|---:|
| mean | 108.782% | 13.043% | 0.527 | 26.0% | 85.98% |
| linear | 101.758% | 12.400% | 0.740 | 11.4% | 54.01% |
| bivariate | 73.406% | 9.601% | 0.558 | 11.6% | 44.15% |
| interaction，有约束 | 115.912% | 13.676% | 0.577 | 26.0% | 83.68% |
| A：固定 55%，立即建仓 | 73.080% | 9.566% | 0.435 | 19.2% | 55.00% |
| B：固定 55%，渐进建仓 | 66.597% | 8.872% | 0.482 | 13.9% | 47.99% |
| C：interaction 重跑诊断 | 115.912% | 13.676% | 0.577 | 26.0% | 83.68% |
| interaction，关闭约束 | 68.023% | 9.027% | 0.543 | 11.5% | 38.43% |

![净值、目标仓位与回撤](results/figures/overview.png)

平均 SPY 目标是 72 次月度目标的简单平均，不是每日实际仓位。C 与最初 interaction
的组合统计一致，不能当作独立新增证据。最后两个 Snake JSON 完全相同，也不能算
两次实验：共 9 份下载、8 次云端运行、7 套不同的有效交易设置。

当前样本中 linear 的风险收益指标较好，但其预测 OOS R² 为 -0.7606%。有约束的
interaction 有 52/72 次拟合回退历史均值、34/72 次触及 99% 仓位上限；关闭约束后
预测 OOS R² 下降到 -20.7547%。现有结果不足以证明交互项带来稳定增量预测优势。
55% 对照是在看过 linear 仓位后选取，全部样本已经查看，不能声称独立留出验证成功。

## 2. 文件说明

| 文件/目录 | 用途 |
|---|---|
| main.py | QuantConnect 算法入口、行情、月度交易、诊断 |
| timing_core.py | OLS、VAR、二次状态、动态目标权重和时间对齐 |
| paper_research.py | market/HML/PC1/joint 研究接口与冻结 PCA |
| tests.py | 26 项原有本地测试 |
| examples/ | 两个宏观数据快照、来源、行数范围、研究输入格式 |
| configs/parameters/ | 每次回测实际导出的参数，可逐项填入 QC Parameters |
| configs/experiments.json | 模型顺序、默认值、无效参数和重复结果登记 |
| results/raw/ | 所有原始 JSON；只调整部分文件名，文件内容不变 |
| results/summary.csv / summary.md | 结果总表，CSV 使用明确单位列名 |
| results/derived/ | 各次导出的净值、目标仓位、订单和回撤 CSV |
| results/figures/ | 回测总览图 |
| results/evidence/ | 4 张原始日志图片 |
| results/evidence_transcription.json | 明确标为人工抄录的截图诊断数据 |
| results/source_inventory.json | 原文件名、归档路径、SHA-256 映射 |
| docs/EXPERIMENT_LOG_zh.md | 实现修正、四模型、A/B/C、最终消融与结论 |
| docs/METHODOLOGY_zh.md | 模型公式、代码职责和成本口径 |
| docs/DATA_AND_REPRODUCIBILITY_zh.md | 数据字典、缺失内容和复现边界 |
| docs/QUANTCONNECT_RUN_zh.md | 云端重新运行说明 |
| docs/GITHUB_UPLOAD_zh.md | 上传仓库的方法和可用简介 |
| references/ | 用户提供的原研报及出处 |
| archive/ | 打包前源码包、原验证说明、本地环境记录 |
| manifest.sha256 | 交付文件完整性快照 |

## 3. 本地查看和重建结果

进入解压后的 quantconnect_factor_timing 目录，使用 Python 3.12 环境：

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-analysis.txt
python tests.py
python scripts/summarize_results.py --plots
python scripts/verify_archive.py
```

不画图时仅需 requirements.txt，执行汇总脚本不加 --plots 即可。精确本地直接依赖版本
记录在 requirements-reproduction.txt；云端 LEAN 版本见导出元数据。重建图的二进制
可能随运行环境元数据变化，校验原始交付快照应先运行 verify_archive.py。

汇总与画图完全离线，只读现有导出，不会重新回测或下载行情。净值取图表 OHLC 的
收盘值，原始 JSON 中所有其他字段仍完整保留。预测记录没有逐月导出，因此不编造
逐月预测 CSV。

## 4. 在 QuantConnect 重新运行

在同一个项目根目录创建 main.py 和 timing_core.py。通过 Object Store 或自己的
HTTPS 直链接入 examples 中两个 CSV，再按 configs/parameters 中的一组设置
Parameters。JSON 仅记录参数，不会被算法自动读取。详细操作见云端运行说明。

main.py 的默认配置仍为 interaction + economic_constraints=true。最后一次关闭
约束的配置保存在独立参数文件，未把它擅自设为默认或宣布为最佳模型。
运行 A/B 固定对照无需宏观 CSV。直接在普通 Python 中执行 main.py 不会启动
QCAlgorithm；完整交易回测需要 QuantConnect/LEAN 行情和执行环境。

## 5. 必须保留的边界

两个 CSV 是修订后的 CFNAI、TB3MS 历史值，并人为假设两个月后可用，不能消除历史
修订偏差。它们不是原文 PMI 或真实历史发布版本。

完整 SPY/BIL 行情未单独导出，HML/PC1/50 异象输入也未提供；schema 文件只有表头。
原始 JSON 中保留了图表、订单和统计，不能把它们当作完整行情数据库。已有研究
代码不代表相关原文实验已经完成。

本次仅整理文件、生成结果分析和执行本地检查，没有新增云端回测、重新调参或向
GitHub 推送。最新策略源码保持原样；旧失败版本和每次云端源码快照未恢复。
原有测试使用最小 QC 模拟，验证范围不等于真实云端集成测试。

上传时应上传解压目录内的文件，让 README.md 直接位于仓库根层。
许可证和第三方资料来源见 LICENSE_NOTICE.md 与 references/README.md。
