import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app


class DatabaseSecurityTests(unittest.TestCase):
    def test_connection_uses_timeout_and_utf8mb4(self):
        with patch.object(app.pymysql, 'connect') as connect:
            expected = MagicMock()
            connect.return_value = expected

            connection = app.get_db_connection()

            self.assertIs(connection, expected)
            kwargs = connect.call_args.kwargs
            self.assertEqual(kwargs['connect_timeout'], 5)
            self.assertEqual(kwargs['charset'], 'utf8mb4')
            self.assertEqual(kwargs['cursorclass'], app.pymysql.cursors.DictCursor)

    def test_connection_failure_is_not_printed_or_exposed(self):
        with patch.object(app.pymysql, 'connect', side_effect=app.pymysql.MySQLError('sensitive detail')):
            with patch.object(app.app.logger, 'exception') as log_exception:
                connection = app.get_db_connection()

        self.assertIsNone(connection)
        log_exception.assert_called_once()

    def test_schema_has_unique_result_and_foreign_key_integrity(self):
        sql = Path(app.app.root_path).joinpath('database.sql').read_text(encoding='utf-8')
        self.assertIn('UNIQUE KEY uq_results_user_quiz (user_id, quiz_id)', sql)
        self.assertIn('FOREIGN KEY (user_id) REFERENCES users(id)', sql)
        self.assertIn('FOREIGN KEY (quiz_id) REFERENCES quizzes(id)', sql)


if __name__ == '__main__':
    unittest.main()
