"""FastAPI wrapper around the bootstrapping flow.

    POST /bootstrap  {"repo_url": "...", "open_pr": true}  -> BootstrapResult

Run with:  uvicorn bootstrap.service:app  (or `python -m bootstrap --serve`)
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .contracts import BootstrapResult
from .core import bootstrap

app = FastAPI(title="CI Bootstrapping Agent", version="0.1.0")


class BootstrapRequest(BaseModel):
    repo_url: str
    open_pr: bool = True


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return INDEX_HTML


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/bootstrap", response_model=BootstrapResult)
def bootstrap_endpoint(req: BootstrapRequest) -> BootstrapResult:
    # bootstrap() converts stage failures into a structured result, so the
    # endpoint returns 200 with status="error" rather than raising -- callers
    # get the classification/workflow context even on failure.
    return bootstrap(req.repo_url, open_pr=req.open_pr)


# A minimal single-page UI served at "/": paste a repo URL, click Run, see the
# result (PR link + authored workflow) without touching curl or the CLI.
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CI Bootstrapping Agent</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 780px; margin: 40px auto; padding: 0 20px; line-height: 1.5; }
  h1 { font-size: 1.5rem; margin-bottom: .25rem; }
  p.sub { color: #6b7280; margin-top: 0; }
  form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 24px 0 8px; }
  input[type=url] { flex: 1 1 320px; padding: 10px 12px; font-size: 1rem;
                    border: 1px solid #9ca3af; border-radius: 8px; }
  button { padding: 10px 18px; font-size: 1rem; font-weight: 600; border: 0; border-radius: 8px;
           background: #2a78d6; color: #fff; cursor: pointer; }
  button:disabled { opacity: .6; cursor: progress; }
  label.chk { font-size: .9rem; color: #6b7280; display: flex; align-items: center; gap: 6px; }
  #out { margin-top: 20px; }
  .card { border: 1px solid #d1d5db; border-radius: 10px; padding: 16px; margin-top: 12px; }
  .banner { padding: 12px 16px; border-radius: 10px; font-weight: 600; }
  .ok { background: rgba(12,163,12,.12); color: #0ca30c; }
  .warn { background: rgba(42,120,214,.12); color: #2a78d6; }
  .err { background: rgba(208,59,59,.12); color: #d03b3b; }
  a { color: #2a78d6; }
  table { border-collapse: collapse; width: 100%; font-size: .92rem; }
  td { padding: 4px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
  td.k { color: #6b7280; width: 130px; }
  pre { background: rgba(127,127,127,.12); padding: 12px; border-radius: 8px; overflow-x: auto;
        font-size: .82rem; }
  .spin { display: inline-block; width: 14px; height: 14px; border: 2px solid currentColor;
          border-right-color: transparent; border-radius: 50%; animation: r .7s linear infinite;
          vertical-align: -2px; margin-right: 8px; }
  @keyframes r { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <h1>🤖 CI Bootstrapping Agent</h1>
  <p class="sub">Give it a GitHub repo URL. It classifies the language, writes a CI workflow, and opens a pull request.</p>

  <form id="f">
    <input id="url" type="url" required placeholder="https://github.com/owner/repo" autocomplete="off"/>
    <button id="go" type="submit">Run agent</button>
    <label class="chk"><input id="pr" type="checkbox" checked/> open a pull request</label>
  </form>

  <div id="out"></div>

<script>
const f = document.getElementById('f');
const out = document.getElementById('out');
const go = document.getElementById('go');

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  const repo_url = document.getElementById('url').value.trim();
  const open_pr = document.getElementById('pr').checked;
  go.disabled = true;
  out.innerHTML = '<div class="card"><span class="spin"></span>Running the agent — cloning, classifying, authoring' + (open_pr ? ', opening PR' : '') + '… (~10–30s)</div>';
  try {
    const resp = await fetch('/bootstrap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({repo_url, open_pr})
    });
    const r = await resp.json();
    render(r);
  } catch (err) {
    out.innerHTML = '<div class="banner err">Request failed: ' + esc(String(err)) + '</div>';
  } finally {
    go.disabled = false;
  }
});

function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function render(r){
  let h = '';
  if (r.status === 'opened') {
    h += '<div class="banner ok">✅ Opened PR #' + r.pr_number + ' — <a href="' + esc(r.pr_url) + '" target="_blank" rel="noopener">' + esc(r.pr_url) + '</a></div>';
  } else if (r.status === 'authored_only') {
    h += '<div class="banner warn">ℹ️ Workflow authored &amp; validated (no PR opened)</div>';
  } else {
    h += '<div class="banner err">❌ ' + esc(r.message) + '</div>';
  }
  const c = r.classification;
  if (c) {
    h += '<div class="card"><strong>Classification</strong><table>'
      + row('language', esc(c.language)) + row('ecosystem', esc(c.ecosystem))
      + row('test command', esc(c.test_command))
      + row('confidence', esc(c.confidence + ' (via ' + c.method + ')'))
      + row('evidence', (c.evidence||[]).map(esc).join('<br>'))
      + '</table></div>';
  }
  const w = r.workflow;
  if (w) {
    h += '<div class="card"><strong>Authored workflow</strong> — <code>' + esc(w.path) + '</code>'
      + (w.repaired ? ' (auto-repaired)' : '')
      + '<p class="sub">' + esc(w.rationale) + '</p>'
      + '<pre>' + esc(w.content) + '</pre></div>';
  }
  out.innerHTML = h;
}
function row(k, v){ return '<tr><td class="k">' + k + '</td><td>' + (v==null?'':v) + '</td></tr>'; }
</script>
</body>
</html>
"""
