import re
import unittest
from unittest.mock import MagicMock, patch

import app


class AdminQuizAnalyticsTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='analytics-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role):
        with self.client.session_transaction() as session:
            session['user_id'] = 1 if role == 'admin' else 4
            session['username'] = 'test-user'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_quiz_with_submissions_reports_analytics(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [
            {'count': 5},
            {'count': 2},
            {'count': 10},
        ]
        cursor.fetchall.side_effect = [
            [],
            [{
                'id': 1,
                'title': 'Science',
                'category': 'General',
                'submission_count': 3,
                'average_percentage': 70.0,
                'highest_score': 8,
                'lowest_score': 5,
            }],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Science', body)
        self.assertIn('70.0%', body)
        self.assertIn('Completed', body)

        analytics_query = cursor.execute.call_args_list[-1].args[0]
        self.assertIn('COUNT(r.id) AS submission_count', analytics_query)
        self.assertIn('AVG(', analytics_query)
        self.assertIn('r.score * 100.0 / NULLIF(r.total_questions, 0)', analytics_query)
        self.assertIn('MAX(r.score) AS highest_score', analytics_query)
        self.assertIn('MIN(r.score) AS lowest_score', analytics_query)
        self.assertIn('LEFT JOIN results r ON r.quiz_id = q.id', analytics_query)
        self.assertIn('GROUP BY q.id, q.title, q.category', analytics_query)
        self.assertNotIn('username', analytics_query.lower())
        self.assertNotIn('r.user_id', analytics_query.lower())

    def test_quiz_with_zero_submissions_displays_na(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [
            {'count': 2},
            {'count': 1},
            {'count': 4},
        ]
        cursor.fetchall.side_effect = [
            [],
            [{
                'id': 3,
                'title': 'Empty Quiz',
                'category': 'Practice',
                'submission_count': 0,
                'average_percentage': None,
                'highest_score': None,
                'lowest_score': None,
            }],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Empty Quiz', body)
        self.assertIn('N/A', body)
        self.assertIn('>0<', body)

    def test_correct_average_and_extreme_scores_render(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [
            {'count': 1},
            {'count': 1},
            {'count': 5},
        ]
        cursor.fetchall.side_effect = [
            [],
            [{
                'title': 'Math',
                'category': 'Math',
                'submission_count': 3,
                'average_percentage': 62.333,
                'highest_score': 9,
                'lowest_score': 4,
            }],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/dashboard')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('62.3%', body)
        self.assertRegex(body, r'<td class="text-center">\s*9\s*</td>')
        self.assertRegex(body, r'<td class="text-center">\s*4\s*</td>')

    def test_student_cannot_access_admin_dashboard(self):
        self.set_session('student')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/admin/dashboard')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_empty_quiz_list_has_empty_state(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [
            {'count': 0},
            {'count': 0},
            {'count': 0},
        ]
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('No quizzes found. Quiz performance will appear here', body)

    def test_existing_dashboard_counts_still_render(self):
        self.set_session('admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [
            {'count': 12},
            {'count': 4},
            {'count': 30},
        ]
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/dashboard')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Total Students', body)
        self.assertIn('Total Quizzes', body)
        self.assertIn('Total Questions', body)
        self.assertIn('12', body)
        self.assertIn('4', body)
        self.assertIn('30', body)


if __name__ == '__main__':
    unittest.main()
