"""
    test_conversation.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    对话业务逻辑单元测试

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from app.constants import BizCode
from app.logic.conversation import (
    _doc_to_response,
    logic_create_conversation,
    logic_delete_conversation,
    logic_get_conversation_list,
    logic_update_conversation_title,
)
from app.models.chat.chat_model import Conversation
from app.utils.exception_handler import AppException
from app.utils.time_helper import datetime2timestamp


# ---------------------------------------------------------------------------
# Helpers — 直接操作 Conversation 类属性以绕过 BaseModelMeta 懒加载
# ---------------------------------------------------------------------------


def _set_mock_collections(*, a_p_col=None, a_s_col=None):
    """将 mock collection 注入到 Conversation 类上。

    直接 setattr 绕过 BaseModelMeta.__getattr__ 的懒加载逻辑，
    避免触发 get_mongo() 调用。
    """
    if a_p_col is not None:
        Conversation.a_p_col = a_p_col
    if a_s_col is not None:
        Conversation.a_s_col = a_s_col


def _clear_mock_collections():
    """清理注入到 Conversation 类上的 mock collection 属性。"""
    for attr in ('a_p_col', 'a_s_col'):
        if attr in Conversation.__dict__:
            delattr(Conversation, attr)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_doc() -> dict:
    """构造一个标准的 MongoDB 文档 fixture。"""
    now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
    return {
        '_id': 'conv_001',
        'title': '测试对话',
        'owner_id': 'user_001',
        'last_msg_id': 'msg_001',
        'last_msg_content': '你好',
        'create_time': now,
        'update_time': now,
    }


@pytest.fixture
def mock_a_p_col() -> AsyncMock:
    """构造异步主库 Collection 的 mock，注入到 Conversation 类。"""
    mock = AsyncMock()
    _set_mock_collections(a_p_col=mock)
    yield mock
    _clear_mock_collections()


@pytest.fixture
def mock_a_s_col() -> AsyncMock:
    """构造异步从库 Collection 的 mock，注入到 Conversation 类。"""
    mock = AsyncMock()
    _set_mock_collections(a_s_col=mock)
    yield mock
    _clear_mock_collections()


# ---------------------------------------------------------------------------
# _doc_to_response
# ---------------------------------------------------------------------------


class TestDocToResponse:
    """测试内部辅助函数 _doc_to_response。"""

    def test_basic_conversion(self, sample_doc: dict):
        """验证 MongoDB 文档正确转换为 API 响应字典。"""
        result = _doc_to_response(sample_doc)

        assert result['_id'] == 'conv_001'
        assert result['title'] == '测试对话'
        assert result['owner_id'] == 'user_001'
        assert result['last_msg_id'] == 'msg_001'
        assert result['last_msg_content'] == '你好'

    def test_timestamp_conversion(self, sample_doc: dict):
        """验证时间字段被转换为毫秒时间戳。"""
        result = _doc_to_response(sample_doc)

        expected_ts = datetime2timestamp(sample_doc['create_time'])
        assert result['create_time'] == expected_ts
        assert result['update_time'] == expected_ts

    def test_missing_optional_fields(self):
        """验证可选字段缺失时返回默认空字符串。"""
        now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
        doc = {
            '_id': 'conv_min',
            'create_time': now,
            'update_time': now,
        }

        result = _doc_to_response(doc)

        assert result['title'] == ''
        assert result['owner_id'] == ''
        assert result['last_msg_id'] == ''
        assert result['last_msg_content'] == ''


# ---------------------------------------------------------------------------
# logic_create_conversation
# ---------------------------------------------------------------------------


class TestLogicCreateConversation:
    """测试创建对话逻辑。"""

    @pytest.mark.asyncio
    async def test_create_success(self, mock_a_p_col: AsyncMock):
        """验证成功创建对话并返回正确响应。"""
        result = await logic_create_conversation(owner_id='user_001')

        # 断言 insert_one 被调用
        mock_a_p_col.insert_one.assert_called_once()
        inserted_doc = mock_a_p_col.insert_one.call_args[0][0]

        # 验证插入的文档字段
        assert '_id' in inserted_doc
        assert inserted_doc['owner_id'] == 'user_001'
        assert inserted_doc['title'] == ''
        assert inserted_doc['is_deleted'] == 0
        assert 'create_time' in inserted_doc
        assert 'update_time' in inserted_doc

        # 验证返回结果
        assert result['owner_id'] == 'user_001'
        assert result['title'] == ''
        assert '_id' in result
        assert 'create_time' in result
        assert 'update_time' in result

    @pytest.mark.asyncio
    async def test_create_with_title(self, mock_a_p_col: AsyncMock):
        """验证带标题创建对话。"""
        result = await logic_create_conversation(
            owner_id='user_001',
            title='新对话',
        )

        inserted_doc = mock_a_p_col.insert_one.call_args[0][0]
        assert inserted_doc['title'] == '新对话'
        assert result['title'] == '新对话'

    @pytest.mark.asyncio
    async def test_create_different_owners(self, mock_a_p_col: AsyncMock):
        """验证不同用户创建的对话相互独立。"""
        result_a = await logic_create_conversation(owner_id='user_a')
        result_b = await logic_create_conversation(owner_id='user_b')

        assert result_a['owner_id'] == 'user_a'
        assert result_b['owner_id'] == 'user_b'
        assert result_a['_id'] != result_b['_id']


# ---------------------------------------------------------------------------
# logic_get_conversation_list
# ---------------------------------------------------------------------------


class TestLogicGetConversationList:
    """测试查询对话列表逻辑。"""

    @pytest.mark.asyncio
    async def test_list_with_items(self, mock_a_s_col: AsyncMock):
        """验证正常分页查询返回列表。"""
        now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
        mock_docs = [
            {
                '_id': 'conv_001', 'title': '对话1', 'owner_id': 'user_001',
                'last_msg_id': '', 'last_msg_content': '',
                'create_time': now, 'update_time': now,
            },
            {
                '_id': 'conv_002', 'title': '对话2', 'owner_id': 'user_001',
                'last_msg_id': '', 'last_msg_content': '',
                'create_time': now, 'update_time': now,
            },
        ]

        mock_a_s_col.count_documents = AsyncMock(return_value=2)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=mock_docs)
        mock_a_s_col.find = Mock(return_value=mock_cursor)
        mock_cursor.sort = Mock(return_value=mock_cursor)
        mock_cursor.skip = Mock(return_value=mock_cursor)
        mock_cursor.limit = Mock(return_value=mock_cursor)

        result = await logic_get_conversation_list(
            owner_id='user_001',
            start=0,
            end=20,
        )

        assert result['total'] == 2
        assert len(result['items']) == 2
        assert result['items'][0]['_id'] == 'conv_001'
        assert result['items'][1]['_id'] == 'conv_002'

    @pytest.mark.asyncio
    async def test_list_empty(self, mock_a_s_col: AsyncMock):
        """验证无数据时返回空列表。"""
        mock_a_s_col.count_documents = AsyncMock(return_value=0)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_a_s_col.find = Mock(return_value=mock_cursor)
        mock_cursor.sort = Mock(return_value=mock_cursor)
        mock_cursor.skip = Mock(return_value=mock_cursor)
        mock_cursor.limit = Mock(return_value=mock_cursor)

        result = await logic_get_conversation_list(
            owner_id='user_001',
            start=0,
            end=20,
        )

        assert result['total'] == 0
        assert result['items'] == []

    @pytest.mark.asyncio
    async def test_list_pagination(self, mock_a_s_col: AsyncMock):
        """验证分页参数正确传递到 MongoDB 查询。"""
        mock_a_s_col.count_documents = AsyncMock(return_value=50)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_a_s_col.find = Mock(return_value=mock_cursor)
        mock_cursor.sort = Mock(return_value=mock_cursor)
        mock_cursor.skip = Mock(return_value=mock_cursor)
        mock_cursor.limit = Mock(return_value=mock_cursor)

        await logic_get_conversation_list(
            owner_id='user_001',
            start=10,
            end=30,
        )

        mock_cursor.skip.assert_called_once_with(10)
        mock_cursor.limit.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_list_query_filters_deleted(self, mock_a_s_col: AsyncMock):
        """验证查询时过滤了已删除的对话。"""
        mock_a_s_col.count_documents = AsyncMock(return_value=0)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_a_s_col.find = Mock(return_value=mock_cursor)
        mock_cursor.sort = Mock(return_value=mock_cursor)
        mock_cursor.skip = Mock(return_value=mock_cursor)
        mock_cursor.limit = Mock(return_value=mock_cursor)

        await logic_get_conversation_list(owner_id='user_001')

        actual_query = mock_a_s_col.find.call_args[0][0]
        assert actual_query == {'owner_id': 'user_001', 'is_deleted': 0}

    @pytest.mark.asyncio
    async def test_list_default_pagination(self, mock_a_s_col: AsyncMock):
        """验证不传分页参数时使用默认值 start=0, end=20。"""
        mock_a_s_col.count_documents = AsyncMock(return_value=0)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_a_s_col.find = Mock(return_value=mock_cursor)
        mock_cursor.sort = Mock(return_value=mock_cursor)
        mock_cursor.skip = Mock(return_value=mock_cursor)
        mock_cursor.limit = Mock(return_value=mock_cursor)

        await logic_get_conversation_list(owner_id='user_001')

        mock_cursor.skip.assert_called_once_with(0)
        mock_cursor.limit.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_list_sorted_by_update_time_desc(self, mock_a_s_col: AsyncMock):
        """验证列表按更新时间倒序排列。"""
        mock_a_s_col.count_documents = AsyncMock(return_value=0)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_a_s_col.find = Mock(return_value=mock_cursor)
        mock_cursor.sort = Mock(return_value=mock_cursor)
        mock_cursor.skip = Mock(return_value=mock_cursor)
        mock_cursor.limit = Mock(return_value=mock_cursor)

        await logic_get_conversation_list(owner_id='user_001')

        mock_cursor.sort.assert_called_once_with('update_time', -1)


# ---------------------------------------------------------------------------
# logic_delete_conversation
# ---------------------------------------------------------------------------


class TestLogicDeleteConversation:
    """测试删除对话逻辑。"""

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_a_p_col: AsyncMock):
        """验证成功逻辑删除对话。"""
        mock_result = Mock()
        mock_result.matched_count = 1
        mock_a_p_col.update_one = AsyncMock(return_value=mock_result)

        result = await logic_delete_conversation(
            conversation_id='conv_001',
            owner_id='user_001',
        )

        assert result is True

        # 验证 update_one 的调用参数
        call_args = mock_a_p_col.update_one.call_args
        filter_query = call_args[0][0]
        update_op = call_args[0][1]

        assert filter_query['_id'] == 'conv_001'
        assert filter_query['owner_id'] == 'user_001'
        assert filter_query['is_deleted'] == 0

        assert update_op['$set']['is_deleted'] == 1
        assert 'update_time' in update_op['$set']

    @pytest.mark.asyncio
    async def test_delete_not_found(self, mock_a_p_col: AsyncMock):
        """验证删除不存在的对话时抛出 AppException。"""
        mock_result = Mock()
        mock_result.matched_count = 0
        mock_a_p_col.update_one = AsyncMock(return_value=mock_result)

        with pytest.raises(AppException) as exc_info:
            await logic_delete_conversation(
                conversation_id='conv_not_exist',
                owner_id='user_001',
            )

        assert exc_info.value.code == BizCode.NOT_FOUND
        assert exc_info.value.status_code == 404
        assert '不存在或已删除' in exc_info.value.message

    @pytest.mark.asyncio
    async def test_delete_wrong_owner(self, mock_a_p_col: AsyncMock):
        """验证只能删除自己的对话（owner_id 不匹配时失败）。"""
        mock_result = Mock()
        mock_result.matched_count = 0
        mock_a_p_col.update_one = AsyncMock(return_value=mock_result)

        with pytest.raises(AppException):
            await logic_delete_conversation(
                conversation_id='conv_001',
                owner_id='another_user',
            )

    @pytest.mark.asyncio
    async def test_delete_already_deleted(self, mock_a_p_col: AsyncMock):
        """验证重复删除已删除的对话时抛出异常。"""
        mock_result = Mock()
        mock_result.matched_count = 0
        mock_a_p_col.update_one = AsyncMock(return_value=mock_result)

        with pytest.raises(AppException) as exc_info:
            await logic_delete_conversation(
                conversation_id='conv_001',
                owner_id='user_001',
            )

        assert exc_info.value.code == BizCode.NOT_FOUND


# ---------------------------------------------------------------------------
# logic_update_conversation_title
# ---------------------------------------------------------------------------


class TestLogicUpdateConversationTitle:
    """测试修改对话标题逻辑。"""

    @pytest.mark.asyncio
    async def test_update_success(self, mock_a_p_col: AsyncMock):
        """验证成功修改对话标题。"""
        now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
        updated_doc = {
            '_id': 'conv_001',
            'title': '新标题',
            'owner_id': 'user_001',
            'last_msg_id': '',
            'last_msg_content': '',
            'create_time': now,
            'update_time': now,
        }
        mock_a_p_col.find_one_and_update = AsyncMock(return_value=updated_doc)

        result = await logic_update_conversation_title(
            conversation_id='conv_001',
            owner_id='user_001',
            title='新标题',
        )

        assert result['title'] == '新标题'
        assert result['_id'] == 'conv_001'

        # 验证 find_one_and_update 的 filter 参数
        call_args = mock_a_p_col.find_one_and_update.call_args
        filter_query = call_args[0][0]
        assert filter_query['_id'] == 'conv_001'
        assert filter_query['owner_id'] == 'user_001'
        assert filter_query['is_deleted'] == 0

        # 验证更新操作
        update_op = call_args[0][1]
        assert update_op['$set']['title'] == '新标题'
        assert 'update_time' in update_op['$set']

        # 验证使用了 ReturnDocument.AFTER（pymongo 中即为 True）
        assert call_args[1]['return_document'] is True

    @pytest.mark.asyncio
    async def test_update_not_found(self, mock_a_p_col: AsyncMock):
        """验证修改不存在对话的标题时抛出 AppException。"""
        mock_a_p_col.find_one_and_update = AsyncMock(return_value=None)

        with pytest.raises(AppException) as exc_info:
            await logic_update_conversation_title(
                conversation_id='conv_not_exist',
                owner_id='user_001',
                title='新标题',
            )

        assert exc_info.value.code == BizCode.NOT_FOUND
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_wrong_owner(self, mock_a_p_col: AsyncMock):
        """验证不能修改他人对话的标题。"""
        mock_a_p_col.find_one_and_update = AsyncMock(return_value=None)

        with pytest.raises(AppException):
            await logic_update_conversation_title(
                conversation_id='conv_001',
                owner_id='another_user',
                title='新标题',
            )

    @pytest.mark.asyncio
    async def test_update_empty_title(self, mock_a_p_col: AsyncMock):
        """验证可以将标题更新为空字符串。"""
        now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
        updated_doc = {
            '_id': 'conv_001',
            'title': '',
            'owner_id': 'user_001',
            'last_msg_id': '',
            'last_msg_content': '',
            'create_time': now,
            'update_time': now,
        }
        mock_a_p_col.find_one_and_update = AsyncMock(return_value=updated_doc)

        result = await logic_update_conversation_title(
            conversation_id='conv_001',
            owner_id='user_001',
            title='',
        )

        assert result['title'] == ''
