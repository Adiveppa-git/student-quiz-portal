import re
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from werkzeug.datastructures import MultiDict

import app


class StudentNotificationTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(
            TESTING=True,
            SECRET_KEY='student-notification-test-secret',
            WTF_CSRF_ENABLED=True,
        )
        self.client = app.app.test_client()

    def set_session(self, user_id=7, role='student'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'notification-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def post_with_csrf(self, path, data=None):
        token_response = self.client.get('/login')
        match = re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            token_response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        form_data = MultiDict(data or {})
        form_data.add('csrf_token', match.group(1))
        return self.client.post(path, data=form_data)

    def notification(self, notification_id=12, is_read=False):
        return {
            'id': notification_id,
            'title': 'New quiz available',
            'message': 'A new quiz, Science, is available to take.',
            'notification_type': 'quiz_available',
            'related_quiz_id': 3,
            'is_read': is_read,
            'created_at': datetime(2026, 11, 10, 9, 0),
            'quiz_title': 'Science',
        }

    def test_student_sees_only_owned_notifications_and_unread_count(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [
            self.notification(12, is_read=False),
            self.notification(13, is_read=True),
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/notifications')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Notifications', body)
        self.assertIn('New quiz available', body)
        self.assertIn('1 unread notification', body)
        self.assertIn('View quiz', body)
        query, parameters = cursor.execute.call_args.args
        self.assertIn('WHERE n.student_id = %s', query)
        self.assertIn('ORDER BY n.created_at DESC', query)
        self.assertEqual(parameters, (41,))
        connection.close.assert_called_once()

    def test_empty_notifications_show_helpful_state(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/notifications')

        self.assertEqual(response.status_code, 200)
        self.assertIn('No notifications yet', response.get_data(as_text=True))
        self.assertIn('You are all caught up.', response.get_data(as_text=True))

    def test_student_can_mark_owned_notification_as_read(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 1

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.post_with_csrf('/student/notifications/12/read')

        self.assertEqual(response.status_code, 302)
        query, parameters = cursor.execute.call_args.args
        self.assertIn('UPDATE student_notifications', query)
        self.assertIn('WHERE id = %s AND student_id = %s', query)
        self.assertEqual(parameters, (12, 41))
        connection.commit.assert_called_once()

    def test_marking_missing_or_another_students_notification_returns_404(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 0
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.post_with_csrf('/student/notifications/99/read')

        self.assertEqual(response.status_code, 404)
        lookup = cursor.execute.call_args_list[-1]
        self.assertIn('WHERE id = %s AND student_id = %s', lookup.args[0])
        self.assertEqual(lookup.args[1], (99, 41))
        connection.commit.assert_not_called()

    def test_marking_already_read_notification_is_idempotent(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 0
        cursor.fetchone.return_value = {'id': 12}

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.post_with_csrf('/student/notifications/12/read')

        self.assertEqual(response.status_code, 302)
        connection.commit.assert_called_once()

    def test_student_can_mark_all_owned_notifications_read(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.post_with_csrf('/student/notifications/read-all')

        self.assertEqual(response.status_code, 302)
        query, parameters = cursor.execute.call_args.args
        self.assertIn('UPDATE student_notifications', query)
        self.assertIn('WHERE student_id = %s AND is_read = FALSE', query)
        self.assertEqual(parameters, (41,))
        connection.commit.assert_called_once()

    def test_student_can_delete_only_owned_notification(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 1

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.post_with_csrf('/student/notifications/12/delete')

        self.assertEqual(response.status_code, 302)
        query, parameters = cursor.execute.call_args.args
        self.assertIn('DELETE FROM student_notifications', query)
        self.assertIn('WHERE id = %s AND student_id = %s', query)
        self.assertEqual(parameters, (12, 41))
        connection.commit.assert_called_once()

    def test_deleting_another_students_or_missing_notification_returns_404(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 0

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.post_with_csrf('/student/notifications/99/delete')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(cursor.execute.call_args.args[1], (99, 41))
        connection.commit.assert_not_called()

    def test_non_numeric_notification_ids_are_rejected_by_route(self):
        self.set_session()
        response = self.client.post('/student/notifications/not-an-id/delete')

        self.assertEqual(response.status_code, 404)

    def test_non_positive_notification_id_is_rejected(self):
        self.set_session()
        with patch.object(app, 'is_session_role_valid', return_value=True):
            response = self.post_with_csrf('/student/notifications/0/delete')

        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_student_is_denied(self):
        response = self.client.get('/student/notifications')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_admin_cannot_access_student_notifications(self):
        self.set_session(user_id=1, role='admin')

        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/notifications')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_state_changes_require_csrf_token(self):
        self.set_session()
        response = self.client.post('/student/notifications/read-all')

        self.assertEqual(response.status_code, 400)

    def test_dashboard_badge_uses_authenticated_students_unread_count(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], [], []]
        cursor.fetchone.return_value = {'unread_count': 3}

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/dashboard')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Notifications', response.get_data(as_text=True))
        self.assertIn('>3</span>', response.get_data(as_text=True))
        count_query = next(
            call for call in cursor.execute.call_args_list
            if 'COUNT(*) AS unread_count' in call.args[0]
        )
        self.assertIn('WHERE student_id = %s AND is_read = FALSE', count_query.args[0])
        self.assertEqual(count_query.args[1], (41,))

    def test_available_quiz_creation_generates_notifications_for_students(self):
        self.set_session(user_id=2, role='admin')
        connection, cursor = self.mock_connection()
        cursor.lastrowid = 15

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'copy_question_bank_to_quiz', return_value=0), \
             patch.object(app, 'record_admin_audit'):
            response = self.post_with_csrf('/admin/quizzes', {
                'action': 'create',
                'title': 'Science',
                'category': 'Science',
                'time_limit': '15',
                'description': 'Quiz description',
                'available_from': '',
                'available_until': '',
            })

        self.assertEqual(response.status_code, 302)
        notification_call = next(
            call for call in cursor.execute.call_args_list
            if 'INSERT IGNORE INTO student_notifications' in call.args[0]
        )
        self.assertIn("u.role = 'student'", notification_call.args[0])
        self.assertIn('UTC_TIMESTAMP() >= q.available_from', notification_call.args[0])
        self.assertIn('UTC_TIMESTAMP() <= q.available_until', notification_call.args[0])
        self.assertEqual(notification_call.args[1], (15,))
        connection.commit.assert_called_once()

    def test_successful_quiz_result_generates_a_deduplicated_notification(self):
        self.set_session(user_id=41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 1
        cursor.fetchall.return_value = [{'id': 1, 'correct_option': 'A'}]
        cursor.fetchone.side_effect = [None, {'id': 55}]
        status = {'time_limit_minutes': 15, 'expired': 0, 'schedule_closed': 0}

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'get_quiz_attempt_status', return_value=status):
            response = self.post_with_csrf(
                '/student/quiz/3/submit',
                {'question_1': 'A'},
            )

        self.assertEqual(response.status_code, 302)
        notification_call = next(
            call for call in cursor.execute.call_args_list
            if 'INSERT IGNORE INTO student_notifications' in call.args[0]
        )
        self.assertEqual(
            notification_call.args[1],
            (41, 'Quiz result recorded',
             'Your quiz has been completed. View your result for details.',
             'quiz_result', 3),
        )
        connection.commit.assert_called_once()

    def test_notifications_use_unique_student_type_quiz_key_and_insert_ignore(self):
        cursor = MagicMock()
        app.create_quiz_result_notification(cursor, 41, 3)

        sql = cursor.execute.call_args.args[0]
        self.assertIn('INSERT IGNORE INTO student_notifications', sql)
        self.assertEqual(cursor.execute.call_args.args[1][-2:], ('quiz_result', 3))

        root = Path(app.app.root_path)
        schema = root.joinpath('database.sql').read_text(encoding='utf-8')
        migration = root.joinpath(
            'migrations',
            '010_create_student_notifications.sql',
        ).read_text(encoding='utf-8')
        for definition in (schema, migration):
            self.assertIn('uq_student_notifications_student_type_quiz', definition)
            self.assertIn('FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE', definition)
            self.assertIn('FOREIGN KEY (related_quiz_id) REFERENCES quizzes(id) ON DELETE SET NULL', definition)

    def test_unsupported_announcement_and_future_schedule_triggers_are_documented(self):
        readme = Path(app.app.root_path).joinpath('README.md').read_text(encoding='utf-8')

        self.assertIn('no scheduled-job', readme)
        self.assertIn('no announcement publishing workflow', readme)


if __name__ == '__main__':
    unittest.main()
