#!/usr/bin/env python3
"""
ResumeFit - match your resume to job descriptions and track your applications.

Matching
  match    Score a resume against one job description
  rank     Score a resume against many JDs (files or a folder) and rank them
  suggest  Concrete tips to tailor your resume to a JD (optional local LLM via Ollama)

Tracker
  add      Save a job application
  list     Show applications (filter by status, search text)
  show     Full details + status timeline of one application
  update   Change status / notes / link / score / follow-up date
  delete   Remove an application
  remind   Applications that need a follow-up (due date passed, or no update in N days)
  gaps     Skills you are missing most often across all saved jobs
  rescore  Re-score saved jobs against an updated resume
  stats    Pipeline funnel, response rate, average match score
  export   Export the tracker to CSV
  import   Import applications from CSV

Usage
  python main.py match resume.pdf jd.txt
  python main.py match resume.pdf jd.txt --save --company Boeing --role "Software Engineer"
  python main.py rank resume.pdf jds/
  python main.py suggest resume.pdf jd.txt --llm
  python main.py add Amgen "AI Engineer" --status interview --follow-up +5
  python main.py update 3 --status offer --add-note "HR call on Monday"
  python main.py remind --days 7

Only the Python standard library is required. Optional:
  pip install pypdf python-docx     # to read .pdf and .docx resumes
  pip install fastapi uvicorn python-multipart   # web app (app.py)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sqlite3
import sys
import textwrap
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("RESUMEFIT_DB") or Path(__file__).with_name("applications.db"))
STATUSES = ["wishlist", "applied", "oa", "interview", "offer", "rejected", "withdrawn"]
ACTIVE_STATUSES = ("wishlist", "applied", "oa", "interview")      # still in play -> can need a follow-up
APPLIED_STATUSES = {"applied", "oa", "interview", "offer", "rejected"}  # proof you actually applied
RESPONSE_STATUSES = {"oa", "interview", "offer"}
RESUME_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}
CSV_FIELDS = ["id", "company", "role", "status", "score", "link", "notes",
              "follow_up", "applied_on", "updated_on", "missing_skills"]

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_LLM_MODEL = os.environ.get("RESUMEFIT_MODEL", "qwen2.5:7b")


class ResumeFitError(Exception):
    """A user-facing error (bad input, missing file, ...). The CLI prints it; the web app returns 400."""


class NotFoundError(ResumeFitError):
    """No application with the given id (web app returns 404)."""


def today() -> date:
    """Wrapped so tests can freeze the date."""
    return date.today()


# --------------------------------------------------------------------------- #
# Skill taxonomy: canonical name -> aliases that count as the same skill
# --------------------------------------------------------------------------- #
SKILLS: dict[str, list[str]] = {
    # Languages
    "Python": ["python"],
    "Java": ["java"],
    "C++": ["c++", "cpp"],
    "C": ["c language", "ansi c"],
    "C#": ["c#", "csharp"],
    "JavaScript": ["javascript", "js", "es6"],
    "TypeScript": ["typescript", "ts"],
    "Go": ["golang"],
    "Rust": ["rust"],
    "Kotlin": ["kotlin"],
    "SQL": ["sql"],
    "Bash": ["bash", "shell scripting"],
    # Backend / web
    "FastAPI": ["fastapi"],
    "Flask": ["flask"],
    "Django": ["django"],
    "Pydantic": ["pydantic"],
    "Celery": ["celery"],
    "Spring Boot": ["spring boot", "spring"],
    "Node.js": ["node.js", "nodejs", "node"],
    "Express": ["express.js", "expressjs", "express"],
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs"],
    "HTML/CSS": ["html", "css", "tailwind"],
    "REST APIs": ["rest api", "rest apis", "restful", "rest"],
    "GraphQL": ["graphql"],
    "WebSockets": ["websocket", "websockets"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices", "microservice"],
    "Android": ["android"],
    "Flutter": ["flutter"],
    # Data stores / infra
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Kafka": ["kafka"],
    "RabbitMQ": ["rabbitmq"],
    "Elasticsearch": ["elasticsearch"],
    "Vector databases": ["vector database", "vector db", "chromadb", "pinecone", "faiss", "qdrant", "weaviate"],
    "Docker": ["docker", "containers", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud"],
    "Cloud computing": ["cloud computing", "cloud"],
    "CI/CD": ["ci/cd", "cicd", "github actions", "jenkins", "gitlab ci"],
    "Git": ["git", "github", "gitlab"],
    "Linux": ["linux", "unix"],
    "Terraform": ["terraform"],
    "Ansible": ["ansible"],
    "Monitoring": ["prometheus", "grafana", "observability"],
    "Airflow": ["airflow"],
    # AI / ML / data
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "neural networks", "neural network"],
    "NLP": ["nlp", "natural language processing", "ner", "named entity recognition"],
    "Computer Vision": ["computer vision", "image processing", "object detection"],
    "LLMs": ["llm", "llms", "large language model", "large language models", "gpt", "generative ai", "genai"],
    "RAG": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
    "Embeddings": ["embeddings", "sentence-transformers", "sentence transformers"],
    "Fine-tuning": ["fine-tuning", "fine tuning", "finetuning", "lora", "qlora", "peft"],
    "Prompt engineering": ["prompt engineering"],
    "AI agents": ["ai agents", "agentic", "agents"],
    "PyTorch": ["pytorch", "torch"],
    "TensorFlow": ["tensorflow", "keras"],
    "scikit-learn": ["scikit-learn", "sklearn"],
    "Hugging Face": ["hugging face", "huggingface", "transformers"],
    "LangChain": ["langchain", "llamaindex"],
    "OpenCV": ["opencv"],
    "YOLO": ["yolo", "yolov8", "yolov5"],
    "Speech (STT/TTS)": ["speech-to-text", "text-to-speech", "stt", "tts", "asr", "speech recognition"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "Spark": ["spark", "pyspark"],
    "Statistics": ["statistics", "statistical analysis", "hypothesis testing"],
    "Excel": ["ms excel", "microsoft excel", "spreadsheets"],
    "Data analysis": ["data analysis", "data analytics", "analytics"],
    "Power BI / Tableau": ["power bi", "tableau"],
    "MLOps": ["mlops", "mlflow", "model deployment"],
    # CS fundamentals / practices
    "Data Structures & Algorithms": ["data structures", "algorithms", "dsa"],
    "OOP": ["oop", "object oriented", "object-oriented"],
    "System design": ["system design", "distributed systems", "scalability"],
    "DBMS": ["dbms", "database management"],
    "Operating systems": ["operating systems", "operating system"],
    "Computer networks": ["computer networks", "networking", "tcp/ip"],
    "Testing": ["unit testing", "pytest", "junit", "testing", "test automation"],
    "Agile": ["agile", "scrum", "jira"],
}

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each etc few for from further
had has have having he her here hers him his how i if in into is it its itself just like may me
more most must my no nor not now of off on once only or other our ours out over own per same
she should so some such than that the their theirs them then there these they this those through
to too under until up very via was we were what when where which while who whom why will with
within would you your yours
ability able across work working works team teams role roles candidate candidates looking join
experience experienced years year strong good great excellent knowledge understanding skills skill
requirements required preferred plus responsibilities responsible including include includes using
use used develop developing development build building new well environment company opportunity
help ensure based related relevant across etc e g ie part time full equal employer
""".split())

