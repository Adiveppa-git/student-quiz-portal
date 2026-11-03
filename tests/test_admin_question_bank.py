import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app


class AdminQuestionBankTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='question-bank-test-secret')
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1
            session['username'] = 'admin'
            session['role'] = 'admin'

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def valid_payload(self, action='create'):
        return {
            'action': action,
            'category': 'Programming',
            'difficulty': 'medium',
            'question_text': 'What does == compare?',
            'option_a': 'Identity',
            'option_b': 'Value',
            'option_c': 'Both',
            'option_d': 'Neither',
            'correct_option': 'B',
        }

    def get_token(self):
        response = self.client.get('/admin/question-bank')
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def test_validation_accepts_supported_category_and_difficulty(self):
        valid, values, error = app.validate_question_bank_payload(
            category='Programming',
            difficulty='HARD',
            question_text='Question?',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='A',
        )
        self.assertTrue(valid)
        self.assertIsNone(error)
        self.assertEqual(values['difficulty'], 'hard')

    def test_validation_rejects_invalid_difficulty_or_empty_category(self):
        valid, _, error = app.validate_question_bank_payload(
            category='',
            difficulty='extreme',
            question_text='Question?',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='A',
        )
        self.assertFalse(valid)
        self.assertEqual(error, 'Question category is required.')

    def test_admin_can_create_bank_question_and_audit_it(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.lastrowid = 55

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            payload = self.valid_payload()
            payload['csrf_token'] = token
            response = self.client.post('/admin/question-bank', data=payload)

        self.assertEqual(response.status_code, 302)
        insert_calls = [c for c in cursor.execute.call_args_list if 'INSERT INTO question_bank' in c.args[0]]
        self.assertEqual(len(insert_calls), 1)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[1], 'QUESTION_BANK_CREATE')

    def get_token_from_mock(self, connection, cursor):
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            return self.get_token()

    def test_admin_can_edit_bank_question(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.rowcount = 1

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            payload = self.valid_payload(action='edit')
            payload.update({'csrf_token': token, 'question_id': '55'})
            response = self.client.post('/admin/question-bank', data=payload)

        self.assertEqual(response.status_code, 302)
        update_call = [c for c in cursor.execute.call_args_list if 'UPDATE question_bank' in c.args[0]][0]
        self.assertIn('WHERE id=%s', update_call.args[0])
        audit.assert_called_once()

    def test_admin_can_delete_bank_question(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.rowcount = 1

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            response = self.client.post('/admin/question-bank', data={
                'csrf_token': token,
                'action': 'delete',
                'question_id': '55',
            })

        self.assertEqual(response.status_code, 302)
        delete_call = [c for c in cursor.execute.call_args_list if 'DELETE FROM question_bank' in c.args[0]][0]
        self.assertEqual(delete_call.args[1], (55,))
        audit.assert_called_once()

    def test_search_and_category_difficulty_filters_are_parameterized(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [
            [{'category': 'Programming'}],
            [],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get(
                '/admin/question-bank?search=compare&category=Programming&difficulty=medium'
            )

        self.assertEqual(response.status_code, 200)
        query_call = cursor.execute.call_args_list[-1]
        self.assertIn('question_text LIKE %s', query_call.args[0])
        self.assertIn('category = %s', query_call.args[0])
        self.assertIn('difficulty = %s', query_call.args[0])
        self.assertEqual(query_call.args[1][0], '%compare%')

    def test_bank_questions_are_copied_into_quiz_as_snapshots(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{
            'question_text': 'Question?',
            'option_a': 'A',
            'option_b': 'B',
            'option_c': 'C',
            'option_d': 'D',
            'correct_option': 'A',
        }]

        copied = app.copy_question_bank_to_quiz(cursor, 7, ['55'])

        self.assertEqual(copied, 1)
        calls = cursor.execute.call_args_list
        self.assertIn('FROM question_bank', calls[0].args[0])
        insert_call = calls[1]
        self.assertIn('INSERT INTO questions', insert_call.args[0])
        self.assertEqual(insert_call.args[1][0], 7)

    def test_quiz_manage_questions_can_add_bank_selection(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Programming Quiz'}
        cursor.fetchall.return_value = []
        cursor.lastrowid = 99

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            response = self.client.post('/admin/quizzes/7/questions', data={
                'csrf_token': token,
                'action': 'add_from_bank',
                'bank_question_ids': ['55'],
            })

        self.assertEqual(response.status_code, 302)

    def test_validation_failure_does_not_create_bank_question(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            payload = self.valid_payload()
            payload.update({'csrf_token': token, 'question_text': '', 'category': ''})
            response = self.client.post('/admin/question-bank', data=payload)

        self.assertEqual(response.status_code, 302)
        insert_calls = [c for c in cursor.execute.call_args_list if 'INSERT INTO question_bank' in c.args[0]]
        self.assertEqual(insert_calls, [])
        audit.assert_not_called()

    def test_missing_csrf_token_is_rejected(self):
        with patch.object(app, 'is_session_role_valid', return_value=True):
            response = self.client.post('/admin/question-bank', data=self.valid_payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Security validation failed', response.data)

    def test_student_and_unauthenticated_users_are_denied(self):
        with self.client.session_transaction() as session:
            session['role'] = 'student'
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/admin/question-bank')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get('/admin/question-bank')
        self.assertEqual(response.status_code, 302)

    def test_migration_and_database_schema_include_question_bank_fields(self):
        root = Path(app.app.root_path)
        schema = root.joinpath('database.sql').read_text(encoding='utf-8')
        migration = root.joinpath('migrations', '005_create_question_bank.sql').read_text(encoding='utf-8')
        for sql in (schema, migration):
            self.assertIn('CREATE TABLE IF NOT EXISTS question_bank', sql)
            self.assertIn('category VARCHAR(50) NOT NULL', sql)
            self.assertIn("difficulty ENUM('easy', 'medium', 'hard')", sql)
            self.assertIn('correct_option CHAR(1) NOT NULL', sql)


if __name__ == '__main__':
    unittest.main()
