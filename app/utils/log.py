"""
    log.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    

    :author: lcg
    :date created: 2026/8/1

"""
import logging.config
import os
import logging
from logging.handlers import RotatingFileHandler

try:
    from concurrent_log_handler import ConcurrentRotatingFileHandler
except ImportError:
    # fallback to standard handler if not installed
    ConcurrentRotatingFileHandler = RotatingFileHandler

import ujson

from app.configs import node_configs


class FileFormatter(logging.Formatter):
    """
    For recording json format data log

    """

    def __init__(self, fmt=None, datefmt=None, style='%'):
        super(FileFormatter, self).__init__(
            fmt=fmt,
            datefmt=datefmt,
            style=style
        )

    def format(self, record):
        """ Override format function"""
        line = (
            '[{time}]|{level_name}|{pathname}|line:{line_no}|'
            '{function_name}|%s'
        ).format(
            time=self.formatTime(record, self.datefmt),
            level_name=record.levelname,
            pathname=record.pathname,
            line_no=record.lineno,
            function_name=record.funcName
        )
        if isinstance(record.msg, dict):
            s = line % ujson.dumps(record.msg, ensure_ascii=False)
            # 防止log太大
            if len(s) > 1000:
                s = s[:500] + '......' + s[-500:]
            return s
        else:
            s = line % str(record.msg)
            # 防止log太大
            if len(s) > 1000:
                s = s[:500] + '......' + s[-250:]
            return s


class Logger:
    def __init__(self, default='default', loggers=None):
        self.loggers = loggers
        self.default = default

    def debug(self, msg, *args, logger_name=None, **kwargs):
        self._log(logger_name, msg, *args, level='debug', **kwargs)

    def info(self, msg, *args, logger_name=None, **kwargs):
        self._log(logger_name, msg, *args, level='info', **kwargs)

    def warning(self, msg, *args, logger_name=None, **kwargs):
        self._log(logger_name, msg, *args, level='warning', **kwargs)

    def error(self, msg, *args, logger_name=None, **kwargs):
        self._log(logger_name, msg, *args, level='error', **kwargs)

    def critical(self, msg, *args, logger_name=None, **kwargs):
        self._log(logger_name, msg, *args, level='critical', **kwargs)

    def exception(self, msg, *args, logger_name=None, **kwargs):
        kwargs.setdefault('exc_info', True)
        self._log(logger_name, msg, *args, level='error', **kwargs)

    def _log(self, logger_name, msg, *args, level='info', **kwargs):
        if not logger_name:
            _logger_name = self.default
        else:
            _logger_name = logger_name

        if not isinstance(_logger_name, list):
            _logger_name = [_logger_name]

        level = level.lower()
        if level in ('error', 'critical') and 'error' not in _logger_name:
            _logger_name.append('error')

        for name in _logger_name:
            if name not in self.loggers:
                continue
            _logger = logging.getLogger(name)
            if not _logger:
                continue

            if level == 'debug':
                _logger.debug(msg, *args, **kwargs)
            elif level == 'info':
                _logger.info(msg, *args, **kwargs)
            elif level == 'warn' or level == 'warning':
                _logger.warning(msg, *args, **kwargs)
            elif level == 'error':
                _logger.error(msg, *args, **kwargs)
            elif level == 'critical':
                _logger.critical(msg, *args, **kwargs)


__all__ = ['logger']

DEBUG = node_configs.get("server", {}).get("debug", False)
LOG_DIR = node_configs.get("log").get("path")
LOG_MAX_BYTES = node_configs.get("log", {}).get("max_bytes", 20 * 1024 * 1024)  # 20MB
LOG_BACKUP_COUNT = node_configs.get("log", {}).get("backup_count", 20)
LOG_ENCODING = node_configs.get("log", {}).get("encoding", "utf-8")

_file_handler = {
    'level': 'INFO',
    'class': 'concurrent_log_handler.ConcurrentRotatingFileHandler',
    'formatter': 'verbose',
    'maxBytes': LOG_MAX_BYTES,
    'backupCount': LOG_BACKUP_COUNT,
    'encoding': LOG_ENCODING,
}

LOGGING_CONF = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            '()': FileFormatter,
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'default': {**_file_handler, 'filename': LOG_DIR + 'default.log'},
        'error': {**_file_handler, 'level': 'ERROR', 'filename': LOG_DIR + 'error.log'},
        'server_error': {**_file_handler, 'level': 'ERROR', 'filename': LOG_DIR + 'server_error.log'},
    },
    'loggers': {
        'default': {
            'handlers': ['default', 'console'] if DEBUG else ['default'],
            'level': 'INFO',
            'propagate': True,
        },
        'error': {
            'handlers': ['error', 'console'] if DEBUG else ['error'],
            'level': 'ERROR',
            'propagate': True,
        },
        'server_error': {
            'handlers': ['server_error', 'console'] if DEBUG else [
                'server_error'],
            'level': 'ERROR',
            'propagate': True,
        },
    }
}

# 确保日志目录存在（相对路径依赖 cwd，此处自动创建）
os.makedirs(LOG_DIR, exist_ok=True)

# 初始化日志配置
logging.config.dictConfig(LOGGING_CONF)
logger = Logger(loggers=list(LOGGING_CONF['loggers'].keys()))
