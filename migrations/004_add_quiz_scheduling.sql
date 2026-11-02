USE student_quiz_portal;

ALTER TABLE quizzes
    ADD COLUMN available_from DATETIME NULL AFTER created_at,
    ADD COLUMN available_until DATETIME NULL AFTER available_from;
