from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import pymysql
import pymysql.cursors
import csv
import io
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', '0') == '1'

csrf = CSRFProtect(app)

rate_limit_storage_uri = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')
if os.getenv('APP_ENV', '').lower() == 'production' and rate_limit_storage_uri.startswith('memory://'):
    raise RuntimeError('RATELIMIT_STORAGE_URI must use a shared backend in production')
if rate_limit_storage_uri.startswith('memory://'):
    app.logger.warning('Using in-memory rate-limit storage; configure RATELIMIT_STORAGE_URI for production')

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri=rate_limit_storage_uri,
    strategy='fixed-window',
    headers_enabled=True,
)

@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    return render_template('csrf_error.html', reason=error.description), 400

@app.errorhandler(429)
def handle_rate_limit_error(error):
    return render_template('rate_limit_error.html', retry_after=getattr(error, 'retry_after', None)), 429

# Database Configuration
# Environment variables allow deployment-specific credentials without changing source code.
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_NAME = os.getenv('DB_NAME', 'student_quiz_portal')


def get_db_connection():
    try:
        return pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
            charset='utf8mb4',
            connect_timeout=5,
        )
    except pymysql.MySQLError:
        app.logger.exception('Database connection failed')
        return None


def close_db_connection(connection):
    if connection is not None and connection.open:
        connection.close()


def rollback_db(connection):
    if connection is not None and connection.open:
        connection.rollback()


def flash_database_error(action='database operation'):
    app.logger.exception('Failed to complete %s', action)
    flash('The database could not complete your request. Please try again.', 'danger')


def validate_quiz_time_limit(value):
    try:
        time_limit = int(value)
    except (TypeError, ValueError):
        return None
    return time_limit if time_limit > 0 else None


