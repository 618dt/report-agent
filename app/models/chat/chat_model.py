"""
    chat_model.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    对话相关表



    :author: lcg
    :date created: 2026/8/1

"""

from pymongo import IndexModel, DESCENDING
from pymongo.errors import OperationFailure

from app.models.base_model import BaseModel


class Message(BaseModel):
    """
    消息模型类

    字段说明：
    * `_id` (str) - 主键为消息id
    * `conversation_id` (str) - 对话id (conversations表主键id)
    * `sender_id` (str) - 发送者id
    * `receiver_id` (str) - 接收id
    * `msg_type` (int) - 消息类型：1-文本, 2-图片, 3-语音, 4-视频, 5-表情, 6-文件
    * `content` (str) - 消息内容，建议存JSON字符串，兼容不同媒体类型的元数据
    * `reply_msg_id` (str | "") - 引用消息ID，若不为空表示回复某条消息
    * `reply_summary` (str | "") - 引用摘要，冗余存储原消息的文本，避免回表查询
    * `status` (int) - 消息状态：0-发送中, 1-成功, 2-失败
    """

    TABLE_NAME = "message"

    class Field(BaseModel.Field):
        conversation_id = 'conversation_id'
        sender_id = 'sender_id'
        receiver_id = 'receiver_id'
        msg_type = 'msg_type'
        content = 'content'
        reply_msg_id = 'reply_msg_id'
        reply_summary = 'reply_summary'
        status = 'status'
        run_id = 'run_id'

    class StatusField(object):
        SENDING = 0
        SUCCESS = 1
        FAIL = 2

    class MsgTypeField(object):
        TEXT = 1
        IMAGE = 2
        VOICE = 3
        VIDEO = 4
        EMOTE = 5
        FILE = 6

    def __init__(
        self,
        conversation_id='',
        sender_id='',
        receiver_id='',
        msg_type=MsgTypeField.TEXT,
        content='',
        reply_msg_id='',
        reply_summary='',
        status=StatusField.SENDING,
        run_id='',
    ):
        super().__init__()
        self.conversation_id = conversation_id
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.msg_type = msg_type
        self.content = content
        self.reply_msg_id = reply_msg_id
        self.reply_summary = reply_summary
        self.status = status
        self.run_id = run_id

    INDEXES = [
        IndexModel([(Field.sender_id, DESCENDING)], sparse=False, unique=False, background=True),
        IndexModel([(Field.conversation_id, DESCENDING)], sparse=False, unique=False, background=True),
        IndexModel([(Field.create_time, DESCENDING)], sparse=False, unique=False, background=True),
    ]

    @classmethod
    def create_index(cls):
        print(f'创建 {cls.TABLE_NAME} 索引')
        try:
            cls.p_col.create_indexes(indexes=cls.INDEXES)
        except OperationFailure as e:
            print(e)
        except Exception as e:
            print(e)


class Conversation(BaseModel):
    """
    对话模型类

    字段说明：
    * `_id` (str) - 主键id, 即对话id
    * `title` (str) - 对话标题,(由ai生成或用户自定义)
    * `owner_id` (str) - 所有者用户id，这条记录属于哪个用户
    * `last_msg_id` (str) - 最后一条消息id，用于展示列表时的预览
    * `last_msg_content` (str) - 最后一条内容摘要，冗余存储避免查消息表
    """

    TABLE_NAME = "conversations"

    class Field(BaseModel.Field):
        owner_id = 'owner_id'
        title = 'title'
        last_msg_id = 'last_msg_id'
        last_msg_content = 'last_msg_content'

    def __init__(
        self,
        title='',
        owner_id='',
        last_msg_id='',
        last_msg_content='',
    ):
        super().__init__()
        self.title = title
        self.owner_id = owner_id
        self.last_msg_id = last_msg_id
        self.last_msg_content = last_msg_content

    INDEXES = [
        IndexModel([(Field.owner_id, DESCENDING)], sparse=False, unique=False, background=True),
    ]

    @classmethod
    def create_index(cls):
        print(f'创建 {cls.TABLE_NAME} 索引')
        try:
            cls.p_col.create_indexes(indexes=cls.INDEXES)
        except OperationFailure as e:
            print(e)
        except Exception as e:
            print(e)


