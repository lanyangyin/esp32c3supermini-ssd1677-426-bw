# web_server/__init__.py
# 对外暴露 web_server 包的统一入口

from .server import start_server

__all__ = ['start_server']