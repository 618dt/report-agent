"""
    mongo.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    

    :author: lcg
    :date created: 2026/8/1

"""
import inspect
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

from pymongo import AsyncMongoClient, MongoClient
from pymongo.read_preferences import ReadPreference

from app.configs import cluster_configs
from app.utils.log import logger


@dataclass(frozen=True)
class MongoConfig:
    uri: str
    db_name: str
    app_name: str = "report-agent-backend"
    server_selection_timeout_ms: int = 5000
    max_pool_size: int = 100
    min_pool_size: int = 10
    max_idle_time_ms: int = 60000
    wait_queue_timeout_ms: int = 5000
    connect_timeout_ms: int = 10000
    socket_timeout_ms: int = 30000

    @staticmethod
    def from_env(
        *,
        uri_env: str = "MONGO_URI",
        db_env: str = "MONGO_DB",
        default_uri: str = "mongodb://localhost:27017",
        default_db: str = "report-agent-db",
    ) -> "MongoConfig":
        return MongoConfig(
            uri=os.getenv(uri_env, default_uri),
            db_name=os.getenv(db_env, default_db),
        )

    @staticmethod
    def from_dict(config: dict) -> "MongoConfig":
        host = config.get("host", "localhost")
        port = config.get("port", 27017)
        db_name = config.get("database", "report-agent-db")
        is_auth = config.get("is_auth", False)
        user = config.get("user", "")
        pwd = config.get("pwd", "")
        is_replica = config.get("is_replica", False)
        replica = config.get("replica", "")
        # Docker MONGO_INITDB_ROOT_* 用户默认认证库为 admin
        auth_source = config.get("auth_source") or ("admin" if is_auth else "")

        # Build URI from discrete fields if not already a full URI
        if not host.startswith("mongodb://"):
            # Append default port for hosts that don't already specify one
            if "," not in host:
                if ":" not in host:
                    hosts = f"{host}:{port}"
                else:
                    hosts = host
            else:
                parts = []
                for h in host.split(","):
                    h = h.strip()
                    if h and ":" not in h:
                        h = f"{h}:{port}"
                    if h:
                        parts.append(h)
                hosts = ",".join(parts)

            if is_auth and user and pwd:
                uri = (
                    f"mongodb://{quote_plus(str(user))}:"
                    f"{quote_plus(str(pwd))}@{hosts}"
                )
            else:
                uri = f"mongodb://{hosts}"

            query_parts: list[str] = []
            if is_replica and replica:
                query_parts.append(f"replicaSet={replica}")
            if auth_source:
                query_parts.append(f"authSource={auth_source}")
            if query_parts:
                uri = f"{uri}/?{'&'.join(query_parts)}"
            else:
                uri = f"{uri}/"
        else:
            # host is already a full URI, use it directly
            uri = host

        return MongoConfig(
            uri=uri,
            db_name=db_name,
            app_name=config.get("app_name", "report-agent-backend"),
            server_selection_timeout_ms=config.get("server_selection_timeout_ms", 5000),
            max_pool_size=config.get("max_pool_size", 100),
            min_pool_size=config.get("min_pool_size", 10),
            max_idle_time_ms=config.get("max_idle_time_ms", 60000),
            wait_queue_timeout_ms=config.get("wait_queue_timeout_ms", 5000),
            connect_timeout_ms=config.get("connect_timeout_ms", 10000),
            socket_timeout_ms=config.get("socket_timeout_ms", 30000),
        )


