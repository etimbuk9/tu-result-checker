from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import dropbox_connect

app = FastAPI()

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


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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


@app.get("/results2/", response_class=HTMLResponse)
async def get_result_html2(request: Request, session: str, semester: str, student: str):

    print(f"Session: {session}, Semester: {semester}, Student: {student}")

    if not session or not semester or not student:
        raise HTTPException(status_code=400, detail="Missing parameters")

    if '/' in session:
        session = str(session).replace('/', '-')

    url = dropbox_connect.get_result_url(session, semester)

    if url:
        result = dropbox_connect.get_student_result(url, student)
        student_name = ""
        print(f"Result: {result}")
        if result is not None:
            result.fillna('', inplace=True)
            student_name = eval(result['student_details'].iloc[0])[
                'student_name']
            total_gp = sum([get_grade_point(grade) * units for grade,
                           units in zip(result['final_grade'], result['course_units'])])
            data = {'status': '', 'results': [], 'html': ''}
            for index, row in result.iterrows():
                # print(type(row['student_details']))
                result_dict = [f"{row['course_name']}-{row['course_ccmas']}-{row['course_title']}",
                               row['course_units'], row['total_score'], row['final_grade']]
                data['status'] = 'success'
                data['results'].append(result_dict)
            # data['html'] = f"""
            #     <table class="table-auto" style="width: 100%;">
            #     <thead class="text-left">
            #         <tr>
            #             <th>Course Name</th>
            #             <th>Course Units</th>
            #             <th>Total Score</th>
            #             <th>Final Grade</th>
            #         </tr>
            #     </thead>
            #     <tbody>
            #         {''.join([f"<tr><td>{r[0]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>" for r in data['results']])}
            #     </tbody>
            #     </table>

            #     <br><hr><br>

            #     <table style="width: 100%;">
            #     <tbody>
            #     <tr>
            #         <td>Total Credit Hours</td>
            #         <td>{sum(result['course_units'].tolist())}</td>
            #     </tr>
            #     <tr>
            #         <td>Total Grade Points</td>
            #         <td>{total_gp}</td>
            #     </tr>
            #     <tr>
            #         <td>Grade Point Average</td>
            #         <td>{round(total_gp/sum(result['course_units'].tolist()), 2)}</td>
            #     </tr>
            #     </tbody>
            #     </table>

            #     <br><hr><br>

            #     """
            return templates.TemplateResponse("results2.html", {
                "request": request,
                "session": session,
                "semester": semester,
                "student": student,
                "student_name": student_name,
                "results": data['results'],
                "total_credit_hours": sum(result['course_units'].tolist()),
                "total_grade_points": total_gp,
                "gpa": round(total_gp/sum(result['course_units'].tolist()), 2),
            })
        else:
            return templates.TemplateResponse("results2.html", {'request': request, 'error': 'Student result not found.'})
