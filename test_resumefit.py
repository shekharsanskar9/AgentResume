"""
Tests for ResumeFit.   Run:  python -m unittest discover -s tests -v   (or: pytest)

Web-app tests run only when fastapi + httpx are installed.
"""

import csv
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as rf  # noqa: E402

RESUME = """Sanskar Shekhar | sanskar@example.com | +91 98765 43210
linkedin.com/in/sanskar-shekhar | github.com/shekharsanskar9
- Built a voice agent pipeline in Python and FastAPI over WebSockets, cutting latency by 40%
- Deployed services with Docker on AWS EC2
- Built a RAG app with ChromaDB and a local LLM
Skills: Python, Java, C++, SQL, PyTorch, Git, Linux
"""
JD = """We need an AI engineer with Python, FastAPI, Docker, Kubernetes, LangChain and RAG experience.
You will build LLM pipelines and REST APIs and collaborate with product stakeholders."""


class TempDBTestCase(unittest.TestCase):
    """Each test gets its own empty database and a frozen 'today'."""
    TODAY = date(2026, 11, 7)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = mock.patch.object(rf, "DB_PATH", Path(self.tmp.name) / "test.db")
        self.today_patch = mock.patch.object(rf, "today", lambda: self.TODAY)
        self.db_patch.start()
        self.today_patch.start()

    def tearDown(self):
        self.today_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
class SkillExtractionTests(unittest.TestCase):
    def test_aliases(self):
        self.assertIn("Kubernetes", rf.find_skills("deployed on k8s"))
        self.assertIn("scikit-learn", rf.find_skills("models in sklearn"))
        self.assertIn("PostgreSQL", rf.find_skills("Postgres and Redis"))

    def test_whole_terms_only(self):
        skills = rf.find_skills("JavaScript developer")
        self.assertIn("JavaScript", skills)
        self.assertNotIn("Java", skills)

    def test_symbols_and_plurals(self):
        skills = rf.find_skills("C++, C#, Node.js, CI/CD and microservices; REST APIs")
        for s in ("C++", "C#", "Node.js", "CI/CD", "Microservices", "REST APIs"):
            self.assertIn(s, skills)

    def test_skill_mentions(self):
        self.assertEqual(rf.skill_mentions("Python, python and more Python", "Python"), 3)
        self.assertEqual(rf.skill_mentions("nothing here", "Python"), 0)


class MatchTests(unittest.TestCase):
    def test_match_result(self):
        r = rf.match(RESUME, JD)
        self.assertTrue(0 <= r["score"] <= 100)
        self.assertIn("FastAPI", r["matched_skills"])
        self.assertIn("Kubernetes", r["missing_skills"])
        self.assertIn("LangChain", r["missing_skills"])
        self.assertIn("Java", r["other_resume_skills"])

    def test_identical_text_similarity(self):
        self.assertAlmostEqual(rf.cosine_similarity("python fastapi docker", "python fastapi docker"), 1.0)

    def test_perfect_match_scores_high(self):
        self.assertGreaterEqual(rf.match(JD, JD)["score"], 95)

    def test_rank_orders_by_score(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "good.txt").write_text(JD)
            Path(d, "bad.txt").write_text("Looking for a Kotlin Android developer with Flutter and Swift.")
            rows = rf.rank(RESUME, [d])
        self.assertEqual([Path(r["jd"]).name for r in rows], ["good.txt", "bad.txt"])
        self.assertGreaterEqual(rows[0]["score"], rows[1]["score"])

    def test_rank_rejects_missing_path(self):
        with self.assertRaises(rf.ResumeFitError):
            rf.rank(RESUME, ["does/not/exist"])


