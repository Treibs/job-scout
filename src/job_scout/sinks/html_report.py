"""Self-contained HTML report — a filterable dashboard built from the CSV tracker.

`render(csv_path)` reads the upserted CSV (so it includes carried-over / stale
rows, not just this run's jobs) and writes a single ``jobs.html`` next to it with
the data embedded inline. No server, no build step, no external data files — open
it in any browser (double-click). Fonts load from Google Fonts when online and
degrade to system fonts offline; everything else works offline.

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

    # Sanitize the only field rendered into an href. Job URLs come from external
    # sources, and HTML-escaping an attribute does NOT neutralize a `javascript:`
    # scheme — so drop anything that isn't a plain http(s) link (the UI then
    # renders a non-clickable title for it). Defends the local dashboard from a
    # malicious posting URL (XSS).
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
    --bg:#0d0e11; --bg2:#14161b; --bg3:#1b1e25; --line:#262a33;
    --ink:#e9e6df; --ink-dim:#9aa0ab; --ink-faint:#646a76;
    --amber:#f5b14c; --amber-dim:#7a5b27; --teal:#54d6c4; --teal-dim:#1f4f4a;
    --mono:'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,monospace;
    --sans:'IBM Plex Sans',system-ui,-apple-system,sans-serif;
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{
    margin:0;background:
      radial-gradient(900px 500px at 88% -8%,rgba(245,177,76,.07),transparent 60%),
      radial-gradient(700px 500px at 0% 0%,rgba(84,214,196,.05),transparent 55%),
      var(--bg);
    color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.5;
    -webkit-font-smoothing:antialiased;
  }
  a{color:var(--teal);text-decoration:none}
  a:hover{text-decoration:underline}

  /* ── masthead ───────────────────────────────────────── */
  header{
    padding:30px 30px 18px;border-bottom:1px solid var(--line);
    background:linear-gradient(180deg,rgba(20,22,27,.6),transparent);
  }
  .brand{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
  .brand h1{
    margin:0;font-family:var(--mono);font-weight:700;font-size:26px;letter-spacing:-.5px;
  }
  .brand h1 .dot{color:var(--amber)}
  .brand .sub{font-family:var(--mono);font-size:11px;color:var(--ink-faint);
    text-transform:uppercase;letter-spacing:3px}
  .gen{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--ink-faint)}

  .stats{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
  .stat{
    background:var(--bg2);border:1px solid var(--line);border-radius:10px;
    padding:10px 16px;min-width:96px;
  }
  .stat .n{font-family:var(--mono);font-size:22px;font-weight:600;line-height:1}
  .stat .l{font-size:10px;color:var(--ink-faint);text-transform:uppercase;
    letter-spacing:1.5px;margin-top:6px}
  .stat.accent .n{color:var(--amber)}

  /* ── controls ───────────────────────────────────────── */
  .controls{
    position:sticky;top:0;z-index:20;
    background:rgba(13,14,17,.86);backdrop-filter:blur(12px);
    border-bottom:1px solid var(--line);padding:14px 30px;
    display:flex;gap:12px;align-items:center;flex-wrap:wrap;
  }
  .field{display:flex;flex-direction:column;gap:4px}
  .field label{font-size:9px;text-transform:uppercase;letter-spacing:1.5px;
    color:var(--ink-faint);font-family:var(--mono)}
  input[type=search],select{
    background:var(--bg2);border:1px solid var(--line);color:var(--ink);
    border-radius:8px;padding:8px 11px;font-family:var(--sans);font-size:13px;
    outline:none;transition:border-color .15s;
  }
  input[type=search]{min-width:240px}
  input[type=search]:focus,select:focus{border-color:var(--amber-dim)}
  input[type=search]::placeholder{color:var(--ink-faint)}
  select{cursor:pointer;appearance:none;padding-right:28px;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath d='M1 3l4 4 4-4' stroke='%23646a76' fill='none' stroke-width='1.5'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 10px center}
  .range{display:flex;flex-direction:column;gap:4px}
  .range .row{display:flex;align-items:center;gap:8px}
  input[type=range]{width:120px;accent-color:var(--amber)}
  .range .val{font-family:var(--mono);font-size:13px;color:var(--amber);min-width:24px}
  .check{display:flex;align-items:center;gap:7px;cursor:pointer;user-select:none;
    font-size:12px;color:var(--ink-dim);margin-top:14px}
  .check input{accent-color:var(--teal);width:15px;height:15px}
  .count{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--ink-dim);
    margin-top:14px}
  .count b{color:var(--ink)}

  /* ── list ───────────────────────────────────────────── */
  main{padding:22px 30px 80px;max-width:1180px}
  .card{
    display:grid;grid-template-columns:64px 1fr;gap:18px;
    background:var(--bg2);border:1px solid var(--line);border-radius:12px;
    padding:16px 18px;margin-bottom:11px;cursor:pointer;
    transition:transform .14s ease,border-color .14s ease,background .14s;
    animation:rise .4s both;
  }
  @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .card:hover{transform:translateY(-2px);border-color:#34394400;
    border-color:var(--amber-dim);background:var(--bg3)}
  .card.stale{opacity:.62}

  .score{
    width:64px;height:64px;border-radius:11px;display:flex;flex-direction:column;
    align-items:center;justify-content:center;border:1px solid;
    font-family:var(--mono);
  }
  .score .v{font-size:23px;font-weight:700;line-height:1}
  .score .k{font-size:8px;letter-spacing:1.5px;text-transform:uppercase;margin-top:3px;opacity:.7}

  .body{min-width:0}
  .titlerow{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
  .title{font-size:16px;font-weight:600;color:var(--ink);letter-spacing:-.2px}
  .title:hover{color:var(--teal)}
  .pill{font-family:var(--mono);font-size:9.5px;text-transform:uppercase;
    letter-spacing:1px;padding:2px 8px;border-radius:20px;border:1px solid var(--line)}
  .pill.new{color:var(--teal);border-color:var(--teal-dim);background:rgba(84,214,196,.07)}
  .pill.stale{color:var(--ink-faint)}
  .pill.applied{color:#7ec97e;border-color:#2c512c}
  .pill.reviewing{color:var(--amber);border-color:var(--amber-dim)}
  .pill.rejected,.pill.archived{color:var(--ink-faint)}

  .meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px;
    color:var(--ink-dim);font-size:12.5px}
  .meta .co{color:var(--ink);font-weight:500}
  .meta .sep{color:var(--ink-faint)}
  .meta .src{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;
    letter-spacing:.5px;color:var(--ink-faint);border:1px solid var(--line);
    padding:1px 7px;border-radius:5px}

  .dims{display:flex;gap:14px;margin-top:11px;flex-wrap:wrap}
  .dim{display:flex;align-items:center;gap:6px}
  .dim .dl{font-family:var(--mono);font-size:9px;color:var(--ink-faint);
    text-transform:uppercase;letter-spacing:.5px;width:34px}
  .dim .bar{width:54px;height:5px;border-radius:3px;background:var(--bg3);overflow:hidden}
  .dim .bar i{display:block;height:100%;background:var(--amber);border-radius:3px}

  .detail{display:none;margin-top:13px;padding-top:13px;border-top:1px solid var(--line)}
  .card.open .detail{display:block;animation:rise .25s both}
  .detail .rat{color:var(--ink-dim);font-size:13px;line-height:1.65}
  .detail .comp{font-family:var(--mono);font-size:12px;color:var(--teal);margin-top:8px}
  .flags{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}
  .flag{font-size:11px;color:#e8a59a;background:rgba(232,115,107,.08);
    border:1px solid rgba(232,115,107,.2);border-radius:6px;padding:3px 9px}
  .chev{margin-left:auto;color:var(--ink-faint);font-family:var(--mono);font-size:11px;
    align-self:center;transition:transform .2s}
  .card.open .chev{transform:rotate(90deg)}

  .empty{text-align:center;padding:80px 20px;color:var(--ink-faint);font-family:var(--mono)}
  footer{padding:24px 30px;border-top:1px solid var(--line);color:var(--ink-faint);
    font-family:var(--mono);font-size:11px}
  @media(max-width:640px){
    .card{grid-template-columns:52px 1fr;gap:12px}
    .score{width:52px;height:52px}.score .v{font-size:19px}
    header,.controls,main{padding-left:16px;padding-right:16px}
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>JOB<span class="dot">/</span>SCOUT</h1>
    <span class="sub">AI · Innovation · Leadership — Chicago</span>
    <span class="gen">updated __GENERATED__</span>
  </div>
  <div class="stats" id="stats"></div>
</header>

<div class="controls">
  <div class="field"><label>Search</label>
    <input type="search" id="q" placeholder="title, company, rationale…"></div>
  <div class="field"><label>Company</label><select id="company"></select></div>
  <div class="field"><label>Source</label><select id="source"></select></div>
  <div class="field"><label>Status</label><select id="status"></select></div>
  <div class="field range"><label>Min score</label>
    <div class="row"><input type="range" id="minscore" min="0" max="100" value="0" step="5">
    <span class="val" id="minval">0</span></div></div>
  <div class="field"><label>Sort</label><select id="sort">
    <option value="score-desc">Score ▾</option>
    <option value="score-asc">Score ▴</option>
    <option value="seen-desc">Newest seen</option>
    <option value="company">Company A–Z</option>
    <option value="title">Title A–Z</option>
  </select></div>
  <label class="check"><input type="checkbox" id="hidestale" checked> Hide stale</label>
  <div class="count" id="count"></div>
</div>

<main id="list"></main>
<footer>job-scout · MiniMax-scored against résumé · click a card to expand rationale &amp; red flags</footer>

<script>
const DATA = /*__DATA__*/null;
const DIMS = [["mission","MIS"],["comp","COMP"],["learning","LRN"],["wlb","WLB"],["prestige","PRES"]];
const SCALE_MAX = 5;

const num = v => { const n = parseFloat(v); return isNaN(n) ? null : n; };
function heat(s){
  if(s==null) return {bg:'#1b1e25',bd:'#262a33',fg:'#646a76'};
  if(s>=75) return {bg:'rgba(110,231,168,.12)',bd:'#2f6b4d',fg:'#7eeaa8'};
  if(s>=60) return {bg:'rgba(182,227,106,.12)',bd:'#566b2f',fg:'#c2e36a'};
  if(s>=45) return {bg:'rgba(245,196,81,.12)',bd:'#6b562a',fg:'#f5c451'};
  if(s>=30) return {bg:'rgba(240,146,90,.12)',bd:'#6b452a',fg:'#f0925a'};
  return {bg:'rgba(232,115,107,.10)',bd:'#6b3330',fg:'#e8736b'};
}
const esc = s => (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// ── populate filters ──
const elQ=document.getElementById('q'), elCo=document.getElementById('company'),
  elSrc=document.getElementById('source'), elSt=document.getElementById('status'),
  elMin=document.getElementById('minscore'), elMinV=document.getElementById('minval'),
  elSort=document.getElementById('sort'), elHide=document.getElementById('hidestale'),
  elList=document.getElementById('list'), elCount=document.getElementById('count');

function fillSelect(el,vals,allLabel){
  el.innerHTML = '<option value="">'+allLabel+'</option>' +
    vals.map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('');
}
const uniq = key => [...new Set(DATA.map(r=>r[key]).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
fillSelect(elCo, uniq('company'), 'All companies');
fillSelect(elSrc, uniq('source'), 'All sources');
fillSelect(elSt, uniq('status'), 'All statuses');

// ── stats ──
(function(){
  const total=DATA.length, news=DATA.filter(r=>r.status==='new').length,
    stale=DATA.filter(r=>r.status==='stale').length,
    scored=DATA.map(r=>num(r.score)).filter(v=>v!=null),
    top=scored.length?Math.max(...scored):0,
    avg=scored.length?(scored.reduce((a,b)=>a+b,0)/scored.length):0;
  const s=[['Total',total,''],['New',news,'accent'],['Stale',stale,''],
    ['Top score',top.toFixed(0),'accent'],['Avg score',avg.toFixed(0),'']];
  document.getElementById('stats').innerHTML = s.map(([l,n,c])=>
    '<div class="stat '+c+'"><div class="n">'+n+'</div><div class="l">'+l+'</div></div>').join('');
})();

// ── render ──
function card(r,i){
  const s=num(r.score), h=heat(s), stale=r.status==='stale';
  const dims = DIMS.map(([k,lab])=>{
    const v=num(r[k]); const pct=v==null?0:Math.round(100*v/SCALE_MAX);
    return '<div class="dim"><span class="dl">'+lab+'</span><span class="bar"><i style="width:'+pct+'%"></i></span></div>';
  }).join('');
  const flags=(r.red_flags||'').split(',').map(x=>x.trim()).filter(Boolean);
  const flagsHtml = flags.length? '<div class="flags">'+flags.map(f=>'<span class="flag">'+esc(f)+'</span>').join('')+'</div>':'';
  const compHtml = r.comp_estimate? '<div class="comp">$ '+esc(r.comp_estimate)+'</div>':'';
  const ratHtml = r.rationale? '<div class="rat">'+esc(r.rationale)+'</div>':'<div class="rat" style="color:var(--ink-faint)">No rationale recorded.</div>';
  const titleInner = r.apply_url
    ? '<a class="title" href="'+esc(r.apply_url)+'" target="_blank" rel="noopener" onclick="event.stopPropagation()">'+esc(r.title)+'</a>'
    : '<span class="title">'+esc(r.title)+'</span>';
  const when = r.first_seen ? 'first seen '+esc(r.first_seen) : '';
  return '<article class="card'+(stale?' stale':'')+'" style="animation-delay:'+Math.min(i*22,400)+'ms" onclick="this.classList.toggle(\'open\')">'
    + '<div class="score" style="background:'+h.bg+';border-color:'+h.bd+';color:'+h.fg+'">'
    +   '<span class="v">'+(s==null?'—':s.toFixed(0))+'</span><span class="k">score</span></div>'
    + '<div class="body">'
    +   '<div class="titlerow">'+titleInner
    +     '<span class="pill '+esc(r.status)+'">'+esc(r.status||'')+'</span><span class="chev">▸</span></div>'
    +   '<div class="meta"><span class="co">'+esc(r.company)+'</span>'
    +     (r.location?'<span class="sep">·</span><span>'+esc(r.location)+'</span>':'')
    +     (r.source?'<span class="src">'+esc(r.source)+'</span>':'')
    +     (r.date_posted?'<span class="sep">·</span><span>'+esc(r.date_posted)+'</span>':'')
    +     (when?'<span class="sep">·</span><span style="color:var(--ink-faint)">'+when+'</span>':'')+'</div>'
    +   '<div class="dims">'+dims+'</div>'
    +   '<div class="detail">'+ratHtml+compHtml+flagsHtml+'</div>'
    + '</div></article>';
}

function apply(){
  const q=elQ.value.trim().toLowerCase(), co=elCo.value, src=elSrc.value,
    st=elSt.value, min=parseFloat(elMin.value), hide=elHide.checked, sort=elSort.value;
  let rows = DATA.filter(r=>{
    if(co && r.company!==co) return false;
    if(src && r.source!==src) return false;
    if(st && r.status!==st) return false;
    if(hide && r.status==='stale') return false;
    const s=num(r.score); if(min>0 && (s==null || s<min)) return false;
    if(q){
      const hay=(r.title+' '+r.company+' '+r.location+' '+r.rationale+' '+r.red_flags).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  const sv=r=>{const v=num(r.score);return v==null?-1:v;};
  rows.sort((a,b)=>{
    switch(sort){
      case 'score-asc': return sv(a)-sv(b);
      case 'seen-desc': return (b.first_seen||'').localeCompare(a.first_seen||'');
      case 'company': return a.company.localeCompare(b.company)||sv(b)-sv(a);
      case 'title': return a.title.localeCompare(b.title);
      default: return sv(b)-sv(a);
    }
  });
  elCount.innerHTML='<b>'+rows.length+'</b> of '+DATA.length+' roles';
  elList.innerHTML = rows.length
    ? rows.map((r,i)=>card(r,i)).join('')
    : '<div class="empty">No roles match these filters.</div>';
}
elMin.addEventListener('input',()=>{elMinV.textContent=elMin.value;apply();});
[elQ,elCo,elSrc,elSt,elSort,elHide].forEach(e=>e.addEventListener('input',apply));
apply();
</script>
</body>
</html>
"""
