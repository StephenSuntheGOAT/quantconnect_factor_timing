# 上传 GitHub 前的使用方法

解压交付包后，`quantconnect_factor_timing/` 就是仓库根目录。
上传该目录内的文件，根层应直接看到 README.md、main.py、timing_core.py、configs、
results 等；不要只上传压缩包，否则 GitHub 不能直接展示源码和 README。

在一个新建、可写的 GitHub 仓库中，可通过文件上传界面加入解压后的内容。
已有 Git 命令行时，也可以在这个解压目录执行：

```bash
git init
git add .
git commit -m "Archive nonlinear factor timing research and backtest diagnostics"
git branch -M main
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

将最后一行之前的仓库地址换成自己新建仓库的实际地址；命令假设当前目录还没有 git
历史和 origin。若已有 origin 或非空远程仓库，应沿用原来的 git 工作流。本次没有替
用户创建远程仓库或执行 push。

可用的仓库名：`nonlinear-factor-timing`。
可用的英文简介：`A QuantConnect SPY/CFNAI adaptation of nonlinear factor timing, with allocation controls, sign-filter ablation, and reproducible backtest diagnostics.`

README.md 是英文入口，README_zh.md 是中文说明。LICENSE_NOTICE.md 记录当前
未擅自指定开源许可证，第三方研报和数据的来源另列。研究结果按已有证据表述，不把
方法代理实验写成原论文精确复现，也不把重复下载算作新增回测。
