-- AI智能助手数据库初始化脚本

-- 创建审计日志表
CREATE TABLE IF NOT EXISTS ai_audit_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    trace_id VARCHAR(64) NOT NULL COMMENT '追踪ID',
    user_id VARCHAR(64) NOT NULL COMMENT '用户ID',
    session_id VARCHAR(64) COMMENT '会话ID',
    request_type VARCHAR(32) NOT NULL COMMENT '请求类型: chat, tool_call, plan',
    request_content TEXT COMMENT '请求内容',
    intent_type VARCHAR(64) COMMENT '意图类型',
    tool_calls JSON COMMENT '工具调用记录',
    response_content TEXT COMMENT '响应内容',
    execution_time_ms INT COMMENT '执行时间(毫秒)',
    status VARCHAR(16) NOT NULL COMMENT '状态: success, error',
    error_message TEXT COMMENT '错误信息',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_user_id (user_id),
    INDEX idx_trace_id (trace_id),
    INDEX idx_session_id (session_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI审计日志表';

-- 插入测试用户数据（如果user表存在）
-- 注意：这里假设你的系统已有user表，只是插入测试数据
-- 如果表结构不同，请根据实际情况调整

-- 测试用户1：普通员工
-- INSERT INTO jhi_user (id, login, password_hash, first_name, last_name, email, activated, lang_key, created_by, created_date)
-- VALUES (9001, 'test_employee', '$2a$10$VEjxo0jq2YG9Rbk2HmX9S.k1uZBGYUHdUcid3g/vfiEl7lwWgOH/O', '测试', '员工', 'employee@test.com', true, 'zh-cn', 'system', NOW())
-- ON DUPLICATE KEY UPDATE login=login;

-- 测试用户2：部门管理员
-- INSERT INTO jhi_user (id, login, password_hash, first_name, last_name, email, activated, lang_key, created_by, created_date)
-- VALUES (9002, 'test_dept_admin', '$2a$10$VEjxo0jq2YG9Rbk2HmX9S.k1uZBGYUHdUcid3g/vfiEl7lwWgOH/O', '测试', '部门管理员', 'dept_admin@test.com', true, 'zh-cn', 'system', NOW())
-- ON DUPLICATE KEY UPDATE login=login;

-- 测试用户3：超级管理员
-- INSERT INTO jhi_user (id, login, password_hash, first_name, last_name, email, activated, lang_key, created_by, created_date)
-- VALUES (9003, 'test_super_admin', '$2a$10$VEjxo0jq2YG9Rbk2HmX9S.k1uZBGYUHdUcid3g/vfiEl7lwWgOH/O', '测试', '超级管理员', 'super_admin@test.com', true, 'zh-cn', 'system', NOW())
-- ON DUPLICATE KEY UPDATE login=login;

-- 初始化知识库文档表（可选，用于存储文档元数据）
CREATE TABLE IF NOT EXISTS ai_knowledge_document (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(255) NOT NULL COMMENT '文档标题',
    source VARCHAR(255) COMMENT '文档来源',
    category VARCHAR(64) COMMENT '文档分类: 制度, 流程, FAQ',
    file_path VARCHAR(512) COMMENT '文件路径',
    content_hash VARCHAR(64) COMMENT '内容哈希',
    chunk_count INT DEFAULT 0 COMMENT '分块数量',
    status VARCHAR(16) DEFAULT 'pending' COMMENT '状态: pending, indexed, error',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库文档表';

-- 插入示例知识库文档
INSERT INTO ai_knowledge_document (title, source, category, content_hash, status)
VALUES 
    ('工时填报管理制度', '企业制度文档', '制度', 'hash001', 'pending'),
    ('项目管理流程', '流程文档', '流程', 'hash002', 'pending'),
    ('常见问题FAQ', 'FAQ文档', 'FAQ', 'hash003', 'pending')
ON DUPLICATE KEY UPDATE title=title;

-- 查询统计
SELECT '审计日志表创建完成' AS status;
SELECT '知识库文档表创建完成' AS status;
SELECT COUNT(*) AS document_count FROM ai_knowledge_document;
