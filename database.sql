-- Create database
CREATE DATABASE IF NOT EXISTS student_quiz_portal;
USE student_quiz_portal;

-- Users table (Admins and Students)
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role ENUM('admin', 'student') NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Quizzes table
CREATE TABLE IF NOT EXISTS quizzes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    description TEXT,
    category VARCHAR(50),
    time_limit_minutes INT DEFAULT 15,
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    available_from DATETIME NULL,
    available_until DATETIME NULL,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Questions table
CREATE TABLE IF NOT EXISTS questions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    quiz_id INT NOT NULL,
    question_text TEXT NOT NULL,
    option_a VARCHAR(255) NOT NULL,
    option_b VARCHAR(255) NOT NULL,
    option_c VARCHAR(255) NOT NULL,
    option_d VARCHAR(255) NOT NULL,
    correct_option CHAR(1) NOT NULL, -- 'A', 'B', 'C', or 'D'
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE,
    INDEX idx_questions_quiz_id (quiz_id)
);

-- Reusable question bank
CREATE TABLE IF NOT EXISTS question_bank (
    id INT AUTO_INCREMENT PRIMARY KEY,
    question_text TEXT NOT NULL,
    option_a VARCHAR(255) NOT NULL,
    option_b VARCHAR(255) NOT NULL,
    option_c VARCHAR(255) NOT NULL,
    option_d VARCHAR(255) NOT NULL,
    correct_option CHAR(1) NOT NULL,
    category VARCHAR(50) NOT NULL,
    difficulty ENUM('easy', 'medium', 'hard') NOT NULL DEFAULT 'medium',
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_question_bank_category (category),
    INDEX idx_question_bank_difficulty (difficulty)
);

-- Quiz attempts table
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    quiz_id INT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_quiz_attempts_user_quiz (user_id, quiz_id),
    INDEX idx_quiz_attempts_quiz_id (quiz_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
);

-- Student quiz bookmarks/favorites
CREATE TABLE IF NOT EXISTS student_quiz_bookmarks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    quiz_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_student_quiz_bookmarks_student_quiz (student_id, quiz_id),
    INDEX idx_student_quiz_bookmarks_quiz_id (quiz_id),
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
);

-- Student study planner tasks
CREATE TABLE IF NOT EXISTS student_study_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NOT NULL,
    title VARCHAR(120) NOT NULL,
    description VARCHAR(1000) NULL,
    quiz_id INT NULL,
    target_date DATE NOT NULL,
    is_completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_student_study_tasks_student_date (student_id, target_date),
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE SET NULL
);

-- Results table
CREATE TABLE IF NOT EXISTS results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    quiz_id INT NOT NULL,
    score INT NOT NULL,
    total_questions INT NOT NULL,
    date_taken TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE,
    UNIQUE KEY uq_results_user_quiz (user_id, quiz_id),
    INDEX idx_results_quiz_id (quiz_id)
);

-- Submitted answers for quiz review
CREATE TABLE IF NOT EXISTS quiz_result_answers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    result_id INT NOT NULL,
    question_id INT NOT NULL,
    selected_option CHAR(1) NOT NULL,
    FOREIGN KEY (result_id) REFERENCES results(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    UNIQUE KEY uq_quiz_result_answer (result_id, question_id),
    INDEX idx_quiz_result_answers_question_id (question_id)
);

-- Admin activity audit records
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    admin_user_id INT,
    action_type VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50),
    entity_id INT,
    details VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_admin_audit_logs_created_at (created_at),
    INDEX idx_admin_audit_logs_admin_user_id (admin_user_id),
    FOREIGN KEY (admin_user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Admin accounts are created through an approved secure provisioning process; do not use a default password.
