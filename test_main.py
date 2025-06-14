import pytest
from fastapi.testclient import TestClient
from main import app, get_grade_point, calculate_cgpa, ResultBreakdown
import pandas as pd
from unittest.mock import patch, MagicMock
import io

client = TestClient(app)

# Test data
MOCK_RESULT_DATA = {
    'course_name': ['MTH101', 'CSC101'],
    'course_ccmas': ['MTH101', 'CSC101'],
    'course_title': ['Mathematics', 'Computer Science'],
    'course_units': [3, 2],
    'total_score': [85, 75],
    'final_grade': ['A', 'B'],
    'out_of_faculty': [False, False],
    'student_details': ['{"student_name": "John Doe"}', '{"student_name": "John Doe"}'],
    'breakdown': ['{"attendance": 10, "assignment": 15, "mid_sem_test": 20, "class_presentation": 5, "senate_recommends": 0, "exam_score": 35}',
                  '{"attendance": 8, "assignment": 12, "mid_sem_test": 15, "class_presentation": 5, "senate_recommends": 0, "exam_score": 35}']
}


def test_get_grade_point():
    assert get_grade_point('A') == 5
    assert get_grade_point('B') == 4
    assert get_grade_point('C') == 3
    assert get_grade_point('D') == 2
    assert get_grade_point('E') == 1
    assert get_grade_point('F') == 0


def test_result_breakdown():
    breakdown = ResultBreakdown({
        'attendance': 10,
        'assignment': 15,
        'mid_sem_test': 20,
        'class_presentation': 5,
        'senate_recommends': 0,
        'exam_score': 35
    })
    assert breakdown.attendance == 10
    assert breakdown.assignment == 15
    assert breakdown.mid_sem_test == 20
    assert breakdown.class_presentation == 5
    assert breakdown.senate_recommends == 0
    assert breakdown.exam_score == 35


@pytest.fixture
def mock_result_df():
    return pd.DataFrame(MOCK_RESULT_DATA)


@patch('main.dropbox_connect.get_result_url')
@patch('main.dropbox_connect.get_student_result')
def test_get_result_html(mock_get_student_result, mock_get_result_url, mock_result_df):
    mock_get_result_url.return_value = "mock_url"
    mock_get_student_result.return_value = mock_result_df

    response = client.get(
        "/results/?session=2021-2022&semester=First%20Semester&student=12345")
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'success'
    assert len(data['results']) == 2


@patch('main.requests.get')
def test_verify_transaction(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': True, 'data': {'reference': 'test_ref'}}
    mock_get.return_value = mock_response

    # Test the function directly since it's not exposed as an endpoint
    from main import verify_transaction
    result = verify_transaction('test_ref')
    assert result['status'] == True
    assert result['data']['reference'] == 'test_ref'


@patch('main.dropbox_connect.get_code_url')
@patch('main.urllib.request.urlopen')
def test_confirm_discount_code(mock_urlopen, mock_get_code_url):
    mock_get_code_url.return_value = "mock_url"

    # Create a proper CSV string and mock a file-like object
    class MockFile(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.close()
    csv_data = b"discountCode\nTEST123"
    mock_file = MockFile(csv_data)
    mock_urlopen.return_value = mock_file

    response = client.get("/confirm-discount-code/?discount_code=TEST123")
    assert response.status_code == 200
    assert response.json()['status'] == True


@patch('main.requests.post')
def test_get_access_code(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': True, 'data': {'authorization_url': 'test_url'}}
    mock_post.return_value = mock_response

    response = client.get(
        "/get-access-code/?email=test@example.com&callbackUrl=http://test.com")
    assert response.status_code == 200
    assert response.json()['status'] == True


def test_calculate_cgpa():
    # Mock the necessary functions and data
    with patch('main.dropbox_connect.get_result_url') as mock_get_url, \
            patch('main.dropbox_connect.get_student_result') as mock_get_result:

        mock_get_url.return_value = "mock_url"
        mock_get_result.return_value = pd.DataFrame(MOCK_RESULT_DATA)

        cgpa = calculate_cgpa("2021-2022", "First Semester", "12345")
        assert isinstance(cgpa, float)
        assert 0 <= cgpa <= 5.0


if __name__ == '__main__':
    pytest.main([__file__])
