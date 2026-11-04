import unittest
from unittest.mock import MagicMock, patch

import app


class StudentSubjectAnalysisTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='subject-analysis-test-secret')
        self.client = app.app.test_client()

    def set_session(self, role='student', user_id=7):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['username'] = 'subject-student'
            session['role'] = role

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def result(self, category, score, total):
        return {'category': category, 'score': score, 'total_questions': total}

    def test_subject_averages_and_completed_quiz_counts(self):
        analysis = app.calculate_subject_wise_analysis([
            self.result('Science', 8, 10),
            self.result('Science', 6, 10),
            self.result('Math', 9, 10),
        ])
        subjects = {item['subject']: item for item in analysis['subjects']}
        self.assertEqual(subjects['Science']['average_score'], 70.0)
        self.assertEqual(subjects['Science']['completed_quizzes'], 2)
        self.assertEqual(subjects['Math']['average_score'], 90.0)
        self.assertEqual(subjects['Math']['completed_quizzes'], 1)
        self.assertEqual(analysis['strongest']['subject'], 'Math')
        self.assertEqual(analysis['weakest']['subject'], 'Science')

    def test_one_subject_is_insufficient_for_strongest_and_weakest(self):
        analysis = app.calculate_subject_wise_analysis([
            self.result('Science', 8, 10),
            self.result('Science', 7, 10),
        ])
        self.assertIsNone(analysis['strongest'])
        self.assertIsNone(analysis['weakest'])

    def test_missing_categories_and_invalid_scores_are_excluded(self):
        analysis = app.calculate_subject_wise_analysis([
            self.result(None, 8, 10),
            self.result('', 8, 10),
            self.result('Science', 0, 0),
            self.result('Science', 11, 10),
        ])
        self.assertEqual(analysis['subjects'], [])
        self.assertEqual(analysis['valid_count'], 0)

    def test_suggestions_are_based_on_actual_weak_subject(self):
        analysis = app.calculate_subject_wise_analysis([
            self.result('Science', 8, 10),
            self.result('Math', 4, 10),
        ])
        self.assertTrue(any('Math' in suggestion for suggestion in analysis['suggestions']))
        self.assertTrue(any('40.0%' in suggestion or '40%' in suggestion
                            for suggestion in analysis['suggestions']))

    def test_route_requires_student_and_is_session_scoped(self):
        response = self.client.get('/student/subject-analysis')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        self.set_session('student', 42)
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [self.result('Science', 8, 10)]
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/subject-analysis?user_id=999')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Subject-Wise Analysis', body)
        self.assertIn('Science', body)
        query = cursor.execute.call_args_list[0]
        self.assertIn('WHERE r.user_id = %s', query.args[0])
        self.assertEqual(query.args[1], (42,))
        self.assertNotIn('999', str(query.args))

    def test_admin_cannot_access_subject_analysis(self):
        self.set_session('admin', 1)
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/student/subject-analysis')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_empty_subject_results_display_empty_state(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = []
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get('/student/subject-analysis')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('No subject performance data yet', body)
        self.assertIn('Browse Quizzes', body)

    def test_database_error_is_generic(self):
        self.set_session()
        connection, cursor = self.mock_connection()
        cursor.execute.side_effect = app.pymysql.err.OperationalError(
            1146,
            "Table 'student_quiz_portal.results' doesn't exist",
        )
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app.app.logger, 'exception'):
            response = self.client.get('/student/subject-analysis')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('The database could not complete your request. Please try again.', body)
        self.assertNotIn('student_quiz_portal.results', body)


if __name__ == '__main__':
    unittest.main()
