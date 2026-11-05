-- Migration 008: Create personal student revision notes.
-- Run manually against an existing student_quiz_portal database only after review.
USE student_quiz_portal;

CREATE TABLE IF NOT EXISTS student_quiz_notes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    quiz_id INT NULL,
    title VARCHAR(120) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_student_quiz_notes_student_id (student_id),
    INDEX idx_student_quiz_notes_quiz_id (quiz_id),
    INDEX idx_student_quiz_notes_updated_at (updated_at),
    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE SET NULL
);
