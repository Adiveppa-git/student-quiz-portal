-- Migration 009: Create student quiz feedback and ratings.
-- Run manually against an existing student_quiz_portal database only after review.
USE student_quiz_portal;

CREATE TABLE IF NOT EXISTS student_quiz_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    quiz_id INT NOT NULL,
    rating TINYINT NOT NULL,
    feedback VARCHAR(1000) NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_student_quiz_feedback_student_quiz (student_id, quiz_id),
    INDEX idx_student_quiz_feedback_quiz_id (quiz_id),
    INDEX idx_student_quiz_feedback_rating (rating),
    INDEX idx_student_quiz_feedback_created_at (created_at),
    CONSTRAINT chk_student_quiz_feedback_rating CHECK (rating BETWEEN 1 AND 5),
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
);
