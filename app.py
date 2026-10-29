"""
ATS Resume Checker — Command Line Application

A lightweight CLI tool that:
  * Extracts text from a PDF resume
  * Compares it against a pasted job description
  * Scores ATS compatibility against a weighted skill matrix
  * Lists missing skills
  * Stores each analysis in a MySQL database
  * Offers a simple menu for new-analysis and history views

Requirements:
    pip install pdfplumber pymysql

Run:
    python app.py
"""

from __future__ import annotations

import json
import os
import re
import site
import sys

# Add user site-packages to sys.path so third-party imports
# (pdfplumber, pymysql) resolve correctly in sandboxed environments.
try:
    _user_site = site.getusersitepackages()
    if os.path.isdir(_user_site) and _user_site not in sys.path:
        sys.path.insert(0, _user_site)
except Exception:
    pass


from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    import mysql.connector
except ImportError:
    mysql = None
else:
    mysql = True

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 3306
DEFAULT_DB_USER = "root"
DEFAULT_DB_PASSWORD = ""
DEFAULT_DB_NAME = "ats_resume_checker"

# Keywords that are treated as *hard* requirements (lightweight keyword scoring).
# You can extend this list with domain-specific terms.
HARD_TERMS = [
    "python", "java", "javascript", "typescript", "react", "node", "sql",
    "aws", "azure", "gcp", "docker", "kubernetes", "git", "api", "rest",
    "graphql", "html", "css", "ci/cd", "agile", "scrum", "machine learning",
    "deep learning", "nlp", "data analysis", "etl", "spark", "snowflake",
    "terraform", "ansible", "jira", "figma", "mongodb", "postgresql",
    "redis", "kafka", "ci", "cd", "cloud", "microservice", "testing",
    "selenium", "pytest", "unit test", "integration test", "linux", "macos",
    "windows", "bash", "powershell", "mongo", "redis", "elasticsearch",
]

# Soft / behavioural skills that are commonly screened by ATS skills parsers.
SOFT_SKILLS = [
    "communication", "teamwork", "leadership", "problem solving", "problem-solving",
    "time management", "adaptability", "creativity", "critical thinking",
    "strategic thinking", "customer focus", "stakeholder", "negotiation",
    "conflict resolution", "mentoring", "coaching", "cross-functional",
    "documentation", "presentation", "analytical", "attention to detail",
    "detail-oriented", "initiative", "ownership", "collaboration",
]

# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------


def extract_pdf_text(pdf_path: Path) -> str:
    """Pull all text out of a PDF using pdfplumber."""
    if pdfplumber is None:
        raise RuntimeError(
            "pdfplumber is required. Install it with: pip install pdfplumber"
        )

    text_parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text()
            if txt:
                text_parts.append(txt)
    return "\n".join(text_parts)


