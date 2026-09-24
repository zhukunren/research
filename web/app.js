const STATUS = {
  CORE: ["核心观察", "CORE"],
  PENDING: ["待核验", "PENDING"],
  REJECTED: ["已排除", "REJECTED"],
  OUTSIDE_SCOPE: ["市场未覆盖", "OUTSIDE_SCOPE"],
};

const state = {
  data: null,
  health: null,
  history: [],
  validation: null,
  selectedCode: null,
  selectedBuildId: null,
  view: "watchlist",
  toastTimer: null,
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));
const fmt = (value, digits = 2) =>
  value === null || value === undefined || !Number.isFinite(Number(value))
    ? "待补"
    : Number(value).toFixed(digits);
const marketName = (market) => ({
  SH: "沪市", SZ: "深市", BJ: "北交所", HK: "港股", KS: "韩股",
}[market] || market || "未知");
const statusName = (status) => (STATUS[status] || ["待核验", "PENDING"])[0];
const statusTag = (status) =>
  `<span class="status-tag ${esc(STATUS[status]?.[1] || "PENDING")}">${esc(statusName(status))}</span>`;
const capText = (value) => value == null ? "待补" : `${fmt(value, 1)} 亿`;
const linkForReport = (name) => `/api/reports/${encodeURIComponent(name || "")}`;

function showToast(message, bad = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("bad", bad);
  toast.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { toast.hidden = true; }, 4200);
}

function buildTimeLabel(value) {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("zh-CN", {hour12: false});
}

