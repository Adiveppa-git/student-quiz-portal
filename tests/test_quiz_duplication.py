import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app


class QuizDuplicationTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='duplication-test-secret')
        app.app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config['WTF_CSRF_ENABLED'] = True

    def set_session(self, role='admin', user_id=1):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'admin-user' if role == 'admin' else 'student-user'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_helper_copies_quiz_metadata_and_all_questions(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {
            'id': 7,
            'title': 'Science',
            'description': 'General science',
            'category': 'Science',
            'time_limit_minutes': 20,
        }
        cursor.fetchall.return_value = [
            {
                'question_text': 'Capital of France?',
                'option_a': 'Rome',
                'option_b': 'Paris',
                'option_c': 'Madrid',
                'option_d': 'Berlin',
                'correct_option': 'B',
            }
        ]
        cursor.lastrowid = 42

        duplicated = app.duplicate_quiz_in_transaction(cursor, 7, 1)

        self.assertEqual(duplicated['id'], 42)
        self.assertEqual(duplicated['title'], 'Science (Copy)')
        self.assertEqual(duplicated['question_count'], 1)
        calls = cursor.execute.call_args_list
        quiz_insert = calls[1].args[0]
        quiz_params = calls[1].args[1]
        self.assertIn('INSERT INTO quizzes', quiz_insert)
        self.assertIn('available_from', quiz_insert)
        self.assertIn('NULL, NULL', quiz_insert)
        self.assertEqual(quiz_params, (
            'Science (Copy)', 'General science', 'Science', 20, 1,
        ))
        question_insert = calls[3].args[0]
        question_params = calls[3].args[1]
        self.assertIn('INSERT INTO questions', question_insert)
        self.assertEqual(question_params, (
            42, 'Capital of France?', 'Rome', 'Paris', 'Madrid', 'Berlin', 'B',
        ))

    def test_successful_route_commits_once_and_logs_duplicate_audit(self):
        self.set_session('admin', 1)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {
            'id': 7, 'title': 'Science', 'description': '',
            'category': 'Science', 'time_limit_minutes': 15,
        }
        cursor.fetchall.return_value = []
        cursor.lastrowid = 88

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit', return_value=True) as audit:
            response = self.client.post('/admin/quizzes/7/duplicate')

        self.assertEqual(response.status_code, 302)
        connection.begin.assert_called_once()
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        audit.assert_called_once()
        audit_call = audit.call_args.args
        self.assertEqual(audit_call[0], 1)
        self.assertEqual(audit_call[1], 'QUIZ_DUPLICATE')
        self.assertEqual(audit_call[3], 88)

    def test_invalid_quiz_id_is_handled_without_inserting(self):
        self.set_session('admin', 1)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/admin/quizzes/999/duplicate')

        self.assertEqual(response.status_code, 302)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_student_cannot_duplicate_quiz(self):
        self.set_session('student', 4)
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.post('/admin/quizzes/7/duplicate')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_unauthenticated_user_cannot_duplicate_quiz(self):
        response = self.client.post('/admin/quizzes/7/duplicate')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_insert_failure_rolls_back_transaction(self):
        self.set_session('admin', 1)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {
            'id': 7, 'title': 'Science', 'description': '',
            'category': 'Science', 'time_limit_minutes': 15,
        }
        cursor.lastrowid = 88
        cursor.fetchall.return_value = [{
            'question_text': 'Q',
            'option_a': 'A',
            'option_b': 'B',
            'option_c': 'C',
            'option_d': 'D',
            'correct_option': 'A',
        }]
        cursor.execute.side_effect = [
            None,
            None,
            app.pymysql.MySQLError('insert failed'),
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.post('/admin/quizzes/7/duplicate')

        self.assertEqual(response.status_code, 302)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_duplicate_action_does_not_select_or_copy_student_data(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {
            'id': 7, 'title': 'Science', 'description': '',
            'category': 'Science', 'time_limit_minutes': 15,
        }
        cursor.fetchall.return_value = []
        cursor.lastrowid = 88

        app.duplicate_quiz_in_transaction(cursor, 7, 1)

        executed_sql = '\n'.join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertNotIn('FROM results', executed_sql)
        self.assertNotIn('FROM quiz_attempts', executed_sql)
        self.assertNotIn('quiz_result_answers', executed_sql)
        self.assertNotIn('admin_audit_logs', executed_sql)

    def test_schema_does_not_require_a_new_migration_for_duplication(self):
        root = Path(app.app.root_path)
        self.assertIn('available_from DATETIME NULL', root.joinpath('database.sql').read_text(encoding='utf-8'))
        self.assertIn('available_until DATETIME NULL', root.joinpath('database.sql').read_text(encoding='utf-8'))
        self.assertFalse(root.joinpath('migrations', '005_quiz_duplication.sql').exists())


if __name__ == '__main__':
    unittest.main()
