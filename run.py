"""
    run.py
    ~~~~~~~~~~~~~~~~~~~~~~~


    :author: lcg
    :date created: 2026/8/1

"""
import asyncio

import uvicorn

from app.configs import node_configs

# 从配置文件中读取配置
server_config = node_configs.get("server", {})

HOST = server_config.get("host", "0.0.0.0")
PORT = server_config.get("port", 8000)
RELOAD = server_config.get("reload", False)
LOG_LEVEL = LOG_DIR = node_configs.get("log").get("level")

if __name__ == "__main__":
    print(f"Starting server on {HOST}:{PORT}")
    print(f"Log Level: {LOG_LEVEL}")
    print(f"API Docs: http://{HOST if HOST != '0.0.0.0' else 'localhost'}:{PORT}/docs")

    # 使用 asyncio.Runner 替代 uvicorn.run()，避免 PyCharm 调试器对
    # asyncio.run() 的 monkey-patch 与 loop_factory 参数不兼容的问题。
    # pydevd 的 _patch_asyncio 包装器不支持 Python 3.12+ 新增的
    # loop_factory 关键字参数，而 uvicorn 0.51 Server.run() 会传递该参数。
    config = uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=RELOAD,
        log_level=LOG_LEVEL,
    )
    config.load_app()
    server = uvicorn.Server(config=config)
    try:
        with asyncio.Runner(loop_factory=config.get_loop_factory()) as runner:
            runner.run(server.serve())
    except KeyboardInterrupt:
        pass
