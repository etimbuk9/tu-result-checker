import pytest
import json
import os
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import pandas as pd

# Set required env vars before importing main
os.environ.setdefault('DROPBOX_KEY', 'test_key')
os.environ.setdefault('PAYSTACK_KEY', 'Bearer test_paystack')
os.environ.setdefault('ADMIN_KEY', 'test_admin_key')

from main import app, load_config, save_config

client = TestClient(app, follow_redirects=False)

MOCK_RESULT_DATA = {
    'course_name': ['MTH101', 'CSC101'],
    'course_ccmas': ['MTH101', 'CSC101'],
    'course_title': ['Mathematics', 'Computer Science'],
    'course_units': [3, 2],
    'total_score': [85.0, 75.0],
    'final_grade': ['A', 'B'],
    'out_of_faculty': [False, False],
    'student_details': ['{"student_name": "JOHN DOE"}', '{"student_name": "JOHN DOE"}'],
    'breakdown': ['{"attendance": 10, "assignment": 15, "mid_sem_test": 20, "class_presentation": 5, "senate_recommends": 0, "exam_score": 35}',
                  '{"attendance": 8, "assignment": 12, "mid_sem_test": 15, "class_presentation": 5, "senate_recommends": 0, "exam_score": 35}']
}


@pytest.fixture
def mock_result_df():
    return pd.DataFrame(MOCK_RESULT_DATA)


@pytest.fixture(autouse=True)
def set_active_config(tmp_path, monkeypatch):
    """Set up a temporary config file with active session/semester for tests."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"session": "2023-2024", "semester": "First Semester", "amount_per_course": 5000, "verification_open": true}')
    monkeypatch.setattr('main.CONFIG_PATH', str(config_file))


def test_root_redirects_to_reassessment():
    response = client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == "/reassessment/"


def test_admin_panel_correct_key():
    response = client.get("/admin/?key=test_admin_key")
    assert response.status_code == 200


def test_admin_panel_wrong_key():
    response = client.get("/admin/?key=wrong_key")
    assert response.status_code == 403


def test_set_config():
    response = client.post("/admin/set-config/", json={
        "session": "2023-2024",
        "semester": "First Semester",
        "amount_per_course": 6000,
        "verification_open": False,
        "key": "test_admin_key"
    })
    assert response.status_code == 200
    assert response.json()["status"] is True


def test_set_config_wrong_key():
    response = client.post("/admin/set-config/", json={
        "session": "2023-2024",
        "semester": "First Semester",
        "amount_per_course": 6000,
        "key": "wrong_key"
    })
    assert response.status_code == 403


def test_reassessment_home_active():
    response = client.get("/reassessment/")
    assert response.status_code == 200


@patch('main.dropbox_connect.get_result_url')
@patch('main.dropbox_connect.get_student_result')
def test_get_reassessment_results(mock_get_result, mock_get_url, mock_result_df):
    mock_get_url.return_value = "mock_url"
    mock_get_result.return_value = mock_result_df

    response = client.get("/reassessment/get-results/?student=202100001")
    assert response.status_code == 200
    data = response.json()
    assert data["student_name"] == "JOHN DOE"
    assert len(data["courses"]) == 2


def test_get_reassessment_results_no_config(monkeypatch, tmp_path):
    empty_config = tmp_path / "empty_config.json"
    monkeypatch.setattr('main.CONFIG_PATH', str(empty_config))
    response = client.get("/reassessment/get-results/?student=202100001")
    assert response.status_code == 503


def test_select_courses():
    response = client.post("/reassessment/select/", json={
        "student": "202100001",
        "student_name": "JOHN DOE",
        "verification_courses": [
            {"course_name": "MTH101", "course_title": "Mathematics", "course_units": 3}
        ],
        "reassessment_courses": []
    })
    assert response.status_code == 200
    assert "uuid" in response.json()


def test_select_courses_empty():
    response = client.post("/reassessment/select/", json={
        "student": "202100001",
        "student_name": "JOHN DOE",
        "verification_courses": [],
        "reassessment_courses": []
    })
    assert response.status_code == 400


def test_complaint_form_valid_uuid():
    # First create a cache entry
    sel_response = client.post("/reassessment/select/", json={
        "student": "202100001",
        "student_name": "JOHN DOE",
        "verification_courses": [{"course_name": "MTH101", "course_title": "Mathematics", "course_units": 3}],
        "reassessment_courses": []
    })
    uuid = sel_response.json()["uuid"]
    response = client.get(f"/reassessment/complaint/{uuid}")
    assert response.status_code == 200


def test_complaint_form_invalid_uuid():
    response = client.get("/reassessment/complaint/nonexistent-uuid")
    assert response.status_code == 404


@patch('main.requests.post')
def test_init_payment(mock_post):
    # Set up cache entry first with reassessment courses (requires payment)
    sel_response = client.post("/reassessment/select/", json={
        "student": "202100001",
        "student_name": "JOHN DOE",
        "verification_courses": [],
        "reassessment_courses": [{"course_name": "MTH101", "course_title": "Mathematics", "course_units": 3}]
    })
    uuid = sel_response.json()["uuid"]

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": True,
        "data": {"access_code": "test_access_code"}
    }
    mock_post.return_value = mock_response

    response = client.post("/reassessment/init-payment/", json={
        "uuid": uuid,
        "verification_reasons": {},
        "complaints": {"MTH101": "Score seems incorrect"}
    })
    assert response.status_code == 200
    assert response.json()["access_code"] == "test_access_code"


@patch('main.requests.get')
@patch('main.dropbox_connect.save_reassessment')
def test_reassessment_confirm_success(mock_save, mock_get):
    # Create cache entry with complaints
    sel_response = client.post("/reassessment/select/", json={
        "student": "202100001",
        "student_name": "JOHN DOE",
        "verification_courses": [],
        "reassessment_courses": [{"course_name": "MTH101", "course_title": "Mathematics", "course_units": 3}]
    })
    uuid = sel_response.json()["uuid"]
    # Add complaints to cache via init-payment
    with patch('main.requests.post') as mock_post:
        mock_post.return_value = MagicMock(json=lambda: {"status": True, "data": {"access_code": "ac"}})
        client.post("/reassessment/init-payment/", json={"uuid": uuid, "verification_reasons": {}, "complaints": {"MTH101": "Wrong score"}})

    mock_get.return_value = MagicMock(json=lambda: {
        "status": True,
        "data": {"status": "success", "reference": "test_ref"}
    })
    mock_save.return_value = None

    response = client.get(f"/reassessment/confirm/?uuid={uuid}&reference=test_ref")
    assert response.status_code == 200
    mock_save.assert_called_once()


@patch('main.requests.get')
def test_reassessment_confirm_payment_failed(mock_get):
    # Create a real cache entry so the route reaches verify_transaction
    sel_response = client.post("/reassessment/select/", json={
        "student": "202100001",
        "student_name": "JOHN DOE",
        "verification_courses": [],
        "reassessment_courses": [{"course_name": "MTH101", "course_title": "Mathematics", "course_units": 3}]
    })
    uuid = sel_response.json()["uuid"]

    # Mock the payment API to return a failure
    mock_get.return_value = MagicMock(json=lambda: {
        "status": True,
        "data": {"status": "failed"}
    })

    response = client.get(f"/reassessment/confirm/?uuid={uuid}&reference=bad_ref")
    assert response.status_code == 200  # renders error template, not 4xx


if __name__ == '__main__':
    pytest.main([__file__])
