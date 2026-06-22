import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASS = os.getenv('SMTP_PASS', '')

STAFF_EMAILS = [
    # 'hod@topfaith.edu.ng',
    # 'dean@topfaith.edu.ng',
    'dict@topfaith.edu.ng',
    'dap@topfaith.edu.ng',
    'registrar@topfaith.edu.ng',
    'vc@topfaith.edu.ng',
]


def get_faculty_emails(programme: str) -> list:
    import pandas as pd

    url = "https://www.dropbox.com/scl/fi/lrj3lemocvce04u79irkc/faculty_mails.csv?rlkey=qs670wcrm94sul0q7idko6uh7&dl=1"
    df = pd.read_csv(url)
    faculty_emails = df[df['Programme'] == programme][[
        'HOD', 'Dean']].iloc[0].values.tolist()
    return faculty_emails


def _send(to: list[str], subject: str, html: str) -> None:
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SMTP_USER
    msg['To'] = ', '.join(to)
    msg.attach(MIMEText(html, 'html'))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, to, msg.as_string())


def send_reassessment_emails(entry: dict, reference: str) -> None:
    """
    Sends student confirmation + staff notifications.
    Called as a FastAPI BackgroundTask — exceptions are caught and logged.
    """
    student_no = entry['student_no']
    student_name = entry['student_name']
    session = entry['session']
    semester = entry['semester']
    courses = entry.get('reassessment_courses', [])
    complaints = entry.get('complaints', {})
    amount_paid = entry.get('amount_per_course', 6000) * len(courses)

    course_rows_student = ''.join(
        f"<tr><td style='padding:4px 8px'>{c['course_name']}</td>"
        f"<td style='padding:4px 8px'>{c['course_title']}</td></tr>"
        for c in courses
    )
    course_rows_staff = ''.join(
        f"<tr><td style='padding:4px 8px'>{c['course_name']}</td>"
        f"<td style='padding:4px 8px'>{c['course_title']}</td>"
        f"<td style='padding:4px 8px'>{complaints.get(c['course_name'], '—')}</td></tr>"
        for c in courses
    )

    student_html = f"""
    <p>Dear {student_name},</p>
    <p>Your reassessment request for <strong>{session} — {semester}</strong> has been received and your payment confirmed.</p>
    <table border='1' cellspacing='0' style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>
      <tr style='background:#1d4ed8;color:white'>
        <th style='padding:6px 12px'>Course Code</th>
        <th style='padding:6px 12px'>Course Title</th>
      </tr>
      {course_rows_student}
    </table>
    <p><strong>Payment reference:</strong> {reference}<br>
    <strong>Amount paid:</strong> &#8358;{amount_paid:,}</p>
    <p>Your request will be reviewed by the relevant departments. Please retain this email and your payment reference for your records.</p>
    <p>Regards,<br>Topfaith University Registry</p>
    """

    staff_html = f"""
    <p>A student has submitted a reassessment request and payment has been confirmed.</p>
    <p>
      <strong>Student:</strong> {student_name} ({student_no})<br>
      <strong>Session:</strong> {session} — {semester}<br>
      <strong>Payment reference:</strong> {reference}<br>
      <strong>Amount paid:</strong> &#8358;{amount_paid:,}
    </p>
    <table border='1' cellspacing='0' style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>
      <tr style='background:#1d4ed8;color:white'>
        <th style='padding:6px 12px'>Course Code</th>
        <th style='padding:6px 12px'>Course Title</th>
        <th style='padding:6px 12px'>Complaint</th>
      </tr>
      {course_rows_staff}
    </table>
    """

    try:
        student_email = f"{student_no}@topfaith.edu.ng"
        # student_email = f"dict@topfaith.edu.ng"
        _send(
            [student_email],
            f"Reassessment Request Received — {session} {semester}",
            student_html,
        )
        logger.info("Confirmation email sent to %s", student_email)
    except Exception as exc:
        logger.error("Failed to send student confirmation email: %s", exc)

    faculty_emails = []
    programme = entry.get('student_programme', '')
    if programme:
        try:
            faculty_emails = get_faculty_emails(programme)
        except Exception as exc:
            logger.error(
                "Failed to get faculty emails for programme %s: %s", programme, exc)

    logger.info("Sending reassessment email to staff for student %s (%s), faculty emails: %s",
                student_no, student_name, faculty_emails)
    try:
        _send(
            STAFF_EMAILS + faculty_emails,
            f"New Reassessment Request — {student_name} ({student_no})",
            staff_html,
        )
        logger.info(
            "Notification email sent to staff for student %s", student_no)
    except Exception as exc:
        logger.error("Failed to send staff notification email: %s", exc)