# ---------------------------------------------------------------------------
# Job description parsing
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Lower-case and extract word tokens from text."""
    lowered = text.lower()
    # Replace all non-alphanumeric characters with spaces (keep a-z, 0-9)
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    # Split on whitespace to get individual words
    parts = lowered.split()
    # Also break hyphenated and slash-separated compounds into individual words
    expanded = []
    for p in parts:
        expanded.extend(p.split("-"))
        expanded.extend(p.split("/"))
    tokens = set(p for p in expanded if p)
    return tokens


def tokenize_job_description(job_desc: str) -> tuple[set[str], set[str]]:
    """Return (skill_tokens, hard_term_matches) from a job description."""
    tokens = _tokenize(job_desc)
    hard_hits = [t for t in HARD_TERMS if t in tokens]
    return tokens, set(hard_hits)


# ---------------------------------------------------------------------------
# Skill-matching engine
# ---------------------------------------------------------------------------


def extract_skills_from_text(text: str) -> set[str]:
    """Scan a resume for known skills (hard + soft)."""
    tokens = _tokenize(text)
    found: set[str] = set()
    for skill in HARD_TERMS + SOFT_SKILLS:
        if skill.lower() in tokens:
            found.add(skill)
    return found


def score_ats(
    resume_text: str,
    job_description: str,
) -> tuple[int, list[str], set[str], set[str]]:
    """Compare a resume against a job description and return scoring data."""
    job_tokens, _ = tokenize_job_description(job_description)
    resume_tokens = _tokenize(resume_text)

    # Normalise job tokens a little more for matching (strip slashes, etc.).
    expanded_job = set()
    for t in job_tokens:
        expanded_job.add(t)
        # Treat hyphenated phrases as separate tokens too.
        for part in t.replace("/", " ").split("-"):
            if part:
                expanded_job.add(part)

    # Token-to-skill matching.
    matched_skills: set[str] = set()
    missing_skills: set[str] = set()

    for skill in HARD_TERMS + SOFT_SKILLS:
        # A skill matches if its core tokens all appear in the job description.
        skill_tokens = set(_tokenize(skill.lower()))
        if skill_tokens.issubset(expanded_job):
            # Only mark matched if the skill is actually present in the resume.
            if skill_tokens.issubset(resume_tokens):
                matched_skills.add(skill)
            else:
                missing_skills.add(skill)

    # 70% of score from hard skills, 30% from soft skills.
    hard_total = len(HARD_TERMS)
    soft_total = len(SOFT_SKILLS)
    hard_count = len(matched_skills & set(HARD_TERMS))
    soft_count = len(matched_skills & set(SOFT_SKILLS))

    # Avoid division by zero.
    hard_score = int(round((hard_count / hard_total) * 100)) if hard_total else 0
    soft_score = int(round((soft_count / soft_total) * 100)) if soft_total else 0
    overall_score = int(round(hard_score * 0.70 + soft_score * 0.30))
    overall_score = min(overall_score, 100)

    # Collect genuinely missing skills (in the job description but not on resume).
    resume_skill_set = extract_skills_from_text(resume_text)
    job_skill_set = extract_skills_from_text(job_description)
    missing = sorted(job_skill_set - resume_skill_set)

    return overall_score, missing, matched_skills, resume_skill_set


# ---------------------------------------------------------------------------
# MySQL storage
# ---------------------------------------------------------------------------


class DBHandler:
    """Light wrapper around mysql-connector-python."""

    def __init__(
        self,
        host: str = DEFAULT_DB_HOST,
        port: int = DEFAULT_DB_PORT,
        user: str = DEFAULT_DB_USER,
        password: str = DEFAULT_DB_PASSWORD,
        database: str = DEFAULT_DB_NAME,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection = None

    def connect(self) -> None:
        """Connect to MySQL and ensure the schema exists."""
        if not mysql:
            raise RuntimeError(
                "MySQL driver (pymysql or mysql-connector-python) is required. "
                "Install it with: pip install pymysql"
            )
        import mysql.connector as _mysql_connector
        self.connection = _mysql_connector.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            connect_timeout=10,
        )
        cursor = self.connection.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
        cursor.execute(f"USE `{self.database}`")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                resume_file_path VARCHAR(500) NOT NULL,
                resume_text MEDIUMTEXT,
                job_description MEDIUMTEXT,
                ats_score INT NOT NULL,
                matched_skills JSON,
                missing_skills JSON,
                matched_count INT,
                total_skills INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.commit()
        cursor.close()

    def save_analysis(
        self,
        resume_file_path: str,
        resume_text: str,
        job_description: str,
        score: int,
        missing: list[str],
        matched: set[str],
    ) -> int:
        """Insert an analysis record and return its ID."""
        if self.connection is None:
            self.connect()

        cursor = self.connection.cursor()
        query = (
            "INSERT INTO analyses "
            "(resume_file_path, resume_text, job_description, ats_score, "
            " missing_skills, matched_skills, matched_count, total_skills) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        values = (
            resume_file_path,
            resume_text,
            job_description,
            score,
            json.dumps(missing),
            json.dumps(sorted(matched)),
            len(matched),
            len(HARD_TERMS) + len(SOFT_SKILLS),
        )
        cursor.execute(query, values)
        self.connection.commit()
        analysis_id = cursor.lastrowid
        cursor.close()
        return analysis_id

    def get_history(self, limit: int = 20) -> list[dict]:
        """Return the most recent analyses."""
        if self.connection is None:
            self.connect()
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, resume_file_path, ats_score, missing_skills, "
            "matched_skills, matched_count, total_skills, created_at "
            "FROM analyses ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def close(self) -> None:
        if self.connection:
            self.connection.close()


# ---------------------------------------------------------------------------
# Report / output formatting
# ---------------------------------------------------------------------------


def format_report(
    resume_file: str,
    resume_text: str,
    job_description: str,
    score: int,
    missing: list[str],
    matched: set[str],
    analysis_id: int,
) -> str:
    """Build a clean, recruiter-scannable report as a string."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  ATS RESUME COMPATIBILITY REPORT")
    lines.append("=" * 60)
    lines.append(f"Analysis ID : {analysis_id}")
    lines.append(f"Resume      : {resume_file}")
    lines.append(f"Checked     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("-" * 60)
    lines.append(f"ATS COMPATIBILITY SCORE : {score}/100")
    lines.append("-" * 60)
    lines.append(" ")
    lines.append("  - MATCHED SKILLS (from job description)")
    if matched:
        for s in sorted(matched):
            lines.append(f"    * {s}")
    else:
        lines.append("    (none)")
    lines.append(" ")
    lines.append("  - MISSING SKILLS (from job description)")
    if missing:
        for s in missing:
            lines.append(f"    X {s}")
        lines.append(f"  ({len(missing)} skill(s))")
    else:
        lines.append("    (none — resume covers all job requirements)")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------


def menu() -> int:
    """Display the main menu and return the user's choice."""
    print("\n" + "=" * 40)
    print("  ATS Resume Checker")
    print("=" * 40)
    print("  1. New Analysis")
    print("  2. View History")
    print("  3. Exit")
    choice = input("  Choose an option (1-3): ").strip()
    return choice


def get_db_config() -> dict:
    """Prompt the user for MySQL connection details."""
    print("\n  --- MySQL Configuration ---")
    host = input("  Host [localhost]: ").strip() or "localhost"
    port = input("  Port [3306]: ").strip() or "3306"
    user = input("  Username [root]: ").strip() or "root"
    password = input("  Password (leave blank if none): ").strip() or ""
    database = input("  Database [ats_resume_checker]: ").strip() or "ats_resume_checker"
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "database": database,
    }


