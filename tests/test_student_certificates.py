import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import app


class StudentCertificateTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='student-certificate-test-secret')
        self.client = app.app.test_client()

    def set_session(self, user_id=7, role='student'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'student-one'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def certificate(self):
        return {
            'id': 42,
            'score': 8,
            'total_questions': 10,
            'date_taken': datetime(2026, 11, 10, 9, 30),
            'quiz_title': 'Science',
            'student_name': 'student-one',
        }

    def test_owner_can_view_and_print_completed_quiz_certificate(self):
        self.set_session(user_id=7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = self.certificate()

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/certificate/42')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Certificate of Completion', body)
        self.assertIn('student-one', body)
        self.assertIn('Science', body)
        self.assertIn('8 out of', body)
        self.assertIn('80.0%', body)
        self.assertIn('Print / Save as PDF', body)
        self.assertIn('window.print()', body)

        query, parameters = cursor.execute.call_args.args
        self.assertIn('WHERE r.id = %s', query)
        self.assertIn('AND r.user_id = %s', query)
        self.assertIn('r.total_questions > 0', query)
        self.assertIn('r.score BETWEEN 0 AND r.total_questions', query)
        self.assertEqual(parameters, (42, 7))
        connection.close.assert_called_once()

    def test_certificate_lookup_uses_session_owner_and_hides_other_results(self):
        self.set_session(user_id=7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/certificate/91')

        self.assertEqual(response.status_code, 404)
        self.assertIn('Certificate not found.', response.get_data(as_text=True))
        self.assertEqual(cursor.execute.call_args.args[1], (91, 7))

    def test_unauthenticated_student_cannot_access_certificate(self):
        response = self.client.get('/student/certificate/42')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_admin_cannot_access_student_certificate(self):
        self.set_session(user_id=1, role='admin')

        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/certificate/42')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_result_page_links_to_its_certificate(self):
        self.set_session(user_id=7)
        connection, cursor = self.mock_connection()
        result = self.certificate()
        result.update({
            'user_id': 7,
            'quiz_id': 9,
            'category': 'Science',
        })
        cursor.fetchone.return_value = result

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/result/9')

        self.assertEqual(response.status_code, 200)
        self.assertIn('/student/certificate/42', response.get_data(as_text=True))

    def test_history_row_links_to_its_certificate(self):
        self.set_session(user_id=7)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [{
            'id': 42,
            'quiz_id': 9,
            'title': 'Science',
            'category': 'Science',
            'score': 8,
            'total_questions': 10,
            'percentage': 80.0,
            'date_taken': datetime(2026, 11, 10, 9, 30),
        }]
        cursor.fetchone.return_value = {
            'average_percentage': 80.0,
            'highest_percentage': 80.0,
            'lowest_percentage': 80.0,
        }

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/history')

        self.assertEqual(response.status_code, 200)
        self.assertIn('/student/certificate/42', response.get_data(as_text=True))

    def test_database_errors_are_reported_without_exposing_details(self):
        self.set_session(user_id=7)
        connection, cursor = self.mock_connection()
        cursor.execute.side_effect = app.pymysql.err.OperationalError(
            1146,
            "Table 'student_quiz_portal.results' doesn't exist",
        )

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app.app.logger, 'exception'):
            response = self.client.get('/student/certificate/42')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/student/history', response.headers['Location'])
        self.assertNotIn('student_quiz_portal.results', response.get_data(as_text=True))
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
