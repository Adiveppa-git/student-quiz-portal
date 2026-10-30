import unittest
from unittest.mock import MagicMock, patch

import app


class QuizDeadlineTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='deadline-test-secret')
        self.client = app.app.test_client()

    def student_session(self):
        with self.client.session_transaction() as session:
            session['user_id'] = 4
            session['username'] = 'student'
            session['role'] = 'student'

    def mock_connection(self, cursor):
        connection = MagicMock()
        connection.open = True
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection

    def test_new_attempt_records_start_time(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {'started_at': '2026-10-30 12:00:00'}

        attempt = app.get_or_create_quiz_attempt(cursor, 4, 7)

        self.assertEqual(attempt['started_at'], '2026-10-30 12:00:00')
        insert_call = cursor.execute.call_args_list[0]
        self.assertIn('INSERT IGNORE INTO quiz_attempts', insert_call.args[0])
        self.assertIn('(user_id, quiz_id)', insert_call.args[0])

    def test_refresh_does_not_reset_attempt_start_time(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {'started_at': '2026-10-30 12:00:00'}

        first = app.get_or_create_quiz_attempt(cursor, 4, 7)
        cursor.fetchone.return_value = {'started_at': '2026-10-30 12:00:00'}
        second = app.get_or_create_quiz_attempt(cursor, 4, 7)

        self.assertEqual(first, second)
        insert_calls = [call for call in cursor.execute.call_args_list if 'INSERT IGNORE' in call.args[0]]
        self.assertEqual(len(insert_calls), 2)

    def test_missing_attempt_cannot_be_submitted(self):
        self.student_session()
        cursor = MagicMock()
        connection = self.mock_connection(cursor)

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=None):
            response = self.client.post('/student/quiz/7/submit')

        self.assertEqual(response.status_code, 302)
        inserted_result = any(
            'INSERT INTO results' in call.args[0]
            for call in cursor.execute.call_args_list
            if call.args
        )
        self.assertFalse(inserted_result)

    def test_expired_attempt_is_rejected(self):
        self.student_session()
        cursor = MagicMock()
        connection = self.mock_connection(cursor)
        status = {'started_at': '2026-10-30 12:00:00', 'time_limit_minutes': 15, 'expired': 1}

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.client.post('/student/quiz/7/submit')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(cursor.fetchall.call_count, 0)

    def test_valid_attempt_can_be_submitted_before_deadline(self):
        self.student_session()
        cursor = MagicMock()
        cursor.rowcount = 1
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = [
            {'id': 1, 'correct_option': 'A'},
            {'id': 2, 'correct_option': 'B'},
        ]
        connection = self.mock_connection(cursor)
        status = {'started_at': '2026-10-30 12:00:00', 'time_limit_minutes': 15, 'expired': 0}

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.client.post('/student/quiz/7/submit', data={'question_1': 'A', 'question_2': 'C'})

        self.assertEqual(response.status_code, 302)
        connection.commit.assert_called_once()
        inserted_result = any(
            'INSERT INTO results' in call.args[0]
            for call in cursor.execute.call_args_list
            if call.args
        )
        self.assertTrue(inserted_result)

    def test_expired_attempt_does_not_create_result(self):
        self.student_session()
        cursor = MagicMock()
        connection = self.mock_connection(cursor)
        status = {'started_at': '2026-10-30 12:00:00', 'time_limit_minutes': 15, 'expired': 1}

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.client.post('/student/quiz/7/submit', data={'question_1': 'A'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(connection.commit.call_count, 0)
        self.assertFalse(any('INSERT INTO results' in call.args[0] for call in cursor.execute.call_args_list if call.args))

    def test_atomic_insert_select_enforces_database_deadline(self):
        cursor = MagicMock()
        cursor.rowcount = 1

        inserted = app.insert_quiz_result_if_unexpired(cursor, 4, 7, 2, 3)

        self.assertEqual(inserted, 1)
        sql = cursor.execute.call_args.args[0]
        self.assertIn('INSERT INTO results', sql)
        self.assertIn('SELECT %s, %s, %s, %s', sql)
        self.assertIn('FROM quiz_attempts a', sql)
        self.assertIn('JOIN quizzes q ON q.id = a.quiz_id', sql)
        self.assertIn('a.user_id = %s', sql)
        self.assertIn('a.quiz_id = %s', sql)
        self.assertIn('CURRENT_TIMESTAMP()', sql)
        self.assertIn('DATE_ADD', sql)
        self.assertEqual(cursor.execute.call_args.args[1], (4, 7, 2, 3, 4, 7, 7))

    def test_atomic_zero_row_result_is_not_committed(self):
        self.student_session()
        cursor = MagicMock()
        cursor.rowcount = 0
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = [{'id': 1, 'correct_option': 'A'}]
        connection = self.mock_connection(cursor)
        status = {'started_at': '2026-10-30 12:00:00', 'time_limit_minutes': 15, 'expired': 0}

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.client.post('/student/quiz/7/submit', data={'question_1': 'A'})

        self.assertEqual(response.status_code, 302)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_attempt_status_query_uses_database_clock_and_date_add(self):
        cursor = MagicMock()

        app.get_quiz_attempt_status(cursor, 4, 7)

        sql = cursor.execute.call_args.args[0]
        self.assertIn('CURRENT_TIMESTAMP()', sql)
        self.assertIn('DATE_ADD', sql)
        self.assertEqual(cursor.execute.call_args.args[1], (4, 7))

    def test_duplicate_submissions_remain_prevented(self):
        self.student_session()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = [{'id': 1, 'correct_option': 'A'}]

        def execute(sql, params=None):
            if 'INSERT INTO results' in sql:
                raise app.pymysql.err.IntegrityError()
        cursor.execute.side_effect = execute
        connection = self.mock_connection(cursor)
        status = {'started_at': '2026-10-30 12:00:00', 'time_limit_minutes': 15, 'expired': 0}

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.client.post('/student/quiz/7/submit', data={'question_1': 'A'})

        self.assertEqual(response.status_code, 302)
        connection.rollback.assert_called_once()

    def test_invalid_quiz_time_limits_are_rejected(self):
        self.assertIsNone(app.validate_quiz_time_limit(None))
        self.assertIsNone(app.validate_quiz_time_limit(''))
        self.assertIsNone(app.validate_quiz_time_limit('0'))
        self.assertIsNone(app.validate_quiz_time_limit('-5'))
        self.assertIsNone(app.validate_quiz_time_limit('1.5'))
        self.assertEqual(app.validate_quiz_time_limit('15'), 15)


if __name__ == '__main__':
    unittest.main()
