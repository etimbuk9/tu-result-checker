import ast
import logging
import os
import json
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import dropbox_connect
import mail_service
import requests
from datetime import datetime as dt
import pandas as pd
from pydantic import BaseModel

from cachetools import TTLCache
from uuid import uuid4
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Holds 1000 entries max, each expires after 15 minutes (900 seconds)
result_cache = TTLCache(maxsize=1000, ttl=900)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
ADMIN_KEY = os.getenv('ADMIN_KEY', '')


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(session: str, semester: str, amount_per_course: int, verification_open: bool = False) -> None:
    with open(CONFIG_PATH, 'w') as f:
        json.dump({'session': session, 'semester': semester,
                  'amount_per_course': amount_per_course, 'verification_open': verification_open}, f)


app = FastAPI()
START_YEAR = 2021
SESSIONS = [f"{x}-{x+1}" for x in range(START_YEAR, dt.now().year + 1)]
SEMESTERS = ['First Semester', 'Supplementary1',
             'Second Semester', 'Supplementary2']


# Assuming you're using a templates directory
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CourseSelection(BaseModel):
    course_name: str
    course_title: str
    course_units: int
    total_score: float = 0.0
    ca_score: float = 0.0
    exam_score: float = 0.0
    final_grade: str = ""


class SelectionRequest(BaseModel):
    student: str
    student_name: str
    student_programme: str = ""
    verification_courses: list[CourseSelection] = []
    reassessment_courses: list[CourseSelection] = []


class ComplaintsRequest(BaseModel):
    uuid: str
    verification_reasons: dict[str, str] = {}
    complaints: dict[str, str] = {}


class AdminConfigRequest(BaseModel):
    session: str
    semester: str
    amount_per_course: int
    verification_open: bool = False
    key: str


def verify_transaction(reference):
    response = requests.get(
        f"https://api.paystack.co/transaction/verify/{reference}",
        headers=dropbox_connect.headers,
    )
    return response.json()


@app.get("/")
async def root():
    return RedirectResponse(url="/reassessment/")


@app.get("/admin/", response_class=HTMLResponse)
async def admin_panel(request: Request, key: str = ""):
    if not ADMIN_KEY or key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    config = load_config()
    return templates.TemplateResponse(request, "admin.html", {
        "sessions": SESSIONS,
        "semesters": SEMESTERS,
        "current_session": config.get("session", ""),
        "current_semester": config.get("semester", ""),
        "current_amount": config.get("amount_per_course", 5000),
        "verification_open": config.get("verification_open", False),
        "admin_key": key,
    })


@app.post("/admin/set-config/")
async def set_config(body: AdminConfigRequest):
    if not ADMIN_KEY or body.key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    if body.session not in SESSIONS:
        raise HTTPException(status_code=400, detail="Invalid session")
    if body.semester not in SEMESTERS:
        raise HTTPException(status_code=400, detail="Invalid semester")
    if body.amount_per_course <= 0:
        raise HTTPException(
            status_code=400, detail="Amount must be greater than zero")
    save_config(body.session, body.semester, body.amount_per_course, body.verification_open)
    return {"status": True, "session": body.session, "semester": body.semester, "amount_per_course": body.amount_per_course, "verification_open": body.verification_open}


@app.get("/reassessment/", response_class=HTMLResponse)
async def reassessment_home(request: Request):
    config = load_config()
    session = config.get("session", "")
    semester = config.get("semester", "")
    return templates.TemplateResponse(request, "reassessment.html", {
        "session": session,
        "semester": semester,
        "reassessment_active": bool(session and semester),
        "verification_active": bool(config.get("verification_open") and session and semester),
    })


@app.get("/reassessment/get-results/")
async def get_reassessment_results(student: str):
    config = load_config()
    if not config.get("session") or not config.get("semester"):
        raise HTTPException(
            status_code=503, detail="Reassessment not currently open")
    url = dropbox_connect.get_result_url(config["session"], config["semester"])
    if url is None:
        raise HTTPException(
            status_code=404, detail="No result sheet found for this period")
    result = dropbox_connect.get_student_result(url, student)
    if result is None:
        raise HTTPException(status_code=404, detail="Student not found")
    result.fillna('', inplace=True)
    student_details = ast.literal_eval(result['student_details'].iloc[0])
    student_name = student_details['student_name']
    student_programme = student_details.get('student_programme', '')
    courses = []
    for _, row in result.iterrows():
        exam_score = row.get('exam_score') or 0
        ca_score = row.get('ca_score') or 0
        courses.append({
            "course_name": row["course_name"],
            "course_ccmas": row["course_ccmas"],
            "course_title": row["course_title"],
            "course_units": int(row["course_units"]),
            "total_score": float(row["total_score"]) if row["total_score"] != '' else 0,
            "ca_score": ca_score,
            "exam_score": exam_score,
            "final_grade": row["final_grade"],
        })
    return {"student_name": student_name, "student_programme": student_programme, "courses": courses}


