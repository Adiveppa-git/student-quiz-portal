import unittest
from unittest.mock import MagicMock, patch

import app


class StudentRevisionNotesTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='notes-test-secret', WTF_CSRF_ENABLED=False)
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config.update(WTF_CSRF_ENABLED=True)

    def set_session(self, user_id=52, role='student'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'notes-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_student_can_create_general_note(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/notes/create', data={'title': 'Formula review', 'content': 'Review quadratic formulas.', 'quiz_id': ''})
        self.assertEqual(response.status_code, 302)
        insert_call = cursor.execute.call_args_list[-1]
        self.assertIn('INSERT INTO student_quiz_notes', insert_call.args[0])
        self.assertEqual(insert_call.args[1], (52, None, 'Formula review', 'Review quadratic formulas.'))
        connection.commit.assert_called_once()

    def test_student_can_create_note_for_quiz(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 12}
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/notes/create', data={'title': 'Quiz notes', 'content': 'Key definitions', 'quiz_id': '12'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('INSERT INTO student_quiz_notes', cursor.execute.call_args_list[-1].args[0])
        self.assertEqual(cursor.execute.call_args_list[-1].args[1], (52, 12, 'Quiz notes', 'Key definitions'))

    def test_notes_list_is_scoped_and_supports_search_and_category(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [
            [{'id': 1, 'title': 'Algebra', 'content': 'Factorization', 'quiz_category': 'Math', 'quiz_title': 'Algebra'}],
            [{'id': 12, 'title': 'Algebra', 'category': 'Math'}],
        ]
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/notes?q=algebra&category=Math')
        self.assertEqual(response.status_code, 200)
        first_call = cursor.execute.call_args_list[0]
        self.assertIn('sn.student_id = %s', first_call.args[0])
        self.assertIn('sn.title LIKE %s OR sn.content LIKE %s', first_call.args[0])
        self.assertIn('q.category = %s', first_call.args[0])
        self.assertEqual(first_call.args[1], (52, '%algebra%', '%algebra%', 'Math'))
        self.assertIn(b'Algebra', response.data)

    def test_empty_notes_state_is_scoped_to_student(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/notes?user_id=999')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'No revision notes yet', response.data)
        self.assertIn('sn.student_id = %s', cursor.execute.call_args_list[0].args[0])
        self.assertEqual(cursor.execute.call_args_list[0].args[1], (52,))

    def test_edit_note_is_student_scoped(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        cursor.fetchone.side_effect = [{'id': 44}, {'id': 44}]
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/notes/44/edit', data={'title': 'Updated note', 'content': 'Updated content', 'quiz_id': ''})
        self.assertEqual(response.status_code, 302)
        owner_call = next(call for call in cursor.execute.call_args_list if 'WHERE id = %s AND student_id = %s' in call.args[0])
        self.assertEqual(owner_call.args[1], (44, 52))
        update_call = cursor.execute.call_args_list[-1]
        self.assertIn('UPDATE student_quiz_notes', update_call.args[0])
        connection.commit.assert_called_once()

    def test_delete_note_is_student_scoped(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 1
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/notes/44/delete')
        self.assertEqual(response.status_code, 302)
        delete_call = cursor.execute.call_args
        self.assertIn('DELETE FROM student_quiz_notes', delete_call.args[0])
        self.assertIn('AND student_id = %s', delete_call.args[0])
        self.assertEqual(delete_call.args[1], (44, 52))

    def test_student_cannot_edit_or_delete_another_students_note(self):
        self.set_session(52)
        for path in ('/student/notes/44/edit', '/student/notes/44/delete'):
            connection, cursor = self.mock_connection()
            cursor.fetchone.return_value = None
            cursor.rowcount = 0
            data = {'title': 'Other note', 'content': 'No access', 'quiz_id': ''} if path.endswith('/edit') else None
            with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
                response = self.client.post(path, data=data or {}, follow_redirects=True)
            self.assertEqual(response.status_code, 200)

    def test_invalid_input_and_quiz_are_rejected(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/notes/create', data={'title': '', 'content': 'Content', 'quiz_id': ''}, follow_redirects=True)
        self.assertIn(b'Note title is required', response.data)

        cursor.fetchone.return_value = None
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/notes/create', data={'title': 'Valid', 'content': 'Content', 'quiz_id': '999'}, follow_redirects=True)
        self.assertIn(b'Related quiz not found', response.data)
        self.assertFalse(any('INSERT INTO student_quiz_notes' in call.args[0] for call in cursor.execute.call_args_list))

    def test_missing_note_is_handled_for_edit_and_delete(self):
        self.set_session(52)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None
        cursor.rowcount = 0
        with patch.object(app, 'is_session_role_valid', return_value=True), patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/notes/999/edit', data={'title': 'Missing', 'content': 'Missing', 'quiz_id': ''}, follow_redirects=True)
        self.assertIn(b'Revision note not found', response.data)

    def test_guest_and_admin_cannot_access_notes(self):
        response = self.client.get('/student/notes')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        self.set_session(1, 'admin')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.post('/student/notes/create', data={'title': 'No', 'content': 'No'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
