CREATE DATABASE IF NOT EXISTS studyflow
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE studyflow;

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    role ENUM('user', 'assistant') NOT NULL,
    content MEDIUMTEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY ix_messages_session_order (session_id, message_id),
    CONSTRAINT fk_messages_session FOREIGN KEY (session_id)
        REFERENCES chat_sessions (session_id)
) ENGINE=InnoDB;
