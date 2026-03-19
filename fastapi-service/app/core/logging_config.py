"""
日志配置
"""
import logging
import sys
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from pythonjsonlogger import jsonlogger


def setup_logging():
    """配置日志系统"""
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    
    # 避免重复添加 handler
    if root_logger.handlers:
        return
    
    root_logger.setLevel(logging.INFO)
    
    # 创建日志格式化器
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 配置控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 配置文件处理器（按天轮转）
    log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    file_handler = TimedRotatingFileHandler(
        log_dir / "app.log",
        when='midnight',
        interval=1,
        backupCount=30,  # 保留30天
        encoding='utf-8'
    )
    file_handler.suffix = "%Y-%m-%d"  # 日志文件后缀格式
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # 设置第三方库日志级别
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
