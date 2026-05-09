# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the development server
uvicorn main:app --reload

# Run all tests
pytest test_main.py

# Run a single test
pytest test_main.py::test_get_grade_point

# Run tests with output
pytest test_main.py -v
```

The app requires a `.env` file with `DROPBOX_KEY` and `PAYSTACK_KEY` set. Without these, the app will crash on import of `dropbox_connect`.

## Architecture

This is a single-file FastAPI app (`main.py`) with no database — all student result data lives as CSV files in a Dropbox folder (`/FinalResults`).

**Request flow for viewing a result:**
1. `index.html` collects student number, session, semester
2. Frontend calls `/get-access-code/` → backend verifies the result exists in Dropbox, then initializes a Paystack transaction (₦1,000)
3. Paystack popup completes payment, frontend redirects to `/results2/?...&reference=<tx_ref>`
4. `/results2/` verifies the Paystack transaction, fetches the CSV from Dropbox, computes GPA/CGPA, caches the result under a UUID, and redirects to `/results2/view/<uuid>`
5. `/results2/view/<uuid>` renders `results2.html` from the TTL cache (15 min, 1000 entries max)
6. Discount codes (stored in `discounts.csv` on Dropbox) bypass Paystack and pass `reference=discountCode`

**Dropbox CSV structure:** Files are named `final-result-{session}-{semester}.csv` (e.g. `final-result-2023-2024-First Semester.csv`). Each row is one course result for one student, identified by `student_name` (confusingly, this column holds the student *number* as an integer). Key columns: `course_name`, `course_ccmas`, `course_title`, `course_units`, `total_score`, `final_grade`, `out_of_faculty`, `student_details` (JSON string with `student_name`), `breakdown` (JSON string with score components).

**CGPA calculation:** `calculate_cgpa()` iterates every session/semester combo from 2021 up to and including the requested one, fetches each CSV from Dropbox, concatenates all rows for the student, and computes a weighted average. `out_of_faculty=True` rows contribute 0 credit units (excluded from GPA). This makes multiple Dropbox API calls per request — it is not cached.

**PDF download:** `/download-result/` re-fetches the same data (no cache reuse), renders `report2.html` via Jinja2, then converts to PDF with `xhtml2pdf`. The `/results2/` view also offers a client-side PDF export via `html2pdf.js`.

**Grade scale:** A=5, B=4, C=3, D=2, E=1, F/anything else=0.

**Session list** is generated dynamically from `START_YEAR = 2021` to the current year. Adding new sessions requires no code change.

## Testing

Tests use `unittest.mock` to patch `dropbox_connect.get_result_url` and `dropbox_connect.get_student_result` — no real Dropbox or Paystack calls are made. The `TestClient` from `fastapi.testclient` is used for endpoint tests. The `test_get_access_code` test patches `requests.post` but the actual endpoint also calls `dropbox_connect` before Paystack — tests that don't mock that path will hit real Dropbox if credentials are present.
