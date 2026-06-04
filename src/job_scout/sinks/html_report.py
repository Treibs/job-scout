"""Self-contained HTML dashboard built from the CSV tracker.

`render(csv_path)` reads the upserted CSV and writes a single ``jobs.html`` with
the data embedded inline. Two-pane CRM: a filterable role list on the left, a full
detail + pipeline + notes panel on the right, and a command bar to scrape a job
link or watch a company.

Progressive enhancement: opened as a file:// it's a read-only viewer (status/notes
fall back to localStorage); served by ``scripts/serve.py`` it's a live app that
persists to the CSV and can scrape/score new roles.

Stdlib only (csv, json, html, datetime, pathlib).
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from ..models import SHEET_COLUMNS

log = logging.getLogger("job_scout.html_report")


def _safe_http_url(url: str) -> str:
    """Return ``url`` only if it's a plain http(s) link, else "" (so the UI won't
    render it as a clickable href). Blocks javascript:/data:/vbscript: etc."""
    u = (url or "").strip()
    return u if u[:7].lower() == "http://" or u[:8].lower() == "https://" else ""


def render(csv_path, html_path=None, generated_at: str | None = None) -> Path | None:
    """Read ``csv_path`` and write a self-contained dashboard to ``html_path``
    (default: the CSV path with a .html suffix). Returns the html path, or None
    if the CSV doesn't exist yet."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        log.info("html report skipped: %s does not exist", csv_path)
        return None
    html_path = Path(html_path) if html_path else csv_path.with_suffix(".html")

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = [
            {k: (r.get(k) or "") for k in SHEET_COLUMNS}
            for r in csv.DictReader(f)
        ]

    # Sanitize the only field rendered into an href (job URLs are external);
    # HTML-escaping an attribute doesn't neutralize a javascript: scheme.
    for r in rows:
        r["apply_url"] = _safe_http_url(r["apply_url"])

    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    generated = generated_at or datetime.now().strftime("%b %d, %Y · %H:%M")

    out = (
        _TEMPLATE
        .replace("/*__DATA__*/null", data_json)
        .replace("__GENERATED__", generated)
    )

    tmp = html_path.with_suffix(html_path.suffix + ".tmp")
    tmp.write_text(out, encoding="utf-8")
    tmp.replace(html_path)
    log.info("html report: %d rows -> %s", len(rows), html_path)
    return html_path


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Scout</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0b0c0f; --bg2:#121419; --bg3:#191c23; --bg4:#21252e; --line:#262a33; --line2:#323845;
    --ink:#ece9e2; --ink-dim:#9aa0ab; --ink-faint:#646a76;
    --amber:#f5b14c; --amber-dim:#7a5b27; --teal:#54d6c4; --teal-dim:#1f4f4a;
    --green:#7eeaa8; --green-dim:#2f6b4d; --rose:#e8736b; --rose-dim:#6b3330;
    --mono:'IBM Plex Mono',ui-monospace,Menlo,monospace; --sans:'IBM Plex Sans',system-ui,sans-serif;
  }
  *{box-sizing:border-box;margin:0}
  body{height:100vh;overflow:hidden;background:
      radial-gradient(900px 500px at 92% -10%,rgba(245,177,76,.06),transparent 60%),
      radial-gradient(700px 500px at -4% 110%,rgba(84,214,196,.05),transparent 55%),var(--bg);
    color:var(--ink);font-family:var(--sans);font-size:14px;-webkit-font-smoothing:antialiased;
    display:flex;flex-direction:column}
  a{color:var(--teal);text-decoration:none} a:hover{text-decoration:underline}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:var(--bg4);border-radius:6px;border:2px solid var(--bg)}

  /* ── top bar ── */
  header{display:flex;align-items:center;gap:20px;padding:13px 22px;border-bottom:1px solid var(--line);
    background:linear-gradient(180deg,rgba(18,20,25,.7),transparent)}
  .brand{font-family:var(--mono);font-weight:700;font-size:19px;letter-spacing:-.5px;white-space:nowrap}
  .brand .d{color:var(--amber)}
  .cmd{flex:1;display:flex;gap:8px;align-items:center;max-width:760px}
  .cmd .in{flex:1;position:relative;display:flex;align-items:center}
  .cmd .in .pre{position:absolute;left:13px;color:var(--ink-faint);font-family:var(--mono);font-size:13px}
  .cmd input{width:100%;background:var(--bg2);border:1px solid var(--line2);color:var(--ink);
    border-radius:9px;padding:10px 13px 10px 30px;font-family:var(--sans);font-size:13.5px;outline:none;transition:border-color .15s}
  .cmd input:focus{border-color:var(--amber-dim)}
  .cmd input::placeholder{color:var(--ink-faint)}
  .cmd button{font-family:var(--mono);font-size:12px;letter-spacing:.3px;border-radius:9px;padding:10px 14px;
    border:1px solid var(--line2);background:var(--bg3);color:var(--ink-dim);cursor:pointer;white-space:nowrap;transition:.15s}
  .cmd button:hover:not(:disabled){border-color:var(--amber-dim);color:var(--ink)}
  .cmd button.j:hover:not(:disabled){border-color:var(--teal-dim);color:var(--teal)}
  .cmd button:disabled{opacity:.4;cursor:not-allowed}
  .conn{margin-left:auto;font-family:var(--mono);font-size:10.5px;letter-spacing:1px;text-transform:uppercase;
    display:flex;align-items:center;gap:7px;color:var(--ink-faint);white-space:nowrap}
  .conn .dot{width:7px;height:7px;border-radius:50%;background:var(--ink-faint)}
  .conn.live .dot{background:var(--green);box-shadow:0 0 8px var(--green)}
  .conn.live{color:var(--green)}

  /* ── app two-pane ── */
  .app{flex:1;display:grid;grid-template-columns:minmax(430px,40%) 1fr;min-height:0}
  .pane{min-height:0;display:flex;flex-direction:column}
  .left{border-right:1px solid var(--line)}

  /* filter bar */
  .filters{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:11px;
    background:rgba(11,12,15,.7);backdrop-filter:blur(8px)}
  .seg{display:flex;gap:4px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:4px}
  .seg button{flex:1;font-family:var(--mono);font-size:11.5px;letter-spacing:.3px;border:0;background:transparent;
    color:var(--ink-faint);padding:7px 6px;border-radius:7px;cursor:pointer;transition:.12s;white-space:nowrap}
  .seg button:hover{color:var(--ink-dim)}
  .seg button.on{background:var(--bg4);color:var(--ink)}
  .seg button.on[data-s="interested"]{color:var(--teal)} .seg button.on[data-s="applied"]{color:var(--green)}
  .seg button .n{opacity:.55;margin-left:5px}
  .frow{display:flex;gap:8px;align-items:center}
  .frow input[type=search],.frow select{background:var(--bg2);border:1px solid var(--line);color:var(--ink);
    border-radius:8px;padding:7px 10px;font-family:var(--sans);font-size:12.5px;outline:none}
  .frow input[type=search]{flex:1;min-width:0}
  .frow select{cursor:pointer;font-family:var(--mono);font-size:11px;max-width:130px}
  .frow input:focus,.frow select:focus{border-color:var(--amber-dim)}
  .lcount{font-family:var(--mono);font-size:11px;color:var(--ink-faint);padding:8px 18px;border-bottom:1px solid var(--line)}
  .lcount b{color:var(--ink-dim)}

  /* list */
  .list{flex:1;overflow-y:auto;padding:8px}
  .row{display:grid;grid-template-columns:46px 1fr auto;gap:13px;align-items:center;padding:12px 12px;
    border-radius:11px;cursor:pointer;border:1px solid transparent;transition:.12s;animation:rise .35s both}
  @keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  .row:hover{background:var(--bg2)}
  .row.sel{background:var(--bg3);border-color:var(--line2)}
  .row.stale{opacity:.5}
  .sc{width:46px;height:46px;border-radius:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;
    border:1px solid;font-family:var(--mono)}
  .sc .v{font-size:17px;font-weight:700;line-height:1} .sc .k{font-size:7px;letter-spacing:1px;opacity:.6;margin-top:1px}
  .rtitle{font-size:14px;font-weight:600;color:var(--ink);line-height:1.25;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .rmeta{font-size:12px;color:var(--ink-dim);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .rmeta .src{font-family:var(--mono);font-size:9.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--ink-faint)}
  .pdot{width:9px;height:9px;border-radius:50%;background:var(--line2)}
  .pdot.interested{background:var(--teal)} .pdot.applied{background:var(--green)}
  .pdot.interview{background:var(--amber)} .pdot.offer{background:#c2e36a;box-shadow:0 0 7px rgba(194,227,106,.6)}
  .pdot.pass,.pdot.rejected,.pdot.archived{background:var(--rose-dim)} .pdot.stale{background:var(--bg4)}

  /* detail */
  .detail{flex:1;overflow-y:auto;padding:30px 38px 60px}
  .empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;
    color:var(--ink-faint);font-family:var(--mono);gap:10px;text-align:center}
  .empty .big{font-size:40px;opacity:.3}
  .dhead{display:flex;gap:22px;align-items:flex-start}
  .dsc{width:84px;height:84px;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;
    border:1px solid;font-family:var(--mono);flex-shrink:0}
  .dsc .v{font-size:32px;font-weight:700;line-height:1} .dsc .k{font-size:9px;letter-spacing:1.5px;opacity:.6;margin-top:3px}
  .dtitle{font-size:25px;font-weight:600;letter-spacing:-.3px;line-height:1.15}
  .dmeta{margin-top:9px;color:var(--ink-dim);font-size:13.5px;display:flex;flex-wrap:wrap;gap:7px;align-items:center}
  .dmeta .src{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--ink-faint);
    border:1px solid var(--line);padding:1px 7px;border-radius:5px}
  .dmeta .co{color:var(--ink);font-weight:600}
  .open{margin-top:14px;display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:13px;
    border:1px solid var(--teal-dim);color:var(--teal);background:rgba(84,214,196,.07);border-radius:9px;padding:9px 16px}
  .open:hover{background:rgba(84,214,196,.14);text-decoration:none}

  .sect{margin-top:28px}
  .slabel{font-family:var(--mono);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--ink-faint);margin-bottom:12px}

  /* pipeline control */
  .pipe{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .stage{font-family:var(--mono);font-size:12.5px;border:1px solid var(--line2);background:var(--bg2);color:var(--ink-dim);
    border-radius:9px;padding:9px 15px;cursor:pointer;transition:.14s;position:relative}
  .stage:hover{border-color:var(--ink-faint);color:var(--ink)}
  .stage.on{color:#0b0c0f;font-weight:600;border-color:transparent}
  .stage.on[data-s="interested"]{background:var(--teal)} .stage.on[data-s="applied"]{background:var(--green)}
  .stage.on[data-s="interview"]{background:var(--amber)} .stage.on[data-s="offer"]{background:#c2e36a}
  .stage.exit{margin-left:auto} .stage.exit.on{background:var(--rose)}
  .appdate{font-family:var(--mono);font-size:11.5px;color:var(--ink-faint);margin-top:9px}

  .dims{display:flex;gap:22px;flex-wrap:wrap}
  .dim{display:flex;flex-direction:column;gap:6px}
  .dim .dl{font-family:var(--mono);font-size:10px;color:var(--ink-faint);text-transform:uppercase;letter-spacing:.5px}
  .dim .bar{width:90px;height:6px;border-radius:3px;background:var(--bg3);overflow:hidden}
  .dim .bar i{display:block;height:100%;background:var(--amber);border-radius:3px}
  .comp{font-family:var(--mono);color:var(--teal);font-size:14px}
  .rat{color:var(--ink-dim);font-size:14.5px;line-height:1.7;white-space:pre-wrap}
  .flags{display:flex;gap:8px;flex-wrap:wrap}
  .flag{font-size:12.5px;color:#e8a59a;background:rgba(232,115,107,.08);border:1px solid rgba(232,115,107,.2);border-radius:7px;padding:4px 11px}
  textarea.notes{width:100%;min-height:120px;background:var(--bg2);border:1px solid var(--line2);border-radius:11px;
    color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.6;padding:14px 16px;resize:vertical;outline:none}
  textarea.notes:focus{border-color:var(--amber-dim)}
  textarea.notes:disabled{opacity:.6}
  .notehint{font-family:var(--mono);font-size:11px;color:var(--ink-faint);margin-top:7px}

  /* toast */
  #toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);opacity:0;
    background:var(--bg3);border:1px solid var(--line2);border-radius:11px;padding:13px 20px;font-size:13.5px;
    box-shadow:0 18px 50px rgba(0,0,0,.6);transition:.25s;pointer-events:none;max-width:560px;z-index:50}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  #toast.err{border-color:var(--rose-dim);color:#e8a59a}
  #toast.ok{border-color:var(--teal-dim)}
  .spin{display:inline-block;width:13px;height:13px;border:2px solid var(--line2);border-top-color:var(--amber);
    border-radius:50%;animation:sp .7s linear infinite;vertical-align:-2px;margin-right:8px}
  @keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header>
  <div class="brand">JOB<span class="d">/</span>SCOUT</div>
  <div class="cmd">
    <div class="in"><span class="pre">⌘</span>
      <input id="cmd" placeholder="paste a job link, or a company careers URL…" autocomplete="off">
    </div>
    <button class="j" id="addJob" title="Scrape this posting + score it into your shortlist">＋ Scrape job</button>
    <button id="addCo" title="Watch this company's careers feed on every scan">＋ Watch company</button>
  </div>
  <div class="conn" id="conn"><span class="dot"></span><span id="connt">read-only</span></div>
</header>

<div class="app">
  <div class="pane left">
    <div class="filters">
      <div class="seg" id="seg"></div>
      <div class="frow">
        <input type="search" id="q" placeholder="search title, company, notes…">
        <select id="company"></select>
        <select id="source"></select>
        <select id="sort">
          <option value="score">Score ▾</option>
          <option value="seen">Newest</option>
          <option value="company">Company</option>
        </select>
      </div>
    </div>
    <div class="lcount" id="count"></div>
    <div class="list" id="list"></div>
  </div>
  <div class="pane">
    <div class="detail" id="detail"></div>
  </div>
</div>
<div id="toast"></div>

<script>
const DATA = /*__DATA__*/null;
const GEN = "__GENERATED__";
const SERVED = location.protocol === 'http:' || location.protocol === 'https:';
const DIMS = [["mission","MIS"],["comp","COMP"],["learning","LRN"],["wlb","WLB"],["prestige","PRES"]];
const PIPE = ["interested","applied","interview","offer"];
const SCALE_MAX = 5;

const $ = s => document.querySelector(s);
const num = v => { const n = parseFloat(v); return isNaN(n)?null:n; };
const esc = s => (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function heat(s){
  if(s==null) return {bg:'var(--bg3)',bd:'var(--line)',fg:'var(--ink-faint)'};
  if(s>=75) return {bg:'rgba(126,234,168,.12)',bd:'#2f6b4d',fg:'#7eeaa8'};
  if(s>=60) return {bg:'rgba(194,227,106,.12)',bd:'#566b2f',fg:'#c2e36a'};
  if(s>=45) return {bg:'rgba(245,196,81,.12)',bd:'#6b562a',fg:'#f5c451'};
  if(s>=30) return {bg:'rgba(240,146,90,.12)',bd:'#6b452a',fg:'#f0925a'};
  return {bg:'rgba(232,115,107,.10)',bd:'#6b3330',fg:'#e8736b'};
}

// local fallback when opened as a file (no server)
const LS = { get(){try{return JSON.parse(localStorage.jobscout||'{}')}catch(e){return {}}},
  set(o){localStorage.jobscout=JSON.stringify(o)} };
function applyLocal(){ if(SERVED) return; const o=LS.get();
  DATA.forEach(r=>{ const e=o[r.apply_url]; if(e){ if(e.status)r.status=e.status; if(e.notes!=null)r.notes=e.notes; }}); }
applyLocal();

let sel = null;
const state = { q:'', stage:'all', company:'', source:'', sort:'score' };

// ── persistence ──
async function save(url, fields){
  const r = DATA.find(x=>x.apply_url===url); if(r) Object.assign(r, fields);
  if(SERVED){
    try{ const res = await fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({apply_url:url, ...fields})});
      if(!res.ok) throw 0;
      if(fields.status==='applied' && r && !r.applied_on) r.applied_on = new Date().toISOString().slice(0,10);
    }catch(e){ toast('Could not save — is the server running?','err'); }
  } else {
    const o=LS.get(); o[url]=Object.assign(o[url]||{}, fields); LS.set(o);
  }
}

async function addUrl(kind){
  const url = $('#cmd').value.trim();
  if(!url){ $('#cmd').focus(); return; }
  if(!SERVED){ toast('Run  scripts/serve.py  to add jobs & companies.','err'); return; }
  const ep = kind==='job' ? '/add-job' : '/add-company';
  toast('<span class="spin"></span>'+(kind==='job'?'Scraping & scoring…':'Resolving & verifying…'), 'ok', true);
  try{
    const res = await fetch(ep,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const j = await res.json();
    if(!j.ok){ toast(j.error||'Failed.','err'); return; }
    $('#cmd').value='';
    if(kind==='job' && j.row){
      const i = DATA.findIndex(x=>x.apply_url===j.row.apply_url);
      if(i>=0) DATA[i]=j.row; else DATA.unshift(j.row);
      buildFilters(); render(); selectRow(j.row.apply_url); toast(j.message||'Added.','ok');
    } else { toast(j.message||'Watching company.','ok'); }
  }catch(e){ toast('Request failed.','err'); }
}

let toastT;
function toast(html, cls, sticky){ const t=$('#toast'); t.className=cls||''; t.innerHTML=html; t.classList.add('show');
  clearTimeout(toastT); if(!sticky) toastT=setTimeout(()=>t.classList.remove('show'), 3200); }

// ── filters / list ──
function uniq(key){ return [...new Set(DATA.map(r=>r[key]).filter(Boolean))].sort((a,b)=>a.localeCompare(b)); }
function buildFilters(){
  const counts = {all:DATA.length}; PIPE.forEach(s=>counts[s]=DATA.filter(r=>r.status===s).length);
  const segs = [['all','All'],['interested','★ Interested'],['applied','✓ Applied'],['interview','◇ Interview'],['offer','◆ Offer']];
  $('#seg').innerHTML = segs.map(([s,l])=>`<button data-s="${s}" class="${state.stage===s?'on':''}">${l}<span class="n">${counts[s]||0}</span></button>`).join('');
  $('#seg').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.stage=b.dataset.s;render()});
  fill('#company', uniq('company'), 'All companies'); fill('#source', uniq('source'), 'All sources');
}
function fill(sel,vals,all){ const e=$(sel), cur=e.value;
  e.innerHTML=`<option value="">${all}</option>`+vals.map(v=>`<option ${v===cur?'selected':''}>${esc(v)}</option>`).join(''); }

function filtered(){
  let rows = DATA.filter(r=>{
    if(state.stage!=='all' && r.status!==state.stage) return false;
    if(state.stage==='all' && r.status==='stale' && !state.q && !state.company) return false;
    if(state.company && r.company!==state.company) return false;
    if(state.source && r.source!==state.source) return false;
    if(state.q){ const h=(r.title+' '+r.company+' '+r.location+' '+r.notes+' '+r.rationale).toLowerCase();
      if(!h.includes(state.q)) return false; }
    return true;
  });
  const sv=r=>{const v=num(r.score);return v==null?-1:v};
  rows.sort((a,b)=> state.sort==='company'?a.company.localeCompare(b.company)||sv(b)-sv(a)
    : state.sort==='seen'?(b.first_seen||'').localeCompare(a.first_seen||'') : sv(b)-sv(a));
  return rows;
}

function render(){
  buildFilters();
  const rows = filtered();
  $('#count').innerHTML = `<b>${rows.length}</b> of ${DATA.length} roles`;
  $('#list').innerHTML = rows.length ? rows.map((r,i)=>rowHtml(r,i)).join('')
    : '<div class="empty" style="padding:60px 20px">No roles match.</div>';
  $('#list').querySelectorAll('.row').forEach(el=>el.onclick=()=>selectRow(el.dataset.url));
  if(sel && rows.some(r=>r.apply_url===sel)) renderDetail(); else if(!rows.some(r=>r.apply_url===sel)) { sel=null; renderDetail(); }
}
function rowHtml(r,i){
  const s=num(r.score), h=heat(s);
  return `<div class="row ${r.apply_url===sel?'sel':''} ${r.status==='stale'?'stale':''}" data-url="${esc(r.apply_url)}" style="animation-delay:${Math.min(i*14,300)}ms">
    <div class="sc" style="background:${h.bg};border-color:${h.bd};color:${h.fg}"><span class="v">${s==null?'—':s.toFixed(0)}</span><span class="k">SCORE</span></div>
    <div><div class="rtitle">${esc(r.title)}</div><div class="rmeta">${esc(r.company)} <span class="src">· ${esc(r.source)}</span>${r.location?' · '+esc(r.location):''}</div></div>
    <div class="pdot ${esc(r.status)}" title="${esc(r.status)}"></div></div>`;
}
function selectRow(url){ sel=url; render(); document.querySelector('.detail').scrollTop=0; }

// ── detail ──
function renderDetail(){
  const r = DATA.find(x=>x.apply_url===sel);
  const d = $('#detail');
  if(!r){ d.innerHTML = `<div class="empty"><div class="big">⌖</div><div>Select a role to see the detail,<br>rationale, and your notes.</div></div>`; return; }
  const s=num(r.score), h=heat(s);
  const dims = DIMS.map(([k,l])=>{ const v=num(r[k]); return `<div class="dim"><span class="dl">${l}</span><span class="bar"><i style="width:${v==null?0:Math.round(100*v/SCALE_MAX)}%"></i></span></div>`;}).join('');
  const flags=(r.red_flags||'').split(',').map(x=>x.trim()).filter(Boolean);
  const exits = [['pass','✕ Pass'],['archived','⌫ Archive']];
  d.innerHTML = `
    <div class="dhead">
      <div class="dsc" style="background:${h.bg};border-color:${h.bd};color:${h.fg}"><span class="v">${s==null?'—':s.toFixed(0)}</span><span class="k">SCORE</span></div>
      <div style="min-width:0">
        <div class="dtitle">${esc(r.title)}</div>
        <div class="dmeta"><span class="co">${esc(r.company)}</span>${r.location?'<span>· '+esc(r.location)+'</span>':''}<span class="src">${esc(r.source)}</span>${r.date_posted?'<span>· posted '+esc(r.date_posted)+'</span>':''}${r.first_seen?'<span style="color:var(--ink-faint)">· seen '+esc(r.first_seen)+'</span>':''}</div>
        ${r.apply_url?`<a class="open" href="${esc(r.apply_url)}" target="_blank" rel="noopener">Open posting ↗</a>`:''}
      </div>
    </div>
    <div class="sect"><div class="slabel">Pipeline</div>
      <div class="pipe">
        ${PIPE.map(p=>`<button class="stage ${r.status===p?'on':''}" data-s="${p}">${p[0].toUpperCase()+p.slice(1)}</button>`).join('')}
        ${exits.map(([s2,l])=>`<button class="stage exit ${r.status===s2?'on':''}" data-s="${s2}">${l}</button>`).join('')}
      </div>${r.applied_on?`<div class="appdate">Applied ${esc(r.applied_on)}</div>`:''}
    </div>
    ${num(r.mission)!=null||num(r.score)!=null?`<div class="sect"><div class="slabel">Fit by dimension</div><div class="dims">${dims}</div></div>`:''}
    ${r.comp_estimate?`<div class="sect"><div class="slabel">Compensation</div><div class="comp">${esc(r.comp_estimate)}</div></div>`:''}
    ${r.rationale?`<div class="sect"><div class="slabel">Why this scored</div><div class="rat">${esc(r.rationale)}</div></div>`:''}
    ${flags.length?`<div class="sect"><div class="slabel">Red flags</div><div class="flags">${flags.map(f=>`<span class="flag">${esc(f)}</span>`).join('')}</div></div>`:''}
    <div class="sect"><div class="slabel">Your notes</div>
      <textarea class="notes" id="notes" placeholder="referred by… · follow up on… · recruiter name… · why you like it">${esc(r.notes)}</textarea>
      <div class="notehint" id="notehint">${SERVED?'autosaves':'saved locally (run the server to persist to the tracker)'}</div>
    </div>`;
  d.querySelectorAll('.stage').forEach(b=>b.onclick=()=>{ save(r.apply_url,{status:b.dataset.s}); render(); });
  const ta = $('#notes'); let nt;
  ta.oninput=()=>{ clearTimeout(nt); nt=setTimeout(()=>{ save(r.apply_url,{notes:ta.value}); $('#notehint').textContent=SERVED?'saved ✓':'saved locally'; }, 600); };
}

// ── wire up ──
function setConn(){ const c=$('#conn'); if(SERVED){ c.classList.add('live'); $('#connt').textContent='live'; }
  else { $('#addJob').disabled=true; $('#addCo').disabled=true; } }
$('#q').oninput=e=>{state.q=e.target.value.trim().toLowerCase();render()};
$('#company').onchange=e=>{state.company=e.target.value;render()};
$('#source').onchange=e=>{state.source=e.target.value;render()};
$('#sort').onchange=e=>{state.sort=e.target.value;render()};
$('#addJob').onclick=()=>addUrl('job'); $('#addCo').onclick=()=>addUrl('company');
$('#cmd').onkeydown=e=>{ if(e.key==='Enter') addUrl(/\/jobs?\/|\/job\/|currentJobId|gh_jid|\/postings?\//.test($('#cmd').value)?'job':'company'); };
setConn();
const _first = filtered()[0]; if(_first) sel = _first.apply_url;  // open the top role by default
render();
</script>
</body>
</html>
"""
