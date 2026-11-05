-- Migration 006: Create persistent student quiz bookmarks/favorites.
-- Run manually against an existing student_quiz_portal database only after review.
USE student_quiz_portal;

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