def send_verification_emails(entry: dict) -> None:
    """
    Sends student confirmation + staff notifications for result verification.
    Called as a FastAPI BackgroundTask — exceptions are caught and logged.
    """
    student_no = entry['student_no']
    student_name = entry['student_name']
    session = entry['session']
    semester = entry['semester']
    courses = entry.get('verification_courses', [])
    reasons = entry.get('verification_reasons', {})

    course_rows_student = ''.join(
        f"<tr><td style='padding:4px 8px'>{c['course_name']}</td>"
        f"<td style='padding:4px 8px'>{c['course_title']}</td></tr>"
        for c in courses
    )
    course_rows_staff = ''.join(
        f"<tr><td style='padding:4px 8px'>{c['course_name']}</td>"
        f"<td style='padding:4px 8px'>{c['course_title']}</td>"
        f"<td style='padding:4px 8px'>{reasons.get(c['course_name'], '—')}</td></tr>"
        for c in courses
    )

    student_html = f"""
    <p>Dear {student_name},</p>
    <p>Your result verification request for <strong>{session} — {semester}</strong> has been received.</p>
    <table border='1' cellspacing='0' style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>
      <tr style='background:#1d4ed8;color:white'>
        <th style='padding:6px 12px'>Course Code</th>
        <th style='padding:6px 12px'>Course Title</th>
      </tr>
      {course_rows_student}
    </table>
    <p>No payment is required. Your request will be reviewed by the relevant departments. Please retain this email for your records.</p>
    <p>Regards,<br>Topfaith University Registry</p>
    """

    staff_html = f"""
    <p>A student has submitted a result verification request.</p>
    <p>
      <strong>Student:</strong> {student_name} ({student_no})<br>
      <strong>Session:</strong> {session} — {semester}
    </p>
    <table border='1' cellspacing='0' style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>
      <tr style='background:#1d4ed8;color:white'>
        <th style='padding:6px 12px'>Course Code</th>
        <th style='padding:6px 12px'>Course Title</th>
        <th style='padding:6px 12px'>Reason</th>
      </tr>
      {course_rows_staff}
    </table>
    """

    try:
        student_email = f"{student_no}@topfaith.edu.ng"
        # student_email = f"dict@topfaith.edu.ng"
        _send(
            [student_email],
            f"Result Verification Request Received — {session} {semester}",
            student_html,
        )
        logger.info("Verification confirmation email sent to %s",
                    student_email)
    except Exception as exc:
        logger.error("Failed to send verification student email: %s", exc)

    faculty_emails = []
    programme = entry.get('student_programme', '')
    logger.info("Fetching faculty emails for programme: %s", programme)
    if programme:
        try:
            faculty_emails = get_faculty_emails(programme)
        except Exception as exc:
            logger.error(
                "Failed to get faculty emails for programme %s: %s", programme, exc)

    logger.info("Sending verification notification email to staff for student %s (%s), faculty emails: %s",
                student_no, student_name, faculty_emails)
    try:
        _send(
            STAFF_EMAILS + faculty_emails,
            f"New Result Verification Request — {student_name} ({student_no})",
            staff_html,
        )
        logger.info(
            "Verification notification email sent to staff for student %s", student_no)
    except Exception as exc:
        logger.error("Failed to send verification staff email: %s", exc)