class ChatRun(BaseModel):
    """Agent 执行记录模型

    每次调用 /api/chat/stream 创建一个 run，记录一次用户提问到 Assistant
    最终回答（或中断）的完整执行过程。

    字段说明：
    * ``_id`` (str) — run_id，主键
    * ``conversation_id`` (str) — 所属会话 ID
    * ``user_message_id`` (str) — 用户消息 ID
    * ``assistant_message_id`` (str) — Assistant 最终消息 ID（完成后回填）
    * ``owner_id`` (str) — 所有者用户 ID
    * ``status`` (str) — 执行状态：running / interrupted / success / failed / cancelled
    * ``deep_thinking`` (bool) — 是否深度思考（True=max；False=关闭思考）
    * ``plan_mode`` (bool) — 是否开启 Plan 模式（先规划确认再执行）
    * ``plan_confirmed`` (bool) — Plan 模式下用户是否已确认计划
    * ``plan`` (dict) — 当前执行计划快照（含 steps 状态）
    * ``outline`` (dict) — 用户确认后的报告章节大纲快照
    * ``started_at`` (datetime) — 开始时间
    * ``completed_at`` (datetime) — 完成时间
    * ``interrupted_at`` (datetime) — 中断时间
    * ``error`` (dict) — 错误信息 {"code": "...", "message": "..."}
    * ``usage`` (dict) — token 用量累计
      ``{input_tokens, output_tokens, total_tokens, model?, estimated?}``
    * ``partial_content`` (str) — 流式正文快照（节流更新，供刷新恢复）
    * ``partial_report`` (dict) — 报告流式草稿快照（artifact_delta 不落库，供刷新恢复）
      ``{tool_call_id, title, topic, markdown, status}``
    * ``last_seq`` (int) — 已发布业务事件最大 seq（>=0）
    """

    TABLE_NAME = "chat_run"

    class Field(BaseModel.Field):
        conversation_id = 'conversation_id'
        user_message_id = 'user_message_id'
        assistant_message_id = 'assistant_message_id'
        owner_id = 'owner_id'
        status = 'status'
        deep_thinking = 'deep_thinking'
        plan_mode = 'plan_mode'
        plan_confirmed = 'plan_confirmed'
        plan = 'plan'
        outline = 'outline'
        started_at = 'started_at'
        completed_at = 'completed_at'
        interrupted_at = 'interrupted_at'
        error = 'error'
        usage = 'usage'
        partial_content = 'partial_content'
        partial_report = 'partial_report'
        last_seq = 'last_seq'

    class StatusField(object):
        RUNNING = "running"
        INTERRUPTED = "interrupted"
        SUCCESS = "success"
        FAILED = "failed"
        CANCELLED = "cancelled"

    def __init__(
        self,
        conversation_id='',
        user_message_id='',
        assistant_message_id='',
        owner_id='',
        status=StatusField.RUNNING,
        deep_thinking=False,
        plan_mode=False,
        plan_confirmed=False,
        plan=None,
        outline=None,
        started_at=None,
        completed_at=None,
        interrupted_at=None,
        error=None,
        usage=None,
        partial_content='',
        partial_report=None,
        last_seq=0,
    ):
        super().__init__()
        self.conversation_id = conversation_id
        self.user_message_id = user_message_id
        self.assistant_message_id = assistant_message_id
        self.owner_id = owner_id
        self.status = status
        self.deep_thinking = bool(deep_thinking)
        self.plan_mode = bool(plan_mode)
        self.plan_confirmed = bool(plan_confirmed)
        self.plan = plan
        self.outline = outline
        self.started_at = started_at or self.create_time
        self.completed_at = completed_at
        self.interrupted_at = interrupted_at
        self.error = error
        self.usage = usage
        self.partial_content = partial_content or ''
        self.partial_report = partial_report
        self.last_seq = int(last_seq or 0)

    INDEXES = [
        IndexModel(
            [(Field.conversation_id, DESCENDING), (Field.create_time, DESCENDING)],
            sparse=False, unique=False, background=True,
        ),
        IndexModel(
            [(Field.owner_id, DESCENDING), (Field.update_time, DESCENDING)],
            sparse=False, unique=False, background=True,
        ),
        IndexModel(
            [(Field.status, 1), (Field.update_time, DESCENDING)],
            sparse=False, unique=False, background=True,
        ),
    ]

    @classmethod
    def create_index(cls):
        print(f'创建 {cls.TABLE_NAME} 索引')
        try:
            cls.p_col.create_indexes(indexes=cls.INDEXES)
        except OperationFailure as e:
            print(e)
        except Exception as e:
            print(e)


class ChatRunEvent(BaseModel):
    """Agent 执行事件模型

    记录一次 Agent Run 中的中间步骤事件：工具调用、工具结果、中断、审批、错误等。

    字段说明：
    * ``_id`` (str) — 事件 ID
    * ``conversation_id`` (str) — 所属会话 ID
    * ``run_id`` (str) — 所属 ChatRun 的 _id
    * ``parent_message_id`` (str) — 关联的 Assistant 消息 ID（可在消息创建后回填）
    * ``seq`` (int) — run 内单调递增序号
    * ``type`` (str) — 事件类型：reasoning / tool_call / tool_result / interrupt / approval / artifact / plan / outline / error
    * ``payload`` (dict) — 事件负载数据
    * ``visible`` (bool) — 是否对用户可见
    """

    TABLE_NAME = "chat_run_event"

    class Field(BaseModel.Field):
        conversation_id = 'conversation_id'
        run_id = 'run_id'
        parent_message_id = 'parent_message_id'
        seq = 'seq'
        type = 'type'
        payload = 'payload'
        visible = 'visible'

    class TypeField(object):
        REASONING = "reasoning"
        TOOL_CALL = "tool_call"
        TOOL_RESULT = "tool_result"
        INTERRUPT = "interrupt"
        APPROVAL = "approval"
        ARTIFACT = "artifact"
        PLAN = "plan"
        OUTLINE = "outline"
        ERROR = "error"

    def __init__(
        self,
        conversation_id='',
        run_id='',
        parent_message_id='',
        seq=0,
        type='',
        payload=None,
        visible=True,
    ):
        super().__init__()
        self.conversation_id = conversation_id
        self.run_id = run_id
        self.parent_message_id = parent_message_id
        self.seq = seq
        self.type = type
        self.payload = payload or {}
        self.visible = visible

    INDEXES = [
        IndexModel(
            [(Field.run_id, 1), (Field.seq, 1)],
            sparse=False, unique=False, background=True,
        ),
        IndexModel(
            [(Field.conversation_id, DESCENDING), (Field.create_time, DESCENDING)],
            sparse=False, unique=False, background=True,
        ),
    ]

    @classmethod
    def create_index(cls):
        print(f'创建 {cls.TABLE_NAME} 索引')
        try:
            cls.p_col.create_indexes(indexes=cls.INDEXES)
        except OperationFailure as e:
            print(e)
        except Exception as e:
            print(e)