WEAK_PHRASES = ["responsible for", "worked on", "helped with", "helped in", "involved in",
                "assisted in", "assisted with", "duties included", "participated in"]
BULLET_RE = re.compile(r"^\s*[•●▪◦‣\-\*–]\s+")


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def read_text(path_or_text: str) -> str:
    """Read .txt/.md/.pdf/.docx, or treat the argument as raw text if it isn't a file."""
    # Long text or text with newlines is never a path (and would crash os.stat with ENAMETOOLONG).
    looks_like_text = "\n" in path_or_text or len(path_or_text) > 255
    path = Path(path_or_text)
    try:
        is_file = not looks_like_text and path.is_file()
    except OSError:
        is_file = False
    if not is_file:
        if looks_like_text or len(path_or_text) > 40 or " " in path_or_text:
            return path_or_text  # raw text passed directly
        raise ResumeFitError(f"file not found: {path_or_text}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ResumeFitError("reading PDFs needs pypdf ->  pip install pypdf") from None
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        if not text.strip():
            raise ResumeFitError(f"no text found in {path.name} - is it a scanned PDF? Try a .docx or .txt export.")
        return text
    if suffix == ".docx":
        try:
            import docx
        except ImportError:
            raise ResumeFitError("reading .docx needs python-docx ->  pip install python-docx") from None
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        # many resume templates put content in tables - include those cells too
        for table in document.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        return "\n".join(parts)
    return path.read_text(encoding="utf-8", errors="ignore")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9+#.\-]*[a-z0-9+#]|[a-z]", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _term_pattern(term: str) -> str:
    """Whole-term regex that also works for 'c++', 'node.js', 'ci/cd' and allows plurals."""
    return r"(?<![a-z0-9])" + re.escape(term) + r"(?:e?s)?(?![a-z0-9+#])"


def contains_term(text: str, term: str) -> bool:
    return re.search(_term_pattern(term), text) is not None


def count_term(text: str, term: str) -> int:
    return len(re.findall(_term_pattern(term), text))


def find_skills(text: str) -> set[str]:
    norm = normalize(text)
    return {skill for skill, aliases in SKILLS.items() if any(contains_term(norm, a) for a in aliases)}


def skill_mentions(text: str, skill: str) -> int:
    """How many times any alias of `skill` appears in `text`."""
    norm = normalize(text)
    return sum(count_term(norm, a) for a in SKILLS.get(skill, []))


def cosine_similarity(a: str, b: str) -> float:
    """Cosine similarity on unigram + bigram counts (stopwords removed)."""
    def features(text: str) -> Counter:
        toks = tokenize(text)
        return Counter(toks + [f"{x} {y}" for x, y in zip(toks, toks[1:])])

    va, vb = features(a), features(b)
    dot = sum(va[k] * vb[k] for k in va.keys() & vb.keys())
    norm_a = math.sqrt(sum(v * v for v in va.values()))
    norm_b = math.sqrt(sum(v * v for v in vb.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def missing_keywords(resume: str, jd: str, top_n: int = 12) -> list[str]:
    """Frequent JD words that never appear in the resume (and aren't already a skill alias)."""
    resume_norm = normalize(resume)
    # words that belong to a skill name are already covered by "missing skills"
    skill_words = {w for aliases in SKILLS.values() for a in aliases for w in re.split(r"[\s\-]+", a)}
    skill_words |= {w + "s" for w in skill_words}

    def is_skill_word(token: str) -> bool:
        return token in skill_words or any(part in skill_words for part in token.split("-"))

    counts = Counter(t for t in tokenize(jd) if len(t) > 2 and not is_skill_word(t))
    return [w for w, _ in counts.most_common(80) if not contains_term(resume_norm, w)][:top_n]


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def match(resume: str, jd: str) -> dict:
    jd_skills = find_skills(jd)
    resume_skills = find_skills(resume)
    matched = sorted(jd_skills & resume_skills)
    missing = sorted(jd_skills - resume_skills)
    extra = sorted(resume_skills - jd_skills)

    coverage = len(matched) / len(jd_skills) if jd_skills else 0.0
    # Raw cosine between a resume and a JD rarely exceeds ~0.35, so rescale it to 0..1.
    similarity = min(cosine_similarity(resume, jd) / 0.35, 1.0)
    score = round(100 * (0.7 * coverage + 0.3 * similarity)) if jd_skills else round(100 * similarity)

    if score >= 75:
        verdict = "Strong match - apply, and mirror the JD's wording in your bullet points."
    elif score >= 50:
        verdict = "Decent match - add the missing skills you genuinely have before applying."
    else:
        verdict = "Weak match - tailor the resume heavily or prioritise other roles."

    return {
        "score": score,
        "skill_coverage": round(100 * coverage),
        "text_similarity": round(100 * similarity),
        "matched_skills": matched,
        "missing_skills": missing,
        "other_resume_skills": extra,
        "missing_keywords": missing_keywords(resume, jd),
        "verdict": verdict,
    }


def collect_jd_files(items: list[str]) -> list[Path]:
    """Expand folders into the resume/JD files they contain."""
    files: list[Path] = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in RESUME_SUFFIXES)
        elif p.is_file():
            files.append(p)
        else:
            raise ResumeFitError(f"not a file or folder: {item}")
    if not files:
        raise ResumeFitError("no job description files found (.txt, .md, .pdf, .docx)")
    return files


def rank(resume: str, jd_items: list[str]) -> list[dict]:
    """Score one resume against many JDs, best match first."""
    rows = []
    for f in collect_jd_files(jd_items):
        r = match(resume, read_text(str(f)))
        rows.append({
            "jd": str(f),
            "score": r["score"],
            "skill_coverage": r["skill_coverage"],
            "text_similarity": r["text_similarity"],
            "missing_skills": r["missing_skills"],
        })
    return sorted(rows, key=lambda x: x["score"], reverse=True)


# --------------------------------------------------------------------------- #
# Suggestions (rule-based, plus optional local LLM)
# --------------------------------------------------------------------------- #
def resume_checks(resume: str) -> list[str]:
    """Generic resume-quality checks that don't depend on the JD."""
    tips: list[str] = []
    norm = normalize(resume)

    words = len(re.findall(r"\w+", resume))
    if words < 250:
        tips.append(f"Your resume is short ({words} words). Add project details, the tools you used and the results.")
    elif words > 900:
        tips.append(f"Your resume is long ({words} words). For student / new-grad roles, one page "
                    "(about 450-700 words) is safer.")

    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", resume):
        tips.append("No email address found. Check your contact line (PDF text extraction can also drop it).")
    phones = re.findall(r"\+?\d[\d\s\-()]{8,}\d", resume)
    if not any(len(re.sub(r"\D", "", p)) >= 10 for p in phones):
        tips.append("No phone number found.")
    for key, name in (("linkedin", "LinkedIn"), ("github", "GitHub")):
        if key not in norm:
            tips.append(f"No {name} link found. Recruiters for tech roles often check it.")

    bullets = [line for line in resume.splitlines() if BULLET_RE.match(line)]
    if len(bullets) >= 4:
        quantified = sum(1 for b in bullets if re.search(r"\d", b))
        if quantified / len(bullets) < 0.4:
            tips.append(f"Only {quantified} of {len(bullets)} bullet points contain a number. Add metrics "
                        "(latency, accuracy, users, % improvement, dataset size) where you can.")

    weak = [p for p in WEAK_PHRASES if p in norm]
    if weak:
        tips.append("Replace weak phrases with action verbs (built, designed, reduced, shipped): "
                    + ", ".join(f'"{p}"' for p in weak) + ".")

    if len(re.findall(r"\b(?:i|my|me)\b", norm)) >= 3:
        tips.append('Drop first-person words ("I", "my") from bullet points - start with the verb instead.')
    return tips


def suggest(resume: str, jd: str) -> dict:
    """Match result plus a list of concrete, JD-specific and generic tips."""
    r = match(resume, jd)
    tips: list[str] = []
    if r["missing_skills"]:
        tips.append("The JD asks for skills your resume doesn't mention: " + ", ".join(r["missing_skills"])
                    + ". Add the ones you have actually used to your Skills section AND to a project or "
                      "experience bullet - a bare skills list carries less weight.")
    once = [s for s in r["matched_skills"] if skill_mentions(resume, s) == 1]
    if once:
        tips.append("These matching skills appear only once in your resume. Show them in action in a bullet: "
                    + ", ".join(once) + ".")
    if r["missing_keywords"]:
        tips.append("Use the JD's own wording where it is true for you: "
                    + ", ".join(r["missing_keywords"][:8]) + ".")
    tips += resume_checks(resume)
    r["tips"] = tips
    return r


def llm_suggestions(resume: str, jd: str, result: dict, model: str = DEFAULT_LLM_MODEL,
                    timeout: int = 300) -> str:
    """Ask a local Ollama model to rewrite bullet points for this JD. Stdlib only (urllib)."""
    prompt = textwrap.dedent(f"""\
        You are a strict, practical resume coach for software / AI roles.

        Rewrite up to 5 bullet points from the RESUME so they fit the JOB DESCRIPTION better.
        Rules:
        - Never invent experience, employers, skills, tools or numbers that are not in the resume.
        - Start each bullet with a strong action verb, keep it under 30 words.
        - Where a metric would help but the resume has none, write [add metric].
        - Reuse the job description's wording where it truthfully applies.

        Format:
        REWRITTEN BULLETS
        - original: <bullet>
          better:   <bullet>
        SKILLS TO ADD ONLY IF TRUE
        - <skill>: <one line on how to show it>

        Skills the JD wants that the resume lacks: {", ".join(result["missing_skills"]) or "none"}

        RESUME:
        {resume[:6000]}

        JOB DESCRIPTION:
        {jd[:6000]}
        """)
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False,
                          "options": {"temperature": 0.3}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())["response"].strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        if e.code == 404:
            raise ResumeFitError(f"Ollama doesn't have model '{model}'. Run:  ollama pull {model}") from None
        raise ResumeFitError(f"Ollama error {e.code}: {detail[:200]}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ResumeFitError(f"could not reach Ollama at {OLLAMA_URL} ({e}). "
                             f"Start it with  ollama serve  and pull a model:  ollama pull {model}") from None


# --------------------------------------------------------------------------- #
# Printing helpers
# --------------------------------------------------------------------------- #
def bar(pct: int, width: int = 30) -> str:
    filled = round(width * pct / 100)
    return "#" * filled + "-" * (width - filled)


def print_report(r: dict) -> None:
    print("\n=== ResumeFit report ===")
    print(f"Overall match   [{bar(r['score'])}] {r['score']}%")
    print(f"Skill coverage  {r['skill_coverage']}%   |   Text similarity {r['text_similarity']}%")
    print(f"\n{r['verdict']}\n")
    print(f"Matched skills ({len(r['matched_skills'])}): " + (", ".join(r["matched_skills"]) or "-"))
    print(f"Missing skills ({len(r['missing_skills'])}): " + (", ".join(r["missing_skills"]) or "-"))
    print("JD keywords not in your resume: " + (", ".join(r["missing_keywords"]) or "-"))
    if r["other_resume_skills"]:
        print("Your other skills (not asked for): " + ", ".join(r["other_resume_skills"]))
    print()


def print_tips(tips: list[str]) -> None:
    if not tips:
        print("No suggestions - this resume already looks well tailored.\n")
        return
    print("=== Suggestions ===")
    for i, tip in enumerate(tips, 1):
        print(textwrap.fill(tip, width=90, initial_indent=f"{i:>2}. ", subsequent_indent="    "))
    print()


def print_rank(rows: list[dict]) -> None:
    print(f"\n{'#':>2}  {'Score':>5}  {'Skills':>6}  {'JD':<32} Top missing skills")
    print("-" * 90)
    for i, r in enumerate(rows, 1):
        name = Path(r["jd"]).name
        missing = ", ".join(r["missing_skills"][:4]) + (" ..." if len(r["missing_skills"]) > 4 else "")
        print(f"{i:>2}  {r['score']:>4}%  {r['skill_coverage']:>5}%  {name[:32]:<32} {missing or '-'}")
    print()


# --------------------------------------------------------------------------- #
# Application tracker (SQLite)
# --------------------------------------------------------------------------- #
def db() -> sqlite3.Connection:
    """Open the database, creating / migrating the schema if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS applications (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               company TEXT NOT NULL,
               role TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'applied',
               score INTEGER,
               link TEXT,
               notes TEXT,
               applied_on TEXT NOT NULL,
               updated_on TEXT NOT NULL
           )"""
    )
    # Migrate databases created by older versions: add the new columns.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
    for name in ("jd", "missing_skills", "follow_up"):
        if name not in cols:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {name} TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS status_history (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               app_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
               status TEXT NOT NULL,
               changed_on TEXT NOT NULL
           )"""
    )
    # Older rows have no history yet: start their timeline with the current status.
    conn.execute(
        """INSERT INTO status_history (app_id, status, changed_on)
           SELECT id, status, updated_on FROM applications a
           WHERE NOT EXISTS (SELECT 1 FROM status_history h WHERE h.app_id = a.id)"""
    )
    conn.commit()
    return conn


@contextmanager
def connect():
    """Connection that commits on success, rolls back on error, and always closes."""
    conn = db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def validate_status(status: str) -> str:
    if status not in STATUSES:
        raise ResumeFitError(f"unknown status '{status}' (choose from: {', '.join(STATUSES)})")
    return status


def validate_score(score) -> int | None:
    if score is None or score == "":
        return None
    try:
        score = int(score)
    except (TypeError, ValueError):
        raise ResumeFitError(f"score must be a whole number, got '{score}'") from None
    if not 0 <= score <= 100:
        raise ResumeFitError("score must be between 0 and 100")
    return score


def parse_date(value: str) -> str:
    """Accept YYYY-MM-DD, 'today', 'tomorrow' or '+N' / '+Nd' (N days from today)."""
    v = value.strip().lower()
    if v == "today":
        return today().isoformat()
    if v == "tomorrow":
        return (today() + timedelta(days=1)).isoformat()
    m = re.fullmatch(r"\+(\d+)d?", v)
    if m:
        return (today() + timedelta(days=int(m.group(1)))).isoformat()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ResumeFitError(f"bad date '{value}' (use YYYY-MM-DD, today, tomorrow, or +N for N days from now)") from None


def _to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["missing_skills"] = json.loads(d["missing_skills"]) if d.get("missing_skills") else []
    return d


def add_application(company, role, status="applied", score=None, link=None, notes=None, *,
                    jd=None, missing_skills=None, follow_up=None, applied_on=None, updated_on=None) -> int:
    company, role = (company or "").strip(), (role or "").strip()
    if not company or not role:
        raise ResumeFitError("company and role are required")
    validate_status(status)
    score = validate_score(score)
    applied_on = applied_on or today().isoformat()
    updated_on = updated_on or applied_on
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications (company, role, status, score, link, notes, applied_on, updated_on,"
            " jd, missing_skills, follow_up) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company, role, status, score, link or None, notes or None, applied_on, updated_on,
             jd or None, json.dumps(missing_skills) if missing_skills is not None else None, follow_up or None),
        )
        app_id = cur.lastrowid
        conn.execute("INSERT INTO status_history (app_id, status, changed_on) VALUES (?, ?, ?)",
                     (app_id, status, applied_on))
    return app_id


def fetch_applications(status: str | None = None, search: str | None = None) -> list[dict]:
    query = "SELECT *, jd IS NOT NULL AS has_jd FROM applications"
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(validate_status(status))
    if search:
        where.append("(company LIKE ? OR role LIKE ? OR IFNULL(notes, '') LIKE ?)")
        params += [f"%{search}%"] * 3
    if where:
        query += " WHERE " + " AND ".join(where)
    with connect() as conn:
        rows = conn.execute(query + " ORDER BY updated_on DESC, id DESC", params).fetchall()
    apps = []
    for row in rows:
        d = _to_dict(row)
        d.pop("jd", None)          # can be long; use get_application() for the full text
        d["has_jd"] = bool(d["has_jd"])
        apps.append(d)
    return apps


def get_application(app_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no application with id {app_id}")
        history = conn.execute(
            "SELECT status, changed_on FROM status_history WHERE app_id = ? ORDER BY changed_on, id", (app_id,)
        ).fetchall()
    d = _to_dict(row)
    d["history"] = [dict(h) for h in history]
    return d


def update_application(app_id: int, status=None, notes=None, *, add_note=None, link=None,
                       score=None, follow_up=None) -> dict:
    """Update fields. `notes` replaces, `add_note` appends a dated note. follow_up='' clears it."""
    current = get_application(app_id)
    fields, params = [], []
    if status is not None:
        fields.append("status = ?")
        params.append(validate_status(status))

    new_notes = notes
    if add_note:
        base = notes if notes is not None else (current["notes"] or "")
        entry = f"[{today().isoformat()}] {add_note.strip()}"
        new_notes = f"{base} | {entry}" if base else entry
    if new_notes is not None:
        fields.append("notes = ?")
        params.append(new_notes or None)
    if link is not None:
        fields.append("link = ?")
        params.append(link or None)
    if score is not None:
        fields.append("score = ?")
        params.append(validate_score(score))
    if follow_up is not None:
        fields.append("follow_up = ?")
        params.append(follow_up or None)
    if not fields:
        raise ResumeFitError("nothing to update (use --status, --notes, --add-note, --link, --score or --follow-up)")

    stamp = today().isoformat()
    fields.append("updated_on = ?")
    params += [stamp, app_id]
    with connect() as conn:
        conn.execute(f"UPDATE applications SET {', '.join(fields)} WHERE id = ?", params)
        if status is not None and status != current["status"]:
            conn.execute("INSERT INTO status_history (app_id, status, changed_on) VALUES (?, ?, ?)",
                         (app_id, status, stamp))
    return get_application(app_id)


def delete_application(app_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM status_history WHERE app_id = ?", (app_id,))
        cur = conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    if cur.rowcount == 0:
        raise NotFoundError(f"no application with id {app_id}")


def reminders(days: int = 7) -> list[dict]:
    """Active applications whose follow-up date has arrived, or with no update in `days` days."""
    t = today()
    cutoff = (t - timedelta(days=days)).isoformat()
    due = []
    for a in fetch_applications():
        if a["status"] not in ACTIVE_STATUSES:
            continue
        if a["follow_up"] and a["follow_up"] <= t.isoformat():
            late = (t - date.fromisoformat(a["follow_up"])).days
            reason = "follow-up due today" if late == 0 else f"follow-up overdue by {late} day{'s' * (late != 1)}"
        elif a["updated_on"] <= cutoff:
            idle = (t - date.fromisoformat(a["updated_on"])).days
            reason = f"no update for {idle} days"
            reason += " - apply or drop it" if a["status"] == "wishlist" else " - send a follow-up"
        else:
            continue
        due.append({**a, "reason": reason})
    return sorted(due, key=lambda a: a["follow_up"] or a["updated_on"])


def skill_gaps(top: int = 10) -> dict:
    """Which skills are missing most often across saved jobs (excluding withdrawn ones)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT missing_skills FROM applications WHERE missing_skills IS NOT NULL AND status != 'withdrawn'"
        ).fetchall()
    counts = Counter(s for r in rows for s in json.loads(r["missing_skills"]))
    jobs = len(rows)
    return {
        "jobs": jobs,
        "gaps": [{"skill": s, "count": c, "pct": round(100 * c / jobs)} for s, c in counts.most_common(top)],
    }


