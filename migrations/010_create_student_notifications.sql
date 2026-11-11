-- Migration 010: Create in-app student notifications.
-- Run manually against an existing student_quiz_portal database after review.
USE student_quiz_portal;

CREATE TABLE IF NOT EXISTS student_notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    title VARCHAR(120) NOT NULL,
    message VARCHAR(1000) NOT NULL,
    notification_type ENUM('quiz_available', 'quiz_result') NOT NULL,
    related_quiz_id INT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_student_notifications_student_type_quiz
        (student_id, notification_type, related_quiz_id),
    INDEX idx_student_notifications_student_read (student_id, is_read),
    INDEX idx_student_notifications_student_created (student_id, created_at),
    INDEX idx_student_notifications_quiz_id (related_quiz_id),
    CONSTRAINT chk_student_notifications_title CHECK (CHAR_LENGTH(TRIM(title)) > 0),
    CONSTRAINT chk_student_notifications_message CHECK (CHAR_LENGTH(TRIM(message)) > 0),
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (related_quiz_id) REFERENCES quizzes(id) ON DELETE SET NULL
);
