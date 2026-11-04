import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import app


class StudentAchievementTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='achievement-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role='student', user_id=7):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'student-achiever'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def achievement_map(self, results):
        return {
            item['key']: item
            for item in app.calculate_student_achievements(results)
        }

    def result(self, quiz_id, score, total, day):
        return {
            'quiz_id': quiz_id,
            'score': score,
            'total_questions': total,
            'date_taken': day,
        }

    def test_first_quiz_achievement(self):
        achievements = self.achievement_map([
            self.result(1, 6, 10, datetime(2026, 1, 1))
        ])
        self.assertTrue(achievements['first_steps']['earned'])
        self.assertFalse(achievements['quiz_explorer']['earned'])

    def test_five_and_ten_quiz_milestones(self):
        base = datetime(2026, 1, 1)
        results = [
            self.result(i, 8, 10, base + timedelta(days=i))
            for i in range(1, 11)
        ]
        achievements = self.achievement_map(results)
        self.assertTrue(achievements['quiz_explorer']['earned'])
        self.assertTrue(achievements['quiz_champion']['earned'])
        self.assertEqual(achievements['quiz_explorer']['progress'], 5)

    def test_perfect_and_high_achiever_badges(self):
        achievements = self.achievement_map([
            self.result(1, 10, 10, datetime(2026, 2, 1)),
            self.result(2, 9, 10, datetime(2026, 2, 2)),
        ])
        self.assertTrue(achievements['perfect_score']['earned'])
        self.assertTrue(achievements['high_achiever']['earned'])

    def test_consistent_learner_uses_distinct_calendar_days(self):
        days = [datetime(2026, 3, 1), datetime(2026, 3, 1),
                datetime(2026, 3, 2), datetime(2026, 3, 3),
                datetime(2026, 3, 4), datetime(2026, 3, 5)]
        achievements = self.achievement_map([
            self.result(i + 1, 5, 10, days[i])
            for i in range(len(days))
        ])
        self.assertTrue(achievements['consistent_learner']['earned'])
        self.assertEqual(achievements['consistent_learner']['progress'], 5)

    def test_zero_question_and_invalid_results_are_ignored(self):
        achievements = self.achievement_map([
            self.result(1, 0, 0, datetime(2026, 1, 1)),
            self.result(2, 11, 10, datetime(2026, 1, 2)),
            self.result(3, -1, 10, datetime(2026, 1, 3)),
        ])
        self.assertFalse(achievements['first_steps']['earned'])
        self.assertFalse(achievements['perfect_score']['earned'])

    def test_each_calculated_badge_is_returned_once(self):
        achievements = app.calculate_student_achievements([
            self.result(1, 10, 10, datetime(2026, 1, 1))
        ])
        keys = [item['key'] for item in achievements]
        self.assertEqual(len(keys), len(set(keys)))

    def test_unauthenticated_and_admin_access_are_denied(self):
        unauthenticated = self.client.get('/student/achievements')
        self.assertEqual(unauthenticated.status_code, 302)

        self.set_session('admin', 1)
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/achievements')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_page_shows_empty_state_and_session_parameter_is_used(self):
        self.set_session('student', 42)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/achievements')

        self.assertEqual(response.status_code, 200)
        self.assertIn('My Achievements', response.get_data(as_text=True))
        query_call = cursor.execute.call_args_list[0]
        self.assertIn('WHERE user_id = %s', query_call.args[0])
        self.assertEqual(query_call.args[1], (42,))

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
            response = self.client.get('/student/achievements')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('The database could not complete your request. Please try again.', body)
        self.assertNotIn('student_quiz_portal.results', body)


if __name__ == '__main__':
    unittest.main()
