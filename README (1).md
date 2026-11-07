# ResumeFit

A Python tool that **scores your resume against job descriptions**, **tells you how to tailor it**, and **tracks your applications**. You can use it from the command line or through a small FastAPI web dashboard.

You paste in a job description. ResumeFit tells you how well your resume fits, which skills and keywords are missing, and what to fix. It can also ask a local LLM to rewrite your bullet points. You then save the job and follow it from *wishlist* to *offer*, with follow-up reminders, a status timeline, and a funnel showing where your applications stop.

```
=== ResumeFit report ===
Overall match   [##############----------------] 46%
Skill coverage  55%   |   Text similarity 28%

Weak match - tailor the resume heavily or prioritise other roles.

Matched skills (6): Docker, FastAPI, LLMs, Python, RAG, Vector databases
Missing skills (5): AWS, CI/CD, Kubernetes, LangChain, REST APIs
JD keywords not in your resume: engineer, applications, pipelines, collaborate, product, stakeholders

=== Suggestions ===
 1. The JD asks for skills your resume doesn't mention: AWS, CI/CD, Kubernetes, LangChain,
    REST APIs. Add the ones you have actually used to your Skills section AND to a project
    or experience bullet - a bare skills list carries less weight.
 2. These matching skills appear only once in your resume. Show them in action in a
    bullet: Docker, FastAPI, LLMs, RAG, Vector databases.
 3. Only 2 of 6 bullet points contain a number. Add metrics (latency, accuracy, users, %
    improvement, dataset size) where you can.
 4. Replace weak phrases with action verbs (built, designed, reduced, shipped):
    "responsible for", "worked on", "helped with".
```

---

## Features

### Matching
- **Resume ↔ JD score**: 70% skill coverage + 30% text similarity (cosine similarity over unigrams and bigrams)
- **Skill extraction** uses a built-in list of 85+ skills with aliases (`k8s` → Kubernetes, `sklearn` → scikit-learn, `lora` → Fine-tuning). It handles `C++`, `C#`, `Node.js`, `CI/CD` and plurals.
- **Rank many JDs** against one resume, so you know which jobs to apply to first (`rank`)
- **Tailoring suggestions** (`suggest`):
  - JD skills your resume is missing
  - Matched skills that appear only once (likely just a skills list with no proof in a bullet)
  - JD wording to reuse
  - General resume checks: length, email/phone/LinkedIn/GitHub present, bullet points without numbers, weak phrases ("responsible for", "worked on"), first-person words
