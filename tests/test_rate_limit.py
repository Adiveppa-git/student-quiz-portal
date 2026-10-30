import io
import logging
import re
import unittest
from unittest.mock import MagicMock, patch

import app


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='rate-limit-test-secret', WTF_CSRF_ENABLED=True)
        app.limiter.reset()
        self.client = app.app.test_client()

    def tearDown(self):
        app.limiter.reset()

    def get_csrf_token(self, path):
        response = self.client.get(path)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def mock_login_connection(self, user=None, password_valid=False):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        cursor.fetchone.return_value = user
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection

    def test_normal_login_request_works(self):
        token = self.get_csrf_token('/login')
        user = {'id': 4, 'username': 'student', 'password': 'hashed-password', 'role': 'student'}
        connection = self.mock_login_connection(user)

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'check_password_hash', return_value=True):
            response = self.client.post('/login', data={
                'csrf_token': token,
                'username': 'student',
                'password': 'correct-password',
            })

        self.assertEqual(response.status_code, 302)
        self.assertIn('/student/dashboard', response.headers['Location'])

    def test_repeated_login_requests_are_rate_limited(self):
        token = self.get_csrf_token('/login')
        connection = self.mock_login_connection(user=None)

        with patch.object(app, 'get_db_connection', return_value=connection):
            for _ in range(10):
                response = self.client.post('/login', data={
                    'csrf_token': token,
                    'username': 'student',
                    'password': 'wrong-password',
                })

            limited = self.client.post('/login', data={
                'csrf_token': token,
                'username': 'student',
                'password': 'wrong-password',
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertIn(b'Too many requests', limited.data)

    def test_registration_requests_are_rate_limited(self):
        token = self.get_csrf_token('/register')
        connection = self.mock_login_connection(user={'id': 99, 'role': 'student'})

        with patch.object(app, 'get_db_connection', return_value=connection):
            for _ in range(5):
                response = self.client.post('/register', data={
                    'csrf_token': token,
                    'username': 'existing-user',
                    'password': 'password',
                    'confirm_password': 'password',
                })

            limited = self.client.post('/register', data={
                'csrf_token': token,
                'username': 'existing-user',
                'password': 'password',
                'confirm_password': 'password',
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertIn(b'Too many requests', limited.data)

    def test_legitimate_login_works_after_limit_is_reset(self):
        token = self.get_csrf_token('/login')
        connection = self.mock_login_connection(user=None)

        with patch.object(app, 'get_db_connection', return_value=connection):
            for _ in range(11):
                self.client.post('/login', data={
                    'csrf_token': token,
                    'username': 'student',
                    'password': 'wrong-password',
                })

        app.limiter.reset()
        user = {'id': 4, 'username': 'student', 'password': 'hashed-password', 'role': 'student'}
        connection = self.mock_login_connection(user)

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'check_password_hash', return_value=True):
            response = self.client.post('/login', data={
                'csrf_token': token,
                'username': 'student',
                'password': 'correct-password',
            })

        self.assertEqual(response.status_code, 302)

    def test_csrf_protection_still_rejects_missing_token_before_login(self):
        app.limiter.reset()
        response = self.client.post('/login', data={'username': 'student', 'password': 'password'})
        self.assertEqual(response.status_code, 400)

    def test_missing_csrf_token_does_not_expose_credentials(self):
        log_output = io.StringIO()
        log_handler = logging.StreamHandler(log_output)
        application_logger = app.app.logger
        previous_level = application_logger.level
        application_logger.setLevel(logging.DEBUG)
        application_logger.addHandler(log_handler)

        try:
            response = self.client.post('/login', data={
                'username': 'private-user',
                'password': 'private-password',
            })
        finally:
            application_logger.removeHandler(log_handler)
            application_logger.setLevel(previous_level)
            log_handler.close()

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b'private-user', response.data)
        self.assertNotIn(b'private-password', response.data)

        captured_logs = log_output.getvalue()
        self.assertNotIn('private-user', captured_logs)
        self.assertNotIn('private-password', captured_logs)


if __name__ == '__main__':
    unittest.main()
