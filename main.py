import os
import json
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import dropbox_connect
import requests
from datetime import datetime as dt
import pandas as pd
from pydantic import BaseModel

from cachetools import TTLCache
from uuid import uuid4
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Holds 1000 entries max, each expires after 15 minutes (900 seconds)
result_cache = TTLCache(maxsize=1000, ttl=900)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
ADMIN_KEY = os.getenv('ADMIN_KEY', '')


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(session: str, semester: str) -> None:
    with open(CONFIG_PATH, 'w') as f:
        json.dump({'session': session, 'semester': semester}, f)


app = FastAPI()
START_YEAR = 2021
SESSIONS = [f"{x}-{x+1}" for x in range(START_YEAR, dt.now().year + 1)]
SEMESTERS = ['First Semester', 'Supplementary1',
             'Second Semester', 'Supplementary2']


def get_session_semester_combos():
    return [(x, y) for x in SESSIONS for y in SEMESTERS]


# Assuming you're using a templates directory
templates = Jinja2Templates(directory="templates")

origins = [
    "http://localhost:5500",  # frontend origin
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CourseSelection(BaseModel):
    course_name: str
    course_title: str
    course_units: int


class SelectionRequest(BaseModel):
    student: str
    student_name: str
    courses: list[CourseSelection]


class ComplaintsRequest(BaseModel):
    uuid: str
    complaints: dict[str, str]


class AdminConfigRequest(BaseModel):
    session: str
    semester: str
    key: str


def get_grade_point(grade):
    if grade == 'A':
        return 5
    elif grade == 'B':
        return 4
    elif grade == 'C':
        return 3
    elif grade == 'D':
        return 2
    elif grade == 'E':
        return 1
    else:
        return 0


def verify_transaction(reference):
    response = requests.get(
        f"https://api.paystack.co/transaction/verify/{reference}",
        headers=dropbox_connect.headers,
    )
    return response.json()


def calculate_cgpa(session, semester, student):
    combos = get_session_semester_combos()
    idx = combos.index((session, semester))

    results = []
    for i in range(idx + 1):
        session, semester = combos[i]
        url = dropbox_connect.get_result_url(session, semester)
        if url:
            result = dropbox_connect.get_student_result(url, student)
            if result is not None:
                results.append(result)

    result_df = pd.concat(results, ignore_index=True)
    result_df['grade'] = result_df['final_grade'].apply(
        lambda x: 5 if x == 'A' else 4 if x == 'B' else 3 if x == 'C' else 2 if x == 'D' else 1 if x == 'E' else 0)
    result_df['course_units'] = [0 if result_df['out_of_faculty'].iloc[x]
                                 else result_df['course_units'].iloc[x] for x in range(result_df.shape[0])]
    result_df['grade_point'] = result_df['grade'] * result_df['course_units']
    total_credit_units = result_df['course_units'].sum()
    total_grade_points = result_df['grade_point'].sum()
    cgpa = total_grade_points / total_credit_units

    print(f"CGPA: {cgpa}")

    return cgpa


@app.get("/")
async def root():
    return RedirectResponse(url="/reassessment/")


@app.get("/admin/", response_class=HTMLResponse)
async def admin_panel(request: Request, key: str = ""):
    if not ADMIN_KEY or key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    config = load_config()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "sessions": SESSIONS,
        "semesters": SEMESTERS,
        "current_session": config.get("session", ""),
        "current_semester": config.get("semester", ""),
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
    save_config(body.session, body.semester)
    return {"status": True, "session": body.session, "semester": body.semester}


@app.get("/reassessment/", response_class=HTMLResponse)
async def reassessment_home(request: Request):
    config = load_config()
    return templates.TemplateResponse("reassessment.html", {
        "request": request,
        "session": config.get("session", ""),
        "semester": config.get("semester", ""),
        "active": bool(config.get("session") and config.get("semester")),
    })


@app.get("/reassessment/get-results/")
async def get_reassessment_results(student: str):
    config = load_config()
    if not config.get("session") or not config.get("semester"):
        raise HTTPException(status_code=503, detail="Reassessment not currently open")
    url = dropbox_connect.get_result_url(config["session"], config["semester"])
    if url is None:
        raise HTTPException(status_code=404, detail="No result sheet found for this period")
    result = dropbox_connect.get_student_result(url, student)
    if result is None:
        raise HTTPException(status_code=404, detail="Student not found")
    result.fillna('', inplace=True)
    student_name = eval(result['student_details'].iloc[0])['student_name']
    courses = []
    for _, row in result.iterrows():
        courses.append({
            "course_name": row["course_name"],
            "course_ccmas": row["course_ccmas"],
            "course_title": row["course_title"],
            "course_units": int(row["course_units"]),
            "total_score": float(row["total_score"]) if row["total_score"] != '' else 0,
            "final_grade": row["final_grade"],
        })
    return {"student_name": student_name, "courses": courses}


@app.post("/reassessment/select/")
async def select_courses(body: SelectionRequest):
    if not body.courses:
        raise HTTPException(status_code=400, detail="Select at least one course")
    config = load_config()
    if not config.get("session") or not config.get("semester"):
        raise HTTPException(status_code=503, detail="Reassessment not currently open")
    uid = str(uuid4())
    result_cache[f"reassessment:{uid}"] = {
        "student_no": body.student,
        "student_name": body.student_name,
        "session": config["session"],
        "semester": config["semester"],
        "courses": [c.model_dump() for c in body.courses],
        "complaints": {},
    }
    return {"uuid": uid}


@app.get("/reassessment/complaint/{uuid}", response_class=HTMLResponse)
async def complaint_form(request: Request, uuid: str):
    entry = result_cache.get(f"reassessment:{uuid}")
    if entry is None:
        raise HTTPException(status_code=404, detail="Session expired or not found")
    n = len(entry["courses"])
    return templates.TemplateResponse("reassessment-complaint.html", {
        "request": request,
        "uuid": uuid,
        "student_no": entry["student_no"],
        "student_name": entry["student_name"],
        "session": entry["session"],
        "semester": entry["semester"],
        "courses": entry["courses"],
        "amount_naira": 6000 * n,
        "amount_kobo": 600000 * n,
    })


@app.post("/reassessment/init-payment/")
async def init_reassessment_payment(request: Request, body: ComplaintsRequest):
    entry = result_cache.get(f"reassessment:{body.uuid}")
    if entry is None:
        raise HTTPException(status_code=404, detail="Session expired")
    entry["complaints"] = body.complaints
    result_cache[f"reassessment:{body.uuid}"] = entry
    amount_kobo = 600000 * len(entry["courses"])
    email = f"{entry['student_no']}@topfaith.edu.ng"
    callback_url = str(request.base_url) + f"reassessment/confirm/?uuid={body.uuid}"
    payload = {
        "email": email,
        "amount": str(amount_kobo),
        "callback_url": callback_url,
        "split_code": "SPL_DHW7LKoOeE",
    }
    response = requests.post(
        "https://api.paystack.co/transaction/initialize",
        headers=dropbox_connect.headers,
        data=payload,
    )
    data = response.json()
    if not data.get("status"):
        raise HTTPException(status_code=502, detail="Payment initialisation failed")
    return {"access_code": data["data"]["access_code"]}


@app.get("/reassessment/confirm/", response_class=HTMLResponse)
async def reassessment_confirm(request: Request, uuid: str, reference: str):
    entry = result_cache.get(f"reassessment:{uuid}")
    if entry is None:
        return templates.TemplateResponse("reassessment-confirm.html", {
            "request": request,
            "error": "Session expired. Please start over.",
        })
    verification = verify_transaction(reference)
    if not verification.get("status") or verification.get("data", {}).get("status") != "success":
        return templates.TemplateResponse("reassessment-confirm.html", {
            "request": request,
            "error": "Payment could not be verified.",
        })
    rows = []
    for course in entry["courses"]:
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
    dropbox_connect.save_reassessment(df, entry["session"], entry["semester"])
    result_cache.pop(f"reassessment:{uuid}", None)
    return templates.TemplateResponse("reassessment-confirm.html", {
        "request": request,
        "student_name": entry["student_name"],
        "student_no": entry["student_no"],
        "session": entry["session"],
        "semester": entry["semester"],
        "courses": entry["courses"],
        "reference": reference,
        "num_courses": len(entry["courses"]),
        "amount_paid": 6000 * len(entry["courses"]),
    })
