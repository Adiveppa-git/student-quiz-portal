import unittest
from unittest.mock import MagicMock, patch

import app


class RbacRouteSecurityTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='rbac-test-secret')
        self.client = app.app.test_client()

    def set_session(self, user_id, role):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'test-user'
            session['role'] = role

    def mock_connection(self, cursor=None):
        connection = MagicMock()
        connection.open = True
        if cursor is None:
            cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_unauthenticated_admin_route_is_denied(self):
        response = self.client.get('/admin/dashboard')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_unauthenticated_student_route_is_denied(self):
        response = self.client.get('/student/dashboard')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_student_cannot_access_admin_route(self):
        self.set_session(4, 'student')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/admin/dashboard')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_admin_can_access_admin_dashboard(self):
        self.set_session(1, 'admin')
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [
            {'count': 3},
            {'count': 2},
            {'count': 6},
            None,
        ]
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/dashboard')

        self.assertEqual(response.status_code, 200)

    def test_legitimate_student_can_access_student_dashboard(self):
        self.set_session(4, 'student')
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/dashboard')

        self.assertEqual(response.status_code, 200)

    def test_admin_cannot_access_student_dashboard(self):
        self.set_session(1, 'admin')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/dashboard')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_student_cannot_view_another_students_result(self):
        self.set_session(4, 'student')
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/result/999')

        self.assertEqual(response.status_code, 302)
        result_query = cursor.execute.call_args_list[0]
        self.assertIn('WHERE r.user_id = %s AND r.quiz_id = %s', result_query.args[0])
        self.assertEqual(result_query.args[1], (4, 999))

    def test_role_helper_rejects_different_session_role(self):
        self.set_session(4, 'student')
        with self.client.session_transaction() as session:
            session['role'] = 'admin'

        with app.app.test_request_context('/'):
            from flask import session
            session['user_id'] = 4
            session['role'] = 'admin'
            self.assertFalse(app.is_session_role_valid('student'))

    def test_role_helper_rejects_deleted_or_different_database_role(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'role': 'student'}

        with app.app.test_request_context('/'):
            from flask import session
            session['user_id'] = 4
            session['role'] = 'admin'

            with patch.object(app, 'get_db_connection', return_value=connection):
                result = app.is_session_role_valid('admin')

        self.assertFalse(result)
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
