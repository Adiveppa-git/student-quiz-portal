import unittest
from unittest.mock import MagicMock, patch

import app


class StudentFeedbackTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='feedback-test-secret', WTF_CSRF_ENABLED=False)
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config.update(WTF_CSRF_ENABLED=True)

    def set_session(self, user_id=7, role='student'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'feedback-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_student_can_submit_feedback_for_completed_quiz(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [{'id': 4}, None]
        cursor.rowcount = 1
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/feedback/create', data={
                'quiz_id': '12', 'rating': '5', 'feedback': 'Very helpful'
            }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        insert_call = cursor.execute.call_args_list[-1]
        self.assertIn('INSERT INTO student_quiz_feedback', insert_call.args[0])
        self.assertEqual(insert_call.args[1], (7, 12, 5, 'Very helpful'))
        connection.commit.assert_called_once()

    def test_student_cannot_submit_feedback_for_incomplete_quiz(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/feedback/create', data={
                'quiz_id': '12', 'rating': '4', 'feedback': 'No result'
            }, follow_redirects=True)
        self.assertIn(b'completed quiz', response.data)
        self.assertFalse(any(
            'INSERT INTO student_quiz_feedback' in call.args[0]
            for call in cursor.execute.call_args_list
        ))

    def test_invalid_rating_is_rejected(self):
        self.set_session(7)
        connection = MagicMock()
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/feedback/create', data={
                'quiz_id': '12', 'rating': '6', 'feedback': 'Too high'
            }, follow_redirects=True)
        self.assertIn(b'between 1 and 5', response.data)

    def test_duplicate_feedback_is_prevented(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [{'id': 4}, {'id': 99}]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/feedback/create', data={
                'quiz_id': '12', 'rating': '5', 'feedback': 'Again'
            }, follow_redirects=True)
        self.assertIn(b'already submitted feedback', response.data)
        self.assertFalse(any(
            'INSERT INTO student_quiz_feedback' in call.args[0]
            for call in cursor.execute.call_args_list
        ))

    def test_student_can_edit_only_their_own_feedback(self):
        self.set_session(41)
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [{'id': 9, 'quiz_id': 12}, {'id': 4}]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/feedback/9/edit', data={
                'quiz_id': '12', 'rating': '3', 'feedback': 'Updated'
            }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        update_call = cursor.execute.call_args_list[-1]
        self.assertIn('UPDATE student_quiz_feedback', update_call.args[0])
        self.assertEqual(update_call.args[1], (3, 'Updated', 9, 41))

    def test_student_can_delete_only_their_own_feedback(self):
        self.set_session(41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 1
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/feedback/9/delete', follow_redirects=False)
        delete_call = cursor.execute.call_args
        self.assertIn('WHERE id = %s AND student_id = %s', delete_call.args[0])
        self.assertEqual(delete_call.args[1], (9, 41))
        connection.commit.assert_called_once()

    def test_feedback_page_is_student_scoped_and_supports_search(self):
        self.set_session(41)
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/feedback?q=python&category=Programming')
        self.assertEqual(response.status_code, 200)
        calls = cursor.execute.call_args_list
        self.assertTrue(any("sf.student_id = %s" in call.args[0] for call in calls))
        student_query = next(call for call in calls if "sf.student_id = %s" in call.args[0])
        self.assertIn('q.category = %s', student_query.args[0])
        self.assertEqual(student_query.args[1][0], 41)

    def test_missing_or_other_students_feedback_is_not_editable(self):
        self.set_session(41)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/feedback/999/edit', data={
                'quiz_id': '12', 'rating': '4', 'feedback': 'Update'
            }, follow_redirects=True)
        self.assertIn(b'Feedback not found', response.data)

    def test_guest_and_admin_cannot_access_student_feedback(self):
        response = self.client.get('/student/feedback')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        self.set_session(1, 'admin')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/feedback')
        self.assertEqual(response.status_code, 302)

    def test_admin_feedback_analytics_is_available_to_admin_only(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'average_rating': 4.25}
        cursor.fetchall.side_effect = [
            [{'rating': 5, 'rating_count': 4}],
            [{'id': 12, 'title': 'Python', 'category': 'Programming', 'average_rating': 4.25, 'rating_count': 4}],
            [{'id': 1, 'rating': 5, 'feedback': 'Excellent', 'created_at': None, 'quiz_title': 'Python', 'quiz_category': 'Programming'}],
        ]
        self.set_session(1, 'admin')
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/admin/feedback')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Feedback Analytics', response.data)
        self.assertIn(b'Python', response.data)

        self.client.get('/logout')
        response = self.client.get('/admin/feedback')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
