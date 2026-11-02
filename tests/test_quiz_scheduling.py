import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import app


class QuizSchedulingTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='scheduling-test-secret')
        self.client = app.app.test_client()
        self.role_validator = patch.object(app, 'is_session_role_valid', return_value=True)
        self.role_validator.start()

    def tearDown(self):
        self.role_validator.stop()

    def set_session(self, user_id, role):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'schedule-user'
            session['role'] = role

    def mock_connection(self, cursor=None):
        connection = MagicMock()
        connection.open = True
        if cursor is None:
            cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_upcoming_active_closed_statuses(self):
        now = datetime(2026, 11, 2, 12, 0, 0)
        self.assertEqual(app.classify_quiz_availability({
            'available_from': datetime(2026, 11, 2, 13, 0, 0),
            'available_until': datetime(2026, 11, 2, 14, 0, 0),
        }, now), 'upcoming')
        self.assertEqual(app.classify_quiz_availability({
            'available_from': datetime(2026, 11, 2, 11, 0, 0),
            'available_until': datetime(2026, 11, 2, 14, 0, 0),
        }, now), 'active')
        self.assertEqual(app.classify_quiz_availability({
            'available_from': datetime(2026, 11, 2, 10, 0, 0),
            'available_until': datetime(2026, 11, 2, 11, 0, 0),
        }, now), 'closed')
        self.assertEqual(app.classify_quiz_availability({}, now), 'active')

    def test_start_and_end_boundary_timestamps(self):
        now = datetime(2026, 11, 2, 12, 0, 0)
        self.assertEqual(app.classify_quiz_availability({
            'available_from': now,
            'available_until': datetime(2026, 11, 2, 13, 0, 0),
        }, now), 'active')
        self.assertEqual(app.classify_quiz_availability({
            'available_from': datetime(2026, 11, 2, 11, 0, 0),
            'available_until': now,
        }, now), 'active')

    def test_invalid_and_reversed_schedules_are_rejected(self):
        valid, start, end, error = app.validate_quiz_schedule('', '')
        self.assertTrue(valid)
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertIsNone(error)

        valid, start, end, error = app.validate_quiz_schedule(
            '2026-11-02T14:00',
            '2026-11-02T13:00',
        )
        self.assertFalse(valid)
        self.assertIn('end time must be after', error)

        valid, start, end, error = app.validate_quiz_schedule(
            'not-a-date',
            '',
        )
        self.assertFalse(valid)
        self.assertIn('valid start and end', error)

    def test_atomic_submission_query_checks_scheduling_and_attempt_limit(self):
        cursor = MagicMock()
        cursor.rowcount = 1
        app.insert_quiz_result_if_unexpired(cursor, 4, 7, 3, 5)
        sql = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]

        self.assertIn('UTC_TIMESTAMP() >= q.available_from', sql)
        self.assertIn('UTC_TIMESTAMP() <= q.available_until', sql)
        self.assertIn('DATE_ADD(', sql)
        self.assertEqual(params[-3:], (4, 7, 7))

    def test_submission_status_includes_schedule_check(self):
        cursor = MagicMock()
        app.get_quiz_attempt_status(cursor, 4, 7)
        sql = cursor.execute.call_args.args[0]
        self.assertIn('q.available_from', sql)
        self.assertIn('q.available_until', sql)
        self.assertIn('UTC_TIMESTAMP()', sql)
        self.assertIn('schedule_closed', sql)

    def test_student_dashboard_displays_upcoming_active_closed(self):
        self.set_session(4, 'student')
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [
            {
                'id': 1, 'title': 'Upcoming', 'category': 'Test',
                'description': '', 'time_limit_minutes': 15,
                'available_from': datetime(2099, 1, 1, 0, 0),
                'available_until': datetime(2099, 1, 2, 0, 0),
            },
            {
                'id': 2, 'title': 'Closed', 'category': 'Test',
                'description': '', 'time_limit_minutes': 15,
                'available_from': datetime(2000, 1, 1, 0, 0),
                'available_until': datetime(2000, 1, 2, 0, 0),
            },
            {
                'id': 3, 'title': 'Active', 'category': 'Test',
                'description': '', 'time_limit_minutes': 15,
                'available_from': None, 'available_until': None,
            },
        ]

        with patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Upcoming', body)
        self.assertIn('Closed', body)
        self.assertIn('Active', body)
        self.assertIn('Not Started Yet', body)
        self.assertIn('Quiz Closed', body)
        self.assertIn('Start Quiz', body)

    def test_upcoming_and_closed_quiz_cannot_be_started(self):
        for quiz, expected_message in (
            ({
                'id': 7, 'title': 'Later',
                'available_from': datetime(2099, 1, 1, 0, 0),
                'available_until': datetime(2099, 1, 2, 0, 0),
                'time_limit_minutes': 15,
            }, 'has not started yet'),
            ({
                'id': 8, 'title': 'Past',
                'available_from': datetime(2000, 1, 1, 0, 0),
                'available_until': datetime(2000, 1, 2, 0, 0),
                'time_limit_minutes': 15,
            }, 'has closed'),
        ):
            with self.subTest(expected_message=expected_message):
                self.set_session(4, 'student')
                connection, cursor = self.mock_connection()
                cursor.fetchone.side_effect = [None, quiz]

                with patch.object(app, 'get_db_connection', return_value=connection):
                    response = self.client.get(f"/student/quiz/{quiz['id']}")

                self.assertEqual(response.status_code, 302)
                inserted_attempt = any(
                    'INSERT IGNORE INTO quiz_attempts' in call.args[0]
                    for call in cursor.execute.call_args_list
                    if call.args
                )
                self.assertFalse(inserted_attempt)

    def test_schema_and_migration_have_schedule_columns(self):
        from pathlib import Path
        root = Path(app.app.root_path)
        schema = root.joinpath('database.sql').read_text(encoding='utf-8')
        migration = root.joinpath('migrations', '004_add_quiz_scheduling.sql').read_text(encoding='utf-8')
        readme = root.joinpath('README.md').read_text(encoding='utf-8')

        self.assertIn('available_from DATETIME NULL', schema)
        self.assertIn('available_until DATETIME NULL', schema)
        self.assertIn('ADD COLUMN available_from DATETIME NULL', migration)
        self.assertIn('ADD COLUMN available_until DATETIME NULL', migration)
        self.assertIn('UTC', readme)

if __name__ == '__main__':
    unittest.main()