class ReadTextTests(unittest.TestCase):
    def test_raw_text(self):
        self.assertEqual(rf.read_text("Python developer with FastAPI"), "Python developer with FastAPI")

    def test_very_long_text_is_not_treated_as_a_path(self):
        text = "x" * 5000
        self.assertEqual(rf.read_text(text), text)

    def test_missing_file(self):
        with self.assertRaises(rf.ResumeFitError):
            rf.read_text("missing.pdf")

    def test_reads_txt_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("hello resume")
        try:
            self.assertEqual(rf.read_text(f.name), "hello resume")
        finally:
            os.unlink(f.name)


class SuggestTests(unittest.TestCase):
    def test_tips_cover_missing_skills(self):
        r = rf.suggest(RESUME, JD)
        self.assertTrue(any("Kubernetes" in t for t in r["tips"]))

    def test_resume_checks(self):
        weak = "- Responsible for testing\n- Worked on APIs\n- Helped with docs\n- Wrote code\nI did my best"
        tips = " ".join(rf.resume_checks(weak))
        self.assertIn("email", tips)
        self.assertIn("phone", tips)
        self.assertIn('"responsible for"', tips)
        self.assertIn("bullet points contain a number", tips)

    def test_good_contact_line_passes(self):
        tips = " ".join(rf.resume_checks(RESUME))
        self.assertNotIn("No email", tips)
        self.assertNotIn("No phone", tips)
        self.assertNotIn("LinkedIn", tips)

    def test_llm_unreachable_gives_clean_error(self):
        with mock.patch.object(rf, "OLLAMA_URL", "http://127.0.0.1:9"):
            with self.assertRaises(rf.ResumeFitError):
                rf.llm_suggestions(RESUME, JD, rf.match(RESUME, JD), timeout=2)


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
class TrackerTests(TempDBTestCase):
    def test_add_list_show(self):
        app_id = rf.add_application("Amgen", "AI Engineer", "applied", 62, follow_up="2026-11-10")
        apps = rf.fetch_applications()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["company"], "Amgen")
        a = rf.get_application(app_id)
        self.assertEqual(a["history"], [{"status": "applied", "changed_on": "2026-11-07"}])

    def test_validation(self):
        with self.assertRaises(rf.ResumeFitError):
            rf.add_application("X", "Y", status="ghosted")
        with self.assertRaises(rf.ResumeFitError):
            rf.add_application("X", "Y", score=150)
        with self.assertRaises(rf.ResumeFitError):
            rf.add_application("", "Y")

    def test_search_and_filter(self):
        rf.add_application("Boeing", "Software Engineer", "applied", notes="referral")
        rf.add_application("Amgen", "AI Engineer", "interview")
        self.assertEqual(len(rf.fetch_applications(status="interview")), 1)
        self.assertEqual(rf.fetch_applications(search="referral")[0]["company"], "Boeing")

    def test_update_records_history_and_notes(self):
        app_id = rf.add_application("Boeing", "SDE", "applied")
        rf.update_application(app_id, "oa", add_note="OA on Monday")
        a = rf.update_application(app_id, "interview", add_note="Round 1 cleared")
        self.assertEqual([h["status"] for h in a["history"]], ["applied", "oa", "interview"])
        self.assertEqual(a["notes"], "[2026-11-07] OA on Monday | [2026-11-07] Round 1 cleared")

    def test_same_status_does_not_duplicate_history(self):
        app_id = rf.add_application("Boeing", "SDE", "applied")
        a = rf.update_application(app_id, "applied", notes="x")
        self.assertEqual(len(a["history"]), 1)

    def test_clear_follow_up(self):
        app_id = rf.add_application("Boeing", "SDE", follow_up="2026-11-10")
        self.assertIsNone(rf.update_application(app_id, follow_up="")["follow_up"])

    def test_update_errors(self):
        with self.assertRaises(rf.NotFoundError):
            rf.update_application(999, "oa")
        app_id = rf.add_application("Boeing", "SDE")
        with self.assertRaises(rf.ResumeFitError):
            rf.update_application(app_id)

    def test_delete(self):
        app_id = rf.add_application("Boeing", "SDE")
        rf.delete_application(app_id)
        self.assertEqual(rf.fetch_applications(), [])
        with self.assertRaises(rf.NotFoundError):
            rf.delete_application(app_id)

    def test_stats_use_history(self):
        a = rf.add_application("A", "r", "applied", 80)
        rf.update_application(a, "interview")
        rf.update_application(a, "rejected")            # still counts as an interview
        rf.add_application("B", "r", "applied", 40)       # no response
        rf.add_application("C", "r", "wishlist", 30)      # not applied -> not in the funnel
        s = rf.get_stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["funnel"], {"applied": 2, "responded": 1, "interview": 1, "offer": 0})
        self.assertEqual(s["response_rate"], 50)
        self.assertEqual(s["average_score_responded"], 80)
        self.assertEqual(s["average_score_no_response"], 40)

    def test_reminders(self):
        rf.add_application("Old", "r", "applied", applied_on="2026-10-20")          # stale
        rf.add_application("Due", "r", "interview", follow_up="2026-11-05")         # overdue follow-up
        rf.add_application("Fresh", "r", "applied")                                 # fine
        rf.add_application("Done", "r", "rejected", applied_on="2026-09-01")        # closed -> ignored
        due = {a["company"]: a["reason"] for a in rf.reminders(days=7)}
        self.assertEqual(set(due), {"Old", "Due"})
        self.assertIn("18 days", due["Old"])
        self.assertIn("overdue by 2 days", due["Due"])

    def test_skill_gaps(self):
        rf.add_application("A", "r", missing_skills=["AWS", "Kubernetes"])
        rf.add_application("B", "r", missing_skills=["AWS"])
        rf.add_application("C", "r", "withdrawn", missing_skills=["Go"])
        g = rf.skill_gaps()
        self.assertEqual(g["jobs"], 2)
        self.assertEqual(g["gaps"][0], {"skill": "AWS", "count": 2, "pct": 100})
        self.assertNotIn("Go", [x["skill"] for x in g["gaps"]])

    def test_rescore(self):
        app_id = rf.add_application("Amgen", "AI Engineer", score=10, jd=JD)
        better = RESUME + "\nKubernetes, LangChain, REST APIs, LLM pipelines"
        change = rf.rescore(better)[0]
        self.assertEqual(change["old_score"], 10)
        self.assertGreater(change["new_score"], 10)
        a = rf.get_application(app_id)
        self.assertNotIn("Kubernetes", a["missing_skills"])
        self.assertEqual(a["updated_on"], "2026-11-07")

    def test_migrates_old_database(self):
        import sqlite3
        conn = sqlite3.connect(rf.DB_PATH)
        conn.execute("""CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL,
                        role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'applied', score INTEGER, link TEXT,
                        notes TEXT, applied_on TEXT NOT NULL, updated_on TEXT NOT NULL)""")
        conn.execute("INSERT INTO applications (company, role, status, applied_on, updated_on)"
                     " VALUES ('Old Co', 'SDE', 'oa', '2026-10-01', '2026-10-05')")
        conn.commit()
        conn.close()
        a = rf.get_application(1)
        self.assertIsNone(a["follow_up"])
        self.assertEqual(a["history"], [{"status": "oa", "changed_on": "2026-10-05"}])

    def test_csv_round_trip(self):
        rf.add_application("Amgen", "AI Engineer", "interview", 62, "https://x.y", "note, with comma",
                           missing_skills=["AWS", "CI/CD"], follow_up="2026-11-10")
        path = Path(self.tmp.name) / "out.csv"
        self.assertEqual(rf.export_csv(str(path)), 1)
        with path.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["missing_skills"], "AWS; CI/CD")

        self.assertEqual(rf.import_csv(str(path)), 1)
        imported = rf.get_application(2)
        self.assertEqual(imported["notes"], "note, with comma")
        self.assertEqual(imported["missing_skills"], ["AWS", "CI/CD"])
        self.assertEqual(imported["follow_up"], "2026-11-10")

    def test_import_validates_before_writing(self):
        path = Path(self.tmp.name) / "bad.csv"
        path.write_text("company,role,status\nGood,SDE,applied\nBad,SDE,ghosted\n")
        with self.assertRaises(rf.ResumeFitError) as ctx:
            rf.import_csv(str(path))
        self.assertIn("line 3", str(ctx.exception))
        self.assertEqual(rf.fetch_applications(), [])     # nothing half-imported

    def test_parse_date(self):
        self.assertEqual(rf.parse_date("+3"), "2026-11-10")
        self.assertEqual(rf.parse_date("+3d"), "2026-11-10")
        self.assertEqual(rf.parse_date("tomorrow"), "2026-11-08")
        self.assertEqual(rf.parse_date("2026-12-01"), "2026-12-01")
        with self.assertRaises(rf.ResumeFitError):
            rf.parse_date("next week")


