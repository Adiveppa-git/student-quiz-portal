CREATE TABLE IF NOT EXISTS student_study_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
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
