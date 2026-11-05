import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import app


class StudentStudyPlannerTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='planner-test-secret')
        app.app.config.update(WTF_CSRF_ENABLED=False)
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config.update(WTF_CSRF_ENABLED=True)

    def set_session(self, user_id=8, role='student'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'planner-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_empty_planner_state_is_scoped_to_student(self):
        self.set_session(31)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/study-planner?user_id=999')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Your study plan is empty', response.data)
        calls = cursor.execute.call_args_list
        self.assertIn('WHERE st.student_id = %s', calls[0].args[0])
        self.assertEqual(calls[0].args[1], (31,))

    def test_student_can_create_task_with_related_quiz(self):
        self.set_session(31)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 12}
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/study-planner/tasks', data={
                'title': 'Review algebra',
                'description': 'Practice word problems',
                'target_date': '2026-12-01',
                'quiz_id': '12',
            })
        self.assertEqual(response.status_code, 302)
        insert_call = cursor.execute.call_args_list[-1]
        self.assertIn('INSERT INTO student_study_tasks', insert_call.args[0])
        self.assertEqual(insert_call.args[1], (31, 'Review algebra', 'Practice word problems', 12, date(2026, 12, 1)))
        connection.commit.assert_called_once()

    def test_edit_task_is_scoped_to_authenticated_student(self):
        self.set_session(31)
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [{'id': 44}, None]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/study-planner/tasks/44/edit', data={
                'title': 'Updated task',
                'description': '',
                'target_date': '2026-11-20',
                'quiz_id': '',
            })
        self.assertEqual(response.status_code, 302)
        owner_call = cursor.execute.call_args_list[0]
        self.assertIn('WHERE id = %s AND student_id = %s', owner_call.args[0])
        self.assertEqual(owner_call.args[1], (44, 31))
        update_call = cursor.execute.call_args_list[-1]
        self.assertIn('UPDATE student_study_tasks', update_call.args[0])
        connection.commit.assert_called_once()

    def test_delete_task_is_student_scoped(self):
        self.set_session(31)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 1
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/study-planner/tasks/44/delete')
        delete_call = cursor.execute.call_args
        self.assertIn('DELETE FROM student_study_tasks', delete_call.args[0])
        self.assertIn('AND student_id = %s', delete_call.args[0])
        self.assertEqual(delete_call.args[1], (44, 31))
        connection.commit.assert_called_once()

    def test_mark_task_complete_and_pending(self):
        self.set_session(31)
        for completed, rowcount in ((True, 1), (False, 1)):
            connection, cursor = self.mock_connection()
            cursor.rowcount = rowcount
            with patch.object(app, 'is_session_role_valid', return_value=True), \
                 patch.object(app, 'get_db_connection', return_value=connection):
                response = self.client.post('/student/study-planner/tasks/44/status', data={
                    'is_completed': 'true' if completed else 'false',
                })
            self.assertEqual(response.status_code, 302)
            status_call = cursor.execute.call_args
            self.assertIn('SET is_completed = %s', status_call.args[0])
            self.assertEqual(status_call.args[1][0], completed)
            self.assertEqual(status_call.args[1][2], 31)

    def test_invalid_input_and_invalid_quiz_are_rejected(self):
        self.set_session(31)
        connection, cursor = self.mock_connection()
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/study-planner/tasks', data={
                'title': '',
                'description': 'x',
                'target_date': 'not-a-date',
                'quiz_id': '',
            }, follow_redirects=True)
        self.assertIn(b'Task title is required', response.data)

        cursor.fetchone.return_value = None
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/study-planner/tasks', data={
                'title': 'Valid title',
                'description': '',
                'target_date': '2026-11-20',
                'quiz_id': '999',
            }, follow_redirects=True)
        self.assertIn(b'Related quiz not found', response.data)
        self.assertFalse(any('INSERT INTO student_study_tasks' in call.args[0]
                            for call in cursor.execute.call_args_list))

    def test_upcoming_overdue_and_completed_classification(self):
        today = date(2026, 11, 5)
        self.assertEqual(app.classify_study_task({'target_date': date(2026, 11, 6), 'is_completed': False}, today), 'upcoming')
        self.assertEqual(app.classify_study_task({'target_date': date(2026, 11, 4), 'is_completed': False}, today), 'overdue')
        self.assertEqual(app.classify_study_task({'target_date': date(2026, 11, 4), 'is_completed': True}, today), 'completed')

    def test_admin_and_guest_cannot_access_planner(self):
        response = self.client.get('/student/study-planner')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        self.set_session(1, 'admin')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.post('/student/study-planner/tasks')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