@app.post("/reassessment/select/")
async def select_courses(body: SelectionRequest):
    if not body.verification_courses and not body.reassessment_courses:
        raise HTTPException(
            status_code=400, detail="Select at least one course")
    config = load_config()
    session = config.get("session", "")
    semester = config.get("semester", "")

    if body.reassessment_courses and (not session or not semester):
        raise HTTPException(
            status_code=503, detail="Reassessment not currently open")
    if body.verification_courses and not config.get("verification_open"):
        raise HTTPException(
            status_code=503, detail="Verification not currently open")

    uid = str(uuid4())
    result_cache[f"reassessment:{uid}"] = {
        "student_no": body.student,
        "student_name": body.student_name,
        "student_programme": body.student_programme,
        "session": session,
        "semester": semester,
        "verification_courses": [c.model_dump() for c in body.verification_courses],
        "reassessment_courses": [c.model_dump() for c in body.reassessment_courses],
        "verification_reasons": {},
        "complaints": {},
    }
    return {"uuid": uid}


@app.get("/reassessment/complaint/{uuid}", response_class=HTMLResponse)
async def complaint_form(request: Request, uuid: str):
    entry = result_cache.get(f"reassessment:{uuid}")
    if entry is None:
        raise HTTPException(
            status_code=404, detail="Session expired or not found")
    amount_per_course = entry.get(
        "amount_per_course") or load_config().get("amount_per_course", 5000)
    entry["amount_per_course"] = amount_per_course
    result_cache[f"reassessment:{uuid}"] = entry

    reassessment_courses = entry.get("reassessment_courses", [])
    n_reassessment = len(reassessment_courses)
    amount_naira = amount_per_course * n_reassessment if n_reassessment > 0 else 0
    amount_kobo = amount_per_course * 100 * n_reassessment if n_reassessment > 0 else 0

    return templates.TemplateResponse(request, "reassessment-complaint.html", {
        "uuid": uuid,
        "student_no": entry["student_no"],
        "student_name": entry["student_name"],
        "session": entry["session"],
        "semester": entry["semester"],
        "verification_courses": entry.get("verification_courses", []),
        "reassessment_courses": reassessment_courses,
        "amount_naira": amount_naira,
        "amount_kobo": amount_kobo,
    })


def get_charge_amount(amount_per_course, num_courses):
    charge = amount_per_course * 100 * num_courses * 0.015 + (500*100)

    if charge < 80000:
        charge = 80000
    elif charge > 250000:
        charge = 250000
    else:
        charge = int(charge)
    return charge


@app.post("/reassessment/init-payment/")
async def init_reassessment_payment(request: Request, body: ComplaintsRequest, background_tasks: BackgroundTasks):
    entry = result_cache.get(f"reassessment:{body.uuid}")
    if entry is None:
        raise HTTPException(status_code=404, detail="Session expired")

    entry["verification_reasons"] = body.verification_reasons
    entry["complaints"] = body.complaints
    result_cache[f"reassessment:{body.uuid}"] = entry

    reassessment_courses = entry.get("reassessment_courses", [])
    verification_courses = entry.get("verification_courses", [])

    if not reassessment_courses:
        if verification_courses:
            rows = []
            for course in verification_courses:
                rows.append({
                    "student_no": entry["student_no"],
                    "student_name": entry["student_name"],
                    "session": entry["session"],
                    "semester": entry["semester"],
                    "course_name": course["course_name"],
                    "course_title": course["course_title"],
                    "reason": entry["verification_reasons"].get(course["course_name"], ""),
                    "timestamp": dt.now().isoformat(),
                })
            df = pd.DataFrame(rows)
            try:
                dropbox_connect.save_verification(df, entry["session"], entry["semester"])
            except Exception:
                raise HTTPException(
                    status_code=502,
                    detail="Could not save your verification request. Please try again or contact the registry.",
                )
            entry["submitted"] = True
            result_cache[f"reassessment:{body.uuid}"] = entry
            background_tasks.add_task(mail_service.send_verification_emails, entry)
            return {"verification_only": True}

    num_courses = len(reassessment_courses)
    amount_kobo = entry.get("amount_per_course", 5000) * 100 * num_courses + get_charge_amount(
        entry.get("amount_per_course", 5000), num_courses)
    email = f"{entry['student_no']}@topfaith.edu.ng"
    base_url = os.getenv('BASE_URL', str(request.base_url)).rstrip('/')
    callback_url = f"{base_url}/reassessment/confirm/?uuid={body.uuid}"
    custom_info = [
        {
            "display_name": "Student Number 202XXXXXX",
            "variable_name": "student_number_202xxxxxx",
            "value": entry['student_no']
        },
        {
            "display_name": "Reassessment",
            "variable_name": "reassessment",
            "value": "Reassessment Request"
        },
    ]
    payload = {
        "email": email,
        "amount": str(amount_kobo),
        "callback_url": callback_url,
        "metadata": {
            "custom_fields": custom_info
        },
    }
    if not os.getenv('DEBUG') == 'True':
        payload['split'] = {
            "type": "flat",
            "bearer_type": "account",
            "subaccounts": [
                    {
                        "subaccount": "ACCT_hm8ktsr7sd7xtxr",
                        "share": get_charge_amount(entry.get("amount_per_course", 5000), num_courses) - 30000,
                    },
            ]
        }
    response = requests.post(
        "https://api.paystack.co/transaction/initialize",
        headers=dropbox_connect.headers,
        json=payload,
    )
    data = response.json()
    logger.info("Paystack init response | student=%s amount_kobo=%s status=%s message=%s",
                entry['student_no'], amount_kobo, data.get("status"), data.get("message"))
    if not data.get("status"):
        logger.error("Paystack init failed | full response: %s", data)
        raise HTTPException(
            status_code=502, detail="Payment initialisation failed")
    return {"access_code": data["data"]["access_code"]}


