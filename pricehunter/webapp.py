"""仪表盘单页前端（原生 HTML/CSS/JS，无外部依赖）。"""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>价格采集运维台</title>
<style>
:root {
  --bg: #0f1420; --panel: #181f2e; --panel2: #1f2839; --line: #2b364b;
  --text: #e6ebf4; --muted: #93a0b5; --accent: #5b8def; --ok: #3fbf7f;
  --warn: #e0a23d; --bad: #e5635e; --purple: #a78bfa;
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:14px/1.5 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
header { padding:14px 22px; background:var(--panel); border-bottom:1px solid var(--line);
  display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
h1 { font-size:17px; margin:0; }
.clockbox { display:flex; align-items:center; gap:8px; margin-left:auto; }
select, button, input { background:var(--panel2); color:var(--text);
  border:1px solid var(--line); border-radius:7px; padding:6px 10px; font-size:13px; }
button { cursor:pointer; }
button:hover { border-color:var(--accent); }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
nav { display:flex; gap:4px; padding:10px 22px 0; background:var(--panel);
  border-bottom:1px solid var(--line); }
nav button { border-radius:8px 8px 0 0; border-bottom:none; padding:9px 18px;
  background:transparent; color:var(--muted); }
nav button.active { background:var(--bg); color:var(--text); font-weight:600; }
main { padding:20px 22px 60px; }
.tab { display:none; } .tab.active { display:block; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:14px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:16px; }
.card h3 { margin:0 0 10px; font-size:15px; display:flex; gap:8px; align-items:center; }
.kv { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; color:var(--muted); }
.kv b { color:var(--text); font-weight:600; text-align:right; }
.badge { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; }
.badge.ok { background:rgba(63,191,127,.15); color:var(--ok); }
.badge.warn { background:rgba(224,162,61,.15); color:var(--warn); }
.badge.bad { background:rgba(229,99,94,.15); color:var(--bad); }
.badge.purple { background:rgba(167,139,250,.15); color:var(--purple); }
.badge.muted { background:var(--panel2); color:var(--muted); }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--muted); font-weight:600; }
tr:hover td { background:rgba(91,141,239,.06); }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; }
.chain { border-left:2px solid var(--line); padding-left:14px; margin-left:6px; }
.vrow { position:relative; padding:8px 10px; border:1px solid var(--line);
  border-radius:8px; margin:6px 0; background:var(--panel2); }
.vrow.superseded { opacity:.62; }
.vrow::before { content:""; position:absolute; left:-21px; top:16px; width:9px;
  height:9px; border-radius:50%; background:var(--accent); }
.vrow.superseded::before { background:var(--muted); }
.muted-text { color:var(--muted); }
.mono { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px; }
pre { background:#0b101b; border:1px solid var(--line); border-radius:10px;
  padding:14px; overflow:auto; max-height:480px; font-size:12px; }
.modal-bg { position:fixed; inset:0; background:rgba(5,8,14,.7); display:none;
  align-items:center; justify-content:center; z-index:50; }
.modal-bg.show { display:flex; }
.modal { width:min(900px,92vw); max-height:86vh; overflow:auto; background:var(--panel);
  border:1px solid var(--line); border-radius:14px; padding:18px; }
.toast { position:fixed; right:22px; bottom:22px; background:var(--panel2);
  border:1px solid var(--accent); border-radius:10px; padding:10px 16px;
  display:none; max-width:420px; z-index:60; }
.toast.show { display:block; }
.diff-add { color:var(--ok); } .diff-del { color:var(--bad); }
.section-title { margin:22px 0 10px; font-size:14px; color:var(--muted);
  text-transform:uppercase; letter-spacing:.08em; }
.pill-row { display:flex; gap:6px; flex-wrap:wrap; }
</style>
</head>
<body>
<header>
  <h1>🛒 价格采集运维台</h1>
  <div class="clockbox">
    <span class="muted-text">模拟日期</span>
    <select id="simDate"></select>
    <button id="setDate">切换日期</button>
    <span id="clockNow" class="badge muted"></span>
  </div>
</header>
<nav>
  <button data-tab="health" class="active">来源健康度</button>
  <button data-tab="failures">解析失败样本</button>
  <button data-tab="replays">回放差异</button>
  <button data-tab="chains">当前价格链路</button>
