# ATS Resume Checker

A lightweight command-line tool that takes a PDF resume, extracts text, and scores it
against a pasted job description using lightweight ATS keyword matching. Every analysis
is saved to a MySQL database so you can look back at previous results later.

## Features

- **PDF Resume Text Extraction** — uses `pdfplumber` to pull text from any PDF resume.
- **ATS Compatibility Scoring** — keyword-based scoring weighted 70% hard skills +
  30% soft skills, producing a 0–100 score.
- **Missing Skills Report** — shows exactly which skills from the job description your
  resume is missing.
- **MySQL Storage** — every analysis is stored with resume details, score, missing skills,
  matched skills, and timestamp.
- **Simple Menu** — choose between starting a new analysis or viewing your history.
- **Clean, recruiter-scannable output** — formatted reports with matched and missing skills.

## Requirements

- Python 3.10+
- MySQL server running (default localhost:3306, root user, empty password)
- Python packages: `pdfplumber`, `mysql-connector-python`

### Install dependencies

```bash
pip install -r requirements.txt
```

## Quick Start

1. Make sure MySQL is running locally.
2. Run the app:

   ```bash
   python app.py
   ```

3. Choose option `1` for a new analysis.
4. Enter the path to your PDF resume.
5. Paste the job description and press Enter (then press Enter twice to finish).
6. Review your ATS score and missing skills.
7. Choose option `2` to view all previously saved analyses.

## MySQL Setup

The app automatically creates a database named `ats_resume_checker` and a table called
`analyses`. If you need to change connection details, edit the `DBHandler` defaults at
the top of `app.py`:

```python
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 3306
DEFAULT_DB_USER = "root"
DEFAULT_DB_PASSWORD = ""
DEFAULT_DB_NAME = "ats_resume_checker"
```

## How Scoring Works

- **Hard skills** (e.g., Python, Java, AWS, SQL) are weighted **70%**.
- **Soft/behavioral skills** (e.g., communication, teamwork, leadership) are weighted **30%**.
- Each skill is a keyword matched against the job description and the resume text.
- The final score is a percentage from 0 to 100.

## Project Structure

```
ats_resume_checker/
├── app.py          # Main CLI application
├── requirements.txt # Python dependencies
└── README.md       # This file
```

## Notes

- The MySQL driver import path is handled dynamically via `sys.path` so the app works
  in sandboxed / containerized environments.
- If MySQL is not reachable, the app will warn you and continue, but results won't be
  saved until a connection is available.
