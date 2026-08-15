"""
    base_model.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    基础模型类，所有 MongoDB 文档模型的基类。

    提供：
    - 通用字段（_id, create_time, update_time, is_deleted）
    - 懒加载的 Collection 类属性（首次访问时解析并缓存）
    - 同步 / 异步双模式访问

    :author: lcg
    :date created: 2026/8/1

"""
from datetime import datetime
from datetime import timezone

from bson import ObjectId

from app.utils.mongo import get_mongo


class BaseModelMeta(type):
    """通过 __getattr__ 实现 Collection 懒加载

    首次访问 cls.a_p_col / cls.a_s_col / cls.p_col 时才解析真正的
    Collection 对象，解析后 setattr 到类上永久缓存，后续访问直接
    命中类属性，不再走 __getattr__。
    """

    __LAZY = {
        'a_p_col': (True, True),   # async + primary
        'a_s_col': (True, False),  # async + secondary
        'p_col':   (False, True),  # sync  + primary
    }

    def __getattr__(cls, name):
        spec = BaseModelMeta.__LAZY.get(name)
        if spec is not None:
            async_mode, use_primary = spec
            col = get_mongo().get_collection(
                name=cls.TABLE_NAME,
                async_mode=async_mode,
                use_primary=use_primary,
            )
            # 缓存为类属性，后续访问命中 normal attribute lookup
            setattr(cls, name, col)
            return col
        raise AttributeError(
            f"type object '{cls.__name__}' has no attribute '{name}'"
        )


class BaseModel(object, metaclass=BaseModelMeta):
    """
    基础模型类
    * `_id` (str) - 主键id
    * `is_deleted` (int) - 是否被删除 0-否, 1-是
    * `create_time` (datetime) - 创建时间
    * `update_time` (datetime) - 更新时间

    子类定义 TABLE_NAME 后自动获得三个懒加载 Collection 属性：
    * `a_p_col` - 异步 + PRIMARY（写操作）
    * `a_s_col` - 异步 + SECONDARY（读操作）
    * `p_col` - 同步 + PRIMARY（建索引等）
    """
    __abstract__ = True

    TABLE_NAME = None

    class Field(object):
        _id = '_id'
        create_time = 'create_time'
        update_time = 'update_time'
        is_deleted = 'is_deleted'

    def __init__(self):
        self._id = str(ObjectId())
        now = datetime.now(timezone.utc)
        self.create_time = now
        self.update_time = now
        self.is_deleted = 0

    def to_dict(self):
        return self.__dict__
