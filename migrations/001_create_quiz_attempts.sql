-- Migration 001: Create server-side quiz attempts tracking.
-- Run manually against an existing student_quiz_portal database only after review.
USE student_quiz_portal;

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
