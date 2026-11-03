import re
import unittest
from unittest.mock import MagicMock, patch

import app


class CsrfProtectionTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='csrf-test-secret')
        self.client = app.app.test_client()

    def get_csrf_token(self, path='/login'):
        response = self.client.get(path)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match, 'Rendered form did not contain a CSRF token')
        return match.group(1)

    def test_missing_csrf_token_is_rejected(self):
        response = self.client.post('/login', data={'username': 'student', 'password': 'password'})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Security validation failed', response.data)

    def test_invalid_csrf_token_is_rejected(self):
        response = self.client.post('/register', data={
            'csrf_token': 'invalid-token',
            'username': 'new-user',
            'password': 'password',
            'confirm_password': 'password',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Security validation failed', response.data)

    def test_valid_csrf_token_allows_logout(self):
        with self.client.session_transaction() as session:
            session['user_id'] = 4
            session['username'] = 'student'
            session['role'] = 'student'

        token = self.get_csrf_token('/login')
        response = self.client.post('/logout', data={'csrf_token': token})

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session)

    def test_valid_csrf_token_allows_login_workflow(self):
        token = self.get_csrf_token('/login')
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        cursor.fetchone.return_value = {
            'id': 4,
            'username': 'student',
            'password': 'hashed-password',
            'role': 'student',
        }
        connection.cursor.return_value.__enter__.return_value = cursor

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'check_password_hash', return_value=True):
            response = self.client.post('/login', data={
                'csrf_token': token,
                'username': 'student',
                'password': 'password',
            })

        self.assertEqual(response.status_code, 302)
        self.assertIn('/student/dashboard', response.headers['Location'])

    def test_missing_csrf_token_is_rejected_on_admin_post(self):
        with self.client.session_transaction() as session:
            session['user_id'] = 1
            session['username'] = 'admin'
            session['role'] = 'admin'

        response = self.client.post('/admin/quizzes', data={'action': 'create', 'title': 'Quiz'})
        self.assertEqual(response.status_code, 400)

    def test_missing_csrf_token_is_rejected_on_bulk_question_bank_post(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, username='admin', role='admin')

        response = self.client.post('/admin/question-bank', data={
            'action': 'bulk_delete',
            'question_ids': ['55'],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Security validation failed', response.data)


if __name__ == '__main__':
    unittest.main()