</nav>
<main>
  <!-- 健康度 -->
  <section id="tab-health" class="tab active">
    <div class="actions">
      <button class="primary" onclick="refreshAll()">刷新全部</button>
      <span class="muted-text" style="align-self:center">重跑只影响所选来源，独立 run / 独立传输，不触碰其他来源</span>
    </div>
    <div id="healthGrid" class="grid"></div>
    <div class="section-title">最近运行</div>
    <div style="overflow:auto"><table id="runsTable"><thead><tr>
      <th>run_id</th><th>来源</th><th>状态</th><th>成功页</th><th>失败页</th>
      <th>429</th><th>重试</th><th>开始</th><th>结束</th><th>备注</th>
    </tr></thead><tbody></tbody></table></div>
  </section>

  <!-- 失败样本 -->
  <section id="tab-failures" class="tab">
    <div class="actions"><button class="primary" onclick="loadFailures()">刷新</button>
      <span class="muted-text" style="align-self:center">结构变化 / 字段缺失的解析尝试，点击“查看快照”核对原始证据</span>
    </div>
    <div style="overflow:auto"><table id="failTable"><thead><tr>
      <th>时间</th><th>来源</th><th>解析器版本</th><th>错误类型</th>
      <th>错误信息</th><th>URL</th><th>操作</th>
    </tr></thead><tbody></tbody></table></div>
  </section>

  <!-- 回放 -->
  <section id="tab-replays" class="tab">
    <div class="actions">
      <select id="replaySource"></select>
      <select id="replayVersion"></select>
      <button class="primary" onclick="runReplay()">执行只读回放</button>
      <span class="muted-text" style="align-self:center">回放不写价格表，仅产出字段级差异</span>
    </div>
    <div id="replayList"></div>
  </section>

  <!-- 价格链路 -->
  <section id="tab-chains" class="tab">
    <div class="actions"><button class="primary" onclick="loadChains()">刷新</button></div>
    <div class="grid">
      <div class="card"><h3>促销命中（当前生效）</h3><div id="promoList"></div></div>
      <div class="card"><h3>异常 / 维护告警（当前生效）</h3><div id="alertList"></div></div>
      <div class="card"><h3>修订记录</h3><div id="revisionList"></div></div>
    </div>
    <div class="section-title">商品价格版本链（双时态：观测日 × 修订号）</div>
    <select id="skuSelect" onchange="renderChain()"></select>
    <div id="chainView" class="chain" style="margin-top:12px"></div>
  </section>
</main>

<div class="modal-bg" id="modalBg" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h3 id="modalTitle" style="margin:0">快照</h3>
      <button onclick="closeModal()">关闭</button>
    </div>
    <pre id="modalBody" class="mono"></pre>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const DAYS = ["2026-11-13","2026-11-14","2026-11-15","2026-11-16","2026-11-17","2026-11-18"];
