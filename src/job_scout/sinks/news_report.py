"""Self-contained News dashboard built from state/news.json.

Two-pane, like the jobs CRM: a filterable headline list on the left, a detail pane
on the right with the 2-paragraph summary and (when we could extract it) the full
article text. Jobs <-> News nav. Served by ``scripts/serve.py`` it's a live app
(feedback persists); opened as a file it's read-only with a localStorage fallback.

Stdlib only (json, html, datetime, pathlib).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..news import store as news_store

log = logging.getLogger("job_scout.news_report")

_FIELDS = ("title", "url", "source", "published", "first_seen", "relevance",
           "summary", "body_text", "topic", "useful", "valuable", "status")


def _safe_http_url(url: str) -> str:
    u = (url or "").strip()
    return u if u[:7].lower() == "http://" or u[:8].lower() == "https://" else ""


def render(store_path=None, html_path="output/news.html", generated_at: str | None = None) -> Path:
    """Render the news feed to ``html_path``. Always writes (empty feed -> empty state)."""
    store = news_store.load(store_path)
    items = []
    for it in news_store.items_sorted(store):
        row = {k: it.get(k) for k in _FIELDS}
        row["url"] = _safe_http_url(row.get("url") or "")
        row["topic"] = news_store.normalize_topic(row.get("topic"))
        items.append(row)

    data_json = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    generated = generated_at or datetime.now().strftime("%b %d, %Y · %H:%M")
    # Replace the static token FIRST (author-controlled template), then inject the
    # data LAST — so article data containing "__GENERATED__" can't be clobbered.
    out = (_TEMPLATE
           .replace("__GENERATED__", generated)
           .replace("/*__DATA__*/null", data_json))

    html_path = Path(html_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = html_path.with_suffix(html_path.suffix + ".tmp")
    tmp.write_text(out, encoding="utf-8")
    tmp.replace(html_path)
    log.info("news report: %d items -> %s", len(items), html_path)
    return html_path


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Scout · News</title>
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
  ::-webkit-scrollbar{width:10px} ::-webkit-scrollbar-thumb{background:var(--bg4);border-radius:6px;border:2px solid var(--bg)}

  header{display:flex;align-items:center;gap:18px;padding:13px 22px;border-bottom:1px solid var(--line);
    background:linear-gradient(180deg,rgba(18,20,25,.7),transparent)}
  .brand{font-family:var(--mono);font-weight:700;font-size:19px;letter-spacing:-.5px;white-space:nowrap}
  .brand .d{color:var(--amber)}
  .nav{display:flex;gap:6px}
  .nav a{font-family:var(--mono);font-size:12px;letter-spacing:.4px;padding:7px 13px;border-radius:9px;
    border:1px solid var(--line2);background:var(--bg3);color:var(--ink-dim)}
  .nav a:hover{color:var(--ink);text-decoration:none;border-color:var(--amber-dim)}
  .nav a.on{background:var(--bg4);color:var(--amber);border-color:var(--amber-dim)}
  .conn{margin-left:auto;font-family:var(--mono);font-size:10.5px;letter-spacing:1px;text-transform:uppercase;
    display:flex;align-items:center;gap:7px;color:var(--ink-faint)}
  .conn .dot{width:7px;height:7px;border-radius:50%;background:var(--ink-faint)}
  .conn.live .dot{background:var(--green);box-shadow:0 0 8px var(--green)} .conn.live{color:var(--green)}

  .app{flex:1;display:grid;grid-template-columns:minmax(380px,38%) 1fr;min-height:0}
  .pane{min-height:0;display:flex;flex-direction:column} .left{border-right:1px solid var(--line)}

  .filters{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap;
    background:rgba(11,12,15,.7);backdrop-filter:blur(8px)}
  .filters input[type=search],.filters select{background:var(--bg2);border:1px solid var(--line);color:var(--ink);
    border-radius:8px;padding:7px 10px;font-family:var(--sans);font-size:12.5px;outline:none}
  .filters input[type=search]{flex:1;min-width:140px}
  .filters select{cursor:pointer;font-family:var(--mono);font-size:11px}
  .filters input:focus,.filters select:focus{border-color:var(--amber-dim)}
  .ftoggle{font-family:var(--mono);font-size:11px;border:1px solid var(--line);background:var(--bg2);
    color:var(--ink-faint);border-radius:8px;padding:7px 10px;cursor:pointer;white-space:nowrap}
  .ftoggle.on{background:rgba(245,177,76,.12);border-color:var(--amber-dim);color:var(--amber)}
  .lcount{font-family:var(--mono);font-size:11px;color:var(--ink-faint);padding:8px 18px;border-bottom:1px solid var(--line)}
  .lcount b{color:var(--ink-dim)}

  .list{flex:1;overflow-y:auto;padding:8px}
  .row{display:grid;grid-template-columns:42px 1fr;gap:12px;align-items:start;padding:12px;border-radius:11px;
    cursor:pointer;border:1px solid transparent;transition:.12s;animation:rise .3s both}
  @keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  .row:hover{background:var(--bg2)} .row.sel{background:var(--bg3);border-color:var(--line2)}
  .row.dismissed{opacity:.45}
  .rc{width:42px;height:42px;border-radius:9px;display:flex;flex-direction:column;align-items:center;justify-content:center;
    border:1px solid;font-family:var(--mono)}
  .rc .v{font-size:15px;font-weight:700;line-height:1} .rc .k{font-size:6.5px;letter-spacing:1px;opacity:.6;margin-top:1px}
  .rtitle{font-size:13.5px;font-weight:600;color:var(--ink);line-height:1.3}
  .rmeta{font-size:11px;color:var(--ink-faint);margin-top:4px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
  .rmeta .src{color:var(--ink-dim)}
  .tag{font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.5px;border:1px solid var(--line2);
    border-radius:5px;padding:0 5px;color:var(--ink-faint)}
  .tag.role-trend{color:var(--teal);border-color:var(--teal-dim)} .tag.sector{color:var(--amber);border-color:var(--amber-dim)}

  .detail{flex:1;overflow-y:auto;padding:30px 40px 70px;max-width:860px}
  .empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;
    color:var(--ink-faint);font-family:var(--mono);gap:12px;text-align:center;padding:40px}
  .empty .big{font-size:42px;opacity:.3}
  .dhead{display:flex;gap:20px;align-items:flex-start}
  .drc{width:72px;height:72px;border-radius:13px;display:flex;flex-direction:column;align-items:center;justify-content:center;
    border:1px solid;font-family:var(--mono);flex-shrink:0}
  .drc .v{font-size:26px;font-weight:700;line-height:1} .drc .k{font-size:8px;letter-spacing:1.5px;opacity:.6;margin-top:3px}
  .dtitle{font-size:23px;font-weight:600;letter-spacing:-.3px;line-height:1.25}
  .dmeta{margin-top:9px;color:var(--ink-dim);font-size:13px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
  .dmeta .src{color:var(--ink);font-weight:600}
  .open{margin-top:14px;display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:13px;
    border:1px solid var(--teal-dim);color:var(--teal);background:rgba(84,214,196,.07);border-radius:9px;padding:9px 16px}
  .open:hover{background:rgba(84,214,196,.14);text-decoration:none}
  .sect{margin-top:26px}
  .slabel{font-family:var(--mono);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--ink-faint);margin-bottom:12px}
  .summary p{color:#d3d6dd;font-size:15px;line-height:1.7;margin-bottom:14px}
  details.col{margin-top:24px}
  details.col>summary{font-family:var(--mono);font-size:11px;letter-spacing:2px;text-transform:uppercase;
    color:var(--ink-faint);cursor:pointer;list-style:none;display:flex;align-items:center;gap:9px;user-select:none}
  details.col>summary::-webkit-details-marker{display:none}
  details.col>summary::before{content:'▸';color:var(--amber);transition:transform .15s} details.col[open]>summary::before{transform:rotate(90deg)}
  .body{margin-top:13px;color:var(--ink-dim);font-size:13.5px;line-height:1.7;white-space:pre-wrap;
    max-height:380px;overflow-y:auto;border-left:2px solid var(--line2);padding:2px 0 2px 14px}
  .pipe{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px}
  .act{font-family:var(--mono);font-size:12px;border:1px solid var(--line2);background:var(--bg2);color:var(--ink-dim);
    border-radius:9px;padding:8px 13px;cursor:pointer;transition:.12s}
  .act:hover{border-color:var(--ink-faint);color:var(--ink)}
  .act.up.on{background:rgba(126,234,168,.14);border-color:var(--green-dim);color:var(--green)}
  .act.down.on{background:rgba(232,115,107,.12);border-color:var(--rose-dim);color:#e8a59a}
  .act.val.on{background:rgba(245,177,76,.14);border-color:var(--amber-dim);color:var(--amber)}
  .act.save.on{background:rgba(84,214,196,.12);border-color:var(--teal-dim);color:var(--teal)}
  #toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);opacity:0;
    background:var(--bg3);border:1px solid var(--line2);border-radius:11px;padding:12px 18px;font-size:13px;
    transition:.25s;pointer-events:none;z-index:50}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)} #toast.err{border-color:var(--rose-dim);color:#e8a59a}
</style>
</head>
<body>
<header>
  <div class="brand">JOB<span class="d">/</span>SCOUT</div>
  <div class="nav"><a id="navJobs" href="/">Jobs</a><a class="on" href="/news">News</a></div>
  <div class="conn" id="conn"><span class="dot"></span><span id="connt">read-only</span></div>
</header>

<div class="app">
  <div class="pane left">
    <div class="filters">
      <input type="search" id="q" placeholder="search headlines, summaries…">
      <select id="topic"><option value="">All topics</option><option value="role-trend">Role / trend</option><option value="sector">Sector</option><option value="other">Other</option></select>
      <select id="minrel"><option value="0">Any rel.</option><option value="0.6">≥60</option><option value="0.75">≥75</option><option value="0.9">≥90</option></select>
      <button id="savedOnly" class="ftoggle" title="Only saved">🔖</button>
      <button id="hideDismissed" class="ftoggle on" title="Hide dismissed">🙈</button>
    </div>
    <div class="lcount" id="count"></div>
    <div class="list" id="list"></div>
  </div>
  <div class="pane"><div class="detail" id="detail"></div></div>
</div>
<div id="toast"></div>

<script>
const DATA = /*__DATA__*/null;
const GEN = "__GENERATED__";
const SERVED = location.protocol === 'http:' || location.protocol === 'https:';
const $ = s => document.querySelector(s);
const esc = s => (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = v => { const n=parseFloat(v); return isNaN(n)?null:n; };

const LS = { get(){try{return JSON.parse(localStorage.jobscout_news||'{}')}catch(e){return {}}},
  set(o){localStorage.jobscout_news=JSON.stringify(o)} };
if(!SERVED){ const o=LS.get(); DATA.forEach(r=>{ const e=o[r.url]; if(e) Object.assign(r,e); });
  const nj=$('#navJobs'); if(nj) nj.setAttribute('href','jobs.html'); }

let sel = null;
const state = { q:'', topic:'', minrel:0, savedOnly:false, hideDismissed:true };

function relColor(s){
  if(s==null) return {bg:'var(--bg3)',bd:'var(--line)',fg:'var(--ink-faint)'};
  if(s>=0.85) return {bg:'rgba(126,234,168,.12)',bd:'#2f6b4d',fg:'#7eeaa8'};
  if(s>=0.7) return {bg:'rgba(194,227,106,.12)',bd:'#566b2f',fg:'#c2e36a'};
  if(s>=0.55) return {bg:'rgba(245,196,81,.12)',bd:'#6b562a',fg:'#f5c451'};
  return {bg:'rgba(240,146,90,.12)',bd:'#6b452a',fg:'#f0925a'};
}
function fmtDate(s){ if(!s) return ''; const d=new Date(s); return isNaN(d)?'':d.toLocaleDateString(undefined,{month:'short',day:'numeric'}); }

async function save(url, fields){
  const r = DATA.find(x=>x.url===url); if(!r) return;
  const prev={}; Object.keys(fields).forEach(k=>prev[k]=r[k]); Object.assign(r, fields); render();
  if(SERVED){
    try{ const res=await fetch('/news-feedback',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url, ...fields})}); if(!res.ok) throw 0; }
    catch(e){ Object.assign(r,prev); render(); toast('Could not save — is the server running?','err'); }
  } else { const o=LS.get(); o[url]=Object.assign(o[url]||{}, fields); LS.set(o); }
}
let toastT;
function toast(msg,cls){ const t=$('#toast'); t.className=cls||''; t.textContent=msg; t.classList.add('show');
  clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove('show'),3000); }

function filtered(){
  return DATA.filter(r=>{
    if(state.hideDismissed && r.status==='dismissed') return false;
    if(state.savedOnly && r.status!=='saved') return false;
    if(state.topic && (r.topic||'other')!==state.topic) return false;
    const rel=num(r.relevance); if(state.minrel>0 && (rel==null || rel<state.minrel)) return false;
    if(state.q){ const h=((r.title||'')+' '+(r.summary||'')+' '+(r.source||'')).toLowerCase();
      if(!h.includes(state.q)) return false; }
    return true;
  });
}

function render(){
  const rows = filtered();
  $('#count').innerHTML = `<b>${rows.length}</b> of ${DATA.length} articles · ${esc(GEN)}`;
  $('#list').innerHTML = rows.length ? rows.map(rowHtml).join('')
    : `<div class="empty" style="padding:50px 16px"><div class="big">📰</div><div>${DATA.length?'No articles match.':'No news yet — run <b>python scripts/news.py</b>.'}</div></div>`;
  $('#list').querySelectorAll('.row').forEach(el=>el.onclick=()=>{ sel=el.dataset.url; render(); $('#detail').scrollTop=0; });
  if(!rows.some(r=>r.url===sel)) sel = rows.length ? rows[0].url : null;
  renderDetail();
}
function rowHtml(r){
  const rel=num(r.relevance), c=relColor(rel), tag=(r.topic||'other');
  return `<div class="row ${r.url===sel?'sel':''} ${r.status==='dismissed'?'dismissed':''}" data-url="${esc(r.url)}">
    <div class="rc" style="background:${c.bg};border-color:${c.bd};color:${c.fg}"><span class="v">${rel==null?'—':Math.round(rel*100)}</span><span class="k">REL</span></div>
    <div><div class="rtitle">${esc(r.title)}</div>
      <div class="rmeta"><span class="src">${esc(r.source||'')}</span>${r.published?'<span>· '+esc(fmtDate(r.published))+'</span>':''}<span class="tag ${esc(tag)}">${esc(tag)}</span></div></div>
  </div>`;
}

function paras(text){ return (text||'').split(/\n\s*\n/).map(p=>p.trim()).filter(Boolean); }

function renderDetail(){
  const r = DATA.find(x=>x.url===sel), d=$('#detail');
  if(!r){ d.innerHTML=`<div class="empty"><div class="big">⌖</div><div>Select an article to read the summary.</div></div>`; return; }
  const rel=num(r.relevance), c=relColor(rel), tag=(r.topic||'other');
  const sp = paras(r.summary);
  d.innerHTML = `
    <div class="dhead">
      <div class="drc" style="background:${c.bg};border-color:${c.bd};color:${c.fg}"><span class="v">${rel==null?'—':Math.round(rel*100)}</span><span class="k">REL</span></div>
      <div style="min-width:0">
        <div class="dtitle">${esc(r.title)}</div>
        <div class="dmeta"><span class="src">${esc(r.source||'')}</span>${r.published?'<span>· '+esc(fmtDate(r.published))+'</span>':''}<span class="tag ${esc(tag)}">${esc(tag)}</span></div>
        ${r.url?`<a class="open" href="${esc(r.url)}" target="_blank" rel="noopener">Read original ↗</a>`:''}
      </div>
    </div>
    <div class="sect"><div class="slabel">Summary</div>
      <div class="summary">${sp.length?sp.map(p=>`<p>${esc(p)}</p>`).join(''):'<p style="color:var(--ink-faint)">No summary yet.</p>'}</div></div>
    ${r.body_text?`<details class="col"><summary>Full article text</summary><div class="body">${esc(r.body_text)}</div></details>`:''}
    <div class="sect"><div class="slabel">Feedback</div>
      <div class="pipe">
        <button class="act up ${r.useful==='up'?'on':''}" data-act="up">👍 Relevant</button>
        <button class="act down ${r.useful==='down'?'on':''}" data-act="down">👎 Not</button>
        <button class="act val ${r.valuable?'on':''}" data-act="val">💎 Valuable</button>
        <button class="act save ${r.status==='saved'?'on':''}" data-act="save">🔖 ${r.status==='saved'?'Saved':'Save'}</button>
        <button class="act" data-act="dismiss">${r.status==='dismissed'?'↩ Restore':'✕ Dismiss'}</button>
      </div></div>`;
  d.querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>{
    const a=b.dataset.act;
    if(a==='up') save(r.url,{useful: r.useful==='up'?null:'up'});
    else if(a==='down') save(r.url,{useful: r.useful==='down'?null:'down'});
    else if(a==='val') save(r.url,{valuable: !r.valuable});
    else if(a==='save') save(r.url,{status: r.status==='saved'?'new':'saved'});
    else if(a==='dismiss') save(r.url,{status: r.status==='dismissed'?'new':'dismissed'});
  });
}

function setConn(){ const c=$('#conn'); if(SERVED){ c.classList.add('live'); $('#connt').textContent='live'; } else $('#connt').textContent='read-only'; }
$('#q').oninput=e=>{state.q=e.target.value.trim().toLowerCase();render()};
$('#topic').onchange=e=>{state.topic=e.target.value;render()};
$('#minrel').onchange=e=>{state.minrel=parseFloat(e.target.value)||0;render()};
$('#savedOnly').onclick=()=>{state.savedOnly=!state.savedOnly;$('#savedOnly').classList.toggle('on',state.savedOnly);render()};
$('#hideDismissed').onclick=()=>{state.hideDismissed=!state.hideDismissed;$('#hideDismissed').classList.toggle('on',state.hideDismissed);render()};
setConn();
const _first = filtered()[0]; if(_first) sel=_first.url;
render();
</script>
</body>
</html>
"""
