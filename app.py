from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql
import pymysql.cursors
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))

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
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Decorator to check if user is student
def student_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'student':
            flash('Student access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('student_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
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

                if user and check_password_hash(user['password'], password):
                    session['user_id'] = user['id']
                    session['username'] = user['username']
                    session['role'] = user['role']
                    
                    flash('Logged in successfully.', 'success')
                    if user['role'] == 'admin':
                        return redirect(url_for('admin_dashboard'))
                    else:
                        return redirect(url_for('student_dashboard'))
                else:
                    flash('Invalid username or password', 'danger')
        finally:
            conn.close()

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
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

@app.route('/logout')
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
        finally:
            conn.close()
            
    return render_template('admin_dashboard.html', stats=stats, quizzes=recent_quizzes)

@app.route('/admin/quizzes', methods=['GET', 'POST'])
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
            time_limit = request.form.get('time_limit', 15)
            description = request.form.get('description')
            
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO quizzes (title, category, time_limit_minutes, description, created_by) VALUES (%s, %s, %s, %s, %s)",
                        (title, category, time_limit, description, session['user_id'])
                    )
                    conn.commit()
                    flash('Quiz created successfully!', 'success')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('quiz creation')
                
        elif action == 'delete':
            quiz_id = request.form.get('quiz_id')
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM quizzes WHERE id = %s", (quiz_id,))
                    conn.commit()
                    flash('Quiz deleted successfully.', 'info')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('quiz deletion')
                
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

@app.route('/admin/quizzes/<int:quiz_id>/questions', methods=['GET', 'POST'])
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
                    conn.commit()
                    flash('Question added successfully!', 'success')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('question creation')
                
        elif action == 'edit':
            q_id = request.form.get('question_id')
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
                    flash('Question updated successfully!', 'success')
            except pymysql.MySQLError:
                rollback_db(conn)
                flash_database_error('question update')
                
        elif action == 'delete':
            q_id = request.form.get('question_id')
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM questions WHERE id = %s AND quiz_id = %s", (q_id, quiz_id))
                    conn.commit()
                    flash('Question deleted.', 'info')
            except pymysql.MySQLError:
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
            # Check if student already took this quiz
            cursor.execute("SELECT id FROM results WHERE user_id = %s AND quiz_id = %s", (session['user_id'], quiz_id))
            if cursor.fetchone():
                flash('You have already taken this quiz. View your results below.', 'warning')
                return redirect(url_for('student_result', quiz_id=quiz_id))

            cursor.execute("SELECT * FROM quizzes WHERE id = %s", (quiz_id,))
            quiz = cursor.fetchone()
            if not quiz:
                flash('Quiz not found.', 'danger')
                return redirect(url_for('student_dashboard'))
                
            cursor.execute("SELECT id, question_text, option_a, option_b, option_c, option_d FROM questions WHERE quiz_id = %s", (quiz_id,))
            questions = cursor.fetchall()
            
            if not questions:
                flash('This quiz has no questions yet.', 'info')
                return redirect(url_for('student_dashboard'))
                
            return render_template('quiz.html', quiz=quiz, questions=questions)
    finally:
        conn.close()

@app.route('/student/quiz/<int:quiz_id>/submit', methods=['POST'])
@student_required
def submit_quiz(quiz_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))
    try:
        with conn.cursor() as cursor:
            # Check again to prevent double submission
            cursor.execute("SELECT id FROM results WHERE user_id = %s AND quiz_id = %s", (session['user_id'], quiz_id))
            if cursor.fetchone():
                return redirect(url_for('student_result', quiz_id=quiz_id))

            cursor.execute("SELECT id, correct_option FROM questions WHERE quiz_id = %s", (quiz_id,))
            questions = cursor.fetchall()
            
            score = 0
            total = len(questions)
            
            for q in questions:
                q_id = str(q['id'])
                selected = request.form.get(f'question_{q_id}')
                if selected == q['correct_option']:
                    score += 1
                    
            try:
                cursor.execute(
                    "INSERT INTO results (user_id, quiz_id, score, total_questions) VALUES (%s, %s, %s, %s)",
                    (session['user_id'], quiz_id, score, total)
                )
                conn.commit()
            except pymysql.err.IntegrityError:
                rollback_db(conn)
                flash('You have already submitted this quiz.', 'warning')
                return redirect(url_for('student_result', quiz_id=quiz_id))
            flash('Quiz submitted successfully!', 'success')
            return redirect(url_for('student_result', quiz_id=quiz_id))
    finally:
        conn.close()

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
                
            return render_template('result.html', result=result)
    finally:
        conn.close()

@app.route('/student/history')
@student_required
def student_history():
    conn = get_db_connection()
    if not conn:
        flash('Database connection failed. Please try again.', 'danger')
        return redirect(url_for('student_dashboard'))
    results = []
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT r.*, q.title, q.category 
                FROM results r 
                JOIN quizzes q ON r.quiz_id = q.id 
                WHERE r.user_id = %s
                ORDER BY r.date_taken DESC
            ''', (session['user_id'],))
            results = cursor.fetchall()
    finally:
        conn.close()
    return render_template('history.html', results=results)

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