@app.get("/reassessment/confirm/", response_class=HTMLResponse)
async def reassessment_confirm(request: Request, uuid: str, reference: str = "",
                               background_tasks: BackgroundTasks = None):
    entry = result_cache.get(f"reassessment:{uuid}")
    if entry is None:
        return templates.TemplateResponse(request, "reassessment-confirm.html", {
            "error": "Session expired. Please start over.",
        })

    reassessment_courses = entry.get("reassessment_courses", [])
    verification_courses = entry.get("verification_courses", [])

    if reassessment_courses and reference:
        verification = verify_transaction(reference)
        if not verification.get("status") or verification.get("data", {}).get("status") != "success":
            return templates.TemplateResponse(request, "reassessment-confirm.html", {
                "error": "Payment could not be verified.",
            })

    if reassessment_courses:
        rows = []
        for course in reassessment_courses:
            rows.append({
                "student_no": entry["student_no"],
                "student_name": entry["student_name"],
                "session": entry["session"],
                "semester": entry["semester"],
                "course_name": course["course_name"],
                "course_title": course["course_title"],
                "complaint": entry["complaints"].get(course["course_name"], ""),
                "payment_reference": reference,
                "timestamp": dt.now().isoformat(),
            })
        df = pd.DataFrame(rows)
        try:
            dropbox_connect.save_reassessment(df, entry["session"], entry["semester"])
        except Exception:
            return templates.TemplateResponse(request, "reassessment-confirm.html", {
                "error": f"Your payment was received but we could not save your request. Please contact the registry with your payment reference: {reference}",
            })
        background_tasks.add_task(mail_service.send_reassessment_emails, entry, reference)

    if verification_courses:
        rows = []
        for course in verification_courses:
            rows.append({
                "student_no": entry["student_no"],
                "student_name": entry["student_name"],
                "session": entry["session"],
                "semester": entry["semester"],
                "course_name": course["course_name"],
                "course_title": course["course_title"],
                "reason": entry["verification_reasons"].get(course["course_name"], ""),
                "timestamp": dt.now().isoformat(),
            })
        df = pd.DataFrame(rows)
        try:
            dropbox_connect.save_verification(df, entry["session"], entry["semester"])
        except Exception:
            logger.error("Failed to save verification for uuid=%s", uuid)
        background_tasks.add_task(mail_service.send_verification_emails, entry)

    result_cache.pop(f"reassessment:{uuid}", None)

    return templates.TemplateResponse(request, "reassessment-confirm.html", {
        "student_name": entry["student_name"],
        "student_no": entry["student_no"],
        "session": entry["session"],
        "semester": entry["semester"],
        "verification_courses": verification_courses,
        "reassessment_courses": reassessment_courses,
        "reference": reference,
        "num_verification": len(verification_courses),
        "num_reassessment": len(reassessment_courses),
        "amount_paid": entry.get("amount_per_course", 5000) * len(reassessment_courses) if reassessment_courses else 0,
    })
