import unittest
from unittest.mock import MagicMock, patch

import app


class StudentLeaderboardTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='leaderboard-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role='student', user_id=4):
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

    def test_unauthenticated_user_cannot_access_leaderboard(self):
        response = self.client.get('/student/leaderboard')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_admin_cannot_access_student_leaderboard(self):
        self.set_session('admin', 1)
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/leaderboard')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_overall_leaderboard_ranking_and_top_ten_query(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [
            [{'id': 1, 'title': 'Science'}],
            [{
                'student_id': 4,
                'username': 'student-one',
                'completed_quizzes': 3,
                'average_score': 82.5,
                'total_score': 24,
                'total_possible': 30,
            }],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/leaderboard')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('student-one', body)
        self.assertIn('82.5%', body)
        self.assertIn('Your Rank', body)
        query = cursor.execute.call_args_list[1].args[0]
        self.assertIn('AVG(r.score * 100.0 / NULLIF(r.total_questions, 0))', query)
        self.assertIn('ORDER BY average_score DESC', query)
        self.assertIn('LIMIT 10', query)

    def test_quiz_specific_leaderboard_uses_parameterized_quiz_id(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Science'}
        cursor.fetchall.side_effect = [
            [{'id': 7, 'title': 'Science'}, {'id': 2, 'title': 'Math'}],
            [{
                'student_id': 4,
                'username': 'student-one',
                'completed_quizzes': 1,
                'average_score': 90.0,
                'total_score': 9,
                'total_possible': 10,
                'date_taken': None,
            }],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/leaderboard?quiz_id=7')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Science', body)
        self.assertIn('one completed result per student', body)
        leaderboard_query = cursor.execute.call_args_list[2].args[0]
        self.assertIn('r.quiz_id = %s', leaderboard_query)
        self.assertEqual(cursor.execute.call_args_list[2].args[1], (7,))

    def test_empty_leaderboard_is_handled(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/leaderboard')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('No completed quiz attempts yet', body)

    def test_student_outside_top_ten_gets_separate_rank(self):
        self.set_session('student', 99)
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [
            [],
            [{
                'student_id': 4,
                'username': 'student-one',
                'completed_quizzes': 1,
                'average_score': 95.0,
                'total_score': 19,
                'total_possible': 20,
            }],
            [{
                'student_id': 4,
                'username': 'student-one',
                'completed_quizzes': 1,
                'average_score': 95.0,
                'total_score': 19,
                'total_possible': 20,
            }, {
                'student_id': 99,
                'username': 'student-one',
                'completed_quizzes': 1,
                'average_score': 50.0,
                'total_score': 5,
                'total_possible': 10,
            }],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/leaderboard')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Your Rank', body)
        self.assertIn('#2', body)
        self.assertIn('50.0%', body)

    def test_invalid_quiz_selection_falls_back_to_overall(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None
        cursor.fetchall.side_effect = [[], []]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/leaderboard?quiz_id=999')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Invalid quiz selection.', body)
        self.assertIn('Overall student rankings', body)

    def test_sql_injection_quiz_selection_is_rejected_before_query(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/leaderboard?quiz_id=1%20OR%201=1')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Invalid quiz selection.', response.get_data(as_text=True))
        for call in cursor.execute.call_args_list:
            self.assertNotIn('1 OR 1=1', str(call.args))

    def test_database_error_is_generic_and_does_not_expose_details(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.execute.side_effect = app.pymysql.err.OperationalError(
            1146,
            "Table 'student_quiz_portal.results' doesn't exist",
        )

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app.app.logger, 'exception'):
            response = self.client.get('/student/leaderboard')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('The database could not complete your request. Please try again.', body)
        self.assertNotIn('student_quiz_portal.results', body)

    def test_database_connection_failure_is_handled(self):
        self.set_session()
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=None):
            response = self.client.get('/student/leaderboard')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/student/dashboard', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
