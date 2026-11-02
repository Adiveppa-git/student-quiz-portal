import unittest
from unittest.mock import MagicMock, patch

import app


class QuizReviewTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='quiz-review-test-secret')
        self.client = app.app.test_client()

    def set_session(self, user_id=7, role='student'):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'student-review'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_compare_review_answer_identifies_correct_answer(self):
        reviewed = app.compare_review_answer({
            'selected_option': 'B',
            'correct_option': 'B',
            'option_a': 'Alpha',
            'option_b': 'Beta',
            'option_c': 'Gamma',
            'option_d': 'Delta',
        })
        self.assertTrue(reviewed['is_correct'])
        self.assertEqual(reviewed['selected_text'], 'Beta')
        self.assertEqual(reviewed['correct_text'], 'Beta')

    def test_compare_review_answer_identifies_incorrect_answer(self):
        reviewed = app.compare_review_answer({
            'selected_option': 'A',
            'correct_option': 'D',
            'option_a': 'Alpha',
            'option_b': 'Beta',
            'option_c': 'Gamma',
            'option_d': 'Delta',
        })
        self.assertFalse(reviewed['is_correct'])
        self.assertEqual(reviewed['selected_text'], 'Alpha')
        self.assertEqual(reviewed['correct_text'], 'Delta')

    def test_completed_quiz_review_displays_score_and_answers(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {
            'id': 31,
            'user_id': 7,
            'quiz_id': 5,
            'score': 1,
            'total_questions': 2,
            'date_taken': None,
            'title': 'Science',
            'category': 'General',
        }
        cursor.fetchall.return_value = [
            {
                'id': 1,
                'question_text': 'Which is correct?',
                'option_a': 'Alpha',
                'option_b': 'Beta',
                'option_c': 'Gamma',
                'option_d': 'Delta',
                'correct_option': 'B',
                'selected_option': 'B',
            },
            {
                'id': 2,
                'question_text': 'Choose another answer?',
                'option_a': 'One',
                'option_b': 'Two',
                'option_c': 'Three',
                'option_d': 'Four',
                'correct_option': 'D',
                'selected_option': 'A',
            },
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/result/5/review')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Science', body)
        self.assertIn('1 / 2', body)
        self.assertIn('50.0%', body)
        self.assertIn('Correct', body)
        self.assertIn('Incorrect', body)
        self.assertIn('Beta', body)
        self.assertIn('Four', body)

        ownership_query = cursor.execute.call_args_list[0]
        self.assertIn('WHERE r.user_id = %s AND r.quiz_id = %s', ownership_query.args[0])
        self.assertEqual(ownership_query.args[1], (7, 5))

        review_query = cursor.execute.call_args_list[1]
        self.assertIn('qra.result_id = %s', review_query.args[0])
        self.assertEqual(review_query.args[1], (31, 5))

    def test_uncompleted_quiz_is_redirected(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/result/999/review')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/student/dashboard', response.headers['Location'])

    def test_review_cannot_use_another_students_result(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = None

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/result/5/review?user_id=999')

        self.assertEqual(response.status_code, 302)
        query = cursor.execute.call_args_list[0]
        self.assertEqual(query.args[1], (7, 5))
        self.assertNotIn('999', query.args[0])

    def test_unauthenticated_and_admin_review_access_is_denied(self):
        unauthenticated = self.client.get('/student/result/5/review')
        self.assertEqual(unauthenticated.status_code, 302)
        self.assertIn('/login', unauthenticated.headers['Location'])

        self.set_session(1, role='admin')
        with patch.object(app, 'is_session_role_valid', return_value=False):
            admin_response = self.client.get('/student/result/5/review')
        self.assertEqual(admin_response.status_code, 302)
        self.assertIn('/login', admin_response.headers['Location'])

    def test_empty_review_data_has_graceful_message(self):
        self.set_session(7)
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {
            'id': 31,
            'user_id': 7,
            'quiz_id': 5,
            'score': 0,
            'total_questions': 0,
            'date_taken': None,
            'title': 'Empty Quiz',
            'category': 'Test',
        }
        cursor.fetchall.return_value = []

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/result/5/review')

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('No review data available', body)
        self.assertIn('N/A', body)

    def test_submission_persists_selected_answers_atomically(self):
        cursor = MagicMock()
        cursor.rowcount = 1
        app.save_quiz_result_answers(
            cursor,
            31,
            {'1': 'A', '2': 'D'},
        )
        calls = cursor.execute.call_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn('INSERT INTO quiz_result_answers', call.args[0])
            self.assertEqual(call.args[1][0], 31)

    def test_schema_and_migration_include_answer_storage_without_explanation_field(self):
        with open('database.sql', 'r', encoding='utf-8') as database_file:
            schema = database_file.read()
        with open('migrations/002_create_quiz_result_answers.sql', 'r', encoding='utf-8') as migration_file:
            migration = migration_file.read()

        self.assertIn('CREATE TABLE IF NOT EXISTS quiz_result_answers', schema)
        self.assertIn('selected_option CHAR(1) NOT NULL', schema)
        self.assertIn('UNIQUE KEY uq_quiz_result_answer', schema)
        self.assertIn('CREATE TABLE IF NOT EXISTS quiz_result_answers', migration)
        self.assertNotIn('explanation', schema.lower())


if __name__ == '__main__':
    unittest.main()