def parse_quiz_schedule_value(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return False


def validate_quiz_schedule(start_value=None, end_value=None):
    start = parse_quiz_schedule_value(start_value)
    end = parse_quiz_schedule_value(end_value)
    if start is False or end is False:
        return False, None, None, 'Enter valid start and end dates and times.'
    if start is not None and end is not None and start >= end:
        return False, None, None, 'The quiz end time must be after the start time.'
    return True, start, end, None


def validate_question_payload(question_text=None, option_a=None, option_b=None,
                              option_c=None, option_d=None, correct_option=None):
    """Normalize and validate question data against the existing questions schema."""
    normalized = {
        'question_text': str(question_text).strip() if question_text is not None else '',
        'option_a': str(option_a).strip() if option_a is not None else '',
        'option_b': str(option_b).strip() if option_b is not None else '',
        'option_c': str(option_c).strip() if option_c is not None else '',
        'option_d': str(option_d).strip() if option_d is not None else '',
        'correct_option': str(correct_option).strip().upper() if correct_option is not None else '',
    }

    if not normalized['question_text']:
        return False, normalized, 'Question text is required.'
    if len(normalized['question_text']) > 5000:
        return False, normalized, 'Question text must be 5000 characters or fewer.'

    option_names = ('option_a', 'option_b', 'option_c', 'option_d')
    for option_name in option_names:
        value = normalized[option_name]
        if not value:
            return False, normalized, 'All four options are required.'
        if len(value) > 255:
            return False, normalized, 'Each option must be 255 characters or fewer.'

    option_values = [normalized[name].casefold() for name in option_names]
    if len(set(option_values)) != 4:
        return False, normalized, 'The four options must be different.'

    if normalized['correct_option'] not in ('A', 'B', 'C', 'D'):
        return False, normalized, 'Correct answer must be option A, B, C, or D.'

    return True, normalized, None


def validate_question_bank_payload(category=None, difficulty=None, **question_values):
    valid, normalized, error = validate_question_payload(**question_values)
    if not valid:
        return False, normalized, error

    normalized_category = str(category).strip() if category is not None else ''
    normalized_difficulty = str(difficulty).strip().lower() if difficulty is not None else ''
    normalized['category'] = normalized_category
    normalized['difficulty'] = normalized_difficulty

    if not normalized_category:
        return False, normalized, 'Question category is required.'
    if len(normalized_category) > 50:
        return False, normalized, 'Category must be 50 characters or fewer.'
    if normalized_difficulty not in ('easy', 'medium', 'hard'):
        return False, normalized, 'Difficulty must be easy, medium, or hard.'

    return True, normalized, None


ACHIEVEMENT_DEFINITIONS = (
    {
        'key': 'first_steps',
        'name': 'First Steps',
        'description': 'Complete your first quiz.',
        'icon': 'fa-shoe-prints',
        'target': 1,
        'kind': 'completed',
        'color': 'primary',
    },
    {
        'key': 'quiz_explorer',
        'name': 'Quiz Explorer',
        'description': 'Complete 5 quizzes.',
        'icon': 'fa-compass',
        'target': 5,
        'kind': 'completed',
        'color': 'info',
    },
    {
        'key': 'quiz_champion',
        'name': 'Quiz Champion',
        'description': 'Complete 10 quizzes.',
        'icon': 'fa-crown',
        'target': 10,
        'kind': 'completed',
        'color': 'warning',
    },
    {
        'key': 'perfect_score',
        'name': 'Perfect Score',
        'description': 'Score 100% on a completed quiz.',
        'icon': 'fa-bullseye',
        'target': 100,
        'kind': 'percentage',
        'color': 'success',
    },
    {
        'key': 'high_achiever',
        'name': 'High Achiever',
        'description': 'Score at least 90% on a completed quiz.',
        'icon': 'fa-star',
        'target': 90,
        'kind': 'percentage',
        'color': 'danger',
    },
    {
        'key': 'consistent_learner',
        'name': 'Consistent Learner',
        'description': 'Complete quizzes on 5 distinct calendar days.',
        'icon': 'fa-calendar-check',
        'target': 5,
        'kind': 'days',
        'color': 'secondary',
    },
)


def calculate_student_achievements(results):
    """Calculate achievements without writing award rows or modifying result records."""
    valid_results = []
    for result in results or []:
        try:
            score = int(result.get('score'))
            total = int(result.get('total_questions'))
        except (TypeError, ValueError, AttributeError):
            continue
        if total <= 0 or score < 0 or score > total:
            continue

        date_taken = result.get('date_taken')
        if isinstance(date_taken, datetime):
            calendar_day = date_taken.date()
        else:
            calendar_day = None
            for date_format in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                try:
                    calendar_day = datetime.strptime(str(date_taken), date_format).date()
                    date_taken = datetime.strptime(str(date_taken), date_format)
                    break
                except (TypeError, ValueError):
                    continue
        percentage = score * 100.0 / total
        valid_results.append({
            'quiz_id': result.get('quiz_id'),
            'score': score,
            'total': total,
            'percentage': percentage,
            'date_taken': date_taken,
            'calendar_day': calendar_day,
        })

    completed_quiz_ids = {
        result['quiz_id'] for result in valid_results if result['quiz_id'] is not None
    }
    completed_count = len(completed_quiz_ids) if completed_quiz_ids else len(valid_results)

    distinct_days = sorted({
        result['calendar_day'] for result in valid_results
        if result['calendar_day'] is not None
    })

    sorted_results = sorted(
        valid_results,
        key=lambda result: (
            result['date_taken'] if isinstance(result['date_taken'], datetime) else datetime.min,
        ),
    )

    earned = {}
    for result in sorted_results:
        quiz_id = result['quiz_id']
        if quiz_id is not None:
            milestone_count = sum(
                1 for existing in sorted_results[:sorted_results.index(result) + 1]
                if existing['quiz_id'] == quiz_id
            )
        else:
            milestone_count = sorted_results.index(result) + 1

        if result['percentage'] >= 100:
            earned.setdefault('perfect_score', result['date_taken'])
        if result['percentage'] >= 90:
            earned.setdefault('high_achiever', result['date_taken'])

    if completed_count >= 1:
        earned['first_steps'] = _first_milestone_date(sorted_results, completed_count, 1)
    if completed_count >= 5:
        earned['quiz_explorer'] = _first_milestone_date(sorted_results, completed_count, 5)
    if completed_count >= 10:
        earned['quiz_champion'] = _first_milestone_date(sorted_results, completed_count, 10)
    if len(distinct_days) >= 5:
        earned['consistent_learner'] = distinct_days[4]

    achievements = []
    for definition in ACHIEVEMENT_DEFINITIONS:
        key = definition['key']
        if definition['kind'] == 'completed':
            progress = min(completed_count, definition['target'])
        elif definition['kind'] == 'days':
            progress = min(len(distinct_days), definition['target'])
        else:
            highest = max((result['percentage'] for result in valid_results), default=0)
            progress = min(highest, definition['target'])

        achievement = dict(definition)
        achievement['earned'] = key in earned
        achievement['earned_date'] = earned.get(key)
        achievement['progress'] = progress
        achievements.append(achievement)
    return achievements


def _first_milestone_date(sorted_results, completed_count, milestone):
    if milestone <= completed_count and sorted_results:
        if any(result['quiz_id'] is None for result in sorted_results):
            return sorted_results[milestone - 1]['date_taken']
        seen_quizzes = set()
        for result in sorted_results:
            quiz_id = result['quiz_id']
            if quiz_id is None or quiz_id not in seen_quizzes:
                if quiz_id is not None:
                    seen_quizzes.add(quiz_id)
                if len(seen_quizzes) >= milestone:
                    return result['date_taken']
    return None


def normalize_question_bank_ids(raw_ids):
    """Return unique positive integer IDs, or None when any submitted ID is invalid."""
    if not raw_ids:
        return None

    normalized = []
    for raw_id in raw_ids:
        for part in str(raw_id).split(','):
            part = part.strip()
            try:
                question_id = int(part)
            except (TypeError, ValueError):
                return None
            if question_id <= 0:
                return None
            if question_id not in normalized:
                normalized.append(question_id)
    return normalized or None


def parse_positive_page(raw_page):
    try:
        page = int(raw_page)
    except (TypeError, ValueError):
        return 1
    return page if page > 0 else 1


QUESTION_BANK_CSV_HEADERS = [
    'question_text',
    'option_a',
    'option_b',
    'option_c',
    'option_d',
    'correct_option',
    'category',
    'difficulty',
]
QUESTION_BANK_CSV_MAX_BYTES = 1024 * 1024


def parse_question_bank_csv(upload):
    """Parse and validate one uploaded UTF-8 CSV; database duplicates are handled separately."""
    filename = getattr(upload, 'filename', '') or ''
    if not filename.lower().endswith('.csv'):
        return None, [], [], 'Please upload a file with a .csv extension.'

    raw = upload.stream.read(QUESTION_BANK_CSV_MAX_BYTES + 1)
    if len(raw) > QUESTION_BANK_CSV_MAX_BYTES:
        return None, [], [], 'CSV file must be 1 MB or smaller.'
    if not raw:
        return None, [], [], 'CSV file is empty.'

    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return None, [], [], 'CSV file must be UTF-8 encoded.'
    if not text.strip():
        return None, [], [], 'CSV file is empty.'

    try:
        reader = csv.DictReader(
            io.StringIO(text, newline=''),
            restkey='__extra_fields__',
            restval='',
            strict=True,
        )
        headers = [
            str(header).strip().lower().replace(' ', '_')
            for header in (reader.fieldnames or [])
        ]
    except csv.Error as error:
        return None, [], [], f'Malformed CSV: {error}'

    missing_headers = [header for header in QUESTION_BANK_CSV_HEADERS if header not in headers]
    if missing_headers:
        return None, [], [], (
            'Missing required columns: ' + ', '.join(missing_headers)
        )

    normalized_rows = []
    invalid_rows = []
    try:
        reader.fieldnames = headers
        for row_number, row in enumerate(reader, start=2):
            if '__extra_fields__' in row and row['__extra_fields__']:
                invalid_rows.append({
                    'row': row_number,
                    'error': 'Row contains more values than the CSV header defines.',
                })
                continue

            values = {header: (row.get(header) or '').strip() for header in headers}
            valid, normalized, error = validate_question_bank_payload(
                category=values.get('category'),
                difficulty=values.get('difficulty'),
                question_text=values.get('question_text'),
                option_a=values.get('option_a'),
                option_b=values.get('option_b'),
                option_c=values.get('option_c'),
                option_d=values.get('option_d'),
                correct_option=values.get('correct_option'),
            )
            if not valid:
                invalid_rows.append({'row': row_number, 'error': error})
                continue
            normalized_rows.append(normalized)
    except csv.Error as error:
        return None, [], [], f'Malformed CSV near row {reader.line_num}: {error}'

    if not normalized_rows and not invalid_rows:
        return None, [], [], 'CSV file contains no question rows.'
    return normalized_rows, invalid_rows, [], None


def copy_question_bank_to_quiz(cursor, quiz_id, bank_question_ids):
    """Append bank-question snapshots to a quiz without linking completed history."""
    normalized_ids = []
    for raw_id in bank_question_ids:
        try:
            bank_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if bank_id > 0 and bank_id not in normalized_ids:
            normalized_ids.append(bank_id)
    if not normalized_ids:
        return 0

    placeholders = ', '.join(['%s'] * len(normalized_ids))
    cursor.execute(
        f"""
        SELECT question_text, option_a, option_b, option_c, option_d, correct_option
        FROM question_bank
        WHERE id IN ({placeholders})
        ORDER BY id ASC
        """,
        tuple(normalized_ids),
    )
    bank_questions = cursor.fetchall()

    for question in bank_questions:
        cursor.execute(
            """
            INSERT INTO questions
                (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                quiz_id,
                question['question_text'],
                question['option_a'],
                question['option_b'],
                question['option_c'],
                question['option_d'],
                question['correct_option'],
            ),
        )
    return len(bank_questions)


def normalize_schedule_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def classify_quiz_availability(quiz, now=None):
    start = normalize_schedule_datetime(quiz.get('available_from'))
    end = normalize_schedule_datetime(quiz.get('available_until'))
    current = normalize_schedule_datetime(now) if now is not None else datetime.now(timezone.utc).replace(tzinfo=None)

    if start is not None and current < start:
        return 'upcoming'
    if end is not None and current > end:
        return 'closed'
    return 'active'


def sanitize_csv_cell(value):
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return value

    text = str(value)
    stripped = text.lstrip()
    if text.startswith(('\t', '\r', '\n')) or stripped.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        return "'" + text
    return text


def get_or_create_quiz_attempt(cursor, user_id, quiz_id):
    cursor.execute(
        "INSERT IGNORE INTO quiz_attempts (user_id, quiz_id) VALUES (%s, %s)",
        (user_id, quiz_id),
    )
    cursor.execute(
        "SELECT started_at FROM quiz_attempts WHERE user_id = %s AND quiz_id = %s",
        (user_id, quiz_id),
    )
    return cursor.fetchone()


def get_quiz_attempt_status(cursor, user_id, quiz_id):
    cursor.execute("""
        SELECT a.started_at,
               q.time_limit_minutes,
               q.available_from,
               q.available_until,
               CASE
                   WHEN CURRENT_TIMESTAMP() <= DATE_ADD(
                       a.started_at,
                       INTERVAL q.time_limit_minutes MINUTE
                   ) THEN 0 ELSE 1
               END AS expired
               ,
               CASE
                   WHEN q.available_from IS NOT NULL
                    AND UTC_TIMESTAMP() < q.available_from THEN 1
                   WHEN q.available_until IS NOT NULL
                    AND UTC_TIMESTAMP() > q.available_until THEN 1
                   ELSE 0
               END AS schedule_closed
        FROM quiz_attempts a
        JOIN quizzes q ON q.id = a.quiz_id
        WHERE a.user_id = %s AND a.quiz_id = %s
    """, (user_id, quiz_id))
    return cursor.fetchone()


def insert_quiz_result_if_unexpired(cursor, user_id, quiz_id, score, total_questions):
    cursor.execute("""
        INSERT INTO results (user_id, quiz_id, score, total_questions)
        SELECT %s, %s, %s, %s
        FROM quiz_attempts a
        JOIN quizzes q ON q.id = a.quiz_id
        WHERE a.user_id = %s
          AND a.quiz_id = %s
          AND q.id = %s
          AND CURRENT_TIMESTAMP() <= DATE_ADD(
              a.started_at,
              INTERVAL q.time_limit_minutes MINUTE
          )
          AND (q.available_from IS NULL OR UTC_TIMESTAMP() >= q.available_from)
          AND (q.available_until IS NULL OR UTC_TIMESTAMP() <= q.available_until)
    """, (user_id, quiz_id, score, total_questions, user_id, quiz_id, quiz_id))
    return cursor.rowcount


def save_quiz_result_answers(cursor, result_id, submitted_answers):
    for question_id, selected_option in submitted_answers.items():
        cursor.execute("""
            INSERT INTO quiz_result_answers (result_id, question_id, selected_option)
            VALUES (%s, %s, %s)
        """, (result_id, int(question_id), selected_option))


def compare_review_answer(question):
    selected = question.get('selected_option')
    correct = question.get('correct_option')
    options = {
        'A': question.get('option_a'),
        'B': question.get('option_b'),
        'C': question.get('option_c'),
        'D': question.get('option_d'),
    }
    return {
        **question,
        'selected_text': options.get(selected),
        'correct_text': options.get(correct),
        'is_correct': bool(selected and selected == correct),
    }


def validate_quiz_submission(form, expected_question_ids):
    valid_answers = {'A', 'B', 'C', 'D'}
    expected_ids = set(str(question_id) for question_id in expected_question_ids)
    submitted_answers = {}
    duplicate_ids = []
    unknown_ids = []
    unexpected_fields = []
    invalid_answers = []

    for field_name, values in form.lists():
        if field_name == 'csrf_token':
            continue
        if not field_name.startswith('question_'):
            unexpected_fields.append(field_name)
            continue

        question_id = field_name[len('question_'):]
        if not question_id.isdigit() or question_id not in expected_ids:
            unknown_ids.append(field_name)
            continue
        if len(values) != 1:
            duplicate_ids.append(question_id)
            continue

        answer = values[0]
        if answer not in valid_answers:
            invalid_answers.append(question_id)
            continue
        submitted_answers[question_id] = answer

    missing_ids = [
        question_id for question_id in sorted(expected_ids, key=int)
        if question_id not in submitted_answers
    ]

    if duplicate_ids:
        return False, 'Duplicate answers for one or more quiz questions are not allowed.', submitted_answers
    if unknown_ids:
        return False, 'The submission contains unknown question IDs.', submitted_answers
    if unexpected_fields:
        return False, 'The submission contains unexpected fields.', submitted_answers
    if invalid_answers:
        return False, 'Answers must be one of A, B, C, or D.', submitted_answers
    if missing_ids:
        return False, 'Missing answers for one or more quiz questions.', submitted_answers

    return True, None, submitted_answers


def is_session_role_valid(expected_role):
    user_id = session.get('user_id')
    session_role = session.get('role')
    if not user_id or session_role != expected_role:
        return False

    connection = get_db_connection()
    if not connection:
        return False

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            account = cursor.fetchone()
            if not account or account.get('role') != expected_role:
                return False
            session['role'] = account['role']
            return True
    except pymysql.MySQLError:
        app.logger.exception('Failed to validate protected route authorization')
        return False
    finally:
        close_db_connection(connection)


# Decorator to check if user is logged in
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Decorator to check if user is admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        if not is_session_role_valid('admin'):
            session.clear()
            flash('Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Decorator to check if user is student
def student_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        if not is_session_role_valid('student'):
            session.clear()
            flash('Student access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def record_admin_audit(admin_user_id, action_type, entity_type=None, entity_id=None, details=None):
    """Write one admin audit event without interrupting the triggering operation."""
    if not admin_user_id:
        return False

    connection = get_db_connection()
    if not connection:
        app.logger.warning('Admin audit log connection failed for action %s', action_type)
        return False

    try:
        safe_details = str(details)[:500] if details else None
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO admin_audit_logs
                    (admin_user_id, action_type, entity_type, entity_id, details)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (admin_user_id, action_type, entity_type, entity_id, safe_details),
            )
            connection.commit()
            return True
    except pymysql.MySQLError:
        rollback_db(connection)
        app.logger.exception('Failed to record admin audit action %s', action_type)
        return False
    finally:
        close_db_connection(connection)


def duplicate_quiz_in_transaction(cursor, source_quiz_id, admin_user_id):
    cursor.execute(
        """
        SELECT id, title, description, category, time_limit_minutes
        FROM quizzes
        WHERE id = %s
        """,
        (source_quiz_id,),
    )
    source_quiz = cursor.fetchone()
    if not source_quiz:
        return None

    time_limit = validate_quiz_time_limit(source_quiz.get('time_limit_minutes'))
    if time_limit is None:
        raise ValueError('The source quiz has an invalid time limit.')

    copied_title = f"{source_quiz['title']} (Copy)"
    copied_title = copied_title[:100]

    cursor.execute(
        """
        INSERT INTO quizzes
            (title, description, category, time_limit_minutes, created_by,
             available_from, available_until)
        VALUES (%s, %s, %s, %s, %s, NULL, NULL)
        """,
        (
            copied_title,
            source_quiz.get('description'),
            source_quiz.get('category'),
            time_limit,
            admin_user_id,
        ),
    )
    new_quiz_id = cursor.lastrowid
    if not new_quiz_id:
        raise pymysql.MySQLError('Failed to create duplicated quiz.')

    cursor.execute(
        """
        SELECT question_text, option_a, option_b, option_c, option_d, correct_option
        FROM questions
        WHERE quiz_id = %s
        ORDER BY id ASC
        """,
        (source_quiz_id,),
    )
    source_questions = cursor.fetchall()

    for question in source_questions:
        cursor.execute(
            """
            INSERT INTO questions
                (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                new_quiz_id,
                question['question_text'],
                question['option_a'],
                question['option_b'],
                question['option_c'],
                question['option_d'],
                question['correct_option'],
            ),
        )

    return {
        'id': new_quiz_id,
        'title': copied_title,
        'question_count': len(source_questions),
    }

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        if session.get('role') == 'student':
            return redirect(url_for('student_dashboard'))
        session.clear()
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'])
@limiter.limit('50 per hour', methods=['POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        conn = get_db_connection()
        if not conn:
            flash('Database connection failed. Please check your setup.', 'danger')
            return render_template('login.html')

        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
                user = cursor.fetchone()

                if user and user.get('role') in ('admin', 'student') and check_password_hash(user['password'], password):
                    session.clear()
                    session['user_id'] = user['id']
                    session['username'] = user['username']
                    session['role'] = user['role']
                    
                    flash('Logged in successfully.', 'success')
                    if user['role'] == 'admin':
                        record_admin_audit(
                            user['id'],
                            'ADMIN_LOGIN',
                            details='Administrator logged in',
                        )
                        return redirect(url_for('admin_dashboard'))
                    else:
                        return redirect(url_for('student_dashboard'))
                else:
                    flash('Invalid username or password', 'danger')
        finally:
            conn.close()

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
@limiter.limit('20 per hour', methods=['POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        role = 'student' # Force all new registrations to be students

        if not username or not password or not confirm_password:
            flash('All fields are required.', 'danger')
            return render_template('register.html')

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('register.html')

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        if not conn:
            flash('Database connection failed.', 'danger')
            return render_template('register.html')

        try:
            with conn.cursor() as cursor:
                # Check if username exists
                cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cursor.fetchone():
                    flash('Username already exists.', 'warning')
                    return render_template('register.html')

                cursor.execute(
                    "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
                    (username, hashed_password, role)
                )
                conn.commit()
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('login'))
        except pymysql.MySQLError:
            rollback_db(conn)
            flash_database_error('registration')
        finally:
            conn.close()

    return render_template('register.html')

@app.route('/logout', methods=['POST'])
@limiter.limit('20 per minute', methods=['POST'])
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# --- ADMIN ROUTES ---

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    stats = {
        'students': 0,
        'quizzes': 0,
        'questions': 0,
        'attempts': 0,
        'average_score': None,
    }
    recent_quizzes = []
    quiz_performance = []
    popular_quizzes = []
    recent_registrations = []
    recent_quiz_activity = []

    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM users WHERE role = 'student') AS students,
                        (SELECT COUNT(*) FROM quizzes) AS quizzes,
                        (SELECT COUNT(*) FROM questions) AS questions,
                        (SELECT COUNT(*) FROM quiz_attempts) AS attempts,
                        (
                            SELECT AVG(r.score * 100.0 / NULLIF(r.total_questions, 0))
                            FROM results r
                        ) AS average_score
                """)
                summary = cursor.fetchone() or {}
                stats.update({
                    'students': summary.get('students', 0) or 0,
                    'quizzes': summary.get('quizzes', 0) or 0,
                    'questions': summary.get('questions', 0) or 0,
                    'attempts': summary.get('attempts', 0) or 0,
                    'average_score': summary.get('average_score'),
                })

                cursor.execute("""
                    SELECT id, title, description, category, time_limit_minutes, created_at
                    FROM quizzes
                    ORDER BY created_at DESC, id DESC
                    LIMIT 5
                """)
                recent_quizzes = cursor.fetchall()

                cursor.execute("""
                    SELECT q.id,
                           q.title,
                           q.category,
                           COUNT(r.id) AS submission_count,
                           AVG(
                               r.score * 100.0 / NULLIF(r.total_questions, 0)
                           ) AS average_percentage,
                           MAX(r.score) AS highest_score,
                           MIN(r.score) AS lowest_score
                    FROM quizzes q
                    LEFT JOIN results r ON r.quiz_id = q.id
                    GROUP BY q.id, q.title, q.category
                    ORDER BY q.title ASC
                """)
                quiz_performance = cursor.fetchall()

                cursor.execute("""
                    SELECT q.id,
                           q.title,
                           q.category,
                           COUNT(r.id) AS attempt_count,
                           AVG(r.score * 100.0 / NULLIF(r.total_questions, 0)) AS average_percentage
                    FROM quizzes q
                    JOIN results r ON r.quiz_id = q.id
                    GROUP BY q.id, q.title, q.category
                    ORDER BY attempt_count DESC, q.title ASC
                    LIMIT 5
                """)
                popular_quizzes = cursor.fetchall()

                cursor.execute("""
                    SELECT id, username, created_at
                    FROM users
                    WHERE role = 'student'
                    ORDER BY created_at DESC, id DESC
                    LIMIT 5
                """)
                recent_registrations = cursor.fetchall()

                cursor.execute("""
                    SELECT r.id,
                           r.score,
                           r.total_questions,
                           r.date_taken,
                           r.score * 100.0 / NULLIF(r.total_questions, 0) AS percentage,
                           u.username AS student_username,
                           q.title AS quiz_title
                    FROM results r
                    JOIN users u ON u.id = r.user_id
                    JOIN quizzes q ON q.id = r.quiz_id
                    WHERE u.role = 'student'
                    ORDER BY r.date_taken DESC, r.id DESC
                    LIMIT 5
                """)
                recent_quiz_activity = cursor.fetchall()
        except pymysql.MySQLError:
            flash_database_error('admin dashboard analytics')
        finally:
            conn.close()
    else:
        flash_database_error('admin dashboard analytics')

    return render_template(
        'admin_dashboard.html',
        stats=stats,
        quizzes=recent_quizzes,
        quiz_performance=quiz_performance,
        popular_quizzes=popular_quizzes,
        recent_registrations=recent_registrations,
        recent_quiz_activity=recent_quiz_activity,
    )

@app.route('/admin/audit-log')
@admin_required
def admin_audit_log():
    per_page = 20
    raw_page = request.args.get('page', '1').strip()
    try:
        page = int(raw_page)
        if page < 1:
            raise ValueError
    except (TypeError, ValueError):
        page = 1
        flash('Invalid page number. Showing page 1.', 'warning')

    search = request.args.get('search', '').strip()[:100]
    action_type = request.args.get('action_type', '').strip()[:100]
    entity_type = request.args.get('entity_type', '').strip()[:50]
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()

    parsed_start = None
    parsed_end = None
    try:
        parsed_start = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
    except ValueError:
        flash('Invalid start date. Please use YYYY-MM-DD.', 'warning')
        start_date = ''
    try:
        parsed_end = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
    except ValueError:
        flash('Invalid end date. Please use YYYY-MM-DD.', 'warning')
        end_date = ''
    if parsed_start and parsed_end and parsed_start > parsed_end:
        flash('Start date must be before or equal to end date.', 'warning')
        parsed_start = None
        parsed_end = None
        start_date = ''
        end_date = ''

    where_sql = []
    where_params = []
    if search:
        where_sql.append('(u.username LIKE %s OR a.action_type LIKE %s)')
        search_pattern = f'%{search}%'
        where_params.extend([search_pattern, search_pattern])
    if action_type:
        where_sql.append('a.action_type = %s')
        where_params.append(action_type)
    if entity_type:
        where_sql.append('a.entity_type = %s')
        where_params.append(entity_type)
    if parsed_start:
        where_sql.append('a.created_at >= %s')
        where_params.append(datetime.combine(parsed_start, datetime.min.time()))
    if parsed_end:
        where_sql.append('a.created_at < %s')
        where_params.append(datetime.combine(parsed_end + timedelta(days=1), datetime.min.time()))

    where_clause = (' WHERE ' + ' AND '.join(where_sql)) if where_sql else ''

    logs = []
    total_logs = 0
    action_types = []
    entity_types = []
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT action_type AS value
                    FROM admin_audit_logs
                    WHERE action_type IS NOT NULL AND action_type <> ''
                    ORDER BY action_type
                    """
                )
                action_types = [
                    row['value'] for row in cursor.fetchall()
                    if isinstance(row, dict) and row.get('value')
                ]

                cursor.execute(
                    """
                    SELECT DISTINCT entity_type AS value
                    FROM admin_audit_logs
                    WHERE entity_type IS NOT NULL AND entity_type <> ''
                    ORDER BY entity_type
                    """
                )
                entity_types = [
                    row['value'] for row in cursor.fetchall()
                    if isinstance(row, dict) and row.get('value')
                ]

                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM admin_audit_logs a
                    LEFT JOIN users u ON u.id = a.admin_user_id
                    {where_clause}
                    """,
                    tuple(where_params),
                )
                count_row = cursor.fetchone()
                total_logs = count_row['count'] if count_row else 0

                total_pages = max(1, (total_logs + per_page - 1) // per_page)
                if page > total_pages:
                    page = total_pages
                cursor.execute(
                    f"""
                    SELECT a.id,
                           a.admin_user_id,
                           a.action_type,
                           a.entity_type,
                           a.entity_id,
                           a.details,
                           a.created_at,
                           u.username AS admin_username
                    FROM admin_audit_logs a
                    LEFT JOIN users u ON u.id = a.admin_user_id
                    {where_clause}
                    ORDER BY a.created_at DESC, a.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple(where_params + [per_page, (page - 1) * per_page]),
                )
                logs = cursor.fetchall()
        except pymysql.MySQLError:
            flash_database_error('audit log lookup')
        finally:
            close_db_connection(conn)
    else:
        flash('The database could not complete your request. Please try again.', 'danger')

    total_pages = max(1, (total_logs + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    return render_template(
        'admin_audit_log.html',
        logs=logs,
        page=page,
        total_pages=total_pages,
        total_logs=total_logs,
        search=search,
        action_type=action_type,
        entity_type=entity_type,
        start_date=start_date,
        end_date=end_date,
        action_types=action_types,
        entity_types=entity_types,
    )


@app.route('/admin/question-bank', methods=['GET', 'POST'])
@limiter.limit('30 per minute', methods=['POST'])
@admin_required
def manage_question_bank():
    if request.method == 'POST':
        action = request.form.get('action')
        conn = get_db_connection()
        if not conn:
            flash('Database connection failed. Please try again.', 'danger')
            return redirect(url_for('manage_question_bank'))

        if action in ('create', 'edit'):
            valid, values, validation_error = validate_question_bank_payload(
                category=request.form.get('category'),
                difficulty=request.form.get('difficulty'),
                question_text=request.form.get('question_text'),
                option_a=request.form.get('option_a'),
                option_b=request.form.get('option_b'),
                option_c=request.form.get('option_c'),
                option_d=request.form.get('option_d'),
                correct_option=request.form.get('correct_option'),
            )
            if not valid:
                close_db_connection(conn)
                flash(validation_error, 'danger')
                return redirect(url_for('manage_question_bank'))

            try:
                with conn.cursor() as cursor:
                    if action == 'create':
                        cursor.execute(
                            """
                            INSERT INTO question_bank
                                (question_text, option_a, option_b, option_c, option_d,
                                 correct_option, category, difficulty, created_by)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                values['question_text'], values['option_a'],
                                values['option_b'], values['option_c'],
                                values['option_d'], values['correct_option'],
                                values['category'], values['difficulty'],
                                session['user_id'],
                            ),
                        )
                        bank_id = cursor.lastrowid
                        audit_type = 'QUESTION_BANK_CREATE'
                        details = (
                            f'Created reusable question ID {bank_id} '
                            f'in category "{values["category"]}"'
                        )
                        success_message = 'Question added to the question bank.'
                    else:
                        try:
                            bank_id = int(request.form.get('question_id'))
                        except (TypeError, ValueError):
                            close_db_connection(conn)
                            flash('Invalid question selection.', 'danger')
                            return redirect(url_for('manage_question_bank'))
                        cursor.execute(
                            """
                            UPDATE question_bank
                            SET question_text=%s, option_a=%s, option_b=%s, option_c=%s,
                                option_d=%s, correct_option=%s, category=%s, difficulty=%s
                            WHERE id=%s
                            """,
                            (
                                values['question_text'], values['option_a'],
                                values['option_b'], values['option_c'],
                                values['option_d'], values['correct_option'],
                                values['category'], values['difficulty'], bank_id,
                            ),
                        )
                        if cursor.rowcount == 0:
                            rollback_db(conn)
                            close_db_connection(conn)
                            flash('Question-bank question not found.', 'danger')
                            return redirect(url_for('manage_question_bank'))
                        audit_type = 'QUESTION_BANK_UPDATE'
                        details = f'Modified reusable question ID {bank_id}'
                        success_message = 'Question-bank question updated.'

                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        audit_type,
                        'question_bank',
                        bank_id,
                        details,
                    )
                    flash(success_message, 'success')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('question-bank operation')
            finally:
                close_db_connection(conn)

        elif action in ('bulk_delete', 'delete_selected', 'bulk-delete'):
            raw_ids = request.form.getlist('question_ids') or request.form.getlist('selected_ids')
            if not raw_ids:
                raw_ids = request.form.getlist('question_id')
            selected_ids = normalize_question_bank_ids(raw_ids)
            if selected_ids is None:
                close_db_connection(conn)
                flash('Select at least one valid question.', 'danger')
                return redirect(url_for('manage_question_bank'))

            placeholders = ', '.join(['%s'] * len(selected_ids))
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT id FROM question_bank WHERE id IN ({placeholders})",
                        tuple(selected_ids),
                    )
                    existing_ids = [row['id'] for row in cursor.fetchall() if row.get('id')]
                    if not existing_ids:
                        rollback_db(conn)
                        close_db_connection(conn)
                        flash('No selected question-bank questions were found.', 'danger')
                        return redirect(url_for('manage_question_bank'))

                    placeholders = ', '.join(['%s'] * len(existing_ids))
                    cursor.execute(
                        f"DELETE FROM question_bank WHERE id IN ({placeholders})",
                        tuple(existing_ids),
                    )
                    deleted_count = cursor.rowcount
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUESTION_BANK_BULK_DELETE',
                        'question_bank',
                        None,
                        f'Deleted {deleted_count} reusable question(s): IDs {", ".join(str(i) for i in existing_ids)}',
                    )
                    if deleted_count == len(selected_ids):
                        flash(f'{deleted_count} question(s) deleted from the question bank.', 'success')
                    else:
                        flash(
                            f'{deleted_count} question(s) deleted; some selected IDs were not found.',
                            'warning',
                        )
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('bulk question-bank deletion')
            finally:
                close_db_connection(conn)

        elif action in ('bulk_update', 'update_selected', 'bulk-update'):
            raw_ids = request.form.getlist('question_ids') or request.form.getlist('selected_ids')
            if not raw_ids:
                raw_ids = request.form.getlist('question_id')
            selected_ids = normalize_question_bank_ids(raw_ids)
            if selected_ids is None:
                close_db_connection(conn)
                flash('Select at least one valid question.', 'danger')
                return redirect(url_for('manage_question_bank'))

            category = str(
                request.form.get('bulk_category', request.form.get('category', ''))
            ).strip()
            difficulty = str(
                request.form.get('bulk_difficulty', request.form.get('difficulty', ''))
            ).strip().lower()
            updates = {}
            if category:
                if len(category) > 50:
                    close_db_connection(conn)
                    flash('Category must be 50 characters or fewer.', 'danger')
                    return redirect(url_for('manage_question_bank'))
                updates['category'] = category
            if difficulty:
                if difficulty not in ('easy', 'medium', 'hard'):
                    close_db_connection(conn)
                    flash('Difficulty must be easy, medium, or hard.', 'danger')
                    return redirect(url_for('manage_question_bank'))
                updates['difficulty'] = difficulty
            if not updates:
                close_db_connection(conn)
                flash('Choose a category or difficulty to update.', 'danger')
                return redirect(url_for('manage_question_bank'))

            placeholders = ', '.join(['%s'] * len(selected_ids))
            assignments = ', '.join([f'{column}=%s' for column in updates])
            params = list(updates.values()) + selected_ids
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"SELECT id FROM question_bank WHERE id IN ({placeholders})",
                        tuple(selected_ids),
                    )
                    existing_ids = [row['id'] for row in cursor.fetchall() if row.get('id')]
                    if not existing_ids:
                        rollback_db(conn)
                        close_db_connection(conn)
                        flash('No selected question-bank questions were found.', 'warning')
                        return redirect(url_for('manage_question_bank'))

                    placeholders = ', '.join(['%s'] * len(existing_ids))
                    cursor.execute(
                        f"UPDATE question_bank SET {assignments} WHERE id IN ({placeholders})",
                        tuple(list(updates.values()) + existing_ids),
                    )
                    updated_count = cursor.rowcount
                    conn.commit()
                    if updated_count == 0:
                        flash('No selected question-bank questions were found.', 'warning')
                    else:
                        changed_fields = ', '.join(
                            f'{column}="{value}"' for column, value in updates.items()
                        )
                        record_admin_audit(
                            session['user_id'],
                            'QUESTION_BANK_BULK_UPDATE',
                            'question_bank',
                            None,
                            f'Updated {updated_count} reusable question(s): {changed_fields}; IDs {", ".join(str(i) for i in existing_ids)}',
                        )
                        flash(
                            f'{updated_count} question(s) updated.',
                            'success',
                        )
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('bulk question-bank update')
            finally:
                close_db_connection(conn)

        elif action == 'delete':
            try:
                bank_id = int(request.form.get('question_id'))
            except (TypeError, ValueError):
                close_db_connection(conn)
                flash('Invalid question selection.', 'danger')
                return redirect(url_for('manage_question_bank'))
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM question_bank WHERE id=%s", (bank_id,))
                    if cursor.rowcount == 0:
                        rollback_db(conn)
                        close_db_connection(conn)
                        flash('Question-bank question not found.', 'danger')
                        return redirect(url_for('manage_question_bank'))
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUESTION_BANK_DELETE',
                        'question_bank',
                        bank_id,
                        f'Deleted reusable question ID {bank_id}',
                    )
                    flash('Question removed from the question bank.', 'info')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('question-bank deletion')
            finally:
                close_db_connection(conn)
        else:
            close_db_connection(conn)
            flash('Invalid question-bank action.', 'danger')

        return redirect(url_for('manage_question_bank'))

    search = request.args.get('search', '').strip()
    category_filter = request.args.get('category', '').strip()
    difficulty_filter = request.args.get('difficulty', '').strip().lower()
    if difficulty_filter not in ('', 'easy', 'medium', 'hard'):
        difficulty_filter = ''
    page = parse_positive_page(request.args.get('page'))
    per_page = 20

    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('admin_dashboard'))

    questions = []
    categories = []
    total_questions = 0
    total_pages = 0
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT category FROM question_bank ORDER BY category ASC"
            )
            categories = [row['category'] for row in cursor.fetchall()]

            clauses = []
            params = []
            if search:
                clauses.append(
                    '(question_text LIKE %s OR option_a LIKE %s OR option_b LIKE %s '
                    'OR option_c LIKE %s OR option_d LIKE %s)'
                )
                term = f'%{search}%'
                params.extend([term, term, term, term, term])
            if category_filter:
                clauses.append('category = %s')
                params.append(category_filter)
            if difficulty_filter:
                clauses.append('difficulty = %s')
                params.append(difficulty_filter)

            where_sql = ''
            if clauses:
                where_sql = ' WHERE ' + ' AND '.join(clauses)

            cursor.execute(
                'SELECT COUNT(*) AS count FROM question_bank' + where_sql,
                tuple(params),
            )
            count_row = cursor.fetchone()
            total_questions = int(count_row['count']) if count_row and count_row.get('count') is not None else 0
            total_pages = (total_questions + per_page - 1) // per_page
            if total_pages == 0:
                page = 1
            elif page > total_pages:
                page = total_pages

            offset = (page - 1) * per_page
            query = (
                'SELECT * FROM question_bank' + where_sql
                + ' ORDER BY category ASC, difficulty ASC, created_at DESC, id DESC'
                + ' LIMIT %s OFFSET %s'
            )
            cursor.execute(query, tuple(params + [per_page, offset]))
            questions = cursor.fetchall()
    except pymysql.MySQLError:
        flash_database_error('question-bank lookup')
    finally:
        close_db_connection(conn)

    return render_template(
        'manage_question_bank.html',
        questions=questions,
        categories=categories,
        search=search,
        category_filter=category_filter,
        difficulty_filter=difficulty_filter,
        page=page,
        total_pages=total_pages,
        total_questions=total_questions,
        per_page=per_page,
    )


@app.route('/admin/question-bank/export.csv')
@admin_required
def export_question_bank_csv():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('manage_question_bank'))

    questions = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT question_text, option_a, option_b, option_c, option_d,
                       correct_option, category, difficulty
                FROM question_bank
                ORDER BY category ASC, difficulty ASC, id ASC
            """)
            questions = cursor.fetchall()
    except pymysql.MySQLError:
        flash_database_error('question-bank export')
        close_db_connection(conn)
        return redirect(url_for('manage_question_bank'))
    finally:
        close_db_connection(conn)

    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(QUESTION_BANK_CSV_HEADERS)
    for question in questions:
        writer.writerow([
            sanitize_csv_cell(question.get('question_text')),
            sanitize_csv_cell(question.get('option_a')),
            sanitize_csv_cell(question.get('option_b')),
            sanitize_csv_cell(question.get('option_c')),
            sanitize_csv_cell(question.get('option_d')),
            sanitize_csv_cell(question.get('correct_option')),
            sanitize_csv_cell(question.get('category')),
            sanitize_csv_cell(question.get('difficulty')),
        ])

    record_admin_audit(
        session['user_id'],
        'QUESTION_BANK_CSV_EXPORT',
        'question_bank',
        details=f'Exported {len(questions)} question-bank question(s)',
    )
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename="question_bank.csv"',
        },
    )


@app.route('/admin/question-bank/import', methods=['POST'])
@limiter.limit('10 per minute', methods=['POST'])
@admin_required
def import_question_bank_csv():
    upload = request.files.get('csv_file')
    if upload is None or not upload.filename:
        flash('Please select a CSV file to import.', 'danger')
        return redirect(url_for('manage_question_bank'))

    rows, invalid_rows, _, parse_error = parse_question_bank_csv(upload)
    if parse_error:
        flash(parse_error, 'danger')
        return redirect(url_for('manage_question_bank'))

    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('manage_question_bank'))

    imported = 0
    skipped = 0
    seen = set()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            for values in rows:
                fingerprint = tuple(
                    values[field]
                    for field in QUESTION_BANK_CSV_HEADERS
                )
                if fingerprint in seen:
                    skipped += 1
                    continue
                seen.add(fingerprint)

                cursor.execute(
                    """
                    SELECT COUNT(*) AS count FROM question_bank
                    WHERE question_text=%s AND option_a=%s AND option_b=%s
                      AND option_c=%s AND option_d=%s AND correct_option=%s
                      AND category=%s AND difficulty=%s
                    """,
                    fingerprint,
                )
                duplicate = cursor.fetchone()
                if duplicate and duplicate.get('count', 0) > 0:
                    skipped += 1
                    continue

                cursor.execute(
                    """
                    INSERT INTO question_bank
                        (question_text, option_a, option_b, option_c, option_d,
                         correct_option, category, difficulty, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        values['question_text'], values['option_a'],
                        values['option_b'], values['option_c'],
                        values['option_d'], values['correct_option'],
                        values['category'], values['difficulty'],
                        session['user_id'],
                    ),
                )
                imported += 1
        conn.commit()
        record_admin_audit(
            session['user_id'],
            'QUESTION_BANK_CSV_IMPORT',
            'question_bank',
            details=(
                f'CSV import summary: {imported} imported, '
                f'{skipped} skipped, {len(invalid_rows)} invalid'
            ),
        )
    except (pymysql.MySQLError, ValueError, TypeError):
        rollback_db(conn)
        imported = 0
        skipped = 0
        flash_database_error('question-bank CSV import')
    finally:
        close_db_connection(conn)

    if imported or skipped or invalid_rows:
        flash(
            (
                f'CSV import summary: {imported} imported, '
                f'{skipped} skipped, {len(invalid_rows)} invalid.'
            ),
            'success' if imported and not invalid_rows else 'warning',
        )
        for invalid in invalid_rows[:10]:
            flash(f"Row {invalid['row']}: {invalid['error']}", 'danger')
        if len(invalid_rows) > 10:
            flash(f'{len(invalid_rows) - 10} additional invalid row(s) omitted.', 'danger')
    return redirect(url_for('manage_question_bank'))


@app.route('/admin/quizzes/<int:quiz_id>/duplicate', methods=['POST'])
@limiter.limit('30 per minute', methods=['POST'])
@admin_required
def duplicate_quiz(quiz_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('manage_quizzes'))

    try:
        conn.begin()
        with conn.cursor() as cursor:
            duplicated = duplicate_quiz_in_transaction(
                cursor,
                quiz_id,
                session['user_id'],
            )
            if not duplicated:
                rollback_db(conn)
                flash('Quiz not found.', 'danger')
                return redirect(url_for('manage_quizzes'))

        conn.commit()
        record_admin_audit(
            session['user_id'],
            'QUIZ_DUPLICATE',
            'quiz',
            duplicated['id'],
            (
                f'Duplicated quiz ID {quiz_id} as "{duplicated["title"]}" '
                f'with {duplicated["question_count"]} question(s)'
            ),
        )
        flash(f'Quiz duplicated successfully as "{duplicated["title"]}".', 'success')
    except (pymysql.MySQLError, ValueError, TypeError):
        rollback_db(conn)
        app.logger.exception('Failed to duplicate quiz ID %s', quiz_id)
        flash_database_error('quiz duplication')
    finally:
        close_db_connection(conn)

    return redirect(url_for('manage_quizzes'))


@app.route('/admin/quizzes', methods=['GET', 'POST'])
@limiter.limit('30 per minute', methods=['POST'])
@admin_required
def manage_quizzes():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_from_bank':
            try:
                with conn.cursor() as cursor:
                    copied_count = copy_question_bank_to_quiz(
                        cursor,
                        quiz_id,
                        request.form.getlist('bank_question_ids'),
                    )
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('question-bank import')
                close_db_connection(conn)
                return redirect(url_for('manage_questions', quiz_id=quiz_id))
            conn.commit()
            record_admin_audit(
                session['user_id'],
                'QUESTION_BANK_ADD_TO_QUIZ',
                'quiz',
                quiz_id,
                f'Added {copied_count} question-bank question(s) to quiz ID {quiz_id}',
            )
            if copied_count:
                flash(f'{copied_count} question(s) copied from the question bank.', 'success')
            else:
                flash('No valid question-bank questions were selected.', 'warning')
            close_db_connection(conn)
            return redirect(url_for('manage_questions', quiz_id=quiz_id))

        if action == 'create':
            title = request.form.get('title')
            category = request.form.get('category')
            time_limit = validate_quiz_time_limit(request.form.get('time_limit', 15))
            description = request.form.get('description')
            valid_schedule, available_from, available_until, schedule_error = validate_quiz_schedule(
                request.form.get('available_from'),
                request.form.get('available_until'),
            )

            if time_limit is None:
                close_db_connection(conn)
                flash('Time limit must be a positive whole number.', 'danger')
                return redirect(url_for('manage_quizzes'))
            if not valid_schedule:
                close_db_connection(conn)
                flash(schedule_error, 'danger')
                return redirect(url_for('manage_quizzes'))
            
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO quizzes (title, category, time_limit_minutes, description, created_by, available_from, available_until) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (title, category, time_limit, description, session['user_id'], available_from, available_until)
                    )
                    quiz_id = cursor.lastrowid
                    copied_count = copy_question_bank_to_quiz(
                        cursor,
                        quiz_id,
                        request.form.getlist('bank_question_ids'),
                    )
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUIZ_CREATE',
                        'quiz',
                        quiz_id,
                        (
                            f'Created quiz "{title}"'
                            + (f' with {copied_count} question-bank question(s)' if copied_count else '')
                        ),
                    )
                    flash('Quiz created successfully!', 'success')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('quiz creation')

        elif action == 'edit':
            quiz_id = request.form.get('quiz_id')
            title = request.form.get('title')
            category = request.form.get('category')
            time_limit = validate_quiz_time_limit(request.form.get('time_limit'))
            description = request.form.get('description')
            valid_schedule, available_from, available_until, schedule_error = validate_quiz_schedule(
                request.form.get('available_from'),
                request.form.get('available_until'),
            )

            if time_limit is None or not quiz_id:
                close_db_connection(conn)
                flash('Quiz ID and a valid time limit are required.', 'danger')
                return redirect(url_for('manage_quizzes'))
            if not valid_schedule:
                close_db_connection(conn)
                flash(schedule_error, 'danger')
                return redirect(url_for('manage_quizzes'))

            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE quizzes
                        SET title=%s, category=%s, time_limit_minutes=%s, description=%s,
                            available_from=%s, available_until=%s
                        WHERE id=%s
                        """,
                        (title, category, time_limit, description, available_from, available_until, quiz_id),
                    )
                    copied_count = copy_question_bank_to_quiz(
                        cursor,
                        int(quiz_id),
                        request.form.getlist('bank_question_ids'),
                    )
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUIZ_UPDATE',
                        'quiz',
                        int(quiz_id),
                        (
                            f'Modified quiz "{title}"'
                            + (f'; appended {copied_count} question-bank question(s)' if copied_count else '')
                        ),
                    )
                    flash('Quiz updated successfully!', 'success')
            except (pymysql.MySQLError, ValueError, TypeError):
                rollback_db(conn)
                flash_database_error('quiz update')
                
        elif action == 'delete':
            quiz_id = request.form.get('quiz_id')
            try:
                quiz_entity_id = int(quiz_id)
            except (TypeError, ValueError):
                close_db_connection(conn)
                flash('Invalid quiz selection.', 'danger')
                return redirect(url_for('manage_quizzes'))
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM quizzes WHERE id = %s", (quiz_id,))
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUIZ_DELETE',
                        'quiz',
                        quiz_entity_id,
                        'Deleted quiz',
                    )
                    flash('Quiz deleted successfully.', 'info')
            except (pymysql.MySQLError, ValueError, TypeError):
                rollback_db(conn)
                flash_database_error('quiz deletion')

        close_db_connection(conn)
        return redirect(url_for('manage_quizzes'))

    quizzes = []
    categories = []
    question_bank = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM quizzes ORDER BY created_at DESC")
            quizzes = cursor.fetchall()
            
            cursor.execute("SELECT DISTINCT category FROM quizzes")
            categories = [row['category'] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT id, question_text, category, difficulty
                FROM question_bank
                ORDER BY category ASC, difficulty ASC, id ASC
                """
            )
            question_bank = cursor.fetchall()
    finally:
        conn.close()

    return render_template(
        'manage_quizzes.html',
        quizzes=quizzes,
        categories=categories,
        question_bank=question_bank,
    )

@app.route('/admin/results/export')
@app.route('/admin/quizzes/<int:quiz_id>/export')
@admin_required
def export_quiz_results(quiz_id=None):
    if quiz_id is None:
        quiz_id_value = request.args.get('quiz_id')
        if quiz_id_value:
            try:
                quiz_id = int(quiz_id_value)
            except (TypeError, ValueError):
                flash('Invalid quiz selection.', 'danger')
                return redirect(url_for('manage_quizzes'))

    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('admin_dashboard'))

    rows = []
    quiz_title = 'all_quiz_results'
    try:
        with conn.cursor() as cursor:
            query = """
                SELECT q.title AS quiz_name,
                       u.username AS student_name,
                       r.score,
                       r.total_questions,
                       r.score * 100.0 / NULLIF(r.total_questions, 0) AS percentage,
                       r.date_taken AS submission_date
                FROM results r
                JOIN quizzes q ON q.id = r.quiz_id
                JOIN users u ON u.id = r.user_id
                WHERE u.role = 'student'
            """
            params = None
            if quiz_id is not None:
                query += " AND r.quiz_id = %s"
                params = (quiz_id,)

                cursor.execute(
                    "SELECT title FROM quizzes WHERE id = %s",
                    (quiz_id,),
                )
                selected_quiz = cursor.fetchone()
                if selected_quiz:
                    quiz_title = str(selected_quiz['title']).strip().lower()
                    quiz_title = ''.join(
                        character if character.isalnum() else '_'
                        for character in quiz_title
                    ).strip('_') or 'quiz_results'
                else:
                    flash('Quiz not found.', 'danger')
                    return redirect(url_for('manage_quizzes'))

            query += " ORDER BY r.date_taken DESC, q.title ASC, u.username ASC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
    except pymysql.MySQLError:
        rollback_db(conn)
        flash_database_error('quiz results export')
        return redirect(url_for('admin_dashboard'))
    finally:
        close_db_connection(conn)

    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([
        'Quiz Name',
        'Student Name',
        'Score',
        'Total Marks',
        'Percentage',
        'Submission Date',
    ])
    for row in rows:
        submission_date = row['submission_date']
        if hasattr(submission_date, 'strftime'):
            submission_date = submission_date.strftime('%Y-%m-%d %H:%M:%S')

        percentage = row['percentage']
        if percentage is not None:
            percentage = f"{float(percentage):.1f}%"

        writer.writerow([
            sanitize_csv_cell(row['quiz_name']),
            sanitize_csv_cell(row['student_name']),
            sanitize_csv_cell(row['score']),
            sanitize_csv_cell(row['total_questions']),
            sanitize_csv_cell(percentage),
            sanitize_csv_cell(submission_date),
        ])

    filename = f"{quiz_title}_results.csv" if quiz_id is not None else 'all_quiz_results.csv'
    if quiz_id is not None:
        record_admin_audit(
            session['user_id'],
            'QUIZ_RESULTS_CSV_EXPORT',
            'quiz',
            quiz_id,
            f'Exported quiz results CSV for quiz ID {quiz_id}; {len(rows)} result(s)',
        )
    else:
        record_admin_audit(
            session['user_id'],
            'QUIZ_RESULTS_CSV_EXPORT',
            details=f'Exported all quiz results CSV; {len(rows)} result(s)',
        )
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
        },
    )

@app.route('/admin/quizzes/<int:quiz_id>/questions', methods=['GET', 'POST'])
@limiter.limit('30 per minute', methods=['POST'])
@admin_required
def manage_questions(quiz_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('manage_quizzes'))
    
    # Get quiz details
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM quizzes WHERE id = %s", (quiz_id,))
            quiz = cursor.fetchone()
            if not quiz:
                flash('Quiz not found.', 'danger')
                return redirect(url_for('manage_quizzes'))
    except pymysql.MySQLError:
        flash_database_error('quiz lookup')
        close_db_connection(conn)
        return redirect(url_for('manage_quizzes'))

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create':
            valid, values, validation_error = validate_question_payload(
                request.form.get('question_text'),
                request.form.get('option_a'),
                request.form.get('option_b'),
                request.form.get('option_c'),
                request.form.get('option_d'),
                request.form.get('correct_option'),
            )
            if not valid:
                close_db_connection(conn)
                flash(validation_error, 'danger')
                return redirect(url_for('manage_questions', quiz_id=quiz_id))

            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            quiz_id,
                            values['question_text'],
                            values['option_a'],
                            values['option_b'],
                            values['option_c'],
                            values['option_d'],
                            values['correct_option'],
                        )
                    )
                    question_id = cursor.lastrowid
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUESTION_CREATE',
                        'question',
                        question_id,
                        f'Created question in quiz ID {quiz_id}',
                    )
                    flash('Question added successfully!', 'success')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('question creation')
                
        elif action == 'edit':
            q_id = request.form.get('question_id')
            try:
                question_entity_id = int(q_id)
            except (TypeError, ValueError):
                close_db_connection(conn)
                flash('Invalid question selection.', 'danger')
                return redirect(url_for('manage_questions', quiz_id=quiz_id))
            valid, values, validation_error = validate_question_payload(
                request.form.get('question_text'),
                request.form.get('option_a'),
                request.form.get('option_b'),
                request.form.get('option_c'),
                request.form.get('option_d'),
                request.form.get('correct_option'),
            )
            if not valid:
                close_db_connection(conn)
                flash(validation_error, 'danger')
                return redirect(url_for('manage_questions', quiz_id=quiz_id))
            
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE questions 
                        SET question_text=%s, option_a=%s, option_b=%s, option_c=%s, option_d=%s, correct_option=%s
                        WHERE id=%s AND quiz_id=%s
                    """, (
                        values['question_text'],
                        values['option_a'],
                        values['option_b'],
                        values['option_c'],
                        values['option_d'],
                        values['correct_option'],
                        question_entity_id,
                        quiz_id,
                    ))
                    if cursor.rowcount == 0:
                        rollback_db(conn)
                        close_db_connection(conn)
                        flash('Question not found in this quiz.', 'danger')
                        return redirect(url_for('manage_questions', quiz_id=quiz_id))
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUESTION_UPDATE',
                        'question',
                        question_entity_id,
                        f'Modified question in quiz ID {quiz_id}',
                    )
                    flash('Question updated successfully!', 'success')
            except (pymysql.MySQLError, ValueError, TypeError):
                rollback_db(conn)
                flash_database_error('question update')
                
        elif action == 'delete':
            q_id = request.form.get('question_id')
            try:
                question_entity_id = int(q_id)
            except (TypeError, ValueError):
                close_db_connection(conn)
                flash('Invalid question selection.', 'danger')
                return redirect(url_for('manage_questions', quiz_id=quiz_id))
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM questions WHERE id = %s AND quiz_id = %s", (q_id, quiz_id))
                    if cursor.rowcount == 0:
                        rollback_db(conn)
                        close_db_connection(conn)
                        flash('Question not found in this quiz.', 'danger')
                        return redirect(url_for('manage_questions', quiz_id=quiz_id))
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUESTION_DELETE',
                        'question',
                        question_entity_id,
                        f'Deleted question from quiz ID {quiz_id}',
                    )
                    flash('Question deleted.', 'info')
            except (pymysql.MySQLError, ValueError, TypeError):
                rollback_db(conn)
                flash_database_error('question deletion')
        else:
            close_db_connection(conn)
            flash('Invalid question action.', 'danger')
            return redirect(url_for('manage_questions', quiz_id=quiz_id))

        close_db_connection(conn)
        return redirect(url_for('manage_questions', quiz_id=quiz_id))

    questions = []
    question_bank = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM questions WHERE quiz_id = %s", (quiz_id,))
            questions = cursor.fetchall()
            cursor.execute(
                """
                SELECT id, question_text, category, difficulty
                FROM question_bank
                ORDER BY category ASC, difficulty ASC, id ASC
                """
            )
            question_bank = cursor.fetchall()
    finally:
        conn.close()

    return render_template(
        'manage_questions.html',
        quiz=quiz,
        questions=questions,
        question_bank=question_bank,
    )