- **Optional AI bullet rewrites** through a local [Ollama](https://ollama.com) model (`--llm`). It runs on your machine, needs no API key, and is told never to invent experience.
- Reads `.pdf`, `.docx` (including text inside tables), `.txt` and `.md`

### Application tracker
- Stored in a local SQLite file (`applications.db`). Older databases are upgraded automatically.
- Statuses: `wishlist`, `applied`, `oa`, `interview`, `offer`, `rejected`, `withdrawn`
- **Status timeline** for each application (`show`)
- **Dated notes** (`--add-note`), links, match scores, and the saved JD text
- **Follow-up dates** (`--follow-up +5`) and **reminders** (`remind`) for overdue follow-ups or applications with no update in N days
- **Skill-gap report** (`gaps`): the skills you're missing most often across all saved jobs, i.e. what to learn next
- **Rescore** (`rescore`): after you update your resume, re-scores every saved job using its stored JD
- **Funnel stats**: applied → responded → interview → offer. These use the status history, so an interview that later ended in a rejection still counts as an interview. Stats also compare the average match score of jobs that responded with jobs that didn't.
- **CSV export / import**. Imports are checked first, so a bad row never leaves you with a half-finished import.

### Web dashboard (`app.py`)
- Upload a resume (or paste it), paste a JD, and get the report, chips and suggestions
- Save the job in one click, then change its status, add notes or delete it from the table
- Pipeline funnel, follow-up list, skill-gap chart, search/filter and CSV download
- REST API with interactive docs at `/docs`

---

## Installation

Requires **Python 3.9+**.

```bash
git clone https://github.com/shekharsanskar9/ResumeFit.git
cd ResumeFit
pip install -r requirements.txt     # or install only what you need, see below
```

| You want to... | Install |
|---|---|
| Use the CLI with .txt / .md files | nothing (standard library only) |
| Read .pdf / .docx resumes | `pip install pypdf python-docx` |
| Run the web dashboard | `pip install fastapi uvicorn python-multipart` |
| Get AI rewrites | [Ollama](https://ollama.com), then `ollama pull qwen2.5:7b` |

---

## CLI usage

### Match, rank and get suggestions

```bash
python main.py match resume.pdf jd.txt                 # score one JD (file or text in quotes)
python main.py match resume.pdf jd.txt --json          # JSON for scripts

python main.py rank resume.pdf jds/                    # rank every JD file in a folder
python main.py rank resume.pdf amgen.txt boeing.txt

python main.py suggest resume.pdf jd.txt               # tailoring tips
python main.py suggest resume.pdf jd.txt --llm         # + AI bullet rewrites (Ollama)
python main.py suggest resume.pdf jd.txt --llm --model llama3.1:8b
```

### Save and track

```bash
# score and save in one step (the JD is stored, so rescore/gaps work later)
python main.py match resume.pdf jd.txt --save --company Amgen --role "AI Engineer" --status applied --follow-up +5

# add manually (optionally with JD + resume to score it)
python main.py add Boeing "Software Engineer" --status applied --link https://careers.example.com/123
python main.py add Rupeek "Technocrat Intern" --jd rupeek_jd.txt --resume resume.pdf

python main.py list                                    # all
python main.py list --status interview                 # filter
python main.py list -q boeing                          # search company / role / notes
python main.py show 2                                  # details + timeline

python main.py update 2 --status oa --add-note "OA on Monday, 90 min" --follow-up 2026-11-12
python main.py update 2 --follow-up none               # clear the follow-up date
python main.py delete 2                                # asks first; --yes to skip
```

### Stay on top of things

```bash
python main.py remind              # follow-ups due + active applications idle for 7+ days
python main.py remind --days 10
python main.py gaps                # skills missing most often across saved jobs
python main.py rescore resume_v2.pdf   # re-score all saved jobs after editing your resume
python main.py stats               # funnel, response rate, average scores
python main.py export applications.csv
python main.py import applications.csv
```

Sample `stats` output:

```
Total applications: 6
  applied      2  #######-------------
  interview    1  ###-----------------
  rejected     3  ##########----------

Funnel (ever reached): applied 6 -> responded 3 -> interview 2 -> offer 0
Response rate (OA/interview/offer): 50%   |   Interview rate: 33%
Average match score: 58%
  ... for jobs that responded: 71%   |   no response: 45%
```

Dates accept `YYYY-MM-DD`, `today`, `tomorrow`, or `+N` (N days from today). Most commands take `--json`.

---

## Web dashboard

```bash
uvicorn app:app --reload
# open http://127.0.0.1:8000        API docs: http://127.0.0.1:8000/docs
```

The CLI and the web app share the same `applications.db`.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/match` | multipart: `jd`, `resume` (file) or `resume_text`, `use_llm` → report + tips |
| `GET` | `/api/applications?status=&q=` | list / filter / search |
| `GET` | `/api/applications/{id}` | details + status history |
| `POST` | `/api/applications` | create (`company`, `role`, `status`, `score`, `follow_up`, ...) |
| `PATCH` | `/api/applications/{id}` | update status / notes / `add_note` / link / score / follow-up |
| `DELETE` | `/api/applications/{id}` | delete |
| `GET` | `/api/stats` · `/api/reminders?days=7` · `/api/gaps` | dashboard data |
| `GET` | `/api/export.csv` | download the tracker |

---

## Commands

| Command | Description | Key options |
|---|---|---|
| `match <resume> <jd>` | Score a resume against a JD | `--json`, `--save`, `--company`, `--role`, `--status`, `--link`, `--notes`, `--follow-up` |
| `rank <resume> <jd...>` | Rank many JDs (files / folders) | `--json` |
| `suggest <resume> <jd>` | Tailoring tips | `--llm`, `--model`, `--json` |
| `add <company> <role>` | Add an application | `--status`, `--score`, `--link`, `--notes`, `--follow-up`, `--jd`, `--resume` |
| `list` | Show applications | `--status`, `-q/--search`, `--json` |
| `show <id>` | Details + timeline | `--json` |
| `update <id>` | Update an application | `--status`, `--notes`, `--add-note`, `--link`, `--score`, `--follow-up` |
| `delete <id>` | Delete an application | `--yes` |
| `remind` | What needs a follow-up | `--days`, `--json` |
| `gaps` | Most common missing skills | `--top`, `--json` |
| `rescore <resume>` | Re-score saved jobs | `--id` |
| `stats` | Pipeline summary | `--json` |
| `export [path]` | Export to CSV (`-` = stdout) | |
| `import <path>` | Import from CSV | |

Run `python main.py <command> -h` for full help.

---

## How the score works

1. **Skill extraction.** Both texts are scanned for every alias in the `SKILLS` dictionary. Matching uses whole terms only, so `java` doesn't match inside `javascript`.
2. **Skill coverage** = matched JD skills ÷ total JD skills.
3. **Text similarity.** Cosine similarity of word and bigram counts, with stopwords removed. A resume and a JD rarely score above about 0.35, so the value is rescaled to 0–100%.
4. **Overall score** = `0.7 × coverage + 0.3 × similarity`.

| Score | Verdict |
|---|---|
| 75%+ | Strong match. Apply, and use the JD's wording in your bullet points. |
| 50–74% | Decent match. Add the missing skills you really have before applying. |
| < 50% | Weak match. Tailor the resume heavily or focus on other roles. |

> Tip: only add a "missing skill" to your resume if you can talk about it in an interview. The same goes for the AI rewrites: check every line.

---

## Customising

- **Add skills:** edit the `SKILLS` dictionary in `main.py`. The key is the display name and the value is a list of lowercase aliases.
- **Ignore filler words:** add them to `STOPWORDS`.
- **Change the weighting:** edit the `0.7` / `0.3` split in `match()`.
- **Environment variables:**
  - `RESUMEFIT_DB`: database path (default: `applications.db` next to `main.py`)
  - `RESUMEFIT_MODEL`: default Ollama model (default: `qwen2.5:7b`)
  - `OLLAMA_URL`: default `http://localhost:11434`

---

## Tests

```bash
python -m unittest discover -s tests -v      # or: pytest
```

There are 40 tests covering skill extraction, scoring, suggestions, the tracker (history, reminders, gaps, rescore, CSV round trip, migrating old databases), the CLI and the web API. Each test uses its own temporary database. GitHub Actions runs them on Python 3.9 and 3.12 for every push.

---

## Project structure

```
ResumeFit/
├── main.py                   # matching, suggestions, tracker, CLI
├── app.py                    # FastAPI web app + dashboard
├── tests/test_resumefit.py   # unit + API tests
├── requirements.txt
├── .github/workflows/tests.yml
├── README.md
└── applications.db           # created on first use (git-ignored)
```

---

## Roadmap

- [x] FastAPI web app with resume upload and a dashboard
- [x] Optional LLM-generated suggestions for resume bullet points (local, via Ollama)
- [x] Export the tracker to CSV (and import)
- [x] Reminders for applications with no update after N days
- [x] Status timeline, skill-gap report, rescoring, multi-JD ranking
- [ ] Semantic matching with sentence embeddings, so related skills count too
- [ ] Browser extension to save a JD straight from a job board

---

## Tech stack

Python · argparse · SQLite · regex-based skill extraction · cosine similarity (unigram/bigram) · FastAPI · Pydantic · vanilla JS · Ollama (optional local LLM) · unittest · GitHub Actions

## Author

**Sanskar Shekhar**, B.Tech CSE, BIT Mesra
[GitHub](https://github.com/shekharsanskar9) · [LinkedIn](https://linkedin.com/in/sanskar-shekhar)
