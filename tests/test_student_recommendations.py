import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import app


class StudentRecommendationsTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='recommendations-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role='student', user_id=7):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'recommendation-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def quiz(self, quiz_id, title, category, start=None, end=None):
        return {
            'id': quiz_id,
            'title': title,
            'description': 'Practice quiz',
            'category': category,
            'time_limit_minutes': 15,
            'available_from': start,
            'available_until': end,
        }

    def test_recommends_weaker_category_first(self):
        results = [
            {'score': 4, 'total_questions': 10, 'category': 'Math'},
            {'score': 9, 'total_questions': 10, 'category': 'Science'},
        ]
        recommendations = app.calculate_quiz_recommendations(results, [
            self.quiz(2, 'Science Quiz', 'Science'),
            self.quiz(1, 'Math Quiz', 'Math'),
        ])
        self.assertEqual(recommendations[0]['category'], 'Math')
        self.assertIn('40.0%', recommendations[0]['reason'])

    def test_filters_upcoming_closed_and_invalid_scores(self):
        now = datetime.now()
        recommendations = app.calculate_quiz_recommendations(
            [{'score': -1, 'total_questions': 10, 'category': 'Math'}],
            [
                self.quiz(1, 'Upcoming', 'Math', start=now + timedelta(days=1)),
                self.quiz(2, 'Closed', 'Math', end=now - timedelta(days=1)),
                self.quiz(3, 'Active', 'Math'),
            ],
        )
        self.assertEqual([item['id'] for item in recommendations], [3])

    def test_no_history_recommends_available_quiz_with_neutral_reason(self):
        recommendations = app.calculate_quiz_recommendations([], [
            self.quiz(1, 'Starter Quiz', 'Science'),
        ])
        self.assertEqual(len(recommendations), 1)
        self.assertIn('No completed quiz history yet', recommendations[0]['reason'])

    def test_recommendation_route_requires_student(self):
        response = self.client.get('/student/recommendations')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        self.set_session('admin', 1)
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/recommendations')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_route_is_session_scoped_and_excludes_completed_quizzes_in_sql(self):
        self.set_session('student', 42)
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [
            [{'score': 4, 'total_questions': 10, 'category': 'Math'}],
            [self.quiz(5, 'Math Practice', 'Math')],
        ]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/recommendations?user_id=999')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Personalized Quiz Recommendations', body)
        self.assertIn('Math Practice', body)
        self.assertIn('40.0%', body)
        first_query = cursor.execute.call_args_list[0]
        second_query = cursor.execute.call_args_list[1]
        self.assertEqual(first_query.args[1], (42,))
        self.assertEqual(second_query.args[1], (42,))
        self.assertIn('NOT EXISTS', second_query.args[0])
        self.assertIn('EXISTS', second_query.args[0])
        self.assertNotIn('999', str(second_query.args))

    def test_empty_results_display_empty_state(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/recommendations')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('No suitable recommendations right now', body)
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
            response = self.client.get('/student/recommendations')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('The database could not complete your request. Please try again.', body)
        self.assertNotIn('student_quiz_portal.results', body)

    def test_sql_injection_text_is_not_used_as_sql_identifier(self):
        self.set_session('student', 42)
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [
            [{'score': 4, 'total_questions': 10, 'category': 'Science; DROP TABLE results;--'}],
            [],
        ]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get("/student/recommendations?category=Science%27%20OR%201%3D1")
        self.assertEqual(response.status_code, 200)
        for call in cursor.execute.call_args_list:
            self.assertEqual(call.args[1], (42,))


if __name__ == '__main__':
    unittest.main()