class CLITests(TempDBTestCase):
    def run_cli(self, *argv) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rf.main(list(argv))
        return buf.getvalue()

    def test_cli_flow(self):
        out = self.run_cli("add", "Boeing", "Software Engineer", "--follow-up", "+2")
        self.assertIn("Added application #1", out)
        self.run_cli("update", "1", "--status", "oa", "--add-note", "OA Monday")
        self.assertIn("oa", self.run_cli("show", "1"))
        self.assertIn("Boeing", self.run_cli("list"))
        self.assertIn("Total applications: 1", self.run_cli("stats"))
        self.assertIn("Deleted", self.run_cli("delete", "1", "--yes"))

    def test_cli_error_exits_cleanly(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("update", "42", "--status", "oa")
        self.assertIn("no application with id 42", str(ctx.exception.code))


# --------------------------------------------------------------------------- #
# Web app (optional)
# --------------------------------------------------------------------------- #
try:
    from fastapi.testclient import TestClient
    import app as web
except ImportError:  # fastapi / httpx not installed
    web = None


@unittest.skipIf(web is None, "fastapi/httpx not installed")
class WebTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(web.app)

    def test_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ResumeFit", r.text)

    def test_match_with_pasted_text_and_upload(self):
        r = self.client.post("/api/match", data={"jd": JD, "resume_text": RESUME})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Kubernetes", r.json()["missing_skills"])

        files = {"resume": ("resume.txt", RESUME.encode(), "text/plain")}
        r2 = self.client.post("/api/match", data={"jd": JD}, files=files)
        self.assertEqual(r2.json()["score"], r.json()["score"])

    def test_match_errors(self):
        r = self.client.post("/api/match", data={"jd": JD})
        self.assertEqual(r.status_code, 400)
        files = {"resume": ("resume.exe", b"MZ", "application/octet-stream")}
        self.assertEqual(self.client.post("/api/match", data={"jd": JD}, files=files).status_code, 400)

    def test_crud(self):
        r = self.client.post("/api/applications", json={"company": "Amgen", "role": "AI Engineer",
                                                        "score": 60, "follow_up": "+3"})
        self.assertEqual(r.status_code, 201, r.text)
        app_id = r.json()["id"]
        self.assertEqual(r.json()["follow_up"], "2026-11-10")

        r = self.client.patch(f"/api/applications/{app_id}", json={"status": "interview", "add_note": "R1"})
        self.assertEqual(r.json()["status"], "interview")
        self.assertEqual(len(r.json()["history"]), 2)

        self.assertEqual(len(self.client.get("/api/applications").json()), 1)
        self.assertEqual(self.client.get("/api/stats").json()["funnel"]["interview"], 1)
        self.assertIn("Amgen", self.client.get("/api/export.csv").text)

        self.assertEqual(self.client.delete(f"/api/applications/{app_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/applications/{app_id}").status_code, 404)

    def test_bad_status_is_400(self):
        r = self.client.post("/api/applications", json={"company": "A", "role": "B", "status": "ghosted"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