def rescore(resume: str, app_id: int | None = None) -> list[dict]:
    """Re-score saved jobs (those with a stored JD) against a new version of the resume."""
    query = "SELECT id, company, role, score, jd FROM applications WHERE jd IS NOT NULL"
    params: tuple = ()
    if app_id is not None:
        query += " AND id = ?"
        params = (app_id,)
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
        changes = []
        for row in rows:
            r = match(resume, row["jd"])
            # updated_on is left alone on purpose: rescoring isn't news about the application
            conn.execute("UPDATE applications SET score = ?, missing_skills = ? WHERE id = ?",
                         (r["score"], json.dumps(r["missing_skills"]), row["id"]))
            changes.append({"id": row["id"], "company": row["company"], "role": row["role"],
                            "old_score": row["score"], "new_score": r["score"]})
    if app_id is not None and not changes:
        raise ResumeFitError(f"application {app_id} doesn't exist or has no saved JD (save one with match --save)")
    return changes


def get_stats() -> dict:
    with connect() as conn:
        apps = conn.execute("SELECT id, status, score FROM applications").fetchall()
        history = conn.execute("SELECT app_id, status FROM status_history").fetchall()

    reached: dict[int, set] = defaultdict(set)
    for h in history:
        reached[h["app_id"]].add(h["status"])
    for a in apps:
        reached[a["id"]].add(a["status"])

    ids = [a["id"] for a in apps]
    applied = {i for i in ids if reached[i] & APPLIED_STATUSES}
    responded = {i for i in applied if reached[i] & RESPONSE_STATUSES}
    interviewed = {i for i in applied if reached[i] & {"interview", "offer"}}
    offers = {i for i in applied if "offer" in reached[i]}

    def avg(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values)) if values else None

    score_of = {a["id"]: a["score"] for a in apps}
    by_status = Counter(a["status"] for a in apps)
    return {
        "total": len(apps),
        "by_status": {s: by_status[s] for s in STATUSES if by_status[s]},
        # "ever reached" counts use the status history, so a rejection after an interview
        # still counts as an interview
        "funnel": {"applied": len(applied), "responded": len(responded),
                   "interview": len(interviewed), "offer": len(offers)},
        "response_rate": round(100 * len(responded) / len(applied)) if applied else None,
        "interview_rate": round(100 * len(interviewed) / len(applied)) if applied else None,
        "average_score": avg(score_of.values()),
        "average_score_responded": avg(score_of[i] for i in responded),
        "average_score_no_response": avg(score_of[i] for i in applied - responded),
    }