function metricBox(label, value) {
  return `<div class="inline-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

function signedClass(value) {
  if (value == null || !Number.isFinite(Number(value)) || Number(value) === 0) return "";
  return Number(value) > 0 ? "positive" : "negative";
}

async function loadState() {
  const [healthResponse, dataResponse, historyResponse, validationResponse] = await Promise.all([
    fetch("/api/health"),
    fetch("/api/watchlist"),
    fetch("/api/history"),
    fetch("/api/validation"),
  ]);
  if (!healthResponse.ok || !dataResponse.ok || !historyResponse.ok || !validationResponse.ok) {
    throw new Error("服务未能读取研究数据");
  }
  state.health = await healthResponse.json();
  state.data = await dataResponse.json();
  state.history = (await historyResponse.json()).builds || [];
  state.validation = await validationResponse.json();

  if (!state.history.some((build) => build.build_id === state.selectedBuildId)) {
    state.selectedBuildId = state.history[0]?.build_id || null;
  }
  renderAll();
}

function renderStatus() {
  const health = state.health || {};
  const data = state.data || {};
  const latest = health.latest_quote_date ? `行情 ${health.latest_quote_date}` : "行情待补";
  const age = health.quote_age_days == null ? "" : ` / AGE ${health.quote_age_days}D`;
  const mode = health.market_data_mode || "未构建";

  $("top-mode").textContent = mode;
  $("top-date").textContent = health.latest_quote_date || "--";
  $("top-build").textContent = data.build_id ? String(data.build_id).slice(0, 15) : "--";

  const issues = [];
  if (!health.tushare_configured) issues.push("Tushare 未配置");
  if (!health.llm_configured) issues.push("AI 未配置");
  if (health.llm_endpoint_blocked) issues.push("AI 端点被阻止");
  if (health.quote_age_days != null && health.quote_age_days > 3) issues.push("行情过期");
  if (health.report_issue_count) issues.push(`${health.report_issue_count} 份研报异常`);

  const strip = $("status-strip");
  strip.classList.toggle("good", issues.length === 0);
  $("status-title").textContent = issues.length ? issues.join(" · ") : "研究数据链路正常";
  $("status-detail").textContent = `${latest}${age} · 最近一次成功构建 ${buildTimeLabel(health.built_at)}`;
  $("status-badges").innerHTML = [
    `<span class="mini-badge">MODE ${esc(mode)}</span>`,
    `<span class="mini-badge">MODEL ${esc(health.llm_model || "--")}</span>`,
  ].join("");

  const tushareOk = !!health.tushare_configured;
  $("conn-tushare-dot").className = `connection-dot ${tushareOk ? "ok" : "warn"}`;
  $("conn-tushare-value").textContent = tushareOk ? "ONLINE" : "MISSING";
  const aiOk = !!health.llm_configured && !health.llm_endpoint_blocked;
  $("conn-ai-dot").className = `connection-dot ${aiOk ? "ok" : "warn"}`;
  $("conn-ai-value").textContent = aiOk ? (health.llm_model || "READY") : "CHECK";
  $("conn-snapshot-dot").className = `connection-dot ${data.generated_at ? "ok" : "warn"}`;
  $("conn-snapshot-value").textContent = data.generated_at || "EMPTY";

  if (state.view === "watchlist") {
    $("page-subtitle").textContent =
      `DATA ${health.latest_quote_date || "--"} · BUILD ${data.generated_at || "--"} · ${data.candidates?.length || 0} SECURITIES`;
  }
}

function renderMetrics() {
  const candidates = state.data?.candidates || [];
  const isA = (item) => ["SH", "SZ", "BJ"].includes(item.market);
  const count = (status) => candidates.filter((item) => item.status === status).length;
  const reports = state.data?.report_count ??
    candidates.reduce((sum, item) => sum + (item.reports || []).length, 0);

  const values = [
    ["A股候选", candidates.filter(isA).length, "研究覆盖池", ""],
    ["核心观察", count("CORE"), "通过全部硬筛", "accent"],
    ["待核验", count("PENDING"), "证据或数据待补", "amber"],
    ["已排除", count("REJECTED"), "命中排除条件", "red"],
    ["研报", reports, "当前报告库", ""],
  ];

  $("metrics").innerHTML = values.map(([label, value, note, tone]) =>
    `<div class="kpi"><div class="kpi-label">${esc(label)}</div><div class="kpi-value ${tone}">${esc(value)}</div><div class="kpi-note">${esc(note)}</div></div>`
  ).join("");

  $("candidate-tab-count").textContent = candidates.length;
  $("report-tab-count").textContent = reports;
  $("history-tab-count").textContent = state.history.length;
  $("validation-tab-count").textContent = state.validation?.matured_record_count ?? 0;
}

function renderWorkflow() {
  const data = state.data || {};
  const health = state.health || {};
  const candidates = data.candidates || [];
  const count = (status) => candidates.filter((item) => item.status === status).length;
  const reportCount = Number(data.report_count ?? health.report_count ?? 0) || 0;
  const textCount = Number(data.report_text_count ?? reportCount) || 0;
  const aiCount = Number(data.report_ai_count ?? health.report_ai_count ?? 0) || 0;
  const issueCount = Number(data.report_issue_count ?? health.report_issue_count ?? 0) || 0;
  const freshCount = Number(data.fresh_quote_count ?? candidates.filter((item) => item.technical?.status === "fresh").length) || 0;
  const total = candidates.length;
  const core = count("CORE");
  const pending = count("PENDING");
  const rejected = count("REJECTED");
  const outside = count("OUTSIDE_SCOPE");
  const modelReady = health.llm_configured && !health.llm_endpoint_blocked;

  const stages = [
    ["研报入库", `${reportCount} PDF`, "SOURCE", reportCount ? "done" : "wait", reportCount ? "READY" : "WAIT"],
    ["文本 / AI", `${aiCount}/${textCount}`, issueCount ? `${issueCount} ISSUE` : modelReady ? "CACHED" : "EXTRACTIVE", issueCount || !modelReady ? "warn" : "done", issueCount || !modelReady ? "CHECK" : "READY"],
    ["行情校验", `${freshCount} FRESH`, health.latest_quote_date || "--", health.latest_quote_date ? (health.quote_age_days > 3 ? "warn" : "done") : "wait", health.quote_age_days > 3 ? "STALE" : "READY"],
    ["筛选 / 估值", `${core} CORE`, `${pending} PENDING / ${rejected} OUT`, total ? (pending ? "warn" : "done") : "wait", total ? (pending ? "CHECK" : "READY") : "WAIT"],
    ["快照产出", data.generated_at || "--", `${total} SEC / JSON+MD`, data.generated_at ? "done" : "wait", data.generated_at ? "READY" : "WAIT"],
  ];

  $("workflow-flow").innerHTML = stages.map(([name, value, detail, tone, stateText], index) =>
    `<div class="pipe-step">
      <div class="pipe-top"><span class="pipe-index">${String(index + 1).padStart(2, "0")}</span><span class="pipe-state ${tone}">${esc(stateText)}</span></div>
      <div class="pipe-name">${esc(name)}</div>
      <div class="pipe-value">${esc(value)}</div>
      <div class="pipe-detail">${esc(detail)}</div>
    </div>`
  ).join("");

  $("workflow-run-meta").textContent =
    data.generated_at ? `BUILD ${data.generated_at} / QUOTE ${health.latest_quote_date || "--"}` : "NO BUILD";

  const distribution = [
    ["CORE", "CORE", core],
    ["PENDING", "PEND", pending],
    ["REJECTED", "OUT", rejected],
    ["OUTSIDE_SCOPE", "N/A", outside],
  ];
  $("distribution-track").innerHTML = distribution.map(([key, , value]) =>
    `<span class="distribution-segment ${key}" style="width:${total ? (value / total) * 100 : 0}%"></span>`
  ).join("");
  $("distribution-legend").innerHTML = distribution.map(([key, label, value]) =>
    `<span class="legend-item"><i class="legend-dot ${key}"></i>${label} ${value}</span>`
  ).join("");
  $("distribution-total").textContent = `${total} SEC`;
}

function visibleCandidates() {
  const marketFilter = $("market-filter").value;
  const statusFilter = $("status-filter").value;
  const needle = $("search-input").value.trim().toLowerCase();

  return (state.data?.candidates || []).filter((item) => {
    const isA = ["SH", "SZ", "BJ"].includes(item.market);
    const marketOk = marketFilter === "ALL" || (marketFilter === "A" ? isA : item.market === marketFilter);
    const statusOk = statusFilter === "ALL" || item.status === statusFilter;
    const haystack = `${item.company} ${item.code} ${item.quote?.industry || ""}`.toLowerCase();
    return marketOk && statusOk && (!needle || haystack.includes(needle));
  });
}

function renderCandidateRows() {
  const candidates = visibleCandidates();
  $("candidate-result-count").textContent = `${candidates.length} ROWS`;

  if (!candidates.length) {
    $("candidate-rows").innerHTML = '<tr><td colspan="8" class="empty-state">没有符合当前筛选条件的标的</td></tr>';
    $("detail-panel").innerHTML = '<div class="empty-state">选择证券后查看研究详情</div>';
    return;
  }

  if (!candidates.some((item) => item.code === state.selectedCode)) {
    state.selectedCode = candidates[0].code;
  }

  $("candidate-rows").innerHTML = candidates.map((item) => {
    const quote = item.quote || {};
    const technical = item.technical || {};
    const ratios = item.valuation?.ratios || {};
    const scenario = item.valuation?.scenario || {};
    const price = technical.price ?? quote.price;
    return `<tr class="candidate-row ${item.code === state.selectedCode ? "selected" : ""}" data-code="${esc(item.code)}" tabindex="0">
      <td><span class="security-name">${esc(item.company)}</span><span class="security-code">${esc(item.code)} · ${esc(marketName(item.market))}</span></td>
      <td>${statusTag(item.status)}</td>
      <td class="numeric">${price == null ? "待补" : fmt(price)}</td>
      <td class="numeric">${esc(capText(quote.market_cap_yi))}</td>
      <td class="numeric">${ratios.pe_ttm == null ? "待补" : `${fmt(ratios.pe_ttm, 1)}x`}</td>
      <td class="numeric">${fmt(ratios.peg, 2)}</td>
      <td class="numeric ${signedClass(scenario.growth_pct)}">${scenario.growth_pct == null ? "待补" : `${fmt(scenario.growth_pct, 1)}%`}</td>
      <td class="numeric">${esc(technical.quote_date || "待补")}</td>
    </tr>`;
  }).join("");

  renderDetail(candidates.find((item) => item.code === state.selectedCode));
}

function evidenceLine(summary) {
  const order = summary?.orders || {};
  const financial = summary?.financial_evidence || {};
  if (order.confirmed && order.source_verified) {
    return `订单已核验：${order.quote || "原文已核验"}（第 ${order.page || "?"} 页）`;
  }
  if (financial.confirmed && financial.source_verified && financial.fact_type === "reported_actual") {
    return `已披露实绩已核验：${financial.quote || "原文已核验"}（第 ${financial.page || "?"} 页）`;
  }
  return "当前没有可作为硬证据的已确认订单或已披露实绩原文。";
}

function renderScenario(candidate) {
  const scenario = candidate.valuation?.scenario || {};
  if (scenario.status !== "calculated") {
    return '<div class="metric-note">缺少满足跨度要求、可核验且正向的 EPS CAGR，暂不外推情景估值。</div>';
  }

  const values = scenario.scenarios || {};
  const entries = [["保守", values.bear], ["基准", values.base], ["乐观", values.bull]];
  const note = `${scenario.growth_start_year || "--"}E → ${scenario.forecast_year || "--"}E · ${scenario.growth_span_years || "--"}Y CAGR ${fmt(scenario.growth_pct, 1)}% · DISCOUNT ${fmt(scenario.discount_rate_pct, 0)}%`;

  return `<table class="scenario-table">
    <thead><tr><th>情景</th><th>估值区间</th><th>对应 PE</th></tr></thead>
    <tbody>${entries.filter(([,value]) => value).map(([label, value]) =>
      `<tr><td>${esc(label)}</td><td>${fmt(value.low)} - ${fmt(value.high)} 元</td><td>${fmt(value.pe_low, 1)} - ${fmt(value.pe_high, 1)}x</td></tr>`
    ).join("")}</tbody>
  </table><div class="metric-note">${esc(note)}</div>`;
}

function summaryPendingMessage() {
  const health = state.health || {};
  if (!health.llm_configured) return "未配置模型 API Key，当前叙事字段来自规则摘录。";
  if (health.llm_endpoint_blocked) return "模型端点被安全策略阻止，请配置 HTTPS 后重跑 AI 归纳。";
  if (health.report_issue_count) return "上次研报归纳存在异常，请核对处理提示并重跑。";
  return "当前标的尚未形成完整 AI 归纳。";
}

function renderDetail(candidate) {
  if (!candidate) return;

  const summary = candidate.summary || {};
  const quote = candidate.quote || {};
  const technical = candidate.technical || {};
  const ratios = candidate.valuation?.ratios || {};
  const scenario = candidate.valuation?.scenario || {};
  const price = technical.price ?? quote.price;
  const summarizedByAi = summary.summary_mode === "ai";
  const financeFacts = [
    [quote.revenue_yoy_pct, "营收同比"],
    [quote.netprofit_yoy_pct, "净利润同比"],
    [quote.roe_pct, "ROE"],
    [quote.gross_margin_pct, "毛利率"],
  ].filter(([value]) => value != null).map(([value, label]) => `${label} ${fmt(value, 1)}%`);
  const finance = summarizedByAi
    ? candidate.financial_quality || "缺少财务质量信息"
    : (financeFacts.length ? `最新披露指标：${financeFacts.join("、")}` : "暂无可用财务质量指标。");
  const risks = summarizedByAi && (summary.risks || []).length
    ? [...summary.risks]
    : ["自动风险归纳尚未完成，请查看原研报风险提示。"];
  for (const flag of candidate.report_risk_flags || []) {
    risks.push(`退市风险线索：${flag.quote}（${flag.file || "研报"}，第 ${flag.page || "?"} 页）`);
  }
  const rank = candidate.industry_rank_assessment || {};
  const rankText = rank.basis === "verified_subindustry"
    ? `细分排名 ${fmt(rank.rank, 0)}`
    : quote.industry_rank ? `宽行业排名 ${fmt(quote.industry_rank, 0)}（参考）` : "行业排名待补";
  const trend = technical.trend_ok === true ? "多头排列" : technical.trend_ok === false ? "未形成多头" : "待核验";
  const ratioRead = ratios.peg == null ? "待补估值数据" : ratios.peg > 2.5 ? "PEG 偏高" : ratios.peg <= 1 ? "PEG 较低" : "需结合情景判断";
  const reasons = [...(candidate.rejection_reasons || []), ...(candidate.pending_reasons || [])];
  const notes = candidate.research_notes || [];
  const sources = (candidate.reports || []).map((report) =>
    `<a href="${esc(linkForReport(report.file))}" target="_blank" rel="noopener">${esc(report.date || "日期未知")} · ${esc(report.title || report.file)}</a>`
  ).join("");

  $("detail-panel").innerHTML = `
    <div class="detail-head">
      <div>
        <h2 class="detail-name">${esc(candidate.company)} ${statusTag(candidate.status)}</h2>
        <div class="detail-code">${esc(candidate.code)} · ${esc(marketName(candidate.market))} · ${esc(quote.industry || "行业待补")} · ${esc(rankText)}</div>
      </div>
      <div class="detail-price">
        <strong>${price == null ? "待补" : `${fmt(price)} 元`}</strong>
        <span>QUOTE ${esc(technical.quote_date || "--")}</span>
      </div>
    </div>

    <section class="detail-section">
      <div class="section-heading"><h3>研究逻辑</h3><span>${summarizedByAi ? "AI VERIFIED SUMMARY" : "EXTRACTIVE"}</span></div>
      ${summarizedByAi ? "" : `<p class="summary-pending">${esc(summaryPendingMessage())}</p>`}
      <div class="logic-grid">
        <div class="logic-block"><div class="logic-label">行业</div><p class="logic-text">${esc(summarizedByAi ? (summary.industry_logic || "待提取") : "尚未生成行业归纳。")}</p></div>
        <div class="logic-block"><div class="logic-label">公司</div><p class="logic-text">${esc(summarizedByAi ? (summary.company_logic || "待提取") : "尚未生成公司竞争力归纳。")}</p></div>
        <div class="logic-block"><div class="logic-label">盈利</div><p class="logic-text">${esc(summarizedByAi ? (summary.profit_model || "待提取") : "尚未生成收入与利润驱动归纳。")}</p></div>
      </div>
    </section>

    <section class="detail-section">
      <div class="section-heading"><h3>财务 / 证据</h3><span>${esc(ratioRead)}</span></div>
      <div class="logic-grid">
        <div class="logic-block"><div class="logic-label">质量</div><p class="logic-text">${esc(finance)}</p></div>
        <div class="logic-block"><div class="logic-label">证据</div><p class="logic-text">${esc(evidenceLine(summary))}</p></div>
      </div>
    </section>

    <section class="detail-section">
      <div class="section-heading"><h3>估值</h3><span>${scenario.growth_method === "verified_eps_cagr" ? "VERIFIED EPS CAGR" : "PENDING"}</span></div>
      <div class="inline-metrics">
        ${metricBox("PE TTM", ratios.pe_ttm == null ? "待补" : `${fmt(ratios.pe_ttm, 1)}x`)}
        ${metricBox("PB", ratios.pb == null ? "待补" : `${fmt(ratios.pb, 2)}x`)}
        ${metricBox("PS TTM", ratios.ps_ttm == null ? "待补" : `${fmt(ratios.ps_ttm, 2)}x`)}
        ${metricBox("PEG", fmt(ratios.peg, 2))}
      </div>
      ${renderScenario(candidate)}
    </section>

    <section class="detail-section">
      <div class="section-heading"><h3>技术 / 流动性</h3><span>${technical.status === "fresh" ? "FRESH" : technical.status === "stale" ? "STALE" : "MISSING"}</span></div>
      <div class="tech-grid">
        <div class="tech-item"><span>趋势</span><span>${esc(trend)}</span></div>
        <div class="tech-item"><span>换手率</span><span>${technical.turnover_rate_pct == null ? "待补" : `${fmt(technical.turnover_rate_pct)}%`}</span></div>
        <div class="tech-item"><span>MA5 / 20 / 60</span><span>${[technical.ma5, technical.ma20, technical.ma60].map((v) => fmt(v)).join(" / ")}</span></div>
        <div class="tech-item"><span>5/20 日均量比</span><span>${fmt(technical.volume_ratio_5d_20d)}</span></div>
        <div class="tech-item"><span>20 日平均成交额</span><span>${technical.avg_amount_20d_yi == null ? "待补" : `${fmt(technical.avg_amount_20d_yi)} 亿`}</span></div>
        <div class="tech-item"><span>5/20 成交额比</span><span>${fmt(technical.amount_ratio_5d_20d)}</span></div>
        <div class="tech-item"><span>近 5 日</span><span class="${signedClass(technical.return_5d_pct)}">${technical.return_5d_pct == null ? "待补" : `${fmt(technical.return_5d_pct)}%`}</span></div>
        <div class="tech-item"><span>量价异常</span><span>${technical.high_volume_stall ? "放量滞涨" : technical.no_volume_rise ? "缩量上涨" : "未触发"}</span></div>
      </div>
    </section>

    ${notes.length ? `<section class="detail-section"><div class="section-heading"><h3>研究备注</h3></div><ul class="risk-list">${notes.map((note) => `<li>${esc(note)}</li>`).join("")}</ul></section>` : ""}

    <section class="detail-section">
      <div class="section-heading"><h3>主要风险</h3><span>${risks.length} ITEMS</span></div>
      <ul class="risk-list">${risks.map((risk) => `<li>${esc(risk)}</li>`).join("")}</ul>
    </section>

    <section class="detail-section">
      <div class="section-heading"><h3>筛选状态</h3><span>${reasons.length ? "ACTION REQUIRED" : "PASS"}</span></div>
      <ul class="risk-list">${reasons.length ? reasons.map((reason) => `<li>${esc(reason)}</li>`).join("") : "<li>满足当前筛选条件。</li>"}</ul>
    </section>

    <section class="detail-section">
      <div class="section-heading"><h3>研报来源</h3><span>${candidate.reports?.length || 0} PDF</span></div>
      <div class="source-list">${sources || '<span class="metric-note">没有关联研报</span>'}</div>
    </section>
  `;
}

function allReports() {
  const result = [];
  for (const candidate of state.data?.candidates || []) {
    for (const report of candidate.reports || []) {
      result.push({...report, code: candidate.code, company: candidate.company, market: candidate.market, status: candidate.status});
    }
  }
  return result.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
}

function renderReports() {
  const market = $("report-market-filter").value;
  const needle = $("report-search-input").value.trim().toLowerCase();
  const rows = allReports().filter((report) => {
    const isA = ["SH", "SZ", "BJ"].includes(report.market);
    const marketOk = market === "ALL" || (market === "A" ? isA : report.market === market);
    const haystack = `${report.company} ${report.code} ${report.title} ${report.file}`.toLowerCase();
    return marketOk && (!needle || haystack.includes(needle));
  });

  $("report-result-count").textContent = `${rows.length} ROWS`;
  $("report-rows").innerHTML = rows.length ? rows.map((report) => `<tr>
    <td class="numeric">${esc(report.date || "待补")}</td>
    <td><span class="security-name">${esc(report.company)}</span><span class="security-code">${esc(report.code)}</span></td>
    <td>${esc(marketName(report.market))}</td>
    <td class="report-title">${esc(report.title || "研究报告")}<small>${esc(report.file)}</small></td>
    <td>${statusTag(report.status)}</td>
    <td><a class="pdf-link" href="${esc(linkForReport(report.file))}" target="_blank" rel="noopener">OPEN PDF</a></td>
  </tr>`).join("") : '<tr><td colspan="6" class="empty-state">没有匹配的研报</td></tr>';
}

function historyStatusLabel(status) {
  return status ? statusName(status) : "未纳入候选";
}

function historyDelta(current, previous, digits = 2, percent = false) {
  if (current == null || previous == null || !Number.isFinite(Number(current)) || !Number.isFinite(Number(previous))) return "N/A";
  const change = percent
    ? (Number(previous) === 0 ? null : (Number(current) / Number(previous) - 1) * 100)
    : Number(current) - Number(previous);
  if (change == null || !Number.isFinite(change)) return "N/A";
  return `${change > 0 ? "+" : ""}${change.toFixed(digits)}${percent ? "%" : ""}`;
}

function renderHistory() {
  const builds = state.history || [];
  if (!builds.length) {
    $("history-rows").innerHTML = '<tr><td colspan="5" class="empty-state">尚无历史构建记录</td></tr>';
    $("history-detail").innerHTML = '<div class="empty-state">完成一次构建后，这里会保留候选快照与状态变化。</div>';
    return;
  }

  $("history-rows").innerHTML = builds.map((build) => {
    const changes = build.changes || {};
    const counts = build.status_counts || {};
    return `<tr class="history-row ${build.build_id === state.selectedBuildId ? "selected" : ""}" data-build-id="${esc(build.build_id)}" tabindex="0">
      <td class="numeric">${esc(buildTimeLabel(build.built_at))}<span class="history-sub">DATA ${esc(build.generated_at || "--")}</span></td>
      <td class="numeric">${esc(build.market_data_mode || "--")}</td>
      <td class="numeric">${esc(build.candidate_count ?? 0)}</td>
      <td class="numeric">${esc(counts.CORE ?? 0)}</td>
      <td class="numeric">+${changes.core_entered?.length || 0} / -${changes.core_exited?.length || 0}</td>
    </tr>`;
  }).join("");

  const build = builds.find((item) => item.build_id === state.selectedBuildId) || builds[0];
  state.selectedBuildId = build.build_id;
  const index = builds.findIndex((item) => item.build_id === build.build_id);
  const previousBuild = builds[index + 1];
  const previousCandidates = new Map((previousBuild?.candidates || []).map((item) => [item.code, item]));
  const changes = build.changes || {};
  const counts = build.status_counts || {};
  const entries = changes.core_entered || [];
  const exits = changes.core_exited || [];
  const changeItems = changes.status_changes || [];

  const candidateRows = (build.candidates || []).map((item) => {
    const previous = previousCandidates.get(item.code);
    const reasons = item.reasons || [];
    return `<tr>
      <td><span class="security-name">${esc(item.company)}</span><span class="security-code">${esc(item.code)} · ${esc(marketName(item.market))}</span></td>
      <td>${statusTag(item.status)}</td>
      <td class="numeric">${item.price == null ? "待补" : `${fmt(item.price)} 元`}<span class="history-sub">${historyDelta(item.price, previous?.price, 2, true)}</span></td>
      <td class="numeric">${fmt(item.peg, 2)}<span class="history-sub">${historyDelta(item.peg, previous?.peg)}</span></td>
      <td class="numeric">${esc(item.growth_pct == null ? "待补" : `${fmt(item.growth_pct, 1)}%`)}</td>
      <td class="history-reason">${esc(reasons.slice(0, 2).join("；") || "无")}</td>
    </tr>`;
  }).join("");

  $("history-detail").innerHTML = `
    <div class="detail-head">
      <div><h2 class="detail-name">构建详情</h2><div class="detail-code">${esc(buildTimeLabel(build.built_at))} · DATA ${esc(build.generated_at || "--")}</div></div>
      <div class="detail-price"><strong>${esc(build.market_data_mode || "--")}</strong><span>${esc(build.report_count ?? 0)} REPORTS</span></div>
    </div>
    <div class="history-counts">
      <div class="history-count"><span>CORE</span><strong>${esc(counts.CORE ?? 0)}</strong></div>
      <div class="history-count"><span>PENDING</span><strong>${esc(counts.PENDING ?? 0)}</strong></div>
      <div class="history-count"><span>REJECTED</span><strong>${esc(counts.REJECTED ?? 0)}</strong></div>
      <div class="history-count"><span>OUTSIDE</span><strong>${esc(counts.OUTSIDE_SCOPE ?? 0)}</strong></div>
    </div>
    <section class="detail-section">
      <div class="section-heading"><h3>核心池变化</h3><span>+${entries.length} / -${exits.length}</span></div>
      <ul class="change-list">${[
        ...entries.map((item) => `<li>新增 ${esc(item.company)}（${esc(item.code)}），原状态 ${esc(historyStatusLabel(item.from_status))}</li>`),
        ...exits.map((item) => `<li>移出 ${esc(item.company)}（${esc(item.code)}），现状态 ${esc(historyStatusLabel(item.to_status))}</li>`),
      ].join("") || "<li>核心观察池没有变化。</li>"}</ul>
    </section>
    <section class="detail-section">
      <div class="section-heading"><h3>候选状态变化</h3><span>${changeItems.length} SEC</span></div>
      <ul class="change-list">${changeItems.map((item) =>
        `<li>${esc(item.company)}（${esc(item.code)}）：${esc(historyStatusLabel(item.from_status))} → ${esc(historyStatusLabel(item.to_status))}</li>`
      ).join("") || "<li>候选状态没有变化。</li>"}</ul>
    </section>
    <section class="detail-section">
      <div class="section-heading"><h3>候选快照</h3><span>${previousBuild ? "VS PREVIOUS" : "BASELINE"}</span></div>
      <div class="table-frame"><table class="history-detail-table">
        <thead><tr><th>证券</th><th>状态</th><th>价格 / 变动</th><th>PEG / 变动</th><th>EPS CAGR</th><th>原因</th></tr></thead>
        <tbody>${candidateRows || '<tr><td colspan="6" class="empty-state">没有候选</td></tr>'}</tbody>
      </table></div>
    </section>
  `;
}

function renderValidation() {
  const validation = state.validation || {};
  const summary = validation.summary || [];
  const benchmarks = validation.benchmark_codes || [];
  const benchmark = benchmarks[0] || null;
  const excessKey = benchmark ? `mean_excess_vs_${benchmark}_pct` : null;

  $("validation-meta").textContent =
    `${validation.history_build_count || 0} BUILDS · ${validation.matured_record_count || 0} MATURED`;

  $("validation-rows").innerHTML = summary.length ? summary.map((item) => `<tr>
    <td>${statusTag(item.status)}</td>
    <td class="numeric">T+${esc(item.horizon_trading_days)}</td>
    <td class="numeric">${esc(item.matured_count)}</td>
    <td class="numeric ${signedClass(item.mean_return_pct)}">${fmt(item.mean_return_pct, 2)}%</td>
    <td class="numeric ${signedClass(item.median_return_pct)}">${fmt(item.median_return_pct, 2)}%</td>
    <td class="numeric">${fmt(item.positive_rate_pct, 1)}%</td>
    <td class="numeric ${excessKey ? signedClass(item[excessKey]) : ""}">${excessKey && item[excessKey] != null ? `${fmt(item[excessKey], 2)}%` : "N/A"}</td>
  </tr>`).join("") : '<tr><td colspan="7" class="empty-state">尚无达到前瞻周期的成熟样本。持续保存每日构建后再运行 python main.py validate。</td></tr>';

  const horizons = (validation.horizons || []).map((value) => `T+${value}`).join(" / ") || "--";
  $("validation-aside").innerHTML = `
    <div class="validation-card"><span>成熟观测</span><strong>${esc(validation.matured_record_count ?? 0)}</strong><small>已经拥有完整未来价格区间的记录。</small></div>
    <div class="validation-card"><span>验证周期</span><strong>${esc(horizons)}</strong><small>按后续第 N 个交易日收盘计算。</small></div>
    <div class="validation-card"><span>基准</span><strong>${esc(benchmarks.join(" / ") || "未配置")}</strong><small>${esc(validation.measurement || "研究验证口径")}</small></div>
  `;
}

function setView(view) {
  state.view = view;
  const views = ["watchlist", "reports", "history", "validation"];
  for (const name of views) {
    const section = $(`${name}-view`);
    const tab = $(`tab-${name}`);
    section.hidden = name !== view;
    if (name === view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }

  const labels = {
    watchlist: ["核心观察池", "RESEARCH WATCHLIST"],
    reports: ["研报库", "SOURCE LIBRARY"],
    history: ["构建历史", "SNAPSHOT HISTORY"],
    validation: ["前瞻验证", "FORWARD VALIDATION"],
  };
  $("page-title").textContent = labels[view][0];

  if (view === "watchlist") {
    const health = state.health || {};
    $("page-subtitle").textContent =
      `DATA ${health.latest_quote_date || "--"} · BUILD ${state.data?.generated_at || "--"} · ${state.data?.candidates?.length || 0} SECURITIES`;
  } else if (view === "reports") {
    $("page-subtitle").textContent = `${state.data?.report_count ?? 0} REPORTS · SOURCE LIBRARY`;
  } else if (view === "history") {
    $("page-subtitle").textContent = `${state.history.length} BUILDS · RETENTION 365`;
  } else {
    $("page-subtitle").textContent = `${state.validation?.matured_record_count ?? 0} MATURED OBSERVATIONS · NO EXECUTION ASSUMPTION`;
  }
}

function renderAll() {
  renderStatus();
  renderMetrics();
  renderWorkflow();
  renderCandidateRows();
  renderReports();
  renderHistory();
  renderValidation();
  setView(state.view);
}

async function refreshBuild() {
  const button = $("refresh-button");
  button.disabled = true;
  button.textContent = "BUILDING...";
  try {
    const response = await fetch("/api/refresh", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({offline: false, refresh_ai: $("refresh-ai").checked}),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "构建失败");
    state.selectedBuildId = null;
    await loadState();
    showToast(payload.report_issues?.length
      ? `观察池已更新；${payload.report_issues.length} 份研报需处理`
      : "观察池已更新");
  } catch (error) {
    showToast(error.message || "构建失败", true);
  } finally {
    button.disabled = false;
    button.textContent = "RUN BUILD";
  }
}

$("candidate-rows").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-code]");
  if (!row) return;
  state.selectedCode = row.dataset.code;
  renderCandidateRows();
});
$("candidate-rows").addEventListener("keydown", (event) => {
  const row = event.target.closest("tr[data-code]");
  if (row && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    state.selectedCode = row.dataset.code;
    renderCandidateRows();
  }
});

for (const id of ["market-filter", "status-filter"]) {
  $(id).addEventListener("change", renderCandidateRows);
}
$("search-input").addEventListener("input", renderCandidateRows);
$("report-market-filter").addEventListener("change", renderReports);
$("report-search-input").addEventListener("input", renderReports);

$("history-rows").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-build-id]");
  if (!row) return;
  state.selectedBuildId = row.dataset.buildId;
  renderHistory();
});
$("history-rows").addEventListener("keydown", (event) => {
  const row = event.target.closest("tr[data-build-id]");
  if (row && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    state.selectedBuildId = row.dataset.buildId;
    renderHistory();
  }
});

for (const name of ["watchlist", "reports", "history", "validation"]) {
  $(`tab-${name}`).addEventListener("click", (event) => {
    event.preventDefault();
    setView(name);
  });
}
$("refresh-button").addEventListener("click", refreshBuild);

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && state.view === "watchlist" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    $("search-input").focus();
  }
});

loadState().catch((error) => {
  $("status-title").textContent = "研究终端不可用";
  $("status-detail").textContent = error.message || "请确认 FastAPI 服务已启动。";
  showToast("读取数据失败", true);
});
