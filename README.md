# AI 辅助选股框架

本框架以 `research_report/` 中的研报为候选来源，串联 PDF 解析、可选 AI 归纳、Tushare/本地行情校验、估值情景计算和技术过滤，并生成核心观察池。港股、韩股研报会被识别并列入覆盖范围提示；当前行情适配器不会把它们混入 A 股核心池。

## 运行

首次运行先安装依赖，并执行只读预检。预检检查研报文件、行情数据结构、凭证配置和上次构建结果，不会访问外部服务或改写文件：

```powershell
python -m pip install -r requirements.txt
python main.py check
```

Tushare Token 可填写在 `config.ini` 的 `[tushare] token`，也可以在 PowerShell 设置 `TUSHARE_TOKEN` 环境变量。环境变量优先。`config.ini` 含有本机凭证，已加入 Git 忽略规则。

在线构建前，在 PowerShell 设置 Tushare Token，然后构建：

```powershell
$env:TUSHARE_TOKEN = "你的 Tushare Token"
python main.py build
```

如果只使用本地行情，先运行离线预检再构建：

```powershell
python main.py check --offline
python main.py build --offline
```

模型名和推理强度默认从 `config.ini` 读取，`LLM_MODEL` 和 `LLM_REASONING_EFFORT` 环境变量可覆盖。研报 AI 归纳使用原生 Responses API，并要求模型返回带有原文页码和证据摘录的结构化结果：

```powershell
$env:LLM_API_KEY = "你的模型 API Key"
$env:LLM_BASE_URL = "你的 HTTPS Responses API 地址"
python main.py build --refresh-ai
```

`config.ini` 保存模型名、Responses API 地址、`disable_response_storage` 和模型目录等配置。API Key 可通过进程环境设置，也可在本机 `config.ini` 配置；Responses 请求默认使用 `store: false`。`config.ini` 已被 Git 忽略。自定义 `LLM_BASE_URL` 建议使用 HTTPS；明文 HTTP 默认会被阻止，只有设置 `LLM_ALLOW_INSECURE_HTTP=true` 才允许。

也可以不访问外部接口，使用本地行情和规则提取研报：

```powershell
python main.py build --offline
```

输出位于 `output/core_watchlist.md` 和 `output/core_watchlist.json`。每次成功构建还会写入 `output/build_history.json`，保存最近 365 次构建的候选状态、价格、PEG、归一化 EPS CAGR 和核心池变化；当前 Markdown/JSON 同时附有与上次构建的状态变化。报告 PDF 无法解析、未提取到文本或 AI 归纳失败时，问题会列在 Markdown 和 JSON 结果中；AI 失败会回退到原文摘录。空研报目录不会覆盖上一次观察池。AI 结果会按研报内容和模型名缓存在 `.cache/stock_picker/`；`--refresh-ai` 会重做 AI 归纳。构建不会覆盖原始 PDF 或行情 Parquet。

保存足够多的历史构建后，可用本地 Parquet 对观察池做前瞻验证：

```powershell
python main.py validate
# 或指定交易日周期
python main.py validate --horizons 5 20 60
```

验证结果写入 `output/forward_validation.md` 和 `output/forward_validation.json`。默认口径是“信号日收盘到随后第 N 个交易日收盘”，用于验证研究筛选是否具有前瞻区分度，不等同于可成交回测。若本地 Parquet 同时包含 `settings.yml` 中配置的基准指数代码，还会计算相对基准收益。

## Web 面板

在 PowerShell 启动本机 Web 面板：

```powershell
.\start_web.ps1
```

浏览器打开 `http://127.0.0.1:8000`。面板提供候选池筛选、单票逻辑/估值/技术详情、研报 PDF 查看、历史构建快照和重新构建操作。历史视图可比较核心观察池进出、候选状态变化及各次价格/PEG。刷新复用同一套 Tushare 与 AI 配置。

## 筛选约定

- 候选仅来自 `research_report/` 中识别为 A 股的研报。要扩展为全市场选股，需要另接全市场候选生成和对应市场的数据适配器。
- 默认剔除总市值低于 100 亿元、非正常上市、ST/退市标记及研报原文明确提示退市风险的公司。Tushare 的宽口径 `industry` 市值排名默认只作为研究参考，不再直接等价为“细分赛道龙头”；只有在 `settings.yml -> universe.verified_subindustry_ranks` 中人工或外部数据核验过的细分行业排名，才按前 3 名执行硬过滤。需要恢复旧行为时可把 `broad_industry_rank_mode` 改为 `hard`。
- 核心池要求订单原文引用和页码能在 PDF 对应页核实，或报告中的历史财务实绩原文已核实，或有 Tushare 正向已披露业绩；分析师预测不作实绩。若净利润同比可用，则以净利润同比是否为正作为 Tushare 实绩判断，只有净利润同比缺失时才退回营收同比，避免“营收增长但利润大幅下滑”被误判为正向实绩。EPS 预测只有在原文页码、年度和数值均核验后才进入 PEG 和三情景估值。
- 基础 PE/PB/PS 只使用正值；PEG 不再使用单个相邻年度的 EPS 跳变，而是使用已核验预测中至少 2 年、最多 3 年跨度的 EPS CAGR。三种情景使用该归一化成长率、目标年度 EPS 与配置的 PEG/折现假设；缺少足够跨度或原文证据时保持待核验，不外推。
- 技术过滤要求 MA5 > MA20 > MA60、近 5/20 日均量比处于配置区间、换手率温和活跃、近 20 日平均成交额不低于 0.3 亿元且近 5 日成交额不低于 20 日均值；高位放量滞涨和缩量上涨会阻止入池。
- 同一股票存在多份研报时，最新研报负责叙事性行业/公司/盈利逻辑，经过原文核验的订单、历史实绩和 EPS 预测会跨研报合并；同一预测年度冲突时以更新研报为准，避免旧证据因新报告未重复披露而消失。情景估值按归一化 EPS CAGR 与 PEG 区间推导情景 PE，再以折现率折回估值日。
- 核心池默认要求 PEG 不高于 2.5，且新鲜现价不高于基准情景估值上沿；这两个估值门槛和情景倍数均可在 `settings.yml` 调整。
- 本地 Parquet 含日成交量和成交额，但不含上市状态、市值、财务报表或换手率。若未配置 Tushare Token，相关硬条件会保持待验证；行情超过 3 天也不会被当作有效技术信号。

## 数据边界

Tushare 需要有效 Token，并且账号需有相应接口权限。研报 PDF 的 AI 归纳会把内容发送到配置的模型服务；未配置模型时只输出可追溯原文摘录。框架生成的是研究观察材料，不包含交易执行或自动下单。
# research

## 测试

开发与修改筛选逻辑时建议安装测试依赖并运行：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

GitHub Actions 会在 Pull Request 上用 Python 3.11/3.12 执行编译检查和测试，重点锁定 PDF 证据核验、多研报证据合并、EPS CAGR、行业排名口径、已披露业绩判断、本地日期解析和前瞻验证。
