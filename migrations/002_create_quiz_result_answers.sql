USE student_quiz_portal;

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
