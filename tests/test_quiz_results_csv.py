import csv
import io
import unittest
from unittest.mock import MagicMock, patch

import app


class QuizResultsCsvTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='csv-export-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role):
        with self.client.session_transaction() as session:
            session['user_id'] = 1 if role == 'admin' else 4
            session['username'] = 'csv-user'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def read_csv(self, response):
        return list(csv.reader(io.StringIO(response.get_data(as_text=True))))

    def test_admin_can_export_all_results_with_headers_and_rows(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [{
            'quiz_name': 'Science',
            'student_name': 'alice',
            'score': 8,
            'total_questions': 10,
            'percentage': 80.0,
            'submission_date': None,
        }]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/results/export')

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response.headers['Content-Type'])
        self.assertIn('all_quiz_results.csv', response.headers['Content-Disposition'])
        rows = self.read_csv(response)
        self.assertEqual(rows[0], [
            'Quiz Name', 'Student Name', 'Score',
            'Total Marks', 'Percentage', 'Submission Date',
        ])
        self.assertEqual(rows[1][0:5], ['Science', 'alice', '8', '10', '80.0%'])
        query = cursor.execute.call_args_list[0].args[0]
        self.assertIn('FROM results r', query)
        self.assertIn('JOIN quizzes q', query)
        self.assertIn('JOIN users u', query)
        self.assertIn('WHERE u.role = \'student\'', query)
        self.assertNotIn('AND r.quiz_id = %s', query)

    def test_admin_can_export_selected_quiz_using_parameterized_query(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'title': 'Math Basics'}
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/quizzes/12/export')

        self.assertEqual(response.status_code, 200)
        self.assertIn('math_basics_results.csv', response.headers['Content-Disposition'])
        calls = cursor.execute.call_args_list
        title_query = calls[0]
        self.assertEqual(title_query.args[1], (12,))
        result_query = calls[1]
        self.assertIn('AND r.quiz_id = %s', result_query.args[0])
        self.assertEqual(result_query.args[1], (12,))
        self.assertEqual(len(self.read_csv(response)), 1)

    def test_empty_results_still_have_csv_headers(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/results/export')

        rows = self.read_csv(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'Quiz Name')

    def test_csv_injection_characters_are_sanitized(self):
        for dangerous in ('=SUM(A1:A2)', '+cmd', '-10', '@name', '\ttext', '=HYPERLINK("x")'):
            with self.subTest(dangerous=dangerous):
                self.assertTrue(app.sanitize_csv_cell(dangerous).startswith("'"))

        self.assertEqual(app.sanitize_csv_cell('Normal Quiz'), 'Normal Quiz')
        self.assertEqual(app.sanitize_csv_cell(None), '')
        self.assertEqual(app.sanitize_csv_cell(42), 42)

    def test_csv_output_sanitizes_formula_like_text_values(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [{
            'quiz_name': '=SUM(A1:A2)',
            'student_name': '+student',
            'score': 3,
            'total_questions': 5,
            'percentage': 60.0,
            'submission_date': None,
        }]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/results/export')

        rows = self.read_csv(response)
        self.assertEqual(rows[1][0], "'=SUM(A1:A2)")
        self.assertEqual(rows[1][1], "'+student")

    def test_student_and_unauthenticated_users_cannot_export(self):
        unauthenticated = self.client.get('/admin/results/export')
        self.assertEqual(unauthenticated.status_code, 302)
        self.assertIn('/login', unauthenticated.headers['Location'])

        self.set_session('student')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            student_response = self.client.get('/admin/results/export')
        self.assertEqual(student_response.status_code, 302)
        self.assertIn('/login', student_response.headers['Location'])

    def test_selected_quiz_not_found_redirects(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/quizzes/999/export')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/quizzes', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