class MongoConnection:
    """
    A small wrapper around pymongo.MongoClient.

    - Lazy-connects on first use.
    - Provides quick access to a default Database / Collections.
    - Supports context-manager usage to ensure close().
    """

    def __init__(self, config: Optional[MongoConfig] = None):
        if isinstance(config, dict):
            config = MongoConfig.from_dict(config)
        self._config = config or MongoConfig.from_env()
        self._client: Optional[MongoClient] = None
        self._async_client: Optional[AsyncMongoClient] = None

    @property
    def uri(self) -> str:
        """MongoDB 连接 URI"""
        return self._config.uri

    @property
    def db_name(self) -> str:
        """默认数据库名"""
        return self._config.db_name

    @property
    def client(self) -> MongoClient:
        if self._client is None:
            self._client = MongoClient(
                self._config.uri,
                connect=False,
                appname=self._config.app_name,
                serverSelectionTimeoutMS=self._config.server_selection_timeout_ms,
                maxPoolSize=self._config.max_pool_size,
                minPoolSize=self._config.min_pool_size,
                maxIdleTimeMS=self._config.max_idle_time_ms,
                waitQueueTimeoutMS=self._config.wait_queue_timeout_ms,
                connectTimeoutMS=self._config.connect_timeout_ms,
                socketTimeoutMS=self._config.socket_timeout_ms,
            )
        return self._client

    @property
    def async_client(self) -> AsyncMongoClient:
        if self._async_client is None:
            self._async_client = AsyncMongoClient(
                self._config.uri,
                connect=False,
                appname=self._config.app_name,
                serverSelectionTimeoutMS=self._config.server_selection_timeout_ms,
                maxPoolSize=self._config.max_pool_size,
                minPoolSize=self._config.min_pool_size,
                maxIdleTimeMS=self._config.max_idle_time_ms,
                waitQueueTimeoutMS=self._config.wait_queue_timeout_ms,
                connectTimeoutMS=self._config.connect_timeout_ms,
                socketTimeoutMS=self._config.socket_timeout_ms,
            )
        return self._async_client

    def get_database(self, db_name: Optional[str] = None, async_mode: bool = False, use_primary: bool = False):
        """
        统一获取数据库的方法
        
        Args:
            db_name: 数据库名称，默认使用配置中的数据库
            async_mode: True返回异步数据库，False返回同步数据库
            use_primary: True使用PRIMARY读偏好，False使用SECONDARY_PREFERRED读偏好
        
        Returns:
            Database 或 AsyncDatabase 实例
        """
        client = self.async_client if async_mode else self.client
        db = client[db_name or self._config.db_name]

        # 根据use_primary设置读偏好，同步和异步模式都支持
        read_pref = ReadPreference.PRIMARY if use_primary else ReadPreference.SECONDARY_PREFERRED
        return db.with_options(read_preference=read_pref)

    def get_collection(
        self,
        name: str,
        *,
        db_name: Optional[str] = None,
        async_mode: bool = False,
        use_primary: bool = False
    ):
        """
        统一获取集合的方法
        
        Args:
            name: 集合名称
            db_name: 数据库名称，默认使用配置中的数据库
            async_mode: True返回异步集合，False返回同步集合
            use_primary: True使用PRIMARY读偏好，False使用SECONDARY_PREFERRED读偏好
        
        Returns:
            Collection 或 AsyncCollection 实例
        """
        db = self.get_database(db_name, async_mode=async_mode, use_primary=use_primary)
        return db[name]

    def ping(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except Exception as e:
            logger.error(f"mongodb ping failed: {e}")
            return False

    async def async_ping(self) -> bool:
        try:
            await self.async_client.admin.command("ping")
            return True
        except Exception as e:
            logger.error(f"mongodb async ping failed: {e}")
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._async_client is not None:
            self._async_client.close()
            self._async_client = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            close_result = self._async_client.close()
            if inspect.isawaitable(close_result):
                await close_result
            self._async_client = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "MongoConnection":
        _ = self.client
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


_mongo: Optional[MongoConnection] = None


def init_mongo() -> MongoConnection:
    """
    Initialize the global MongoConnection once (typically at app startup).
    """
    global _mongo
    if _mongo is None:
        mongo_config = cluster_configs.get("mongodb", {})
        _mongo = MongoConnection(mongo_config)
    return _mongo


def get_mongo() -> MongoConnection:
    """
    Get the initialized MongoConnection.
    Usable in routes, background tasks, and scheduled jobs without Request.
    """
    if _mongo is None:
        raise RuntimeError("MongoConnection is not initialized. Call init_mongo() at startup.")
    return _mongo


def close_mongo() -> None:
    global _mongo
    if _mongo is not None:
        _mongo.close()
        _mongo = None


async def aclose_mongo() -> None:
    global _mongo
    if _mongo is not None:
        await _mongo.aclose()
        _mongo = None
