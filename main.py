from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import dropbox_connect
import requests
from datetime import datetime as dt
import pandas as pd
import urllib
import ssl
from xhtml2pdf import pisa
import pdfkit
from io import BytesIO

from cachetools import TTLCache
from uuid import uuid4
import json

# Holds 1000 entries max, each expires after 15 minutes (900 seconds)
result_cache = TTLCache(maxsize=1000, ttl=900)


app = FastAPI()
START_YEAR = 2021
SESSIONS = [f"{x}-{x+1}" for x in range(START_YEAR, dt.now().year + 1)]
SEMESTERS = ['First Semester', 'Supplementary1',
             'Second Semester', 'Supplementary2']


def get_session_semester_combos():

    return [(x, y) for x in SESSIONS for y in SEMESTERS]


# Assuming you're using a templates directory
# app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

origins = [
    "http://localhost:5500",  # frontend origin
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["*"] to allow all
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResultBreakdown:
    def __init__(self, result: dict):
        self.attendance = result.get('attendance', 0)
        self.assignment = result.get('assignment', 0)
        self.mid_sem_test = result.get('mid_sem_test', 0)
        self.class_presentation = result.get('class_presentation', 0)
        self.senate_recommends = result.get('senate_recommends', 0)
        self.exam_score = result.get('exam_score', 0)


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


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/confirm-discount-code/")
async def confirm_discount_code(request: Request, discount_code: str):
    codes_name = "discounts.csv"
    codes_url = dropbox_connect.get_code_url(codes_name)

    if codes_url is None:
        return {'status': False, 'message': 'Discount code file not found.'}

    context = ssl._create_unverified_context()

    # Open the URL manually and read into pandas
    with urllib.request.urlopen(codes_url, context=context) as response:
        df = pd.read_csv(response)

    if discount_code.strip() in df['discountCode'].values:

        return {'status': True, 'message': 'Discount code is valid.'}

    return {'status': False, 'message': 'Discount code is invalid.'}


def calculate_cgpa(session, semester, student):

    combos = get_session_semester_combos()
    # print(combos)
    idx = combos.index((session, semester))

    results = []
    for i in range(idx+1):
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


@app.get("/get-access-code/")
async def get_access_code(email: str, callbackUrl: str):
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    data = {
        "email": email,
        "amount": "100000",
        'callback_url': callbackUrl,
        'split_code': 'SPL_DHW7LKoOeE',
    }

    response = requests.post(
        "https://api.paystack.co/transaction/initialize",
        headers=dropbox_connect.headers,
        data=data,)

    return response.json()


@app.get("/results/")
async def get_result_html(session: str, semester: str, student: str):

    print(f"Session: {session}, Semester: {semester}, Student: {student}")

    if not session or not semester or not student:
        raise HTTPException(status_code=400, detail="Missing parameters")

    if '/' in session:
        session = str(session).replace('/', '-')

    url = dropbox_connect.get_result_url(session, semester)

    if url:
        result = dropbox_connect.get_student_result(url, student)
        print(f"Result: {result}")
        if result is not None:
            result.fillna('', inplace=True)
            total_gp = sum([get_grade_point(grade) * units for grade,
                           units in zip(result['final_grade'], result['course_units'])])
            data = {'status': '', 'results': [], 'html': ''}
            for index, row in result.iterrows():
                # print(type(row['student_details']))
                result_dict = [f"{row['course_name']}-{row['course_ccmas']}-{row['course_title']}", eval(row['student_details'])['student_name'],
                               row['course_units'], row['total_score'], row['final_grade']]
                data['status'] = 'success'
                data['results'].append(result_dict)
            data['html'] = f"""
                <table class="table-auto" style="width: 100%;">
                <thead class="text-left">
                    <tr>
                        <th>Course Name</th>
                        <th>Course Units</th>
                        <th>Total Score</th>
                        <th>Final Grade</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join([f"<tr><td>{r[0]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>" for r in data['results']])}
                </tbody>
                </table>
                
                <br><hr><br>
                
                <table style="width: 100%;">
                <tbody>
                <tr>
                    <td>Total Credit Hours</td>
                    <td>{sum(result['course_units'].tolist())}</td>
                </tr>
                <tr>
                    <td>Total Grade Points</td>
                    <td>{total_gp}</td>
                </tr>
                <tr>
                    <td>Grade Point Average</td>
                    <td>{round(total_gp/sum(result['course_units'].tolist()), 2)}</td>
                </tr>
                </tbody>
                </table>
                
                <br><hr><br>
                
                """
            return data
        else:
            return {"detail": "Student result not found."}

    return {"detail": "Student result not found."}


# @app.get("/results2/", response_class=HTMLResponse)
# async def get_result_html2(request: Request, session: str, semester: str, student: str, reference: str):

#     print(f"Session: {session}, Semester: {semester}, Student: {student}")

#     if not session or not semester or not student:
#         raise HTTPException(status_code=400, detail="Missing parameters")

#     if '/' in session:
#         session = str(session).replace('/', '-')

#     url = dropbox_connect.get_result_url(session, semester)

#     if reference != 'discountCode':
#         valid_transaction = verify_transaction(reference)
#     else:
#         valid_transaction = {'status': True}

#     if valid_transaction['status']:

#         if url:
#             result = dropbox_connect.get_student_result(url, student)
#             student_name = ""
#             # print(f"Result: {result}")
#             if result is not None:
#                 result.fillna('', inplace=True)
#                 student_name = eval(result['student_details'].iloc[0])[
#                     'student_name']

#                 # print(type(result['out_of_faculty'].iloc[0]))
#                 total_gp = sum([get_grade_point(grade) * units for grade,
#                                 units, oof in zip(result['final_grade'], result['course_units'], result['out_of_faculty']) if not oof])
#                 data = {'status': '', 'results': [], 'html': ''}
#                 for index, row in result.iterrows():
#                     # print(type(row['student_details']))
#                     result_dict = [f"{row['course_name']}-{row['course_ccmas']}-{row['course_title']}",
#                                    row['course_units'] if not row['out_of_faculty'] else 0, row['total_score'], row['final_grade'], ResultBreakdown(eval(row['breakdown']))]
#                     data['status'] = 'success'
#                     data['results'].append(result_dict)

#                 cgpa = calculate_cgpa(session, semester, student)
#                 tch = sum([unit for unit, oof in zip(
#                     result['course_units'], result['out_of_faculty']) if not oof])

#                 return templates.TemplateResponse("results2.html", {
#                     "request": request,
#                     "session": session,
#                     "semester": semester,
#                     "student": student,
#                     "student_name": student_name,
#                     "results": data['results'],
#                     "total_credit_hours": tch,
#                     "total_grade_points": total_gp,
#                     "gpa": round(total_gp/tch, 2) if tch > 0 else 0,
#                     "cgpa": round(cgpa, 2),
#                 })

#     return templates.TemplateResponse("results2.html", {'request': request, 'error': 'Student result not found.'})


@app.get("/results2/", response_class=HTMLResponse)
async def get_result_html2(request: Request, session: str, semester: str, student: str, reference: str):
    if not session or not semester or not student:
        raise HTTPException(status_code=400, detail="Missing parameters")

    session = session.replace('/', '-')
    url = dropbox_connect.get_result_url(session, semester)

    valid_transaction = verify_transaction(
        reference) if reference != 'discountCode' else {'status': True}

    if not (valid_transaction['status'] and url):
        return templates.TemplateResponse("results2.html", {'request': request, 'error': 'Student result not found.'})

    result = dropbox_connect.get_student_result(url, student)
    if result is None:
        return templates.TemplateResponse("results2.html", {'request': request, 'error': 'Result data not found.'})

    result.fillna('', inplace=True)
    student_name = eval(result['student_details'].iloc[0])['student_name']

    results = []
    total_gp = 0
    total_ch = 0

    for _, row in result.iterrows():
        breakdown = ResultBreakdown(eval(row['breakdown']))
        course_units = row['course_units']
        is_oof = row['out_of_faculty']
        units = course_units if not is_oof else 0
        grade_point = get_grade_point(row['final_grade']) * units
        total_gp += grade_point
        total_ch += units
        results.append([
            f"{row['course_name']}-{row['course_ccmas']}-{row['course_title']}",
            units,
            row['total_score'],
            row['final_grade'],
            breakdown
        ])

    cgpa = calculate_cgpa(session, semester, student)
    gpa = round(total_gp / total_ch, 2) if total_ch > 0 else 0

    # Generate ID and cache result
    result_id = str(uuid4())
    result_cache[result_id] = {
        "session": session,
        "semester": semester,
        "student": student,
        "student_name": student_name,
        "results": results,
        "total_credit_hours": total_ch,
        "total_grade_points": total_gp,
        "gpa": gpa,
        "cgpa": round(cgpa, 2),
    }

    return RedirectResponse(url=f"/results2/view/{result_id}", status_code=302)


@app.get("/results2/view/{result_id}", response_class=HTMLResponse)
async def view_result(request: Request, result_id: str):
    if result_id not in result_cache:
        return templates.TemplateResponse("results2.html", {
            "request": request,
            "error": "Result expired or not found."
        })

    data = result_cache[result_id]

    return templates.TemplateResponse("results2.html", {
        "request": request,
        "session": data['session'],
        "semester": data['semester'],
        "student": data['student'],
        "student_name": data['student_name'],
        "results": data['results'],
        "total_credit_hours": data['total_credit_hours'],
        "total_grade_points": data['total_grade_points'],
        "gpa": data['gpa'],
        "cgpa": data['cgpa'],
    })


def render_template(template_name: str, context: dict) -> str:
    """Render a Jinja2 template with context."""
    template = templates.get_template(template_name)
    return template.render(context)


def convert_html_to_pdf(source_html: str) -> bytes:
    """Convert HTML content to PDF and return as bytes."""
    result = BytesIO()
    pdf = pisa.CreatePDF(src=source_html, dest=result)
    if not pdf.err:
        return result.getvalue()
    return None


@app.get("/download-result/")
async def download_result(request: Request, session: str, semester: str, student: str):
    if not session or not semester or not student:
        raise HTTPException(status_code=400, detail="Missing parameters")

    if '/' in session:
        session = str(session).replace('/', '-')

    url = dropbox_connect.get_result_url(session, semester)

    if url:
        result = dropbox_connect.get_student_result(url, student)
        student_name = ""
        # print(f"Result: {result}")
        if result is not None:
            result.fillna('', inplace=True)
            student_name = eval(result['student_details'].iloc[0])[
                'student_name']
            total_gp = sum([get_grade_point(grade) * units for grade,
                            units, oof in zip(result['final_grade'], result['course_units'], result['out_of_faculty']) if not oof])
            data = {'status': '', 'results': [], 'html': ''}
            for index, row in result.iterrows():
                # print(type(row['student_details']))
                result_dict = [f"{row['course_name']}-{row['course_ccmas']}-{row['course_title']}",
                               row['course_units'] if not row['out_of_faculty'] else 0, row['total_score'], row['final_grade'], ResultBreakdown(eval(row['breakdown']))]
                data['status'] = 'success'
                data['results'].append(result_dict)

            cgpa = calculate_cgpa(session, semester, student)

            tch = sum([unit for unit, oof in zip(
                result['course_units'], result['out_of_faculty']) if not oof])

            context = {
                "session": session,
                "semester": semester,
                "student": student,
                "student_name": student_name,
                "results": data['results'],
                "total_credit_hours": tch,
                "total_grade_points": total_gp,
                "gpa": round(total_gp/tch, 2) if tch > 0 else 0,
                "cgpa": round(cgpa, 2),
            }

            temp = render_template('report.html', context)
            pdf_options = {
                "page-size": "A4",
                "orientation": "Landscape",
                "encoding": "UTF-8",
                "margin-top": "10mm",
                "margin-bottom": "10mm",
                "margin-left": "10mm",
                "margin-right": "10mm",
            }

            pdf = pdfkit.from_string(temp, False, options=pdf_options)

            return Response(content=pdf, media_type="application/pdf", headers={
                "Content-Disposition": f"attachment; filename=result_{session}_{semester}_{student}.pdf"
            })

    # return templates.TemplateResponse("results2.html", {
    #     "request": request,
    #     "session": session,
    #     "semester": semester,
    #     "student": student,
    #     "student_name": student_name,
    #     "results": data['results'],
    #     "total_credit_hours": sum(result['course_units'].tolist()),
    #     "total_grade_points": total_gp,
    #     "gpa": round(total_gp/sum(result['course_units'].tolist()), 2),
    #     "cgpa": round(cgpa, 2),
    # })
