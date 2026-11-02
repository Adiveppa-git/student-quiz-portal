import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app


class AdminAuditLogTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='audit-log-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role='admin', user_id=1):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'audit-admin'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_record_admin_audit_creates_parameterized_event(self):
        connection, cursor = self.mock_connection()
        with patch.object(app, 'get_db_connection', return_value=connection):
            recorded = app.record_admin_audit(
                1,
                'QUIZ_CREATE',
                'quiz',
                12,
                'Created quiz "Science"',
            )

        self.assertTrue(recorded)
        query = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]
        self.assertIn('INSERT INTO admin_audit_logs', query)
        self.assertEqual(params, (1, 'QUIZ_CREATE', 'quiz', 12, 'Created quiz "Science"'))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_audit_log_rejects_missing_admin_id_without_writing(self):
        with patch.object(app, 'get_db_connection') as get_connection:
            recorded = app.record_admin_audit(None, 'QUIZ_DELETE')
        self.assertFalse(recorded)
        get_connection.assert_not_called()

    def test_audit_logging_failure_does_not_raise_and_does_not_commit(self):
        connection, cursor = self.mock_connection()
        cursor.execute.side_effect = app.pymysql.MySQLError('audit table unavailable')

        with patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app.app.logger, 'exception'):
            recorded = app.record_admin_audit(
                1,
                'QUIZ_UPDATE',
                'quiz',
                5,
                'Modified quiz',
            )

        self.assertFalse(recorded)
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_admin_can_view_reverse_chronological_audit_history(self):
        self.set_session('admin', 1)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'count': 3}
        cursor.fetchall.return_value = [{
            'id': 9,
            'admin_user_id': 1,
            'admin_username': 'admin-one',
            'action_type': 'QUIZ_DELETE',
            'entity_type': 'quiz',
            'entity_id': 8,
            'details': 'Deleted quiz',
            'created_at': None,
        }]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/audit-log')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Admin Activity Audit Log', body)
        self.assertIn('QUIZ_DELETE', body)
        self.assertIn('admin-one', body)
        calls = cursor.execute.call_args_list
        history_query = calls[-1].args[0]
        self.assertIn('ORDER BY a.created_at DESC, a.id DESC', history_query)
        self.assertIn('LIMIT %s OFFSET %s', history_query)

    def test_student_cannot_access_audit_history(self):
        self.set_session('student', 4)
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/admin/audit-log')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_unauthenticated_user_cannot_access_audit_history(self):
        response = self.client.get('/admin/audit-log')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_empty_audit_history_is_handled(self):
        self.set_session('admin', 1)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'count': 0}
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/audit-log')

        self.assertEqual(response.status_code, 200)
        self.assertIn('No administrative activity has been recorded.', response.get_data(as_text=True))

    def test_audit_schema_migration_and_database_include_required_fields(self):
        root = Path(app.app.root_path)
        schema = root.joinpath('database.sql').read_text(encoding='utf-8')
        migration = root.joinpath('migrations', '003_create_admin_audit_logs.sql').read_text(encoding='utf-8')

        for sql in (schema, migration):
            self.assertIn('admin_user_id INT', sql)
            self.assertIn('action_type VARCHAR(100) NOT NULL', sql)
            self.assertIn('entity_id INT', sql)
            self.assertIn('details VARCHAR(500)', sql)
            self.assertIn('created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP', sql)

        self.assertIn('FOREIGN KEY (admin_user_id) REFERENCES users(id)', migration)


if __name__ == '__main__':
    unittest.main()