def print_applications(apps: list[dict]) -> None:
    if not apps:
        print("No applications found. Add one with:  python main.py add <company> <role>")
        return
    t = today().isoformat()
    print(f"\n{'ID':>3}  {'Company':<18} {'Role':<26} {'Status':<10} {'Score':>5}  {'Follow-up':<11} Updated")
    print("-" * 92)
    for r in apps:
        score = f"{r['score']}%" if r["score"] is not None else "-"
        fu = r["follow_up"] or "-"
        if r["follow_up"] and r["follow_up"] <= t and r["status"] in ACTIVE_STATUSES:
            fu += "!"
        print(f"{r['id']:>3}  {r['company'][:18]:<18} {r['role'][:26]:<26} {r['status']:<10} {score:>5}  "
              f"{fu:<11} {r['updated_on']}")
        if r["notes"]:
            print(f"     note: {r['notes']}")
    print()


def print_application(a: dict) -> None:
    score = f"{a['score']}%" if a["score"] is not None else "-"
    print(f"\n#{a['id']}  {a['company']} - {a['role']}")
    print(f"  Status     {a['status']:<14} Score    {score}")
    print(f"  Applied    {a['applied_on']:<14} Updated  {a['updated_on']}")
    print(f"  Follow-up  {a['follow_up'] or '-'}")
    if a["link"]:
        print(f"  Link       {a['link']}")
    if a["notes"]:
        print(textwrap.fill(a["notes"], 90, initial_indent="  Notes      ", subsequent_indent=" " * 13))
    if a["missing_skills"]:
        print(f"  Missing    {', '.join(a['missing_skills'])}")
    print(f"  JD saved   {'yes (' + format(len(a['jd']), ',') + ' chars)' if a['jd'] else 'no'}")
    print("  Timeline")
    for h in a["history"]:
        print(f"    {h['changed_on']}  {h['status']}")
    print()


