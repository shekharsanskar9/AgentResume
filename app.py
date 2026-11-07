"""
ResumeFit web app - resume upload, match report and an application-tracker dashboard.

    pip install fastapi uvicorn python-multipart pypdf python-docx
    uvicorn app:app --reload          ->  http://127.0.0.1:8000   (API docs at /docs)

All logic lives in main.py; this file is a thin FastAPI layer over it, sharing the same
applications.db as the CLI.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

import main as rf

MAX_UPLOAD_BYTES = 5 * 1024 * 1024

app = FastAPI(title="ResumeFit", version="2.0",
              description="Match a resume to job descriptions and track applications.")


@app.exception_handler(rf.ResumeFitError)
async def resumefit_error(_: Request, exc: rf.ResumeFitError) -> JSONResponse:
    status = 404 if isinstance(exc, rf.NotFoundError) else 400
    return JSONResponse(status_code=status, content={"detail": str(exc)})


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ApplicationIn(BaseModel):
    company: str = Field(min_length=1)
    role: str = Field(min_length=1)
    status: str = "applied"
    score: Optional[int] = Field(default=None, ge=0, le=100)
    link: Optional[str] = None
    notes: Optional[str] = None
    jd: Optional[str] = None
    missing_skills: Optional[List[str]] = None
    follow_up: Optional[str] = Field(default=None, description="YYYY-MM-DD, today, tomorrow or +N")


class ApplicationUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    add_note: Optional[str] = None
    link: Optional[str] = None
    score: Optional[int] = Field(default=None, ge=0, le=100)
    follow_up: Optional[str] = Field(default=None, description="date, +N, or empty string to clear")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def resume_from_upload(upload: Optional[UploadFile], pasted: Optional[str]) -> str:
    """Resume text from an uploaded file, or from pasted text."""
    if upload is not None and upload.filename:
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in rf.RESUME_SUFFIXES:
            raise rf.ResumeFitError(f"unsupported file type '{suffix}' (use .pdf, .docx, .txt or .md)")
        data = upload.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise rf.ResumeFitError("file is larger than 5 MB")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
        try:
            return rf.read_text(tmp.name)
        finally:
            os.unlink(tmp.name)
    if pasted and pasted.strip():
        return pasted
    raise rf.ResumeFitError("upload a resume file or paste the resume text")


# --------------------------------------------------------------------------- #
# Matching API
# --------------------------------------------------------------------------- #
@app.post("/api/match")
def api_match(
    jd: str = Form(..., min_length=20),
    resume: Optional[UploadFile] = File(None),
    resume_text: Optional[str] = Form(None),
    use_llm: bool = Form(False),
    model: str = Form(rf.DEFAULT_LLM_MODEL),
):
    """Score a resume against a JD and return the report plus tailoring tips."""
    text = resume_from_upload(resume, resume_text)
    result = rf.suggest(text, jd)
    if use_llm:
        result["llm_suggestions"] = rf.llm_suggestions(text, jd, result, model)
    return result


# --------------------------------------------------------------------------- #
# Tracker API
# --------------------------------------------------------------------------- #
@app.get("/api/applications")
def api_list(status: Optional[str] = None, q: Optional[str] = None):
    return rf.fetch_applications(status or None, q or None)


@app.get("/api/applications/{app_id}")
def api_get(app_id: int):
    return rf.get_application(app_id)


@app.post("/api/applications", status_code=201)
def api_create(body: ApplicationIn):
    follow_up = rf.parse_date(body.follow_up) if body.follow_up else None
    app_id = rf.add_application(
        body.company, body.role, body.status, body.score, body.link, body.notes,
        jd=body.jd, missing_skills=body.missing_skills, follow_up=follow_up,
    )
    return rf.get_application(app_id)


@app.patch("/api/applications/{app_id}")
def api_update(app_id: int, body: ApplicationUpdate):
    follow_up = None
    if body.follow_up is not None:
        follow_up = rf.parse_date(body.follow_up) if body.follow_up.strip() else ""
    return rf.update_application(app_id, body.status, body.notes, add_note=body.add_note,
                                 link=body.link, score=body.score, follow_up=follow_up)


@app.delete("/api/applications/{app_id}", status_code=204)
def api_delete(app_id: int):
    rf.delete_application(app_id)
    return Response(status_code=204)


@app.get("/api/stats")
def api_stats():
    return rf.get_stats()


@app.get("/api/reminders")
def api_reminders(days: int = 7):
    return rf.reminders(days)


@app.get("/api/gaps")
def api_gaps(top: int = 10):
    return rf.skill_gaps(top)


@app.get("/api/export.csv")
def api_export():
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        path = tmp.name
    try:
        rf.export_csv(path)
        data = Path(path).read_bytes()
    finally:
        os.unlink(path)
    return Response(data, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="applications.csv"'})


# --------------------------------------------------------------------------- #
# Dashboard (single page, vanilla JS)
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML.replace("__STATUSES__", json.dumps(rf.STATUSES))


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ResumeFit</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1d2330; --muted:#667085; --line:#e4e7ec;
          --accent:#2f5bea; --good:#12a150; --mid:#d98a00; --bad:#d64545; }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--ink); }
  header { padding:28px 24px 8px; max-width:1100px; margin:auto; }
  header h1 { margin:0; font-size:26px; } header p { margin:4px 0 0; color:var(--muted); }
  main { max-width:1100px; margin:auto; padding:16px 24px 60px; display:grid; gap:18px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px; }
  .card h2 { margin:0 0 14px; font-size:17px; }
  .grid3 { display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }
  label { display:block; font-weight:600; font-size:13px; margin:10px 0 4px; }
  label.inline { display:flex; gap:8px; align-items:center; font-weight:400; }
  input, select, textarea { width:100%; font:inherit; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:#fff; }
  label.inline input { width:auto; }
  textarea { resize:vertical; }
  button { font:inherit; border:0; border-radius:8px; padding:9px 16px; background:var(--accent); color:#fff; cursor:pointer; }
  button.ghost { background:transparent; color:var(--muted); border:1px solid var(--line); padding:4px 10px; }
  button:disabled { opacity:.6; cursor:wait; }
  a.ghost-link { color:var(--muted); border:1px solid var(--line); border-radius:8px; padding:7px 12px; text-decoration:none; }
  .row { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); }
  .error { color:var(--bad); margin-top:10px; white-space:pre-wrap; }
  .meter { height:12px; background:var(--line); border-radius:6px; overflow:hidden; margin:6px 0 4px; }
  .meter > div { height:100%; }
  .big { font-size:34px; font-weight:700; }
  .chips span { display:inline-block; padding:2px 10px; margin:3px 4px 3px 0; border-radius:999px; font-size:13px; }
  .ok span { background:#e7f6ed; color:#0b6b35; } .miss span { background:#fdecec; color:#9b2c2c; }
  .kw span { background:#eef2ff; color:#3346a8; }
  ol.tips li { margin:6px 0; }
  pre.llm { background:#f3f4f6; padding:12px; border-radius:8px; white-space:pre-wrap; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; }
  td select { padding:4px 6px; width:auto; }
  .note { color:var(--muted); font-size:13px; }
  .due { color:var(--bad); font-weight:600; }
  .muted { color:var(--muted); }
  .toolbar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
  .toolbar input, .toolbar select { width:auto; flex:1; min-width:150px; }
  .stat { display:flex; justify-content:space-between; padding:3px 0; }
  .hbar { height:8px; background:var(--accent); border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>ResumeFit</h1>
  <p>Score your resume against a job, fix the gaps, then track the application.</p>
</header>
<main>
  <section class="card">
    <h2>1 · Match a job</h2>
    <form id="match-form">
      <div class="row">
        <div>
          <label for="resume">Resume file (.pdf, .docx, .txt, .md)</label>
          <input id="resume" type="file" name="resume" accept=".pdf,.docx,.txt,.md">
        </div>
        <div>
          <label for="resume_text">…or paste resume text</label>
          <textarea id="resume_text" name="resume_text" rows="2"></textarea>
        </div>
      </div>
      <label for="jd">Job description</label>
      <textarea id="jd" name="jd" rows="8" required placeholder="Paste the full job description"></textarea>
      <label class="inline"><input type="checkbox" name="use_llm" value="true"> Also get AI bullet rewrites (needs Ollama running locally)</label>
      <p><button id="match-btn">Score my resume</button></p>
      <div id="match-error" class="error"></div>
    </form>
    <div id="result" hidden></div>
  </section>

  <section class="card" id="save-card" hidden>
    <h2>2 · Save this job to the tracker</h2>
    <form id="save-form" class="row">
      <div><label>Company</label><input name="company" required></div>
      <div><label>Role</label><input name="role" required></div>
      <div><label>Status</label><select name="status" id="save-status"></select></div>
      <div><label>Follow up on</label><input name="follow_up" type="date"></div>
      <div><label>Job link</label><input name="link" type="url"></div>
      <div style="align-self:end"><button>Save application</button></div>
    </form>
    <div id="save-error" class="error"></div>
  </section>

  <section class="grid3">
    <div class="card"><h2>Pipeline</h2><div id="stats" class="muted">Loading…</div></div>
    <div class="card"><h2>Needs follow-up</h2><div id="reminders" class="muted">Loading…</div></div>
    <div class="card"><h2>Skill gaps across saved jobs</h2><div id="gaps" class="muted">Loading…</div></div>
  </section>

  <section class="card">
    <h2>Applications</h2>
    <div class="toolbar">
      <input id="search" placeholder="Search company, role, notes">
      <select id="filter"><option value="">All statuses</option></select>
      <a href="/api/export.csv" class="ghost-link">Export CSV</a>
    </div>
    <div id="apps" class="muted">Loading…</div>
  </section>
</main>

<script>
const STATUSES = __STATUSES__;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const today = () => new Date().toISOString().slice(0, 10);
let lastMatch = null;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const body = (res.headers.get("content-type") || "").includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const d = body && body.detail !== undefined ? body.detail : body;
    throw new Error(typeof d === "string" ? d : d.map((e) => `${(e.loc || []).slice(-1)[0]}: ${e.msg}`).join("\n"));
  }
  return body;
}
const jsonOpts = (method, data) => ({ method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(data) });
const chips = (items, cls) => items.length ? `<div class="chips ${cls}">${items.map((x) => `<span>${esc(x)}</span>`).join("")}</div>` : `<span class="muted">none</span>`;
const scoreColor = (s) => s >= 75 ? "var(--good)" : s >= 50 ? "var(--mid)" : "var(--bad)";

for (const sel of ["#save-status", "#filter"]) {
  for (const s of STATUSES) $(sel).insertAdjacentHTML("beforeend", `<option value="${s}">${s}</option>`);
}
$("#save-status").value = "wishlist";

// ---- Match -----------------------------------------------------------------
$("#match-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#match-btn"), fd = new FormData(e.target);
  if (!fd.get("resume") || !fd.get("resume").name) fd.delete("resume");
  $("#match-error").textContent = "";
  btn.disabled = true;
  btn.textContent = fd.get("use_llm") ? "Scoring + asking the AI…" : "Scoring…";
  try {
    lastMatch = await api("/api/match", { method: "POST", body: fd });
    lastMatch.jd = fd.get("jd");
    renderResult(lastMatch);
    $("#save-card").hidden = false;
  } catch (err) {
    $("#match-error").textContent = err.message;
  } finally {
    btn.disabled = false; btn.textContent = "Score my resume";
  }
});

function renderResult(r) {
  const el = $("#result");
  el.hidden = false;
  el.innerHTML = `
    <hr style="border:0;border-top:1px solid var(--line);margin:18px 0">
    <div class="row">
      <div><div class="muted">Overall match</div><div class="big" style="color:${scoreColor(r.score)}">${r.score}%</div>
        <div class="meter"><div style="width:${r.score}%;background:${scoreColor(r.score)}"></div></div>
        <div class="note">Skill coverage ${r.skill_coverage}% · Text similarity ${r.text_similarity}%</div></div>
      <div style="grid-column:span 2"><p><strong>${esc(r.verdict)}</strong></p></div>
    </div>
    <label>Matched skills (${r.matched_skills.length})</label>${chips(r.matched_skills, "ok")}
    <label>Missing skills (${r.missing_skills.length})</label>${chips(r.missing_skills, "miss")}
    <label>JD keywords not in your resume</label>${chips(r.missing_keywords, "kw")}
    <label>Suggestions</label>
    ${r.tips.length ? `<ol class="tips">${r.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>` : `<p class="muted">Looks well tailored already.</p>`}
    ${r.llm_suggestions ? `<label>AI rewrites — check every line before using it</label><pre class="llm">${esc(r.llm_suggestions)}</pre>` : ""}`;
}

// ---- Save ------------------------------------------------------------------
$("#save-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  $("#save-error").textContent = "";
  try {
    await api("/api/applications", jsonOpts("POST", {
      company: f.company, role: f.role, status: f.status,
      link: f.link || null, follow_up: f.follow_up || null,
      score: lastMatch ? lastMatch.score : null,
      missing_skills: lastMatch ? lastMatch.missing_skills : null,
      jd: lastMatch ? lastMatch.jd : null,
    }));
    e.target.reset(); $("#save-status").value = "wishlist";
    $("#save-card").hidden = true;
    refresh();
  } catch (err) { $("#save-error").textContent = err.message; }
});

// ---- Dashboard -------------------------------------------------------------
async function loadApps() {
  const params = new URLSearchParams({ status: $("#filter").value, q: $("#search").value });
  const apps = await api(`/api/applications?${params}`);
  if (!apps.length) { $("#apps").innerHTML = `<p class="muted">No applications yet. Score a job above and save it.</p>`; return; }
  $("#apps").innerHTML = `<table><thead><tr><th>#</th><th>Company / role</th><th>Status</th><th>Score</th>
    <th>Follow-up</th><th>Updated</th><th></th></tr></thead><tbody>${apps.map((a) => `
    <tr data-id="${a.id}">
      <td>${a.id}</td>
      <td><strong>${esc(a.company)}</strong><br>${a.link ? `<a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.role)}</a>` : esc(a.role)}
        ${a.notes ? `<div class="note">${esc(a.notes)}</div>` : ""}</td>
      <td><select class="status">${STATUSES.map((s) => `<option ${s === a.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
      <td>${a.score ?? "–"}${a.score != null ? "%" : ""}</td>
      <td class="${a.follow_up && a.follow_up <= today() ? "due" : ""}">${a.follow_up || "–"}</td>
      <td>${a.updated_on}</td>
      <td><button class="ghost note-btn" title="Add a note">+ note</button>
          <button class="ghost del-btn" title="Delete">✕</button></td>
    </tr>`).join("")}</tbody></table>`;
}

$("#apps").addEventListener("change", async (e) => {
  if (!e.target.classList.contains("status")) return;
  const id = e.target.closest("tr").dataset.id;
  try { await api(`/api/applications/${id}`, jsonOpts("PATCH", { status: e.target.value })); refresh(); }
  catch (err) { alert(err.message); }
});
$("#apps").addEventListener("click", async (e) => {
  const row = e.target.closest("tr"); if (!row) return;
  const id = row.dataset.id;
  try {
    if (e.target.classList.contains("note-btn")) {
      const note = prompt("Add a note (it gets today's date):");
      if (note) { await api(`/api/applications/${id}`, jsonOpts("PATCH", { add_note: note })); refresh(); }
    } else if (e.target.classList.contains("del-btn")) {
      if (confirm(`Delete application #${id}?`)) { await api(`/api/applications/${id}`, { method: "DELETE" }); refresh(); }
    }
  } catch (err) { alert(err.message); }
});

async function loadStats() {
  const s = await api("/api/stats");
  if (!s.total) { $("#stats").textContent = "Nothing tracked yet."; return; }
  const f = s.funnel, max = Math.max(f.applied, 1);
  const line = (label, n) => `<div class="stat"><span>${label}</span><strong>${n}</strong></div>
    <div class="hbar" style="width:${Math.max(2, 100 * n / max)}%"></div>`;
  $("#stats").innerHTML = `
    <div class="stat"><span>Total tracked</span><strong>${s.total}</strong></div>
    ${line("Applied", f.applied)}${line("Got a response", f.responded)}${line("Interview", f.interview)}${line("Offer", f.offer)}
    <p class="note">Response rate ${s.response_rate ?? "–"}% · Interview rate ${s.interview_rate ?? "–"}%<br>
    Avg match ${s.average_score ?? "–"}%${s.average_score_responded != null && s.average_score_no_response != null
      ? ` (responded ${s.average_score_responded}% vs no response ${s.average_score_no_response}%)` : ""}</p>`;
}

async function loadReminders() {
  const due = await api("/api/reminders?days=7");
  $("#reminders").innerHTML = due.length
    ? `<ul style="padding-left:18px;margin:0">${due.map((a) => `<li><strong>#${a.id} ${esc(a.company)}</strong> — ${esc(a.role)}<br><span class="note">${esc(a.reason)}</span></li>`).join("")}</ul>`
    : `<p class="muted">You're all caught up.</p>`;
}

async function loadGaps() {
  const g = await api("/api/gaps?top=8");
  $("#gaps").innerHTML = g.gaps.length
    ? `<p class="note">From ${g.jobs} saved job(s)</p>${g.gaps.map((x) => `
        <div class="stat"><span>${esc(x.skill)}</span><span>${x.count} · ${x.pct}%</span></div>
        <div class="hbar" style="width:${x.pct}%;background:var(--bad)"></div>`).join("")}`
    : `<p class="muted">Save matched jobs to see which skills come up most.</p>`;
}

function refresh() {
  for (const [fn, el] of [[loadApps, "#apps"], [loadStats, "#stats"], [loadReminders, "#reminders"], [loadGaps, "#gaps"]]) {
    fn().catch((err) => { $(el).innerHTML = `<span class="error">${esc(err.message)}</span>`; });
  }
}
let searchTimer;
$("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadApps, 250); });
$("#filter").addEventListener("change", loadApps);
refresh();
</script>
</body>
</html>
"""