@app.route('/admin/students')
@admin_required
def manage_students():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('admin_dashboard'))
    students = []
    try:
        with conn.cursor() as cursor:
            # Query to get students and count their results
            cursor.execute('''
                SELECT u.id, u.username, u.created_at, COUNT(DISTINCT r.quiz_id) as quizzes_taken
                FROM users u
                LEFT JOIN results r ON u.id = r.user_id
                WHERE u.role = 'student'
                GROUP BY u.id
                ORDER BY u.created_at DESC
            ''')
            students = cursor.fetchall()
    finally:
        conn.close()
        
    return render_template('manage_students.html', students=students)

# --- STUDENT ROUTES ---

@app.route('/student/dashboard')
@student_required
def student_dashboard():
    # Show available quizzes
    conn = get_db_connection()
    quizzes = []
    achievements = []
    achievement_error = False
    
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM quizzes ORDER BY created_at DESC")
                quizzes = cursor.fetchall()
                for quiz in quizzes:
                    quiz['availability_status'] = classify_quiz_availability(quiz)
                cursor.execute(
                    "SELECT quiz_id, score, total_questions, date_taken "
                    "FROM results "
                    "WHERE user_id = %s AND total_questions > 0 "
                    "AND score >= 0 AND score <= total_questions "
                    "ORDER BY date_taken ASC",
                    (session['user_id'],),
                )
                achievements = calculate_student_achievements(cursor.fetchall())
        except pymysql.MySQLError:
            rollback_db(conn)
            flash_database_error('loading your achievements')
            achievement_error = True
        finally:
            conn.close()
    else:
        achievement_error = True
        flash_database_error('loading your achievements')

    return render_template(
        'student_dashboard.html',
        quizzes=quizzes,
        achievements=[item for item in achievements if item['earned']][:4],
        achievement_error=achievement_error,
    )

