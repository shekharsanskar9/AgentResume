#!/usr/bin/env python3
"""
ResumeFit - match your resume to a job description and track applications.

Features
  match   Score a resume against a job description (skill coverage + text similarity),
          list matched / missing skills, and the JD keywords your resume never mentions.
  add     Save a job application to a local SQLite tracker.
  list    Show tracked applications (filter by status).
  update  Change the status / notes of an application.
  stats   Summary of your pipeline (counts per status, average match score).

Usage
  python main.py match resume.pdf jd.txt
  python main.py match resume.pdf jd.txt --save --company Boeing --role "Software Engineer"
  python main.py add Amgen "AI Engineer" --status interview --link https://...
  python main.py list --status applied
  python main.py update 3 --status offer --notes "HR call on Monday"
  python main.py stats

Only the Python standard library is required. Optional:
  pip install pypdf python-docx     # to read .pdf and .docx resumes
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).with_name("applications.db")
STATUSES = ["wishlist", "applied", "oa", "interview", "offer", "rejected", "withdrawn"]

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
    # AI / ML / data
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "neural networks", "neural network"],
    "NLP": ["nlp", "natural language processing", "ner", "named entity recognition"],
    "Computer Vision": ["computer vision", "image processing", "object detection"],
    "LLMs": ["llm", "llms", "large language model", "large language models", "gpt", "generative ai", "genai"],
    "RAG": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
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


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def read_text(path_or_text: str) -> str:
    """Read .txt/.md/.pdf/.docx, or treat the argument as raw text if it isn't a file."""
    path = Path(path_or_text)
    if not path.exists():
        if len(path_or_text) > 40 or " " in path_or_text:
            return path_or_text  # raw text passed directly
        sys.exit(f"error: file not found: {path_or_text}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit("error: reading PDFs needs pypdf ->  pip install pypdf")
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        try:
            import docx
        except ImportError:
            sys.exit("error: reading .docx needs python-docx ->  pip install python-docx")
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8", errors="ignore")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9+#.\-]*[a-z0-9+#]|[a-z]", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def contains_term(text: str, term: str) -> bool:
    """Whole-term match that also works for terms like 'c++', 'node.js', 'ci/cd'."""
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?:e?s)?(?![a-z0-9+#])"  # allow plurals
    return re.search(pattern, text) is not None


def find_skills(text: str) -> set[str]:
    norm = normalize(text)
    return {skill for skill, aliases in SKILLS.items() if any(contains_term(norm, a) for a in aliases)}


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


# --------------------------------------------------------------------------- #
# Application tracker (SQLite)
# --------------------------------------------------------------------------- #
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    return conn


def add_application(company, role, status="applied", score=None, link=None, notes=None) -> int:
    today = date.today().isoformat()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO applications (company, role, status, score, link, notes, applied_on, updated_on)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (company, role, status, score, link, notes, today, today),
        )
        return cur.lastrowid


def list_applications(status: str | None = None) -> None:
    with db() as conn:
        query = "SELECT * FROM applications"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        rows = conn.execute(query + " ORDER BY updated_on DESC, id DESC", params).fetchall()

    if not rows:
        print("No applications tracked yet. Add one with:  python main.py add <company> <role>")
        return
    print(f"\n{'ID':>3}  {'Company':<18} {'Role':<26} {'Status':<10} {'Score':>5}  Updated")
    print("-" * 80)
    for r in rows:
        score = f"{r['score']}%" if r["score"] is not None else "-"
        print(f"{r['id']:>3}  {r['company'][:18]:<18} {r['role'][:26]:<26} {r['status']:<10} {score:>5}  {r['updated_on']}")
        if r["notes"]:
            print(f"     note: {r['notes']}")
    print()


def update_application(app_id: int, status=None, notes=None) -> None:
    fields, params = [], []
    if status:
        fields.append("status = ?")
        params.append(status)
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if not fields:
        sys.exit("error: nothing to update (use --status and/or --notes)")
    fields.append("updated_on = ?")
    params += [date.today().isoformat(), app_id]
    with db() as conn:
        cur = conn.execute(f"UPDATE applications SET {', '.join(fields)} WHERE id = ?", params)
    if cur.rowcount == 0:
        sys.exit(f"error: no application with id {app_id}")
    print(f"Updated application #{app_id}.")


def show_stats() -> None:
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        by_status = dict(conn.execute("SELECT status, COUNT(*) FROM applications GROUP BY status").fetchall())
        avg = conn.execute("SELECT AVG(score) FROM applications WHERE score IS NOT NULL").fetchone()[0]
    if not total:
        print("No applications tracked yet.")
        return
    print(f"\nTotal applications: {total}")
    for s in STATUSES:
        if by_status.get(s):
            print(f"  {s:<10} {by_status[s]:>3}  {bar(round(100 * by_status[s] / total), 20)}")
    responded = sum(by_status.get(s, 0) for s in ("oa", "interview", "offer"))
    print(f"Response rate (OA/interview/offer): {round(100 * responded / total)}%")
    if avg is not None:
        print(f"Average match score: {round(avg)}%")
    print()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Match your resume to job descriptions and track applications.")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("match", help="score a resume against a job description")
    m.add_argument("resume", help="resume file (.txt/.md/.pdf/.docx)")
    m.add_argument("jd", help="job description file, or the JD text in quotes")
    m.add_argument("--json", action="store_true", help="print the result as JSON")
    m.add_argument("--save", action="store_true", help="also save this job to the tracker")
    m.add_argument("--company", help="company name (with --save)")
    m.add_argument("--role", help="role title (with --save)")
    m.add_argument("--status", choices=STATUSES, default="wishlist", help="status to save with (default: wishlist)")

    a = sub.add_parser("add", help="add an application to the tracker")
    a.add_argument("company")
    a.add_argument("role")
    a.add_argument("--status", choices=STATUSES, default="applied")
    a.add_argument("--score", type=int)
    a.add_argument("--link")
    a.add_argument("--notes")

    ls = sub.add_parser("list", help="list tracked applications")
    ls.add_argument("--status", choices=STATUSES)

    u = sub.add_parser("update", help="update an application")
    u.add_argument("id", type=int)
    u.add_argument("--status", choices=STATUSES)
    u.add_argument("--notes")

    sub.add_parser("stats", help="pipeline summary")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "match":
        result = match(read_text(args.resume), read_text(args.jd))
        print(json.dumps(result, indent=2)) if args.json else print_report(result)
        if args.save:
            if not (args.company and args.role):
                sys.exit("error: --save needs --company and --role")
            app_id = add_application(args.company, args.role, args.status, result["score"])
            print(f"Saved as application #{app_id} ({args.status}).")
    elif args.command == "add":
        app_id = add_application(args.company, args.role, args.status, args.score, args.link, args.notes)
        print(f"Added application #{app_id}: {args.company} - {args.role} ({args.status})")
    elif args.command == "list":
        list_applications(args.status)
    elif args.command == "update":
        update_application(args.id, args.status, args.notes)
    elif args.command == "stats":
        show_stats()


if __name__ == "__main__":
    main()
