import re
import unittest
from unittest.mock import MagicMock, patch

from werkzeug.datastructures import MultiDict

import app


class QuizSubmissionValidationTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='submission-validation-test-secret')
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 4
            session['username'] = 'student'
            session['role'] = 'student'

        self.role_validator = patch.object(app, 'is_session_role_valid', return_value=True)
        self.role_validator.start()

    def tearDown(self):
        self.role_validator.stop()

    def mock_connection(self, cursor):
        connection = MagicMock()
        connection.open = True
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection

    def post_with_csrf(self, path, data):
        response = self.client.get('/login')
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)

        form_data = MultiDict()
        for key, values in data.items():
            if isinstance(values, list):
                for value in values:
                    form_data.add(key, value)
            else:
                form_data.add(key, values)
        form_data.add('csrf_token', match.group(1))
        return self.client.post(path, data=form_data)

    def prepare_valid_submission(self):
        cursor = MagicMock()
        cursor.rowcount = 1
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = [
            {'id': 1, 'correct_option': 'A'},
            {'id': 2, 'correct_option': 'B'},
        ]
        connection = self.mock_connection(cursor)
        status = {
            'started_at': '2026-10-30 12:00:00',
            'time_limit_minutes': 15,
            'expired': 0,
        }
        return cursor, connection, status

    def test_valid_quiz_submission(self):
        cursor, connection, status = self.prepare_valid_submission()

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/7/submit',
                {'question_1': 'A', 'question_2': 'B'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(connection.commit.called)
        self.assertTrue(any(
            'INSERT INTO results' in call.args[0]
            for call in cursor.execute.call_args_list
            if call.args
        ))

    def test_missing_answer_is_rejected(self):
        cursor, connection, status = self.prepare_valid_submission()

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/7/submit',
                {'question_1': 'A'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(connection.commit.called)

    def test_invalid_answer_value_is_rejected(self):
        cursor, connection, status = self.prepare_valid_submission()

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/7/submit',
                {'question_1': 'Z', 'question_2': 'B'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(connection.commit.called)

    def test_duplicate_question_field_is_rejected(self):
        cursor, connection, status = self.prepare_valid_submission()

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/7/submit',
                {'question_1': ['A', 'C'], 'question_2': 'B'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(connection.commit.called)

    def test_question_from_another_quiz_is_rejected(self):
        cursor, connection, status = self.prepare_valid_submission()

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/7/submit',
                {'question_1': 'A', 'question_2': 'B', 'question_99': 'A'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(connection.commit.called)

    def test_unexpected_form_field_is_rejected(self):
        cursor, connection, status = self.prepare_valid_submission()

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/7/submit',
                {'question_1': 'A', 'question_2': 'B', 'unexpected': 'value'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(connection.commit.called)

    def test_browser_submitted_fake_scores_are_rejected(self):
        cursor, connection, status = self.prepare_valid_submission()

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/7/submit',
                {
                    'question_1': 'A',
                    'question_2': 'B',
                    'score': '100',
                    'marks': '100',
                    'total': '2',
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(connection.commit.called)

    def test_validation_helper_accepts_only_expected_complete_answers(self):
        form = MultiDict([
            ('question_1', 'A'),
            ('question_2', 'D'),
            ('csrf_token', 'token'),
        ])
        valid, error, answers = app.validate_quiz_submission(form, [1, 2])

        self.assertTrue(valid)
        self.assertIsNone(error)
        self.assertEqual(answers, {'1': 'A', '2': 'D'})


if __name__ == '__main__':
    unittest.main()
