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
