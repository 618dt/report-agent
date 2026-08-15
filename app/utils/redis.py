"""
    redis.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    

    :author: lcg
    :date created: 2026/8/1

"""

import os
from dataclasses import dataclass, field
from typing import Optional, Union

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster
from redis.asyncio.connection import ConnectionPool as AsyncConnectionPool
from redis.cluster import RedisCluster
from redis.connection import ConnectionPool

from app.configs import cluster_configs
from app.utils.log import logger


@dataclass(frozen=True)
class RedisConfig:
    mode: str = "single"  # "single" or "cluster"
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    socket_timeout: int = 5
    socket_connect_timeout: int = 5
    max_connections: int = 50
    decode_responses: bool = True
    startup_nodes: list[dict] = field(default_factory=list)

    @staticmethod
    def from_env(
        *,
        host_env: str = "REDIS_HOST",
        port_env: str = "REDIS_PORT",
        db_env: str = "REDIS_DB",
        pwd_env: str = "REDIS_PASSWORD",
        default_host: str = "localhost",
        default_port: str = "6379",
        default_db: str = "0",
    ) -> "RedisConfig":
        return RedisConfig(
            host=os.getenv(host_env, default_host),
            port=int(os.getenv(port_env, default_port)),
            db=int(os.getenv(db_env, default_db)),
            password=os.getenv(pwd_env) or None,
        )

    @staticmethod
    def from_dict(config: dict) -> "RedisConfig":
        is_auth = config.get("is_auth", False)
        password = config.get("password", "") if is_auth else None

        return RedisConfig(
            mode=config.get("mode", "single"),
            host=config.get("host", "localhost"),
            port=config.get("port", 6379),
            db=config.get("db", 0),
            password=password or None,
            socket_timeout=config.get("socket_timeout", 5),
            socket_connect_timeout=config.get("socket_connect_timeout", 5),
            max_connections=config.get("max_connections", 50),
            startup_nodes=config.get("startup_nodes", []),
        )


class RedisConnection:
    """
    A small wrapper around redis.Redis / redis.cluster.RedisCluster.

    - Lazy-connects on first use.
    - Provides quick access to a Redis client (single node or cluster).
    - Supports context-manager usage to ensure close().
    """

    def __init__(self, config: Optional[RedisConfig] = None):
        if isinstance(config, dict):
            config = RedisConfig.from_dict(config)
        self._config = config or RedisConfig.from_env()
        self._client: Optional[Union[Redis, RedisCluster]] = None
        self._async_client: Optional[Union[AsyncRedis, AsyncRedisCluster]] = None
        self._pool: Optional[ConnectionPool] = None
        self._async_pool: Optional[AsyncConnectionPool] = None

    @property
    def client(self) -> Union[Redis, RedisCluster]:
        if self._client is None:
            if self._config.mode == "cluster":
                startup_nodes = self._config.startup_nodes or [
                    {"host": self._config.host, "port": self._config.port}
                ]
                self._client = RedisCluster(
                    startup_nodes=startup_nodes,
                    password=self._config.password,
                    socket_timeout=self._config.socket_timeout,
                    socket_connect_timeout=self._config.socket_connect_timeout,
                    max_connections=self._config.max_connections,
                    decode_responses=self._config.decode_responses,
                    skip_full_coverage_check=True,
                )
            else:
                pool_kwargs = {
                    "host": self._config.host,
                    "port": self._config.port,
                    "db": self._config.db,
                    "password": self._config.password,
                    "socket_timeout": self._config.socket_timeout,
                    "socket_connect_timeout": self._config.socket_connect_timeout,
                    "max_connections": self._config.max_connections,
                    "decode_responses": self._config.decode_responses,
                }
                self._pool = ConnectionPool(**pool_kwargs)
                self._client = Redis(connection_pool=self._pool)
        return self._client

    @property
    def async_client(self) -> Union[AsyncRedis, AsyncRedisCluster]:
        if self._async_client is None:
            if self._config.mode == "cluster":
                startup_nodes = self._config.startup_nodes or [
                    {"host": self._config.host, "port": self._config.port}
                ]
                self._async_client = AsyncRedisCluster(
                    startup_nodes=startup_nodes,
                    password=self._config.password,
                    socket_timeout=self._config.socket_timeout,
                    socket_connect_timeout=self._config.socket_connect_timeout,
                    max_connections=self._config.max_connections,
                    decode_responses=self._config.decode_responses,
                    skip_full_coverage_check=True,
                )
            else:
                pool_kwargs = {
                    "host": self._config.host,
                    "port": self._config.port,
                    "db": self._config.db,
                    "password": self._config.password,
                    "socket_timeout": self._config.socket_timeout,
                    "socket_connect_timeout": self._config.socket_connect_timeout,
                    "max_connections": self._config.max_connections,
                    "decode_responses": self._config.decode_responses,
                }
                self._async_pool = AsyncConnectionPool(**pool_kwargs)
                self._async_client = AsyncRedis(connection_pool=self._async_pool)
        return self._async_client

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception as e:
            logger.error(f"redis ping failed: {e}")
            return False

    async def async_ping(self) -> bool:
        try:
            return bool(await self.async_client.ping())
        except Exception as e:
            logger.error(f"redis async ping failed: {e}")
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._pool is not None:
            self._pool.disconnect()
            self._pool = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
        if self._async_pool is not None:
            await self._async_pool.aclose()
            self._async_pool = None
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._pool is not None:
            self._pool.disconnect()
            self._pool = None

    def __enter__(self) -> "RedisConnection":
        _ = self.client
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


_redis: Optional[RedisConnection] = None


def init_redis() -> RedisConnection:
    """
    Initialize the global RedisConnection once (typically at app startup).
    """
    global _redis
    if _redis is None:
        redis_config = cluster_configs.get("redis", {})
        _redis = RedisConnection(redis_config)
    return _redis


def get_redis() -> RedisConnection:
    """
    Get the initialized RedisConnection.
    Usable in routes, background tasks, and scheduled jobs without Request.
    """
    if _redis is None:
        raise RuntimeError("RedisConnection is not initialized. Call init_redis() at startup.")
    return _redis


def get_client() -> Union[Redis, RedisCluster]:
    """
    Get the underlying Redis client directly.

    Supports both single-node Redis and RedisCluster.
    """
    return get_redis().client


def get_async_client() -> Union[AsyncRedis, AsyncRedisCluster]:
    return get_redis().async_client


def close_redis() -> None:
    global _redis
    if _redis is not None:
        _redis.close()
        _redis = None


async def aclose_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
