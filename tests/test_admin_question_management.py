import re
import unittest
from unittest.mock import MagicMock, patch

import app


class AdminQuestionManagementTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='question-management-test-secret')
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
            'question_text': 'What is 2 + 2?',
            'option_a': '3',
            'option_b': '4',
            'option_c': '5',
            'option_d': '6',
            'correct_option': 'B',
        }

    def get_csrf_token(self, connection, cursor):
        cursor.fetchone.return_value = {'id': 7, 'title': 'Math Quiz'}
        cursor.fetchall.return_value = []
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/quizzes/7/questions')
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def test_question_payload_validation_accepts_valid_data(self):
        valid, values, error = app.validate_question_payload(
            '  What is 2 + 2?  ',
            '3', '4', '5', '6', 'b',
        )
        self.assertTrue(valid)
        self.assertIsNone(error)
        self.assertEqual(values['question_text'], 'What is 2 + 2?')
        self.assertEqual(values['correct_option'], 'B')

    def test_question_payload_validation_rejects_empty_and_duplicate_values(self):
        valid, _, error = app.validate_question_payload('', 'A', 'B', 'C', 'D', 'A')
        self.assertFalse(valid)
        self.assertEqual(error, 'Question text is required.')

        valid, _, error = app.validate_question_payload('Question?', 'same', 'Same', 'C', 'D', 'A')
        self.assertFalse(valid)
        self.assertEqual(error, 'The four options must be different.')

    def test_admin_can_add_question(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Math Quiz'}
        cursor.lastrowid = 21
        token = self.get_csrf_token(connection, cursor)
        payload = self.valid_payload()
        payload['csrf_token'] = token

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            response = self.client.post('/admin/quizzes/7/questions', data=payload)

        self.assertEqual(response.status_code, 302)
        insert_call = cursor.execute.call_args_list[-1]
        self.assertIn('INSERT INTO questions', insert_call.args[0])
        self.assertEqual(insert_call.args[1][0], 7)
        audit.assert_called_once()
        connection.commit.assert_called_once()

    def test_admin_can_edit_question_owned_by_quiz(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Math Quiz'}
        cursor.rowcount = 1
        token = self.get_csrf_token(connection, cursor)
        payload = self.valid_payload(action='edit')
        payload.update({'csrf_token': token, 'question_id': '21'})

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            response = self.client.post('/admin/quizzes/7/questions', data=payload)

        self.assertEqual(response.status_code, 302)
        update_call = cursor.execute.call_args_list[-1]
        self.assertIn('UPDATE questions', update_call.args[0])
        self.assertIn('WHERE id=%s AND quiz_id=%s', update_call.args[0])
        audit.assert_called_once()

    def test_admin_can_delete_question_with_confirmation_post(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Math Quiz'}
        cursor.rowcount = 1
        token = self.get_csrf_token(connection, cursor)

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            response = self.client.post('/admin/quizzes/7/questions', data={
                'csrf_token': token,
                'action': 'delete',
                'question_id': '21',
            })

        self.assertEqual(response.status_code, 302)
        delete_call = cursor.execute.call_args_list[-1]
        self.assertIn('DELETE FROM questions WHERE id = %s AND quiz_id = %s', delete_call.args[0])
        audit.assert_called_once()

    def test_edit_or_delete_for_question_in_another_quiz_is_rejected(self):
        for action in ('edit', 'delete'):
            connection, cursor = self.mock_connection()
            cursor.fetchone.return_value = {'id': 7, 'title': 'Math Quiz'}
            cursor.rowcount = 0
            token = self.get_csrf_token(connection, cursor)
            payload = {'csrf_token': token, 'action': action, 'question_id': '999'}
            if action == 'edit':
                payload.update(self.valid_payload(action='edit'))
                payload['csrf_token'] = token
                payload['question_id'] = '999'

            with patch.object(app, 'is_session_role_valid', return_value=True), \
                 patch.object(app, 'get_db_connection', return_value=connection), \
                 patch.object(app, 'record_admin_audit') as audit:
                response = self.client.post('/admin/quizzes/7/questions', data=payload)

            self.assertEqual(response.status_code, 302)
            audit.assert_not_called()

    def test_create_validation_failures_do_not_insert(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Math Quiz'}
        token = self.get_csrf_token(connection, cursor)
        payload = self.valid_payload()
        payload.update({
            'csrf_token': token,
            'question_text': '',
            'option_a': '',
        })

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            response = self.client.post('/admin/quizzes/7/questions', data=payload)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(any(
            'INSERT INTO questions' in call.args[0]
            for call in cursor.execute.call_args_list
        ))
        audit.assert_not_called()

    def test_student_and_unauthenticated_users_cannot_manage_questions(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Math Quiz'}

        with self.client.session_transaction() as session:
            session['role'] = 'student'
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/admin/quizzes/7/questions')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get('/admin/quizzes/7/questions')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_missing_csrf_token_is_rejected_for_question_changes(self):
        with patch.object(app, 'is_session_role_valid', return_value=True):
            response = self.client.post('/admin/quizzes/7/questions', data=self.valid_payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Security validation failed', response.data)


if __name__ == '__main__':
    unittest.main()
