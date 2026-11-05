import unittest
from unittest.mock import MagicMock, patch

import app


class StudentFavoritesTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='favorites-test-secret')
        app.app.config.update(WTF_CSRF_ENABLED=False)
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config.update(WTF_CSRF_ENABLED=True)

    def set_session(self, user_id=7, role='student'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'favorite-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_student_can_add_favorite(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 12}
        cursor.rowcount = 1
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/favorites/12/add', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        insert_call = cursor.execute.call_args_list[-1]
        self.assertIn('INSERT IGNORE', insert_call.args[0])
        self.assertEqual(insert_call.args[1], (7, 12))
        connection.commit.assert_called_once()

    def test_duplicate_favorite_is_prevented(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 12}
        cursor.rowcount = 0
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/favorites/12/add', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'already in your favorites', response.data)

    def test_student_can_remove_only_their_favorite(self):
        self.set_session(41)
        connection, cursor = self.mock_connection()
        cursor.rowcount = 1
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/favorites/12/remove', follow_redirects=False)
        delete_call = cursor.execute.call_args
        self.assertIn('DELETE FROM student_quiz_bookmarks', delete_call.args[0])
        self.assertEqual(delete_call.args[1], (41, 12))
        connection.commit.assert_called_once()

    def test_favorites_page_is_student_scoped(self):
        self.set_session(41)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/favorites?user_id=999')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'You have no favorite quizzes yet', response.data)
        query_call = cursor.execute.call_args
        self.assertIn('WHERE b.student_id = %s', query_call.args[0])
        self.assertEqual(query_call.args[1], (41,))

    def test_favorites_display_category_and_availability(self):
        self.set_session(41)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [{
            'id': 12,
            'title': 'Python Basics',
            'description': 'Learn Python',
            'category': 'Programming',
            'time_limit_minutes': 15,
            'available_from': None,
            'available_until': None,
        }]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/favorites')
        body = response.get_data(as_text=True)
        self.assertIn('Python Basics', body)
        self.assertIn('Programming', body)
        self.assertIn('Active', body)

    def test_invalid_quiz_cannot_be_bookmarked(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/student/favorites/999/add', follow_redirects=True)
        self.assertIn(b'Quiz not found', response.data)
        self.assertFalse(any(
            'INSERT IGNORE' in call.args[0]
            for call in cursor.execute.call_args_list
        ))

    def test_admin_and_guest_cannot_access_favorites(self):
        response = self.client.get('/student/favorites')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        self.set_session(1, 'admin')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.post('/student/favorites/12/add')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
