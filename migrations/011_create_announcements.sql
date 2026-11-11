
-- Migration 011: Create admin announcements.
-- Run manually against student_quiz_portal after review.

USE student_quiz_portal;

CREATE TABLE IF NOT EXISTS announcements (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(150) NOT NULL,
    message TEXT NOT NULL,
    created_by INT NOT NULL,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_announcements_published (is_published),
    INDEX idx_announcements_created (created_at),
    INDEX idx_announcements_admin (created_by),

    CONSTRAINT chk_announcement_title
        CHECK (CHAR_LENGTH(TRIM(title)) > 0),

    CONSTRAINT chk_announcement_message
        CHECK (CHAR_LENGTH(TRIM(message)) > 0),

    CONSTRAINT fk_announcement_admin
        FOREIGN KEY (created_by)
        REFERENCES users(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
