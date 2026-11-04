import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import app


class StudentLearningProgressTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='progress-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role='student', user_id=7):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'progress-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def result(self, quiz_id, title, score, total, day):
        return {
            'quiz_id': quiz_id,
            'title': title,
            'category': 'Science',
            'score': score,
            'total_questions': total,
            'date_taken': datetime(2026, 1, day),
        }

    def test_normal_performance_summary_and_history(self):
        data = [
            self.result(1, 'Science', 5, 10, 1),
            self.result(2, 'Math', 9, 10, 2),
        ]
        progress = app.calculate_learning_progress(data)
        self.assertEqual(progress['summary']['completed_quizzes'], 2)
        self.assertEqual(progress['summary']['average_score'], 70.0)
        self.assertEqual(progress['summary']['highest_score'], 90.0)
        self.assertEqual(progress['history'][0]['title'], 'Math')
        self.assertEqual(len(progress['chart_points']), 2)

    def test_improving_trend_is_detected(self):
        progress = app.calculate_learning_progress([
            self.result(1, 'One', 40, 100, 1),
            self.result(2, 'Two', 50, 100, 2),
            self.result(3, 'Three', 80, 100, 3),
            self.result(4, 'Four', 90, 100, 4),
        ])
        self.assertEqual(progress['trend_status'], 'Improving')

    def test_declining_trend_is_detected(self):
        progress = app.calculate_learning_progress([
            self.result(1, 'One', 90, 100, 1),
            self.result(2, 'Two', 80, 100, 2),
            self.result(3, 'Three', 40, 100, 3),
            self.result(4, 'Four', 30, 100, 4),
        ])
        self.assertEqual(progress['trend_status'], 'Declining')

    def test_missing_and_zero_question_scores_are_ignored(self):
        progress = app.calculate_learning_progress([
            {'quiz_id': 1, 'title': 'Zero', 'score': 0, 'total_questions': 0, 'date_taken': None},
            {'quiz_id': 2, 'title': 'Negative', 'score': -1, 'total_questions': 10, 'date_taken': None},
            {'quiz_id': 3, 'title': 'Missing', 'score': None, 'total_questions': 10, 'date_taken': None},
        ])
        self.assertEqual(progress['summary']['completed_quizzes'], 0)
        self.assertIsNone(progress['summary']['average_score'])

    def test_progress_route_is_session_scoped_and_authenticated(self):
        response = self.client.get('/student/progress')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        self.set_session('student', 42)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [
            self.result(1, 'Science', 8, 10, 1),
        ]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/progress?user_id=999')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Learning Progress', body)
        self.assertIn('Science', body)
        query = cursor.execute.call_args_list[0]
        self.assertIn('WHERE r.user_id = %s', query.args[0])
        self.assertEqual(query.args[1], (42,))
        self.assertNotIn('999', str(query.args))

    def test_admin_cannot_access_student_progress(self):
        self.set_session('admin', 1)
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/progress')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_empty_progress_displays_empty_state(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/progress')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('No performance data yet', body)
        self.assertIn('Browse Quizzes', body)

    def test_database_error_is_generic(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.execute.side_effect = app.pymysql.err.OperationalError(
            1146,
            "Table 'student_quiz_portal.results' doesn't exist",
        )
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app.app.logger, 'exception'):
            response = self.client.get('/student/progress')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('The database could not complete your request. Please try again.', body)
        self.assertNotIn('student_quiz_portal.results', body)


if __name__ == '__main__':
    unittest.main()
