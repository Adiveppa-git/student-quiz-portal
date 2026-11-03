import re
import io
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app


class AdminQuestionBankTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SECRET_KEY='question-bank-test-secret')
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1
            session['username'] = 'admin'
            session['role'] = 'admin'

    def mock_connection(self):
        connection = MagicMock()
        connection.open = True
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def valid_payload(self, action='create'):
        return {
            'action': action,
            'category': 'Programming',
            'difficulty': 'medium',
            'question_text': 'What does == compare?',
            'option_a': 'Identity',
            'option_b': 'Value',
            'option_c': 'Both',
            'option_d': 'Neither',
            'correct_option': 'B',
        }

    def get_token(self):
        response = self.client.get('/admin/question-bank')
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def test_validation_accepts_supported_category_and_difficulty(self):
        valid, values, error = app.validate_question_bank_payload(
            category='Programming',
            difficulty='HARD',
            question_text='Question?',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='A',
        )
        self.assertTrue(valid)
        self.assertIsNone(error)
        self.assertEqual(values['difficulty'], 'hard')

    def test_validation_rejects_invalid_difficulty_or_empty_category(self):
        valid, _, error = app.validate_question_bank_payload(
            category='',
            difficulty='extreme',
            question_text='Question?',
            option_a='A',
            option_b='B',
            option_c='C',
            option_d='D',
            correct_option='A',
        )
        self.assertFalse(valid)
        self.assertEqual(error, 'Question category is required.')

    def test_admin_can_create_bank_question_and_audit_it(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.lastrowid = 55

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            payload = self.valid_payload()
            payload['csrf_token'] = token
            response = self.client.post('/admin/question-bank', data=payload)

        self.assertEqual(response.status_code, 302)
        insert_calls = [c for c in cursor.execute.call_args_list if 'INSERT INTO question_bank' in c.args[0]]
        self.assertEqual(len(insert_calls), 1)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[1], 'QUESTION_BANK_CREATE')

    def get_token_from_mock(self, connection, cursor):
        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            return self.get_token()

    def test_admin_can_edit_bank_question(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.rowcount = 1

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            payload = self.valid_payload(action='edit')
            payload.update({'csrf_token': token, 'question_id': '55'})
            response = self.client.post('/admin/question-bank', data=payload)

        self.assertEqual(response.status_code, 302)
        update_call = [c for c in cursor.execute.call_args_list if 'UPDATE question_bank' in c.args[0]][0]
        self.assertIn('WHERE id=%s', update_call.args[0])
        audit.assert_called_once()

    def test_admin_can_delete_bank_question(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.rowcount = 1

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            response = self.client.post('/admin/question-bank', data={
                'csrf_token': token,
                'action': 'delete',
                'question_id': '55',
            })

        self.assertEqual(response.status_code, 302)
        delete_call = [c for c in cursor.execute.call_args_list if 'DELETE FROM question_bank' in c.args[0]][0]
        self.assertEqual(delete_call.args[1], (55,))
        audit.assert_called_once()

    def test_search_and_category_difficulty_filters_are_parameterized(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [
            [{'category': 'Programming'}],
            [],
        ]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection):
            response = self.client.get(
                '/admin/question-bank?search=compare&category=Programming&difficulty=medium'
            )

        self.assertEqual(response.status_code, 200)
        query_call = cursor.execute.call_args_list[-1]
        self.assertIn('question_text LIKE %s', query_call.args[0])
        self.assertIn('category = %s', query_call.args[0])
        self.assertIn('difficulty = %s', query_call.args[0])
        self.assertEqual(query_call.args[1][0], '%compare%')

    def test_bank_questions_are_copied_into_quiz_as_snapshots(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{
            'question_text': 'Question?',
            'option_a': 'A',
            'option_b': 'B',
            'option_c': 'C',
            'option_d': 'D',
            'correct_option': 'A',
        }]

        copied = app.copy_question_bank_to_quiz(cursor, 7, ['55'])

        self.assertEqual(copied, 1)
        calls = cursor.execute.call_args_list
        self.assertIn('FROM question_bank', calls[0].args[0])
        insert_call = calls[1]
        self.assertIn('INSERT INTO questions', insert_call.args[0])
        self.assertEqual(insert_call.args[1][0], 7)

    def test_quiz_manage_questions_can_add_bank_selection(self):
        connection, cursor = self.mock_connection()
        cursor.fetchone.return_value = {'id': 7, 'title': 'Programming Quiz'}
        cursor.fetchall.return_value = []
        cursor.lastrowid = 99

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            response = self.client.post('/admin/quizzes/7/questions', data={
                'csrf_token': token,
                'action': 'add_from_bank',
                'bank_question_ids': ['55'],
            })

        self.assertEqual(response.status_code, 302)

    def test_validation_failure_does_not_create_bank_question(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            payload = self.valid_payload()
            payload.update({'csrf_token': token, 'question_text': '', 'category': ''})
            response = self.client.post('/admin/question-bank', data=payload)

        self.assertEqual(response.status_code, 302)
        insert_calls = [c for c in cursor.execute.call_args_list if 'INSERT INTO question_bank' in c.args[0]]
        self.assertEqual(insert_calls, [])
        audit.assert_not_called()

    def test_missing_csrf_token_is_rejected(self):
        with patch.object(app, 'is_session_role_valid', return_value=True):
            response = self.client.post('/admin/question-bank', data=self.valid_payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Security validation failed', response.data)

    def test_student_and_unauthenticated_users_are_denied(self):
        with self.client.session_transaction() as session:
            session['role'] = 'student'
        with patch.object(app, 'is_session_role_valid', return_value=False):
            response = self.client.get('/admin/question-bank')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get('/admin/question-bank')
        self.assertEqual(response.status_code, 302)

    def make_csv_upload(self, text, filename='questions.csv'):
        class Upload:
            pass
        upload = Upload()
        upload.filename = filename
        upload.stream = io.BytesIO(text.encode('utf-8'))
        return upload

    def csv_text(self, rows):
        output = io.StringIO(newline='')
        writer = app.csv.writer(output)
        writer.writerow(app.QUESTION_BANK_CSV_HEADERS)
        writer.writerows(rows)
        return output.getvalue()

    def test_valid_csv_import_inserts_parameterized_rows(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.fetchone.return_value = {'count': 0}
        text = self.csv_text([[
            'What is Python?', 'A language', 'A snake', 'Both', 'Neither',
            'A', 'Programming', 'easy',
        ]])

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            token = self.get_token_from_mock(connection, cursor)
            response = self.client.post(
                '/admin/question-bank/import',
                data={'csrf_token': token, 'csv_file': (io.BytesIO(text.encode('utf-8')), 'questions.csv')},
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 302)
        insert_calls = [
            call for call in cursor.execute.call_args_list
            if 'INSERT INTO question_bank' in call.args[0]
        ]
        self.assertEqual(len(insert_calls), 1)
        self.assertEqual(insert_calls[0].args[1][-1], 1)
        connection.commit.assert_called_once()
        audit.assert_called_once()

    def test_csv_import_missing_columns_is_rejected(self):
        upload = self.make_csv_upload('question_text,option_a\nWhat?,A\n')
        rows, invalid, _, error = app.parse_question_bank_csv(upload)
        self.assertIsNone(rows)
        self.assertIsNotNone(error)
        self.assertIn('Missing required columns', error)

    def test_csv_import_invalid_correct_option_difficulty_and_empty_values(self):
        text = self.csv_text([
            ['Question?', 'A', 'B', 'C', 'D', 'X', 'General', 'easy'],
            ['', 'A', 'B', 'C', 'D', 'A', 'General', 'medium'],
            ['Question 3?', 'A', 'B', 'C', 'D', 'C', 'General', 'extreme'],
        ])
        upload = self.make_csv_upload(text)
        rows, invalid, _, error = app.parse_question_bank_csv(upload)
        self.assertIsNone(error)
        self.assertEqual(rows, [])
        self.assertEqual(len(invalid), 3)

    def test_empty_and_malformed_csv_are_rejected(self):
        for text, filename in (('', 'empty.csv'), ('"unterminated', 'bad.csv'), ('   \n', 'blank.csv')):
            rows, _, _, error = app.parse_question_bank_csv(self.make_csv_upload(text, filename))
            self.assertIsNone(rows)
            self.assertIsNotNone(error)

        wrong_type, _, _, error = app.parse_question_bank_csv(
            self.make_csv_upload('question_text\nQuestion?', 'questions.txt')
        )
        self.assertIsNone(wrong_type)
        self.assertIn('.csv extension', error)

    def test_csv_import_duplicate_rows_and_database_duplicates_are_skipped(self):
        text = self.csv_text([
            ['Duplicate?', 'A', 'B', 'C', 'D', 'A', 'General', 'easy'],
            ['Duplicate?', 'A', 'B', 'C', 'D', 'A', 'General', 'easy'],
        ])
        connection, cursor = self.mock_connection()
        cursor.fetchall.side_effect = [[], []]
        cursor.fetchone.return_value = {'count': 1}

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit'):
            token = self.get_token_from_mock(connection, cursor)
            response = self.client.post(
                '/admin/question-bank/import',
                data={'csrf_token': token, 'csv_file': (io.BytesIO(text.encode('utf-8')), 'questions.csv')},
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(any(
            'INSERT INTO question_bank' in call.args[0]
            for call in cursor.execute.call_args_list
        ))
        with self.client.session_transaction():
            pass

    def test_csv_export_download_has_headers_utf8_and_formula_protection(self):
        connection, cursor = self.mock_connection()
        cursor.fetchall.return_value = [{
            'question_text': '=SUM(A1:A2)',
            'option_a': 'A',
            'option_b': 'B',
            'option_c': 'C',
            'option_d': 'D',
            'correct_option': 'A',
            'category': 'General',
            'difficulty': 'easy',
        }]

        with patch.object(app, 'is_session_role_valid', return_value=True), \
             patch.object(app, 'get_db_connection', return_value=connection), \
             patch.object(app, 'record_admin_audit') as audit:
            response = self.client.get('/admin/question-bank/export.csv')

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response.headers['Content-Type'])
        self.assertIn('question_bank.csv', response.headers['Content-Disposition'])
        body = response.get_data(as_text=True)
        self.assertIn('question_text,option_a,option_b', body)
        self.assertIn("'=SUM(A1:A2)", body)
        audit.assert_called_once()

    def test_student_and_unauthenticated_users_cannot_import_or_export(self):
        with self.client.session_transaction() as session:
            session['role'] = 'student'
        with patch.object(app, 'is_session_role_valid', return_value=False):
            export_response = self.client.get('/admin/question-bank/export.csv')
            login_page = self.client.get('/login')
            token_match = re.search(
                r'name="csrf_token"[^>]*value="([^"]+)"',
                login_page.get_data(as_text=True),
            )
            self.assertIsNotNone(token_match)
            import_response = self.client.post(
                '/admin/question-bank/import',
                data={
                    'csrf_token': token_match.group(1),
                    'csv_file': (io.BytesIO(b'a'), 'a.csv'),
                },
                content_type='multipart/form-data',
            )
        self.assertEqual(export_response.status_code, 302)
        self.assertEqual(import_response.status_code, 302)

        with self.client.session_transaction() as session:
            session.clear()
        export_response = self.client.get('/admin/question-bank/export.csv')
        login_page = self.client.get('/login')
        token_match = re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            login_page.get_data(as_text=True),
        )
        import_response = self.client.post(
            '/admin/question-bank/import',
            data={
                'csrf_token': token_match.group(1),
                'csv_file': (io.BytesIO(b'a'), 'a.csv'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(export_response.status_code, 302)
        self.assertEqual(import_response.status_code, 302)

    def test_missing_csrf_token_is_rejected_for_csv_import(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, username='admin', role='admin')
        with patch.object(app, 'is_session_role_valid', return_value=True):
            response = self.client.post(
                '/admin/question-bank/import',
                data={'csv_file': (io.BytesIO(b'a,b\n1,2\n'), 'questions.csv')},
                content_type='multipart/form-data',
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Security validation failed', response.data)

    def test_csv_import_rejects_empty_and_oversized_files(self):
        upload = self.make_csv_upload('', 'empty.csv')
        rows, _, _, error = app.parse_question_bank_csv(upload)
        self.assertIsNone(rows)
        self.assertEqual(error, 'CSV file is empty.')

        class LargeUpload:
            filename = 'large.csv'
            stream = io.BytesIO(b'x' * (app.QUESTION_BANK_CSV_MAX_BYTES + 1))
        rows, _, _, error = app.parse_question_bank_csv(LargeUpload())
        self.assertIsNone(rows)
        self.assertIn('1 MB or smaller', error)

    def test_migration_and_database_schema_include_question_bank_fields(self):
        root = Path(app.app.root_path)
        schema = root.joinpath('database.sql').read_text(encoding='utf-8')
        migration = root.joinpath('migrations', '005_create_question_bank.sql').read_text(encoding='utf-8')
        for sql in (schema, migration):
            self.assertIn('CREATE TABLE IF NOT EXISTS question_bank', sql)
            self.assertIn('category VARCHAR(50) NOT NULL', sql)
            self.assertIn("difficulty ENUM('easy', 'medium', 'hard')", sql)
            self.assertIn('correct_option CHAR(1) NOT NULL', sql)


if __name__ == '__main__':
    unittest.main()