let SOURCES = [];
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
const post = (path, body) => api(path, {method:"POST",
  headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const money = c => c==null ? "—" : "$" + (c/100).toFixed(2);
const toast = msg => {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 4200);
};
const statusBadge = s => {
  const map = {ok:["ok","正常"], partial:["warn","降级"],
    maintenance_blocked:["bad","维护拦截"], failed:["bad","失败"], running:["purple","运行中"]};
  const [cls, label] = map[s] || ["muted", s];
  return `<span class="badge ${cls}">${label}</span>`;
};

// ---- 日期切换 ----
document.getElementById("simDate").innerHTML =
  DAYS.map(d => `<option>${d}</option>`).join("");
document.getElementById("setDate").onclick = async () => {
  const day = document.getElementById("simDate").value;
  await post("/api/clock", {sim_date: day});
  toast(`模拟日期已切换到 ${day}，夹具限流计数已重置`);
  refreshAll();
};

// ---- tab 切换 ----
document.querySelectorAll("nav button").forEach(btn => btn.onclick = () => {
  document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  if (btn.dataset.tab === "failures") loadFailures();
  if (btn.dataset.tab === "replays") loadReplays();
  if (btn.dataset.tab === "chains") loadChains();
});

// ---- 健康度 ----
async function loadHealth() {
  const [health, runs] = await Promise.all([api("/api/health"), api("/api/runs")]);
  const grid = document.getElementById("healthGrid");
  grid.innerHTML = health.map(h => {
    const rate = (h.success_rate * 100).toFixed(1);
    const rateCls = h.success_rate >= 0.9 ? "ok" : h.success_rate >= 0.6 ? "warn" : "bad";
    return `<div class="card">
      <h3>${esc(h.name)} <span class="badge muted">${esc(h.source_id)}</span></h3>
      <div class="kv">
        <span>解析器</span><b class="mono">${esc(h.parser_version)}</b>
        <span>快照成功率</span><b><span class="badge ${rateCls}">${rate}%
          (${h.snaps_ok}/${h.snaps})</span></b>
        <span>429 限流</span><b>${h.snaps_429 ? `<span class="badge warn">${h.snaps_429}</span>` : 0}</b>
        <span>维护拦截</span><b>${h.snaps_maint ? `<span class="badge bad">${h.snaps_maint}</span>` : 0}</b>
        <span>解析失败</span><b>${h.parse_failures ? `<span class="badge bad">${h.parse_failures}</span>` : 0}</b>
        <span>运行次数</span><b>${h.runs}（正常 ${h.runs_ok}）</b>
        <span>最近抓取</span><b>${esc(h.last_fetch || "—")}</b>
      </div>
      <div class="pill-row" style="margin-top:12px">
        <button class="primary" onclick="rerun('${h.source_id}')">单来源重跑</button>
        <button onclick="upgradePrompt('${h.source_id}','${esc(h.parser_version)}')">升级解析器</button>
        <button onclick="backfillPrompt('${h.source_id}')">归档回溯修订</button>
      </div>
    </div>`;
  }).join("");
  document.querySelector("#runsTable tbody").innerHTML = runs.map(r => `<tr>
    <td class="mono">${esc(r.run_id)}</td><td>${esc(r.source_id)}</td>
    <td>${statusBadge(r.status)}</td><td>${r.pages_ok}</td><td>${r.pages_failed}</td>
    <td>${r.rate_limited}</td><td>${r.retries}</td>
    <td>${esc(r.started_at)}</td><td>${esc(r.finished_at || "—")}</td>
    <td>${esc(r.note || "")}</td></tr>`).join("");
}

async function rerun(sourceId) {
  toast(`正在重跑 ${sourceId} …`);
  try {
    const r = await post("/api/rerun", {source_id: sourceId});
    toast(`${sourceId} 重跑完成：${r.status}，成功页 ${r.pages_ok}，失败页 ${r.pages_failed}，重试 ${r.retries}`);
    refreshAll();
  } catch (e) { toast("重跑失败: " + e.message); }
}

async function upgradePrompt(sourceId, current) {
  const v = prompt(`输入新解析器版本（当前 ${current}）。\nacme 可升级到 acme-parser-v2`,
    sourceId === "acme" ? "acme-parser-v2" : current);
  if (!v) return;
  try {
    await post("/api/parser", {source_id: sourceId, parser_version: v});
    toast(`${sourceId} 解析器已切换为 ${v}，可执行重跑验证`);
    refreshAll();
  } catch (e) { toast("升级失败: " + e.message); }
}

async function backfillPrompt(sourceId) {
  const d = prompt("输入要回填修正的归档日期（YYYY-MM-DD）", "2026-11-14");
  if (!d) return;
  try {
    const r = await post("/api/backfill", {source_id: sourceId, archive_date: d});
    toast(`回溯完成：修订 ${r.revisions.length} 条，促销/告警已级联重算`);
    refreshAll();
  } catch (e) { toast("回溯失败: " + e.message); }
}

// ---- 失败样本 ----
async function loadFailures() {
  const rows = await api("/api/failures");
  document.querySelector("#failTable tbody").innerHTML = rows.map(r => `<tr>
    <td>${esc(r.parsed_at)}</td><td>${esc(r.source_id)}</td>
    <td class="mono">${esc(r.parser_version)}</td>
    <td><span class="badge bad">${esc(r.error_kind)}</span></td>
    <td>${esc(r.error_message)}</td><td class="mono">${esc(r.url)}</td>
    <td><button onclick="showSnapshot('${r.snapshot_id}')">查看快照</button></td>
    </tr>`).join("") || `<tr><td colspan="7" class="muted-text">暂无失败记录</td></tr>`;
}

async function showSnapshot(id) {
  const data = await api("/api/snapshot?id=" + encodeURIComponent(id));
  document.getElementById("modalTitle").textContent =
    `原始快照 ${id}（HTTP ${data.meta.http_status} / ${data.meta.outcome}）`;
  document.getElementById("modalBody").textContent = data.body;
  document.getElementById("modalBg").classList.add("show");
}
function closeModal() { document.getElementById("modalBg").classList.remove("show"); }

// ---- 回放 ----
async function loadReplays() {
  const [sources, replays] = await Promise.all([api("/api/sources"), api("/api/replays")]);
  SOURCES = sources;
  document.getElementById("replaySource").innerHTML =
    sources.map(s => `<option value="${s.source_id}">${esc(s.name)}</option>`).join("");
  fillReplayVersions();
  document.getElementById("replaySource").onchange = fillReplayVersions;
  document.getElementById("replayList").innerHTML = replays.map(r => {
    const diffs = r.diffs || [];
    const rows = diffs.map(d => `<tr>
      <td class="mono">${esc(d.snapshot_id.slice(0,18))}…</td>
      <td>${esc(d.sku)}</td><td class="mono">${esc(d.field)}</td>
      <td class="diff-del">${esc(d.old_value)}</td>
      <td class="diff-add">${esc(d.new_value)}</td>
      <td><span class="badge ${d.diff_type==="structure_changed"?"bad":"warn"}">
        ${esc(d.diff_type)}</span></td></tr>`).join("");
    return `<div class="card" style="margin-bottom:12px">
      <h3>${esc(r.source_id)} 回放 <span class="badge purple mono">${esc(r.parser_version)}</span>
        <span class="badge ${r.snapshots_failed ? "bad" : "ok"}">
          失败 ${r.snapshots_failed}/${r.snapshots_total}</span>
        <span class="badge ${diffs.length ? "warn" : "ok"}">差异 ${diffs.length}</span></h3>
      <div class="muted-text" style="margin-bottom:8px">${esc(r.started_at)} · ${esc(r.note || "")}</div>
      <table><thead><tr><th>快照</th><th>SKU</th><th>字段</th><th>原值</th><th>新值</th><th>类型</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6" class="muted-text">无差异</td></tr>`}</tbody></table></div>`;
  }).join("") || `<p class="muted-text">暂无回放记录</p>`;
}
function fillReplayVersions() {
  const sid = document.getElementById("replaySource").value;
  const src = SOURCES.find(s => s.source_id === sid);
  const opts = sid === "acme"
    ? ["acme-parser-v1", "acme-parser-v2"]
    : [src.parser_version];
  document.getElementById("replayVersion").innerHTML =
    opts.map(v => `<option ${v===src.parser_version?"selected":""}>${v}</option>`).join("");
}
async function runReplay() {
  const source_id = document.getElementById("replaySource").value;
  const parser_version = document.getElementById("replayVersion").value;
  try {
    const r = await post("/api/replay", {source_id, parser_version});
    toast(`回放完成：${r.snapshots} 份快照，解析失败 ${r.failed}，差异 ${r.diffs} 条（只读，未改价格数据）`);
    loadReplays();
  } catch (e) { toast("回放失败: " + e.message); }
}

// ---- 价格链路 ----
let CHAIN_DATA = null;
async function loadChains() {
  CHAIN_DATA = await api("/api/chains");
  const skus = Object.keys(CHAIN_DATA.chains).sort();
  document.getElementById("skuSelect").innerHTML =
    skus.map(s => `<option>${s}</option>`).join("");
  document.getElementById("promoList").innerHTML =
    CHAIN_DATA.promos.map(p => `<div class="vrow">
      <b>${esc(p.sku)}</b> <span class="badge ok">${(p.discount_rate*100).toFixed(1)}%</span>
      <span class="muted-text">${esc(p.observed_date)} · ${esc(p.promo_code || "无码")}</span></div>`
    ).join("") || `<p class="muted-text">无促销命中</p>`;
  document.getElementById("alertList").innerHTML =
    CHAIN_DATA.alerts.map(a => {
      const cls = a.severity === "high" ? "bad" : "warn";
      return `<div class="vrow"><b>${esc(a.sku)}</b>
        <span class="badge ${cls}">${esc(a.kind)} · ${esc(a.severity)}</span>
        <div class="muted-text">${esc(a.observed_date)}：${esc(a.message)}</div></div>`;
    }).join("") || `<p class="muted-text">无告警</p>`;
  document.getElementById("revisionList").innerHTML =
    CHAIN_DATA.revisions.map(r => `<div class="vrow">
      <b>${esc(r.sku)}</b> <span class="muted-text">${esc(r.observed_date)}</span>
      <div class="muted-text mono" style="word-break:break-all">${esc(r.old_version_id.slice(0,20))}… →
        ${esc(r.new_version_id.slice(0,20))}…</div>
      <div class="muted-text">${esc(r.reason)}</div></div>`
    ).join("") || `<p class="muted-text">无修订</p>`;
  renderChain();
}
function renderChain() {
  const sku = document.getElementById("skuSelect").value;
  const versions = (CHAIN_DATA.chains[sku] || []);
  document.getElementById("chainView").innerHTML = versions.map(v => `
    <div class="vrow ${v.state === "superseded" ? "superseded" : ""}">
      <div><b>${esc(v.observed_date)}</b> rev${v.revision_no}
        <span class="badge ${v.state==="current"?"ok":"muted"}">
          ${v.state === "current" ? "当前版本" : "已被修订"}</span>
        <span style="float:right" class="mono">${esc(v.source_id)} · ${esc(v.parser_version)}</span></div>
      <div style="margin-top:4px">售价 <b>${money(v.price_cents)}</b>
        <span class="muted-text">划线价 ${money(v.list_price_cents)} · ${esc(v.currency)}
        ${v.promo_code ? "· 促销码 " + esc(v.promo_code) : ""}</span></div>
      <div class="muted-text mono" style="margin-top:2px">记录于 ${esc(v.recorded_at)}
        · 快照 ${esc(v.snapshot_id.slice(0,18))}…</div>
    </div>`).join("");
}

async function refreshAll() {
  const c = await api("/api/clock");
  document.getElementById("clockNow").textContent = c.sim_date;
  document.getElementById("simDate").value = c.sim_date;
  loadHealth();
}
refreshAll();
</script>
</body>
</html>
"""