def print_stats(s: dict) -> None:
    if not s["total"]:
        print("No applications tracked yet.")
        return
    print(f"\nTotal applications: {s['total']}")
    for status, n in s["by_status"].items():
        print(f"  {status:<10} {n:>3}  {bar(round(100 * n / s['total']), 20)}")
    f = s["funnel"]
    print(f"\nFunnel (ever reached): applied {f['applied']} -> responded {f['responded']} "
          f"-> interview {f['interview']} -> offer {f['offer']}")
    if s["response_rate"] is not None:
        print(f"Response rate (OA/interview/offer): {s['response_rate']}%   |   Interview rate: {s['interview_rate']}%")
    if s["average_score"] is not None:
        print(f"Average match score: {s['average_score']}%")
    if s["average_score_responded"] is not None and s["average_score_no_response"] is not None:
        print(f"  ... for jobs that responded: {s['average_score_responded']}%   "
              f"|   no response: {s['average_score_no_response']}%")
    print()


# --------------------------------------------------------------------------- #
# CSV export / import
# --------------------------------------------------------------------------- #
def export_csv(path: str = "applications.csv") -> int:
    apps = sorted(fetch_applications(), key=lambda a: a["id"])
    out = sys.stdout if path == "-" else open(path, "w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for a in apps:
            writer.writerow({**a, "missing_skills": "; ".join(a["missing_skills"])})
    finally:
        if out is not sys.stdout:
            out.close()
    return len(apps)


def import_csv(path: str) -> int:
    """Import rows (company and role required). All rows are validated before anything is written."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        raise ResumeFitError(f"file not found: {path}") from None

    cleaned = []
    for line_no, row in enumerate(rows, start=2):  # line 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("company") or not row.get("role"):
            raise ResumeFitError(f"{path} line {line_no}: company and role are required")
        try:
            cleaned.append({
                "company": row["company"],
                "role": row["role"],
                "status": validate_status(row.get("status") or "applied"),
                "score": validate_score(row.get("score")),
                "link": row.get("link") or None,
                "notes": row.get("notes") or None,
                "follow_up": parse_date(row["follow_up"]) if row.get("follow_up") else None,
                "applied_on": parse_date(row["applied_on"]) if row.get("applied_on") else None,
                "updated_on": parse_date(row["updated_on"]) if row.get("updated_on") else None,
                "missing_skills": [s.strip() for s in row["missing_skills"].split(";") if s.strip()]
                if row.get("missing_skills") else None,
            })
        except ResumeFitError as e:
            raise ResumeFitError(f"{path} line {line_no}: {e}") from None

    for r in cleaned:
        add_application(r.pop("company"), r.pop("role"), **r)
    return len(cleaned)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Match your resume to job descriptions and track applications.")
    sub = p.add_subparsers(dest="command", required=True)
    date_help = "YYYY-MM-DD, today, tomorrow, or +N (days from now)"

    m = sub.add_parser("match", help="score a resume against a job description")
    m.add_argument("resume", help="resume file (.txt/.md/.pdf/.docx)")
    m.add_argument("jd", help="job description file, or the JD text in quotes")
    m.add_argument("--json", action="store_true", help="print the result as JSON")
    m.add_argument("--save", action="store_true", help="also save this job (and its JD) to the tracker")
    m.add_argument("--company", help="company name (with --save)")
    m.add_argument("--role", help="role title (with --save)")
    m.add_argument("--status", choices=STATUSES, default="wishlist", help="status to save with (default: wishlist)")
    m.add_argument("--link", help="job posting URL (with --save)")
    m.add_argument("--notes", help="notes (with --save)")
    m.add_argument("--follow-up", help=f"follow-up date (with --save): {date_help}")

    r = sub.add_parser("rank", help="rank many job descriptions against one resume")
    r.add_argument("resume")
    r.add_argument("jds", nargs="+", help="JD files and/or folders of JD files")
    r.add_argument("--json", action="store_true")

    s = sub.add_parser("suggest", help="tips to tailor your resume to a job description")
    s.add_argument("resume")
    s.add_argument("jd", help="job description file, or the JD text in quotes")
    s.add_argument("--llm", action="store_true", help="also ask a local Ollama model to rewrite bullet points")
    s.add_argument("--model", default=DEFAULT_LLM_MODEL, help=f"Ollama model (default: {DEFAULT_LLM_MODEL})")
    s.add_argument("--json", action="store_true")

    a = sub.add_parser("add", help="add an application to the tracker")
    a.add_argument("company")
    a.add_argument("role")
    a.add_argument("--status", choices=STATUSES, default="applied")
    a.add_argument("--score", type=int)
    a.add_argument("--link")
    a.add_argument("--notes")
    a.add_argument("--follow-up", help=date_help)
    a.add_argument("--jd", help="job description file or text to store (enables rescore / gaps)")
    a.add_argument("--resume", help="with --jd: compute the match score now")

    ls = sub.add_parser("list", help="list tracked applications")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("-q", "--search", help="text to look for in company, role or notes")
    ls.add_argument("--json", action="store_true")

    sh = sub.add_parser("show", help="show one application with its status timeline")
    sh.add_argument("id", type=int)
    sh.add_argument("--json", action="store_true")

    u = sub.add_parser("update", help="update an application")
    u.add_argument("id", type=int)
    u.add_argument("--status", choices=STATUSES)
    u.add_argument("--notes", help="replace the notes")
    u.add_argument("--add-note", help="append a dated note")
    u.add_argument("--link")
    u.add_argument("--score", type=int)
    u.add_argument("--follow-up", help=f"{date_help}, or 'none' to clear")

    d = sub.add_parser("delete", help="delete an application")
    d.add_argument("id", type=int)
    d.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    rm = sub.add_parser("remind", help="applications that need a follow-up")
    rm.add_argument("--days", type=int, default=7, help="flag applications with no update for this many days (default 7)")
    rm.add_argument("--json", action="store_true")

    g = sub.add_parser("gaps", help="skills you're missing most often across saved jobs")
    g.add_argument("--top", type=int, default=10)
    g.add_argument("--json", action="store_true")

    rs = sub.add_parser("rescore", help="re-score saved jobs against an updated resume")
    rs.add_argument("resume")
    rs.add_argument("--id", type=int, help="only this application")

    st = sub.add_parser("stats", help="pipeline summary")
    st.add_argument("--json", action="store_true")

    ex = sub.add_parser("export", help="export the tracker to CSV")
    ex.add_argument("path", nargs="?", default="applications.csv", help="output file, or - for stdout")

    im = sub.add_parser("import", help="import applications from CSV")
    im.add_argument("path")
    return p


def run(args: argparse.Namespace) -> None:
    dump = lambda obj: print(json.dumps(obj, indent=2))  # noqa: E731

    if args.command == "match":
        jd_text = read_text(args.jd)
        result = match(read_text(args.resume), jd_text)
        dump(result) if args.json else print_report(result)
        if args.save:
            if not (args.company and args.role):
                raise ResumeFitError("--save needs --company and --role")
            app_id = add_application(
                args.company, args.role, args.status, result["score"], args.link, args.notes,
                jd=jd_text, missing_skills=result["missing_skills"],
                follow_up=parse_date(args.follow_up) if args.follow_up else None,
            )
            print(f"Saved as application #{app_id} ({args.status}).")

    elif args.command == "rank":
        rows = rank(read_text(args.resume), args.jds)
        dump(rows) if args.json else print_rank(rows)

    elif args.command == "suggest":
        resume, jd = read_text(args.resume), read_text(args.jd)
        result = suggest(resume, jd)
        if args.llm:
            print(f"Asking {args.model} via Ollama (this can take a minute)...", file=sys.stderr)
            result["llm_suggestions"] = llm_suggestions(resume, jd, result, args.model)
        if args.json:
            dump(result)
        else:
            print_report(result)
            print_tips(result["tips"])
            if result.get("llm_suggestions"):
                print(f"=== AI rewrites ({args.model}) - check every line before using it ===")
                print(result["llm_suggestions"] + "\n")

    elif args.command == "add":
        jd_text = read_text(args.jd) if args.jd else None
        score, missing = args.score, None
        if args.resume:
            if not jd_text:
                raise ResumeFitError("--resume needs --jd")
            result = match(read_text(args.resume), jd_text)
            score, missing = result["score"], result["missing_skills"]
        app_id = add_application(
            args.company, args.role, args.status, score, args.link, args.notes,
            jd=jd_text, missing_skills=missing,
            follow_up=parse_date(args.follow_up) if args.follow_up else None,
        )
        extra = f", match {score}%" if score is not None else ""
        print(f"Added application #{app_id}: {args.company} - {args.role} ({args.status}{extra})")

    elif args.command == "list":
        apps = fetch_applications(args.status, args.search)
        dump(apps) if args.json else print_applications(apps)

    elif args.command == "show":
        app = get_application(args.id)
        dump(app) if args.json else print_application(app)

    elif args.command == "update":
        follow_up = None
        if args.follow_up is not None:
            follow_up = "" if args.follow_up.strip().lower() in ("none", "clear", "") else parse_date(args.follow_up)
        app = update_application(args.id, args.status, args.notes, add_note=args.add_note,
                                 link=args.link, score=args.score, follow_up=follow_up)
        print(f"Updated application #{app['id']}: {app['company']} - {app['role']} ({app['status']}).")

    elif args.command == "delete":
        app = get_application(args.id)
        if not args.yes:
            if not sys.stdin.isatty():
                raise ResumeFitError("refusing to delete without confirmation; pass --yes")
            answer = input(f"Delete #{app['id']} {app['company']} - {app['role']}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("Cancelled.")
                return
        delete_application(args.id)
        print(f"Deleted application #{args.id}.")

    elif args.command == "remind":
        due = reminders(args.days)
        if args.json:
            dump(due)
        elif not due:
            print(f"Nothing to follow up on. (Checked active applications idle for {args.days}+ days.)")
        else:
            print(f"\n{len(due)} application(s) need attention:")
            for a in due:
                print(f"  #{a['id']:<3} {a['company'][:18]:<18} {a['role'][:24]:<24} {a['status']:<9} {a['reason']}")
            print("\nUpdate one with:  python main.py update <id> --status ... --add-note \"...\"\n")

    elif args.command == "gaps":
        g = skill_gaps(args.top)
        if args.json:
            dump(g)
        elif not g["gaps"]:
            print("No skill gaps recorded yet. Save jobs with  match --save  (or add --jd --resume) first.")
        else:
            print(f"\nMost common missing skills across {g['jobs']} saved job(s):")
            for x in g["gaps"]:
                print(f"  {x['skill']:<28} {x['count']:>3} job(s)  {bar(x['pct'], 20)} {x['pct']}%")
            print("\nLearning the top ones (and showing them in a project) helps the most applications.\n")

    elif args.command == "rescore":
        changes = rescore(read_text(args.resume), args.id)
        if not changes:
            print("No saved job descriptions to rescore. Save jobs with  match --save  first.")
        for c in changes:
            old = f"{c['old_score']}%" if c["old_score"] is not None else "-"
            delta = c["new_score"] - c["old_score"] if c["old_score"] is not None else None
            arrow = f" ({delta:+d})" if delta else ""
            print(f"  #{c['id']:<3} {c['company'][:18]:<18} {c['role'][:24]:<24} {old:>4} -> {c['new_score']}%{arrow}")

    elif args.command == "stats":
        s = get_stats()
        dump(s) if args.json else print_stats(s)

    elif args.command == "export":
        n = export_csv(args.path)
        if args.path != "-":
            print(f"Exported {n} application(s) to {args.path}")

    elif args.command == "import":
        n = import_csv(args.path)
        print(f"Imported {n} application(s) from {args.path}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except ResumeFitError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