@app.route('/student/quiz/<int:quiz_id>', methods=['GET'])
@student_required
def take_quiz(quiz_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM results WHERE user_id = %s AND quiz_id = %s",
                (session['user_id'], quiz_id),
            )
            if cursor.fetchone():
                flash('You have already taken this quiz. View your results below.', 'warning')
                return redirect(url_for('student_result', quiz_id=quiz_id))

            cursor.execute("SELECT * FROM quizzes WHERE id = %s", (quiz_id,))
            quiz = cursor.fetchone()
            if not quiz:
                flash('Quiz not found.', 'danger')
                return redirect(url_for('student_dashboard'))
            availability_status = classify_quiz_availability(quiz)
            if availability_status == 'upcoming':
                flash('This quiz has not started yet.', 'warning')
                return redirect(url_for('student_dashboard'))
            if availability_status == 'closed':
                flash('This quiz has closed.', 'warning')
                return redirect(url_for('student_dashboard'))

            cursor.execute(
                "SELECT id, question_text, option_a, option_b, option_c, option_d "
                "FROM questions WHERE quiz_id = %s",
                (quiz_id,),
            )
            questions = cursor.fetchall()
            if not questions:
                flash('This quiz has no questions yet.', 'info')
                return redirect(url_for('student_dashboard'))

            time_limit = validate_quiz_time_limit(quiz.get('time_limit_minutes'))
            if time_limit is None:
                flash('This quiz has an invalid time limit.', 'danger')
                return redirect(url_for('student_dashboard'))

            attempt = get_or_create_quiz_attempt(cursor, session['user_id'], quiz_id)
            if not attempt:
                flash('Unable to start this quiz. Please try again.', 'danger')
                return redirect(url_for('student_dashboard'))
            conn.commit()

            status = get_quiz_attempt_status(cursor, session['user_id'], quiz_id)
            if not status:
                flash('Unable to start this quiz. Please try again.', 'danger')
                return redirect(url_for('student_dashboard'))
            if status['expired']:
                flash('The quiz deadline has expired.', 'danger')
                return redirect(url_for('student_dashboard'))

            return render_template('quiz.html', quiz=quiz, questions=questions)
    except pymysql.MySQLError:
        rollback_db(conn)
        flash_database_error('starting quiz attempt')
        return redirect(url_for('student_dashboard'))
    finally:
        close_db_connection(conn)

