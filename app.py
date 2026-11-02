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
               CASE
                   WHEN CURRENT_TIMESTAMP() <= DATE_ADD(
                       a.started_at,
                       INTERVAL q.time_limit_minutes MINUTE
                   ) THEN 0 ELSE 1
               END AS expired
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
    # Show summary: total students, total quizzes, etc.
    conn = get_db_connection()
    stats = {'students': 0, 'quizzes': 0, 'questions': 0}
    recent_quizzes = []
    quiz_performance = []
    
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='student'")
                stats['students'] = cursor.fetchone()['count']
                
                cursor.execute("SELECT COUNT(*) as count FROM quizzes")
                stats['quizzes'] = cursor.fetchone()['count']
                
                cursor.execute("SELECT COUNT(*) as count FROM questions")
                stats['questions'] = cursor.fetchone()['count']
                
                cursor.execute("SELECT * FROM quizzes ORDER BY created_at DESC LIMIT 5")
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
        finally:
            conn.close()
            
    return render_template(
        'admin_dashboard.html',
        stats=stats,
        quizzes=recent_quizzes,
        quiz_performance=quiz_performance,
    )

@app.route('/admin/audit-log')
@admin_required
def admin_audit_log():
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    per_page = 25
    offset = (page - 1) * per_page

    logs = []
    total_logs = 0
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS count FROM admin_audit_logs")
                count_row = cursor.fetchone()
                total_logs = count_row['count'] if count_row else 0

                cursor.execute(
                    """
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
                    ORDER BY a.created_at DESC, a.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (per_page, offset),
                )
                logs = cursor.fetchall()
        except pymysql.MySQLError:
            flash_database_error('audit log lookup')
        finally:
            close_db_connection(conn)

    total_pages = max(1, (total_logs + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * per_page
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT a.id, a.admin_user_id, a.action_type, a.entity_type,
                               a.entity_id, a.details, a.created_at,
                               u.username AS admin_username
                        FROM admin_audit_logs a
                        LEFT JOIN users u ON u.id = a.admin_user_id
                        ORDER BY a.created_at DESC, a.id DESC
                        LIMIT %s OFFSET %s
                        """,
                        (per_page, offset),
                    )
                    logs = cursor.fetchall()
            except pymysql.MySQLError:
                flash_database_error('audit log lookup')
            finally:
                close_db_connection(conn)

    return render_template(
        'admin_audit_log.html',
        logs=logs,
        page=page,
        total_pages=total_pages,
        total_logs=total_logs,
    )

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
        
        if action == 'create':
            title = request.form.get('title')
            category = request.form.get('category')
            time_limit = validate_quiz_time_limit(request.form.get('time_limit', 15))
            description = request.form.get('description')

            if time_limit is None:
                close_db_connection(conn)
                flash('Time limit must be a positive whole number.', 'danger')
                return redirect(url_for('manage_quizzes'))
            
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO quizzes (title, category, time_limit_minutes, description, created_by) VALUES (%s, %s, %s, %s, %s)",
                        (title, category, time_limit, description, session['user_id'])
                    )
                    quiz_id = cursor.lastrowid
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUIZ_CREATE',
                        'quiz',
                        quiz_id,
                        f'Created quiz "{title}"',
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

            if time_limit is None or not quiz_id:
                close_db_connection(conn)
                flash('Quiz ID and a valid time limit are required.', 'danger')
                return redirect(url_for('manage_quizzes'))

            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE quizzes
                        SET title=%s, category=%s, time_limit_minutes=%s, description=%s
                        WHERE id=%s
                        """,
                        (title, category, time_limit, description, quiz_id),
                    )
                    conn.commit()
                    record_admin_audit(
                        session['user_id'],
                        'QUIZ_UPDATE',
                        'quiz',
                        int(quiz_id),
                        f'Modified quiz "{title}"',
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
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM quizzes ORDER BY created_at DESC")
            quizzes = cursor.fetchall()
            
            cursor.execute("SELECT DISTINCT category FROM quizzes")
            categories = [row['category'] for row in cursor.fetchall()]
    finally:
        conn.close()

    return render_template('manage_quizzes.html', quizzes=quizzes, categories=categories)

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
            q_text = request.form.get('question_text')
            opt_a = request.form.get('option_a')
            opt_b = request.form.get('option_b')
            opt_c = request.form.get('option_c')
            opt_d = request.form.get('option_d')
            correct = request.form.get('correct_option')
            
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (quiz_id, q_text, opt_a, opt_b, opt_c, opt_d, correct)
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
            q_text = request.form.get('question_text')
            opt_a = request.form.get('option_a')
            opt_b = request.form.get('option_b')
            opt_c = request.form.get('option_c')
            opt_d = request.form.get('option_d')
            correct = request.form.get('correct_option')
            
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE questions 
                        SET question_text=%s, option_a=%s, option_b=%s, option_c=%s, option_d=%s, correct_option=%s
                        WHERE id=%s AND quiz_id=%s
                    """, (q_text, opt_a, opt_b, opt_c, opt_d, correct, q_id, quiz_id))
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
                
        return redirect(url_for('manage_questions', quiz_id=quiz_id))

    questions = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM questions WHERE quiz_id = %s", (quiz_id,))
            questions = cursor.fetchall()
    finally:
        conn.close()

    return render_template('manage_questions.html', quiz=quiz, questions=questions)

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
    
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM quizzes ORDER BY created_at DESC")
                quizzes = cursor.fetchall()
        finally:
            conn.close()
            
    return render_template('student_dashboard.html', quizzes=quizzes)

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

@app.route('/student/leaderboard')
@student_required
def leaderboard():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))
    leaders = []
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT u.username, SUM(r.score) as total_score, SUM(r.total_questions) as total_possible
                FROM users u
                JOIN results r ON u.id = r.user_id
                WHERE u.role = 'student'
                GROUP BY u.id
                ORDER BY total_score DESC
                LIMIT 10
            ''')
            leaders = cursor.fetchall()
    finally:
        conn.close()
    return render_template('leaderboard.html', leaders=leaders)

if __name__ == "__main__":
    debug_enabled = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1' if debug_enabled else '0.0.0.0', port=5000, debug=debug_enabled)