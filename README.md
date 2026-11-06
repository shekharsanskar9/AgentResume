# ResumeFit

A command-line tool that **scores your resume against a job description** and **tracks your job applications**, all in a single Python file.

Paste in a job description and ResumeFit tells you how well your resume fits, which skills you're missing, and which job-description keywords your resume never mentions. Then save the job to a local tracker and follow it from *wishlist* to *offer*.

```
=== ResumeFit report ===
Overall match   [############------------------] 40%
Skill coverage  47%   |   Text similarity 24%

Weak match - tailor the resume heavily or prioritise other roles.

Matched skills (7): Docker, FastAPI, LLMs, PyTorch, Python, RAG, Vector databases
Missing skills (8): AWS, CI/CD, Flask, Kubernetes, LangChain, REST APIs, System design, Testing
JD keywords not in your resume: engineer, applications, pipelines, familiarity, collaborate, product, stakeholders
Your other skills (not asked for): C++, Git, Hugging Face, Java, Linux, NLP, OpenCV, SQL, WebSockets, YOLO
```

---

## Features

- **Resume ↔ JD matching**
  - Finds skills using a built-in list of 75+ skills, each with its aliases (e.g. `k8s` → Kubernetes, `sklearn` → scikit-learn, `postgres` → PostgreSQL)
  - Handles tricky names like `C++`, `C#`, `Node.js`, `CI/CD`, and plural forms
  - Compares the two texts with cosine similarity over unigrams and bigrams
  - Gives one overall score: **70% skill coverage + 30% text similarity**
  - Lists the JD keywords that are missing from your resume, so you can match the job's wording
  - Prints the result as a readable report or as JSON (`--json`)
- **Application tracker**
  - Stores applications in a local SQLite file (`applications.db`)
  - Statuses: `wishlist`, `applied`, `oa`, `interview`, `offer`, `rejected`, `withdrawn`
  - Notes, links and match scores for each application
  - Pipeline stats: count per status, response rate and average match score
- **Light on dependencies.** It runs on the Python standard library. PDF and DOCX support is optional.

---

## Installation

Requires **Python 3.9+**.

```bash
git clone https://github.com/shekharsanskar9/ResumeFit.git
cd ResumeFit

# optional: needed only to read .pdf / .docx resumes
pip install pypdf python-docx
```

---

## Usage

### Match a resume to a job description

```bash
python main.py match resume.pdf jd.txt
```

The job description can be a file or text in quotes:

```bash
python main.py match resume.pdf "We are hiring a Python developer with FastAPI, Docker and AWS experience..."
```

Get JSON output, for example to use in scripts:

```bash
python main.py match resume.pdf jd.txt --json
```

Match a job and save it to the tracker in one step:

```bash
python main.py match resume.pdf jd.txt --save --company Amgen --role "AI Engineer" --status applied
```

### Track applications

```bash
# add an application
python main.py add Boeing "Software Engineer" --status applied --link https://careers.example.com/123 --notes "Referral from senior"

# list all of them, or filter by status
python main.py list
python main.py list --status interview

# update status or notes
python main.py update 2 --status oa --notes "OA on Monday, 90 min"

# pipeline summary
python main.py stats
```

Sample `list` output:

```
 ID  Company            Role                       Status     Score  Updated
--------------------------------------------------------------------------------
  2  Boeing             Software Engineer          oa             -  2026-11-06
     note: OA next week
  1  Amgen              AI Engineer                wishlist     40%  2026-11-06
```

---

## Commands

| Command | Description | Key options |
|---|---|---|
| `match <resume> <jd>` | Score a resume against a JD | `--json`, `--save`, `--company`, `--role`, `--status` |
| `add <company> <role>` | Add an application | `--status`, `--score`, `--link`, `--notes` |
| `list` | Show applications | `--status` |
| `update <id>` | Update an application | `--status`, `--notes` |
| `stats` | Pipeline summary | none |

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

> Tip: only add a "missing skill" to your resume if you can talk about it in an interview.

---

## Customising

- **Add skills:** edit the `SKILLS` dictionary in `main.py`. The key is the display name and the value is a list of lowercase aliases:
  ```python
  "FastAPI": ["fastapi"],
  "Speech (STT/TTS)": ["speech-to-text", "text-to-speech", "stt", "tts", "asr"],
  ```
- **Ignore filler words:** add them to `STOPWORDS`.
- **Change the weighting:** edit the `0.7` / `0.3` split in `match()`.
- **Database location:** `DB_PATH` sets it. By default it's `applications.db`, next to `main.py`.

---

## Project structure

```
ResumeFit/
├── main.py            # everything: parsing, matching, tracker, CLI
├── README.md
└── applications.db    # created automatically on first tracker use (add it to .gitignore)
```

---

## Roadmap

- [ ] FastAPI web app with resume upload and a dashboard
- [ ] Semantic matching with sentence embeddings, so related skills count too
- [ ] Optional LLM-generated suggestions for resume bullet points
- [ ] Export the tracker to CSV
- [ ] Reminders for applications with no update after N days

---

## Tech stack

Python · argparse · SQLite · regex-based skill extraction · cosine similarity (unigram/bigram) · optional `pypdf` and `python-docx`

## Author

**Sanskar Shekhar**, B.Tech CSE, BIT Mesra
[GitHub](https://github.com/shekharsanskar9) · [LinkedIn](https://linkedin.com/in/sanskar-shekhar)