@app.route('/student/quiz/<int:quiz_id>/submit', methods=['POST'])
@limiter.limit('20 per minute', methods=['POST'])
@student_required
def submit_quiz(quiz_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))
    try:
        with conn.cursor() as cursor:
            status = get_quiz_attempt_status(cursor, session['user_id'], quiz_id)
            if not status:
                flash('You must start the quiz before submitting it.', 'danger')
                return redirect(url_for('take_quiz', quiz_id=quiz_id))

            time_limit = validate_quiz_time_limit(status.get('time_limit_minutes'))
            if time_limit is None:
                flash('This quiz has an invalid time limit.', 'danger')
                return redirect(url_for('student_dashboard'))
            if status['expired']:
                flash('The quiz deadline has expired. Your result was not submitted.', 'danger')
                return redirect(url_for('student_dashboard'))
            if status.get('schedule_closed') == 1:
                flash('This quiz is not currently available. Your result was not submitted.', 'danger')
                return redirect(url_for('student_dashboard'))

            cursor.execute(
                "SELECT id, correct_option FROM questions WHERE quiz_id = %s",
                (quiz_id,),
            )
            questions = cursor.fetchall()
            expected_question_ids = [question['id'] for question in questions]
            if not expected_question_ids:
                flash('This quiz has no questions yet.', 'info')
                return redirect(url_for('student_dashboard'))
            valid_submission, validation_error, submitted_answers = validate_quiz_submission(
                request.form,
                expected_question_ids,
            )
            if not valid_submission:
                flash(validation_error, 'danger')
                return redirect(url_for('take_quiz', quiz_id=quiz_id))

            cursor.execute(
                "SELECT id FROM results WHERE user_id = %s AND quiz_id = %s",
                (session['user_id'], quiz_id),
            )
            if cursor.fetchone():
                return redirect(url_for('student_result', quiz_id=quiz_id))

            score = 0
            total = len(questions)
            for q in questions:
                q_id = str(q['id'])
                selected = submitted_answers[q_id]
                if selected == q['correct_option']:
                    score += 1

            try:
                inserted = insert_quiz_result_if_unexpired(
                    cursor,
                    session['user_id'],
                    quiz_id,
                    score,
                    total,
                )
                if inserted != 1:
                    rollback_db(conn)
                    flash('The quiz deadline has expired. Your result was not submitted.', 'danger')
                    return redirect(url_for('student_dashboard'))

                cursor.execute(
                    "SELECT id FROM results WHERE user_id = %s AND quiz_id = %s",
                    (session['user_id'], quiz_id),
                )
                submitted_result = cursor.fetchone()
                if submitted_result and submitted_result.get('id') is not None:
                    save_quiz_result_answers(
                        cursor,
                        submitted_result['id'],
                        submitted_answers,
                    )
                conn.commit()
            except pymysql.err.IntegrityError:
                rollback_db(conn)
                flash('You have already submitted this quiz.', 'warning')
                return redirect(url_for('student_result', quiz_id=quiz_id))
            flash('Quiz submitted successfully!', 'success')
            return redirect(url_for('student_result', quiz_id=quiz_id))
    except pymysql.MySQLError:
        rollback_db(conn)
        flash_database_error('quiz submission')
        return redirect(url_for('student_dashboard'))
    finally:
        close_db_connection(conn)

