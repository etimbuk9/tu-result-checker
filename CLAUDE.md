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
pytest test_main.py::test_root_redirects_to_reassessment

# Run tests with output
pytest test_main.py -v
```

The app requires a `.env` file. Required vars: `DROPBOX_KEY`, `PAYSTACK_KEY`. Optional: `PS_TEST_KEY` (Paystack test key used when `DEBUG=True`), `ADMIN_KEY` (protects `/admin/`), `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` (for email), `BASE_URL` (callback URL base, defaults to request base URL), `DEBUG` (`True` to use test Paystack key and skip payment splits).

Without `DROPBOX_KEY` the app crashes on import of `dropbox_connect`.

## Architecture

Single-file FastAPI app (`main.py`) with no database. Student result CSVs live in Dropbox (`/FinalResults`). Reassessment submissions are uploaded as CSVs to Dropbox (`/Reassessments`). Active session/semester/amount config is stored locally in `config.json`.

**Request flow for a reassessment:**
1. Admin sets active session/semester/amount at `/admin/?key=<ADMIN_KEY>` → `POST /admin/set-config/` → writes `config.json`
2. Student opens `/reassessment/` → fetches their courses via `GET /reassessment/get-results/?student=<no>` (reads CSV from Dropbox)
3. Student selects courses → `POST /reassessment/select/` → entry cached under `reassessment:<uuid>` (TTL 15 min), returns UUID
4. Student fills complaint form at `/reassessment/complaint/<uuid>` → `POST /reassessment/init-payment/` → initializes Paystack transaction, returns `access_code`
5. Paystack popup completes, redirects to `GET /reassessment/confirm/?uuid=<uuid>&reference=<tx_ref>`
6. `/reassessment/confirm/` verifies Paystack transaction, uploads a reassessment CSV to Dropbox, fires background email (student + staff), clears cache entry, renders confirmation page

**Paystack payment split:** When `DEBUG != 'True'`, the payment payload includes a split that sends 16.67% of the amount minus ₦300 (3000 kobo) to subaccount `ACCT_hm8ktsr7sd7xtxr`. In debug mode no split is applied. `dropbox_connect.headers` is the Paystack `Authorization` header dict (despite the module name) — it selects the live or test key based on `DEBUG` and is imported directly by `main.py` for both `transaction/initialize` and `transaction/verify` calls.

**Dropbox CSV structure:** Files are named `final-result-{session}-{semester}.csv`. Each row is one course for one student. `student_name` column holds the student *number* (integer). Key columns: `course_name`, `course_ccmas`, `course_title`, `course_units`, `total_score`, `final_grade`, `out_of_faculty`, `student_details` (JSON string containing `student_name`), `breakdown` (JSON with score components).

**Reassessment uploads:** `dropbox_connect.save_reassessment()` uploads a CSV to `/Reassessments/reassessment-{session}-{semester}-{timestamp}.csv` using `WriteMode.add`.

**Session list** is generated dynamically from `START_YEAR = 2021` to the current year. No code change needed to add new sessions. The `SEMESTERS` constant is `['First Semester', 'Supplementary1', 'Second Semester', 'Supplementary2']` — these exact strings must match Dropbox CSV filenames and are validated in `POST /admin/set-config/`.

**Email notifications** (`mail_service.py`): after a confirmed reassessment, a background task sends an HTML confirmation email to the student and a notification with complaint details to addresses in `STAFF_EMAILS`. Both use `SMTP_USER`/`SMTP_PASS` via STARTTLS. Failures are logged but do not affect the response. The student confirmation email is currently sent to `dict@topfaith.edu.ng` (hardcoded override of `{student_no}@topfaith.edu.ng`) — change line 98 in `mail_service.py` to restore per-student delivery. `STAFF_EMAILS` lists the active recipient addresses; others are commented out.

**`utils.py`:** Windows-only helper that adds all PATH entries as DLL search directories — only relevant when deploying on Windows.

**SSL:** `dropbox_connect.get_student_result()` uses `ssl._create_unverified_context()` to fetch Dropbox shared-link CSVs, bypassing certificate verification. This is intentional to work around SSL issues on the VPS.

**Unused dependencies:** `requirements.txt` includes PDF-generation libraries (`pdfkit`, `pyhanko`, `reportlab`, `xhtml2pdf`) that are not used in the current codebase — likely from a previous or planned feature.

## Testing

Tests set `DROPBOX_KEY` and `PAYSTACK_KEY` env vars before importing `main`, so no real credentials are needed. An `autouse` fixture (`set_active_config`) monkeypatches `main.CONFIG_PATH` to a temp file with a valid session/semester so every test starts with an active config.

Dropbox calls are patched via `@patch('main.dropbox_connect.get_result_url')` and `@patch('main.dropbox_connect.get_student_result')`. Paystack calls patch `main.requests.post` / `main.requests.get`. Email sending is not explicitly patched — `send_reassessment_emails` is a background task and its SMTP calls will fail silently in tests.

The `TestClient` is created with `follow_redirects=False` so redirect assertions check the 307 location directly.