def run_new_analysis(db: DBHandler) -> None:
    """Run a single resume-vs-job-description analysis."""
    print("\n  --- New Analysis ---")

    resume_path = input("  Path to PDF resume: ").strip()
    if not resume_path:
        print("  X No resume path provided.")
        return
    pdf_file = Path(resume_path)
    if not pdf_file.exists():
        print(f"  X File not found: {resume_path}")
        return
    if pdf_file.suffix.lower() != ".pdf":
        print(f"  X Expected a .pdf file, got {pdf_file.suffix}")
        return

    job_desc = input("\n  Paste the job description below (Ctrl+D / Enter twice to finish):\n")
    if not job_desc.strip():
        print("  X No job description provided.")
        return

    try:
        resume_text = extract_pdf_text(pdf_file)
    except Exception as exc:
        print(f"  X Failed to read PDF: {exc}")
        return

    if not resume_text.strip():
        print("  X No text could be extracted from the PDF.")
        return

    score, missing, matched, _ = score_ats(resume_text, job_description)

    try:
        analysis_id = db.save_analysis(
            resume_file=str(pdf_file),
            resume_text=resume_text,
            job_description=job_description,
            score=score,
            missing=missing,
            matched=matched,
        )
    except Exception as exc:
        print(f"  X Failed to save analysis: {exc}")
        return

    print("\n" + format_report(
        resume_file=pdf_file.name,
        resume_text=resume_text,
        job_description=job_description,
        score=score,
        missing=missing,
        matched=matched,
        analysis_id=analysis_id,
    ))


def run_history(db: DBHandler) -> None:
    """Show previously saved analyses."""
    print("\n  --- Analysis History ---")
    rows = db.get_history(limit=20)
    if not rows:
        print("  (No analyses saved yet)")
        return

    for row in rows:
        missing: list[str] = []
        matched: set[str] = set()
        if row.get("missing_skills"):
            try:
                missing = json.loads(row["missing_skills"])
            except Exception:
                pass
        if row.get("matched_skills"):
            try:
                matched = set(json.loads(row["matched_skills"]))
            except Exception:
                pass

        print("\n  " + "=" * 40)
        print(f"  Analysis #{row['id']}")
        print(f"  Resume : {row['resume_file_path']}")
        print(f"  Score  : {row['ats_score']}/100  (matched {row['matched_count']}/{row['total_skills']})")
        print(f"  Date   : {row['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(row['created_at'], 'strftime') else row['created_at']}")
        missing_text = ', '.join(missing[:10])
        if len(missing) > 10:
            missing_text += ' ...'
        print(f"  Missing: {missing_text}")
        print("  " + "=" * 40)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print("""
  ╔═══════════════════════════════════════╗
  ║   ATS Resume Checker v1.0             ║
  ║   Resume vs Job Description Scorer    ║
  ╚═══════════════════════════════════════╝
    """)

    db = DBHandler()
    try:
        db.connect()
    except Exception as exc:
        print(f"  Warning: could not connect to MySQL ({exc})")
        print("  The app will still work for analysis, but results will not be saved.")
        db.connection = None

    while True:
        choice = menu()
        if choice == "1":
            try:
                run_new_analysis(db)
            except KeyboardInterrupt:
                print("\n  Interrupted.")
        elif choice == "2":
            run_history(db)
        elif choice == "3":
            print("  Goodbye!")
            break
        else:
            print("  X Invalid option. Please choose 1, 2, or 3.")


if __name__ == "__main__":
    main()