@app.route('/student/result/<int:quiz_id>')
@student_required
def student_result(quiz_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT r.*, q.title, q.category 
                FROM results r 
                JOIN quizzes q ON r.quiz_id = q.id 
                WHERE r.user_id = %s AND r.quiz_id = %s
            ''', (session['user_id'], quiz_id))
            result = cursor.fetchone()
            
            if not result:
                flash('Result not found.', 'danger')
                return redirect(url_for('student_dashboard'))
                
            return render_template('result.html', result=result, review_quiz_id=quiz_id)
    finally:
        conn.close()

@app.route('/student/result/<int:quiz_id>/review')
@student_required
def student_quiz_review(quiz_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))

    result = None
    questions = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT r.id, r.user_id, r.quiz_id, r.score, r.total_questions,
                       r.date_taken, q.title, q.category
                FROM results r
                JOIN quizzes q ON q.id = r.quiz_id
                WHERE r.user_id = %s AND r.quiz_id = %s
            """, (session['user_id'], quiz_id))
            result = cursor.fetchone()

            if not result:
                flash('You have not completed this quiz.', 'danger')
                return redirect(url_for('student_dashboard'))

            cursor.execute("""
                SELECT qu.id,
                       qu.question_text,
                       qu.option_a,
                       qu.option_b,
                       qu.option_c,
                       qu.option_d,
                       qu.correct_option,
                       qra.selected_option
                FROM questions qu
                LEFT JOIN quiz_result_answers qra
                  ON qra.question_id = qu.id
                 AND qra.result_id = %s
                WHERE qu.quiz_id = %s
                ORDER BY qu.id ASC
            """, (result['id'], quiz_id))
            questions = cursor.fetchall()
    except pymysql.MySQLError:
        rollback_db(conn)
        flash_database_error('quiz review')
        return redirect(url_for('student_result', quiz_id=quiz_id))
    finally:
        conn.close()

    percentage = None
    if result['total_questions'] > 0:
        percentage = round(result['score'] * 100.0 / result['total_questions'], 1)

    questions = [compare_review_answer(question) for question in questions]

    return render_template(
        'quiz_review.html',
        result=result,
        questions=questions,
        percentage=percentage,
    )

@app.route('/student/history')
@student_required
def student_history():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))
    results = []
    performance = {
        'average_percentage': None,
        'highest_percentage': None,
        'lowest_percentage': None,
    }
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT r.*,
                       q.title,
                       q.category,
                       r.score * 100.0 / NULLIF(r.total_questions, 0) AS percentage
                FROM results r 
                JOIN quizzes q ON r.quiz_id = q.id 
                WHERE r.user_id = %s
                ORDER BY r.date_taken DESC
            ''', (session['user_id'],))
            results = cursor.fetchall()

            cursor.execute('''
                SELECT AVG(r.score * 100.0 / NULLIF(r.total_questions, 0)) AS average_percentage,
                       MAX(r.score * 100.0 / NULLIF(r.total_questions, 0)) AS highest_percentage,
                       MIN(r.score * 100.0 / NULLIF(r.total_questions, 0)) AS lowest_percentage
                FROM results r
                WHERE r.user_id = %s
            ''', (session['user_id'],))
            performance = cursor.fetchone() or performance
    finally:
        conn.close()
    return render_template(
        'history.html',
        results=results,
        performance=performance,
    )

