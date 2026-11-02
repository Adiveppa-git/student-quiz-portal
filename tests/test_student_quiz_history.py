import unittest
from unittest.mock import MagicMock, patch

import app


class StudentQuizHistoryTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='student-history-test-secret')
        self.client = app.app.test_client()

    def set_session(self, user_id, role='student'):
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

    def test_history_displays_completed_quizzes_and_calculates_percentage(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [{
            'id': 1,
            'user_id': 7,
            'quiz_id': 2,
            'title': 'Science',
            'category': 'General',
            'score': 8,
            'total_questions': 10,
            'percentage': 80.0,
            'date_taken': None,
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
        body = response.get_data(as_text=True)
        self.assertIn('Science', body)
        self.assertIn('80.0%', body)
        self.assertIn('Overall Average', body)
        self.assertIn('Highest Score', body)
        self.assertIn('Lowest Score', body)

        history_query = cursor.execute.call_args_list[0]
        self.assertIn('WHERE r.user_id = %s', history_query.args[0])
        self.assertEqual(history_query.args[1], (7,))

        aggregate_query = cursor.execute.call_args_list[1]
        self.assertIn('AVG(r.score * 100.0 / NULLIF(r.total_questions, 0))', aggregate_query.args[0])
        self.assertIn('MAX(r.score * 100.0 / NULLIF(r.total_questions, 0))', aggregate_query.args[0])
        self.assertIn('MIN(r.score * 100.0 / NULLIF(r.total_questions, 0))', aggregate_query.args[0])
        self.assertIn('WHERE r.user_id = %s', aggregate_query.args[0])
        self.assertEqual(aggregate_query.args[1], (7,))

    def test_empty_history_displays_graceful_empty_state(self):
        self.set_session(8)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = {
            'average_percentage': None,
            'highest_percentage': None,
            'lowest_percentage': None,
        }

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/history')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('No history yet', body)
        self.assertNotIn('Overall Average', body)

    def test_calculations_use_percentage_across_different_totals(self):
        self.set_session(9)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [
            {'title': 'Quiz A', 'score': 9, 'total_questions': 10, 'percentage': 90.0,
             'category': 'A', 'date_taken': None},
            {'title': 'Quiz B', 'score': 1, 'total_questions': 4, 'percentage': 25.0,
             'category': 'B', 'date_taken': None},
        ]
        cursor.fetchone.return_value = {
            'average_percentage': 57.5,
            'highest_percentage': 90.0,
            'lowest_percentage': 25.0,
        }

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/history')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('57.5%', body)
        self.assertIn('90.0%', body)
        self.assertIn('25.0%', body)

    def test_unauthenticated_student_is_denied(self):
        response = self.client.get('/student/history')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_admin_cannot_access_student_history(self):
        self.set_session(1, role='admin')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/history')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_user_id_query_parameter_is_not_used(self):
        self.set_session(42)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/history?user_id=999')

        self.assertEqual(response.status_code, 200)
        for call in cursor.execute.call_args_list:
            self.assertNotIn('999', call.args[0])
            self.assertEqual(call.args[1], (42,))

    def test_result_route_remains_scoped_to_session_user(self):
        self.set_session(42)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/result/3?user_id=999')

        self.assertEqual(response.status_code, 302)
        result_query = cursor.execute.call_args_list[0]
        self.assertIn('WHERE r.user_id = %s AND r.quiz_id = %s', result_query.args[0])
        self.assertEqual(result_query.args[1], (42, 3))


if __name__ == '__main__':
    unittest.main()
