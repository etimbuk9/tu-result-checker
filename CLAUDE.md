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

Single-file FastAPI app (`main.py`) with no database. Student result CSVs live in Dropbox (`/FinalResults`). Verification and reassessment submissions are uploaded as CSVs to Dropbox (`/Verifications/` and `/Reassessments/`). Active session/semester/amount config and feature toggles are stored locally in `config.json`.

**Verification vs. Reassessment:**
- **Result Verification** (free): Student requests confirmation that a result is correct; no payment required.
- **Reassessment** (paid): Student requests a course be re-marked; payment via Paystack required.
- Both features can be toggled open/closed independently via admin panel.
- A student can request verification for some courses and reassessment for others in a single submission.

**Request flow:**
1. Admin sets active session/semester/amount and toggles features at `/admin/?key=<ADMIN_KEY>` → `POST /admin/set-config/` → writes `config.json` with `session`, `semester`, `amount_per_course`, `verification_open`
2. Student opens `/reassessment/` → fetches their courses via `GET /reassessment/get-results/?student=<no>` (reads CSV from Dropbox)
3. Student selects courses for verification and/or reassessment (two checkbox columns) → `POST /reassessment/select/` → entry cached under `reassessment:<uuid>` with both `verification_courses` and `reassessment_courses` (TTL 15 min), returns UUID
4. Student fills reason/complaint form at `/reassessment/complaint/<uuid>` → `POST /reassessment/init-payment/` with `verification_reasons` and `complaints` dicts
5. **Verification-only path** (no courses require reassessment): Server saves verification CSV to `/Verifications/`, sends verification email, returns `{"verification_only": True}`, redirects to confirmation
6. **Reassessment path** (with or without verification courses): Server initializes Paystack transaction, returns `access_code` for popup
7. Paystack popup completes, redirects to `GET /reassessment/confirm/?uuid=<uuid>&reference=<tx_ref>`
8. `/reassessment/confirm/` verifies Paystack (if reassessment included), uploads verification CSV (if any verification courses), uploads reassessment CSV (if any reassessment courses), fires background emails for each type, clears cache entry, renders confirmation page

**Paystack payment split:** When `DEBUG != 'True'`, the payment payload includes a split that sends 16.67% of the amount minus ₦300 (3000 kobo) to subaccount `ACCT_hm8ktsr7sd7xtxr`. In debug mode no split is applied. `dropbox_connect.headers` is the Paystack `Authorization` header dict (despite the module name) — it selects the live or test key based on `DEBUG` and is imported directly by `main.py` for both `transaction/initialize` and `transaction/verify` calls.

**Dropbox CSV structure:** Files are named `final-result-{session}-{semester}.csv`. Each row is one course for one student. `student_name` column holds the student *number* (integer). Key columns: `course_name`, `course_ccmas`, `course_title`, `course_units`, `total_score`, `final_grade`, `out_of_faculty`, `student_details` (JSON string containing `student_name`), `breakdown` (JSON with score components).

**Reassessment uploads:** `dropbox_connect.save_reassessment()` uploads a CSV to `/Reassessments/reassessment-{session}-{semester}-{timestamp}.csv` using `WriteMode.add`. Columns: `student_no`, `student_name`, `session`, `semester`, `course_name`, `course_title`, `complaint`, `payment_reference`, `timestamp`.

**Verification uploads:** `dropbox_connect.save_verification()` uploads a CSV to `/Verifications/verification-{session}-{semester}-{timestamp}.csv` using `WriteMode.add`. Columns: `student_no`, `student_name`, `session`, `semester`, `course_name`, `course_title`, `reason`, `timestamp`.

**Session list** is generated dynamically from `START_YEAR = 2021` to the current year. No code change needed to add new sessions. The `SEMESTERS` constant is `['First Semester', 'Supplementary1', 'Second Semester', 'Supplementary2']` — these exact strings must match Dropbox CSV filenames and are validated in `POST /admin/set-config/`.

**Email notifications** (`mail_service.py`): 
- **Reassessment emails** (`send_reassessment_emails`): after confirmed payment, sends HTML confirmation email to student (with payment reference and amount) and notification with complaint details to `STAFF_EMAILS`. Subject: "Reassessment Request Received — {session} {semester}".
- **Verification emails** (`send_verification_emails`): after confirmed verification submission, sends HTML confirmation email to student (no payment info) and notification with reason details to `STAFF_EMAILS`. Subject: "Result Verification Request Received — {session} {semester}".
- Both use `SMTP_USER`/`SMTP_PASS` via STARTTLS. Failures are logged but do not affect the response. Student emails are currently sent to `dict@topfaith.edu.ng` (hardcoded override of `{student_no}@topfaith.edu.ng`) — change line 98 in `mail_service.py` to restore per-student delivery. `STAFF_EMAILS` lists the active recipient addresses; others are commented out.

**`utils.py`:** Windows-only helper that adds all PATH entries as DLL search directories — only relevant when deploying on Windows.

**SSL:** `dropbox_connect.get_student_result()` uses `ssl._create_unverified_context()` to fetch Dropbox shared-link CSVs, bypassing certificate verification. This is intentional to work around SSL issues on the VPS.

**Unused dependencies:** `requirements.txt` includes PDF-generation libraries (`pdfkit`, `pyhanko`, `reportlab`, `xhtml2pdf`) that are not used in the current codebase — likely from a previous or planned feature.

## Testing

Tests set `DROPBOX_KEY` and `PAYSTACK_KEY` env vars before importing `main`, so no real credentials are needed. An `autouse` fixture (`set_active_config`) monkeypatches `main.CONFIG_PATH` to a temp file with a valid session/semester/amount and `verification_open: true` so every test starts with both features enabled.

Dropbox calls are patched via `@patch('main.dropbox_connect.get_result_url')` and `@patch('main.dropbox_connect.get_student_result')`. Paystack calls patch `main.requests.post` / `main.requests.get`. Email sending is not explicitly patched — both `send_reassessment_emails` and `send_verification_emails` are background tasks and their SMTP calls will fail silently in tests.

The `TestClient` is created with `follow_redirects=False` so redirect assertions check the 307 location directly.

**New test cases for verification:**
- `test_select_courses` now includes both `verification_courses` and `reassessment_courses` fields (both optional, at least one required)
- Tests now POST to `/reassessment/init-payment/` with both `verification_reasons` and `complaints` dicts
- Tests validate verification-only submission flows (no Paystack)