@app.route('/student/achievements')
@student_required
def student_achievements():
    conn = get_db_connection()
    achievements = []
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return render_template('achievements.html', achievements=achievements)

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT quiz_id, score, total_questions, date_taken "
                "FROM results "
                "WHERE user_id = %s AND total_questions > 0 "
                "AND score >= 0 AND score <= total_questions "
                "ORDER BY date_taken ASC",
                (session['user_id'],),
            )
            achievements = calculate_student_achievements(cursor.fetchall())
    except pymysql.MySQLError:
        rollback_db(conn)
        flash_database_error('loading achievements')
        achievements = []
    finally:
        conn.close()

    return render_template('achievements.html', achievements=achievements)


@app.route('/student/leaderboard')
@student_required
def leaderboard():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))

    quizzes = []
    leaders = []
    current_student = None
    selected_quiz = None
    selected_quiz_id = request.args.get('quiz_id', '').strip()
    quiz_filter = None

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, title FROM quizzes ORDER BY title ASC, id ASC"
            )
            quizzes = cursor.fetchall()

            if selected_quiz_id:
                try:
                    quiz_filter = int(selected_quiz_id)
                    if quiz_filter <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    quiz_filter = None
                    flash('Invalid quiz selection.', 'warning')

                if quiz_filter is not None:
                    cursor.execute(
                        "SELECT id, title FROM quizzes WHERE id = %s",
                        (quiz_filter,),
                    )
                    selected_quiz = cursor.fetchone()
                    if not selected_quiz:
                        quiz_filter = None
                        flash('Invalid quiz selection.', 'warning')

            if quiz_filter is None:
                cursor.execute("""
                    SELECT u.id AS student_id,
                           u.username,
                           COUNT(r.id) AS completed_quizzes,
                           AVG(r.score * 100.0 / NULLIF(r.total_questions, 0)) AS average_score,
                           SUM(r.score) AS total_score,
                           SUM(r.total_questions) AS total_possible
                    FROM users u
                    JOIN results r ON r.user_id = u.id
                    WHERE u.role = 'student' AND r.total_questions > 0
                    GROUP BY u.id, u.username
                    ORDER BY average_score DESC, completed_quizzes DESC,
                             total_score DESC, u.username ASC
                    LIMIT 10
                """)
            else:
                cursor.execute("""
                    SELECT u.id AS student_id,
                           u.username,
                           1 AS completed_quizzes,
                           r.score * 100.0 / NULLIF(r.total_questions, 0) AS average_score,
                           r.score AS total_score,
                           r.total_questions AS total_possible,
                           r.date_taken
                    FROM results r
                    JOIN users u ON u.id = r.user_id
                    JOIN quizzes q ON q.id = r.quiz_id
                    WHERE u.role = 'student'
                      AND r.quiz_id = %s
                      AND r.total_questions > 0
                    ORDER BY average_score DESC, r.score DESC,
                             u.username ASC, r.id ASC
                    LIMIT 10
                """, (quiz_filter,))
            leaders = cursor.fetchall()

            if leaders:
                previous_key = None
                rank = 0
                for index, leader in enumerate(leaders, start=1):
                    if quiz_filter is None:
                        key = (
                            round(float(leader['average_score'] or 0), 10),
                            leader['completed_quizzes'],
                            leader['total_score'],
                        )
                    else:
                        key = (
                            round(float(leader['average_score'] or 0), 10),
                            leader['total_score'],
                        )
                    if key != previous_key:
                        rank = index
                        previous_key = key
                    leader['rank'] = rank

            current_student_id = session['user_id']
            if leaders and not any(leader['student_id'] == current_student_id for leader in leaders):
                if quiz_filter is None:
                    cursor.execute("""
                        SELECT u.id AS student_id,
                               u.username,
                               COUNT(r.id) AS completed_quizzes,
                               AVG(r.score * 100.0 / NULLIF(r.total_questions, 0)) AS average_score,
                               SUM(r.score) AS total_score,
                               SUM(r.total_questions) AS total_possible
                        FROM users u
                        JOIN results r ON r.user_id = u.id
                        WHERE u.role = 'student' AND r.total_questions > 0
                        GROUP BY u.id, u.username
                        ORDER BY average_score DESC, completed_quizzes DESC,
                                 total_score DESC, u.username ASC
                    """)
                else:
                    cursor.execute("""
                        SELECT u.id AS student_id,
                               u.username,
                               1 AS completed_quizzes,
                               r.score * 100.0 / NULLIF(r.total_questions, 0) AS average_score,
                               r.score AS total_score,
                               r.total_questions AS total_possible,
                               r.date_taken
                        FROM results r
                        JOIN users u ON u.id = r.user_id
                        WHERE u.role = 'student'
                          AND r.quiz_id = %s
                          AND r.total_questions > 0
                        ORDER BY average_score DESC, r.score DESC,
                                 u.username ASC, r.id ASC
                    """, (quiz_filter,))

                all_ranked = cursor.fetchall()
                previous_key = None
                rank = 0
                for index, row in enumerate(all_ranked, start=1):
                    if quiz_filter is None:
                        key = (
                            round(float(row['average_score'] or 0), 10),
                            row['completed_quizzes'],
                            row['total_score'],
                        )
                    else:
                        key = (
                            round(float(row['average_score'] or 0), 10),
                            row['total_score'],
                        )
                    if key != previous_key:
                        rank = index
                        previous_key = key
                    if row['student_id'] == current_student_id:
                        row['rank'] = rank
                        current_student = row
                        break
            else:
                for leader in leaders:
                    if leader['student_id'] == current_student_id:
                        current_student = leader
                        break
    except pymysql.MySQLError:
        flash_database_error('student leaderboard')
        leaders = []
        current_student = None
    finally:
        conn.close()
    return render_template(
        'leaderboard.html',
        leaders=leaders,
        quizzes=quizzes,
        selected_quiz=selected_quiz,
        quiz_id=quiz_filter,
        current_student=current_student,
    )

if __name__ == "__main__":
    debug_enabled = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1' if debug_enabled else '0.0.0.0', port=5000, debug=debug_enabled)