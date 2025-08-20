# PSM/app/models.py
# 最终版 - 包含所有模块的完整模型定义
from datetime import datetime
from enum import Enum as PyEnum

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, text, Index, UniqueConstraint
import bcrypt
from flask_login import UserMixin
from . import db


# ------------------- 枚举 (Enums) -------------------
class RoleEnum(PyEnum):
    SUPER = 0
    ADMIN = 1
    LEADER = 2
    MEMBER = 3


class StatusEnum(PyEnum):
    # 已暂停
    PAUSED = 'paused'
    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'


# ------------------- 权限与用户模型 (Permission & User Models) -------------------

class Permission(db.Model):
    __tablename__ = 'permissions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, comment="权限的唯一标识符, e.g., 'edit_project'")
    description = db.Column(db.String(255), comment="权限描述")
    is_active = db.Column(db.Boolean, default=True)


class RolePermission(db.Model):
    __tablename__ = 'role_permissions'
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.Enum(RoleEnum), nullable=False, comment="角色枚举")
    permission_id = db.Column(db.Integer, db.ForeignKey('permissions.id'), nullable=False)
    is_allowed = db.Column(db.Boolean, default=True)
    permission = db.relationship('Permission', backref='role_permissions')


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.Enum(RoleEnum), nullable=False, default=RoleEnum.MEMBER)
    avatar_url = db.Column(db.String(255))
    team_leader_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.now)

    # 关系
    leader = db.relationship('User', remote_side=[id], backref='team_members')
    specific_permissions = db.relationship('UserPermission', back_populates='user', cascade='all, delete-orphan')
    # AI API Key, one-to-one relationship
    ai_api = db.relationship('AIApi', uselist=False, back_populates='user', cascade='all, delete-orphan')


    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    def can(self, permission_name: str) -> bool:
        if self.role == RoleEnum.SUPER:
            return True
        user_perm = UserPermission.query.join(Permission).filter(UserPermission.user_id == self.id,
                                                                 Permission.name == permission_name).first()
        if user_perm:
            return user_perm.is_allowed
        role_perm = RolePermission.query.join(Permission).filter(RolePermission.role == self.role,
                                                                 Permission.name == permission_name).first()
        if role_perm:
            return role_perm.is_allowed
        return False


class UserPermission(db.Model):
    __tablename__ = 'user_permissions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    permission_id = db.Column(db.Integer, db.ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False)
    is_allowed = db.Column(db.Boolean, default=True)
    user = db.relationship('User', back_populates='specific_permissions')
    permission = db.relationship('Permission')
    __table_args__ = (UniqueConstraint('user_id', 'permission_id', name='_user_permission_uc'),)


# ------------------- 项目管理核心模型 (Project Management Core Models) -------------------

class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    start_date = db.Column(db.DateTime, default=datetime.now)
    deadline = db.Column(db.DateTime)
    progress = db.Column(db.Float, default=0.0)
    status = db.Column(db.Enum(StatusEnum), default=StatusEnum.PENDING)
    edit_count = db.Column(db.Integer, default=0, comment="编辑次数")
    total_edit_duration = db.Column(db.Integer, default=0, comment="总编辑时长(秒)")

    employee = db.relationship('User', backref=db.backref('projects', passive_deletes=True))
    subprojects = db.relationship('Subproject', back_populates='project', lazy='dynamic', cascade='all, delete-orphan')
    updates = db.relationship('ProjectUpdate', back_populates='project', cascade='all, delete-orphan')
    stages = db.relationship('ProjectStage', back_populates='project', lazy='dynamic', cascade='all, delete-orphan')
    files = db.relationship('ProjectFile', back_populates='project', lazy='dynamic')


# --- 新增：子项目与成员的关联表 ---
subproject_members = db.Table('subproject_members',
                              db.Column('subproject_id', db.Integer, db.ForeignKey('subprojects.id'), primary_key=True),
                              db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True)
                              )


class Subproject(db.Model):
    __tablename__ = 'subprojects'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    # 移除一对一，改为多对多
    # employee_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    start_date = db.Column(db.DateTime, default=datetime.now)
    deadline = db.Column(db.DateTime)
    progress = db.Column(db.Float, default=0.0)
    status = db.Column(db.Enum(StatusEnum), default=StatusEnum.PENDING)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    edit_count = db.Column(db.Integer, default=0, comment="编辑次数")
    total_edit_duration = db.Column(db.Integer, default=0, comment="总编辑时长(秒)")

    project = db.relationship('Project', back_populates='subprojects')
    # 多对多
    # employee = db.relationship('User', backref='assigned_subprojects')
    members = db.relationship('User', secondary=subproject_members, lazy='subquery',
                              backref=db.backref('assigned_subprojects', lazy=True))

    stages = db.relationship('ProjectStage', back_populates='subproject', lazy='dynamic', cascade='all, delete-orphan')
    files = db.relationship('ProjectFile', back_populates='subproject', lazy='dynamic')


class ProjectStage(db.Model):
    __tablename__ = 'project_stages'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    subproject_id = db.Column(db.Integer, db.ForeignKey('subprojects.id', ondelete='CASCADE'))
    name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    progress = db.Column(db.Integer, default=0)
    status = db.Column(db.Enum(StatusEnum), default=StatusEnum.PENDING)
    edit_count = db.Column(db.Integer, default=0, comment="编辑次数")
    total_edit_duration = db.Column(db.Integer, default=0, comment="总编辑时长(秒)")

    project = db.relationship('Project', back_populates='stages')
    subproject = db.relationship('Subproject', back_populates='stages')
    tasks = db.relationship('StageTask', back_populates='stage', lazy='dynamic', cascade='all, delete-orphan')


class StageTask(db.Model):
    __tablename__ = 'stage_tasks'
    id = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.Integer, db.ForeignKey('project_stages.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    due_date = db.Column(db.Date)
    status = db.Column(db.Enum(StatusEnum), default=StatusEnum.PENDING)
    progress = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    edit_count = db.Column(db.Integer, default=0, comment="编辑次数")
    total_edit_duration = db.Column(db.Integer, default=0, comment="总编辑时长(秒)")

    stage = db.relationship('ProjectStage', back_populates='tasks')
    progress_updates = db.relationship('TaskProgressUpdate', back_populates='task', cascade='all, delete-orphan')


class ProjectUpdate(db.Model):
    __tablename__ = 'project_updates'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    progress = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.now)
    type = db.Column(db.String(50))
    project = db.relationship('Project', back_populates='updates')


class TaskProgressUpdate(db.Model):
    __tablename__ = 'task_progress_updates'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('stage_tasks.id', ondelete='CASCADE'), nullable=False)
    recorder_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    progress = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.now)

    task = db.relationship('StageTask', back_populates='progress_updates')
    recorder = db.relationship('User')


# ------------------- 文件与内容模型 (File & Content Models) -------------------

class ProjectFile(db.Model):
    __tablename__ = 'project_files'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'))
    subproject_id = db.Column(db.Integer, db.ForeignKey('subprojects.id', ondelete='CASCADE'))
    stage_id = db.Column(db.Integer, db.ForeignKey('project_stages.id', ondelete='CASCADE'))
    task_id = db.Column(db.Integer, db.ForeignKey('stage_tasks.id', ondelete='SET NULL'))
    upload_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    original_name = db.Column(db.String(255))
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(100))
    upload_date = db.Column(db.DateTime, default=datetime.now)
    is_public = db.Column(db.Boolean, default=False)
    text_extracted = db.Column(db.Boolean, default=False)

    upload_user = db.relationship('User', backref='uploaded_files')
    project = db.relationship('Project', back_populates='files')
    subproject = db.relationship('Subproject', back_populates='files')
    stage = db.relationship('ProjectStage', backref='files')
    task = db.relationship('StageTask', backref='files')
    content = db.relationship('FileContent', back_populates='file', uselist=False, cascade='all, delete-orphan')


class FileContent(db.Model):
    __tablename__ = 'file_contents'
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('project_files.id', ondelete='CASCADE'), unique=True, nullable=False)
    content = db.Column(db.Text)
    file = db.relationship('ProjectFile', back_populates='content')
    fts_content = db.relationship('FileContentFts', back_populates='file_content_ref', uselist=False, cascade='all, delete-orphan')


class FileContentFts(db.Model):
    __tablename__ = 'file_contents_fts'
    rowid = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text)
    content_rowid = db.Column(db.Integer, db.ForeignKey('file_contents.id'))
    file_content_ref = db.relationship('FileContent', back_populates='fts_content')

@event.listens_for(FileContent, 'after_insert')
@event.listens_for(FileContent, 'after_update')
def update_fts_content(mapper, connection, target):
    if target.id is None or target.content is None:
        return

    # 检查FTS表是否存在
    inspector = db.inspect(db.engine)
    if not inspector.has_table(FileContentFts.__tablename__):
        return

    fts_table = FileContentFts.__table__
    connection.execute(
        fts_table.delete().where(fts_table.c.content_rowid == target.id)
    )
    if target.content:
        connection.execute(
            fts_table.insert().values(
                content_rowid=target.id,
                content=target.content
            )
        )


# ------------------- 人力资源相关模型 (HR Models) -------------------

class ReportClockin(db.Model):
    __tablename__ = 'report_clockins'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    report_date = db.Column(db.DateTime, default=datetime.now)
    created_at = db.Column(db.DateTime, default=datetime.now)

    employee = db.relationship('User', backref=db.backref('report_clockins', cascade='all, delete-orphan'))
    details = db.relationship('ReportClockinDetail', back_populates='report', cascade='all, delete-orphan')


class RequestTypeEnum(PyEnum):
    LEAVE = 'leave'      # 请假 (工作日)
    CLOCK_IN = 'clock_in'  # 补卡 (周末)

class ReportClockinDetail(db.Model):
    __tablename__ = 'report_clockin_details'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('report_clockins.id', ondelete='CASCADE'), nullable=False)
    request_type = db.Column(db.Enum(RequestTypeEnum), nullable=False, default=RequestTypeEnum.CLOCK_IN)
    clockin_date = db.Column(db.Date, nullable=False)
    weekday = db.Column(db.String(20))
    remarks = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)
    report = db.relationship('ReportClockin', back_populates='details')


# ------------------- 公告与培训模型 (Announcement & Training Models) -------------------

class Announcement(db.Model):
    __tablename__ = 'announcements'
    id = db.Column(db.Integer, primary_key=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    priority = db.Column(db.Integer, default=0, comment="0=普通, 1=重要, 2=紧急")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    creator = db.relationship('User', backref='created_announcements')
    read_statuses = db.relationship('AnnouncementReadStatus', back_populates='announcement',
                                    cascade='all, delete-orphan')
    attachments = db.relationship('AnnouncementAttachment', back_populates='announcement', cascade='all, delete-orphan')


class AnnouncementReadStatus(db.Model):
    __tablename__ = 'announcement_read_status'
    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(db.Integer, db.ForeignKey('announcements.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    read_at = db.Column(db.DateTime)

    announcement = db.relationship('Announcement', back_populates='read_statuses')
    user = db.relationship('User', backref='announcement_read_statuses')
    __table_args__ = (UniqueConstraint('announcement_id', 'user_id', name='_announcement_user_uc'),)


class AnnouncementAttachment(db.Model):
    __tablename__ = 'announcement_attachments'
    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(db.Integer, db.ForeignKey('announcements.id', ondelete='CASCADE'), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer)
    file_type = db.Column(db.String(100))
    uploaded_at = db.Column(db.DateTime, default=datetime.now)

    announcement = db.relationship('Announcement', back_populates='attachments')


class Training(db.Model):
    __tablename__ = 'trainings'
    id = db.Column(db.Integer, primary_key=True)
    trainer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assignee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True) # 新增：被分配者
    training_month = db.Column(db.String(7), nullable=False, comment="格式: 'YYYY-MM'")
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    material_path = db.Column(db.String(255))
    upload_time = db.Column(db.DateTime)
    create_time = db.Column(db.DateTime, default=datetime.now)

    trainer = db.relationship('User', foreign_keys=[trainer_id], backref='created_trainings')
    assignee = db.relationship('User', foreign_keys=[assignee_id], backref='assigned_trainings') # 新增：关系
    comments = db.relationship('Comment', back_populates='training', cascade='all, delete-orphan')


class Comment(db.Model):
    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    training_id = db.Column(db.Integer, db.ForeignKey('trainings.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)

    training = db.relationship('Training', back_populates='comments')
    user = db.relationship('User', backref='comments')
    replies = db.relationship('Reply', back_populates='comment', cascade='all, delete-orphan')


class Reply(db.Model):
    __tablename__ = 'replies'
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comments.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('replies.id'), nullable=True)  # 新增, 用于回复的回复
    content = db.Column(db.Text, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)

    comment = db.relationship('Comment', back_populates='replies')
    user = db.relationship('User', backref='replies')
    parent = db.relationship('Reply', remote_side=[id], backref='child_replies')


# ------------------- 用户追踪与日志 (Tracking & Logging Models) -------------------

class UserSession(db.Model):
    __tablename__ = 'user_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    login_time = db.Column(db.DateTime, default=datetime.now)
    logout_time = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    last_activity_time = db.Column(db.DateTime, default=datetime.now)
    session_duration = db.Column(db.Integer, comment="in seconds")
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(255))
    user = db.relationship('User', backref=db.backref('sessions', lazy='dynamic', cascade='all, delete-orphan'))


class UserActivityLog(db.Model):
    __tablename__ = 'user_activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    session_id = db.Column(db.Integer, db.ForeignKey('user_sessions.id', ondelete='SET NULL'))
    action_type = db.Column(db.String(50), nullable=False)
    action_detail = db.Column(db.Text)
    status_code = db.Column(db.Integer)
    request_method = db.Column(db.String(10))
    endpoint = db.Column(db.String(255))
    duration_seconds = db.Column(db.Integer)
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.now)
    resource_type = db.Column(db.String(50))
    resource_id = db.Column(db.Integer)
    module = db.Column(db.String(50), nullable=True, comment="前端模块名，例如 'ai', 'project'")
    user = db.relationship('User', backref='activity_logs')
    session = db.relationship('UserSession', backref='activity_logs')

    def to_dict(self):
        """将此对象序列化为字典。"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.user.username if self.user else 'N/A',
            'session_id': self.session_id,
            'action_type': self.action_type,
            'action_detail': self.action_detail,
            'module': self.module,
            'endpoint': self.endpoint,
            'request_method': self.request_method,
            'status_code': self.status_code,
            'duration_seconds': self.duration_seconds,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'ip_address': self.ip_address,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


# ------------------- AI 功能模型 (AI Models) -------------------
class SystemConfig(db.Model):
    __tablename__ = 'system_configs'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, comment="配置项的键")
    value = db.Column(db.String(255), comment="配置项的值")
    description = db.Column(db.String(255), comment="配置描述")


class AIApi(db.Model):
    __tablename__ = 'ai_api'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True)
    ai_model = db.Column(db.String(50), default='deepseek-chat')
    api_key = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    user = db.relationship('User', back_populates='ai_api')


class AIConversation(db.Model):
    __tablename__ = 'ai_conversations'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    is_archived = db.Column(db.Boolean, default=False)

    user = db.relationship('User', backref=db.backref('ai_conversations', lazy='dynamic', cascade='all, delete-orphan'))
    messages = db.relationship('AIMessage', back_populates='conversation', lazy='dynamic', cascade='all, delete-orphan')
    tags = db.relationship('AITag', secondary='ai_conversation_tags', back_populates='conversations', lazy='dynamic')


class AIMessage(db.Model):
    __tablename__ = 'ai_messages'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('ai_conversations.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    role = db.Column(db.String(10), nullable=False, comment="'user' or 'assistant'")
    created_at = db.Column(db.DateTime, default=datetime.now)
    prompt_tokens = db.Column(db.Integer, nullable=True)
    completion_tokens = db.Column(db.Integer, nullable=True)
    total_tokens = db.Column(db.Integer, nullable=True)
    model_version = db.Column(db.String(50), nullable=True)

    conversation = db.relationship('AIConversation', back_populates='messages')
    feedback = db.relationship('AIMessageFeedback', back_populates='message', lazy='dynamic',
                               cascade='all, delete-orphan')
    __table_args__ = (db.CheckConstraint("role IN ('user', 'assistant', 'system')", name='check_role'),)


class AITag(db.Model):
    __tablename__ = 'ai_tags'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    conversations = db.relationship('AIConversation', secondary='ai_conversation_tags', back_populates='tags',
                                    lazy='dynamic')


class AIConversationTag(db.Model):
    __tablename__ = 'ai_conversation_tags'
    conversation_id = db.Column(db.Integer, db.ForeignKey('ai_conversations.id', ondelete='CASCADE'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('ai_tags.id', ondelete='CASCADE'), primary_key=True)


class AIMessageFeedback(db.Model):
    __tablename__ = 'ai_message_feedback'
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('ai_messages.id', ondelete='CASCADE'), nullable=False)
    rating = db.Column(db.Integer, nullable=False, comment="1 for like, -1 for dislike")
    feedback_text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

    message = db.relationship('AIMessage', back_populates='feedback')
    __table_args__ = (db.CheckConstraint("rating IN (1, -1)", name='check_rating'),)


# ------------------- 数据库索引 (Indexes) -------------------
Index('idx_ai_messages_conversation_id', AIMessage.conversation_id)
Index('idx_ai_conversations_user_id', AIConversation.user_id)
Index('idx_ai_conversations_updated_at', AIConversation.updated_at)
Index('idx_ai_message_feedback_message_id', AIMessageFeedback.message_id)


# ------------------- 文件合并模型 (File Merge Models) -------------------
class FileMergeTaskStatusEnum(PyEnum):
    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    GENERATING_PREVIEW = 'generating_preview'
    PREVIEW_READY = 'preview_ready'
    GENERATING_FINAL = 'generating_final'
    COMPLETED = 'completed'
    FAILED = 'failed'


class FileMergeTask(db.Model):
    __tablename__ = 'file_merge_tasks' 
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.String(100), unique=True, nullable=False, comment="Celery任务ID")
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.Enum(FileMergeTaskStatusEnum), default=FileMergeTaskStatusEnum.PENDING)
    progress = db.Column(db.Integer, default=0, comment="进度百分比 0-100")
    
    # 合并配置
    merge_config = db.Column(db.JSON, comment="合并配置JSON")
    selected_file_ids = db.Column(db.JSON, comment="选中的文件ID列表")
    pages_to_delete_indices = db.Column(db.JSON, comment="删除的页面索引列表")
    
    # 预览相关
    preview_session_id = db.Column(db.String(100), comment="预览会话ID")
    preview_image_urls = db.Column(db.JSON, comment="预览图片URL列表")
    
    # 结果文件
    final_file_path = db.Column(db.String(500), comment="最终合并文件路径")
    final_file_name = db.Column(db.String(255), comment="最终文件名")
    
    # 状态和时间
    status_message = db.Column(db.String(500), comment="状态消息")
    error_message = db.Column(db.Text, comment="错误信息")
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    completed_at = db.Column(db.DateTime, comment="完成时间")
    
    # 关系
    project = db.relationship('Project', backref='merge_tasks')
    user = db.relationship('User', backref='merge_tasks')


# ------------------- 提醒模型 (Alerts) -------------------
class Alert(db.Model):
    __tablename__ = 'alerts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    message = db.Column(db.String(512), nullable=False)
    # alert_type 用于避免重复生成同类提醒
    alert_type = db.Column(db.String(50), nullable=False)
    # related_key 用于唯一标识一个提醒事件，如 'project_deadline_15_days_35'
    related_key = db.Column(db.String(100), unique=True, nullable=False)
    # related_url 方便前端点击跳转
    related_url = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', backref=db.backref('alerts', cascade='all, delete-orphan'))


# ------------------- 实体编辑活动模型 (Entity Edit Activity) -------------------
class UserEntityActivity(db.Model):
    __tablename__ = 'user_entity_activities'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    entity_type = db.Column(db.String(50), nullable=False, comment="实体类型, e.g., 'project', 'task'")
    entity_id = db.Column(db.Integer, nullable=False, comment="实体ID")
    duration_seconds = db.Column(db.Integer, nullable=False, comment="本次编辑时长(秒)")
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', backref=db.backref('entity_activities', cascade='all, delete-orphan'))


# ------------------- 邮件系统模型 (Email System Models) -------------------

class EmailTemplateTypeEnum(PyEnum):
    WEEKLY_REPORT = 'weekly_report'  # 周报
    MONTHLY_REPORT = 'monthly_report'  # 月报
    CLOCK_IN_SUMMARY = 'clock_in_summary'  # 补卡汇总
    PROJECT_DEADLINE = 'project_deadline'  # 项目到期提醒
    CUSTOM = 'custom'  # 自定义模板


class EmailTaskFrequencyEnum(PyEnum):
    ONCE = 'once'  # 一次性
    DAILY = 'daily'  # 每天
    WEEKLY = 'weekly'  # 每周
    MONTHLY = 'monthly'  # 每月


class EmailStatusEnum(PyEnum):
    PENDING = 'pending'  # 待发送
    SENDING = 'sending'  # 发送中
    SUCCESS = 'success'  # 发送成功
    FAILED = 'failed'  # 发送失败
    CANCELLED = 'cancelled'  # 已取消


class EmailConfig(db.Model):
    """邮件配置表 - 存储SMTP服务器配置"""
    __tablename__ = 'email_configs'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, comment="配置名称")
    smtp_host = db.Column(db.String(255), nullable=False, comment="SMTP服务器地址")
    smtp_port = db.Column(db.Integer, default=587, comment="SMTP端口")
    smtp_use_tls = db.Column(db.Boolean, default=True, comment="是否使用TLS")
    smtp_use_ssl = db.Column(db.Boolean, default=False, comment="是否使用SSL")
    sender_email = db.Column(db.String(255), nullable=False, comment="发件人邮箱")
    sender_name = db.Column(db.String(100), comment="发件人名称")
    username = db.Column(db.String(255), nullable=False, comment="认证用户名")
    password = db.Column(db.String(255), nullable=False, comment="认证密码(加密存储)")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    is_default = db.Column(db.Boolean, default=False, comment="是否为默认配置")
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    tasks = db.relationship('EmailTask', back_populates='email_config', cascade='all, delete-orphan')
    logs = db.relationship('EmailLog', back_populates='email_config')


class EmailTemplate(db.Model):
    """邮件模板表 - 存储邮件模板内容"""
    __tablename__ = 'email_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, comment="模板名称")
    template_type = db.Column(db.Enum(EmailTemplateTypeEnum), nullable=False, comment="模板类型")
    subject = db.Column(db.String(255), nullable=False, comment="邮件主题(支持变量)")
    body_html = db.Column(db.Text, comment="HTML格式邮件内容")
    body_text = db.Column(db.Text, comment="纯文本格式邮件内容")
    variables = db.Column(db.JSON, comment="可用变量列表及说明")
    description = db.Column(db.String(500), comment="模板描述")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    creator = db.relationship('User', backref='created_email_templates')
    tasks = db.relationship('EmailTask', back_populates='template', cascade='all, delete-orphan')


class EmailRecipientGroup(db.Model):
    """邮件接收组表 - 管理收件人分组"""
    __tablename__ = 'email_recipient_groups'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True, comment="组名称")
    description = db.Column(db.String(255), comment="组描述")
    # 收件人配置: 可以是角色、具体用户ID列表、邮箱列表
    recipient_roles = db.Column(db.JSON, comment="接收角色列表，如['LEADER', 'MEMBER']")
    recipient_user_ids = db.Column(db.JSON, comment="具体用户ID列表")
    recipient_emails = db.Column(db.JSON, comment="外部邮箱列表")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    tasks = db.relationship('EmailTask', back_populates='recipient_group')


class EmailTask(db.Model):
    """邮件任务表 - 定时任务配置"""
    __tablename__ = 'email_tasks'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, comment="任务名称")
    description = db.Column(db.String(500), comment="任务描述")
    template_id = db.Column(db.Integer, db.ForeignKey('email_templates.id', ondelete='SET NULL'))
    email_config_id = db.Column(db.Integer, db.ForeignKey('email_configs.id', ondelete='SET NULL'))
    recipient_group_id = db.Column(db.Integer, db.ForeignKey('email_recipient_groups.id', ondelete='SET NULL'))
    
    # 调度配置
    frequency = db.Column(db.Enum(EmailTaskFrequencyEnum), nullable=False, comment="执行频率")
    cron_expression = db.Column(db.String(100), comment="Cron表达式(高级调度)")
    send_time = db.Column(db.Time, comment="发送时间")
    send_day_of_week = db.Column(db.Integer, comment="每周几发送(0-6, 0=周一)")
    send_day_of_month = db.Column(db.Integer, comment="每月几号发送(1-31)")
    
    # 数据配置
    data_query_config = db.Column(db.JSON, comment="数据查询配置")
    additional_recipients = db.Column(db.JSON, comment="额外收件人列表")
    
    # 状态
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    last_run_at = db.Column(db.DateTime, comment="上次执行时间")
    next_run_at = db.Column(db.DateTime, comment="下次执行时间")
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    template = db.relationship('EmailTemplate', back_populates='tasks')
    email_config = db.relationship('EmailConfig', back_populates='tasks')
    recipient_group = db.relationship('EmailRecipientGroup', back_populates='tasks')
    creator = db.relationship('User', backref='created_email_tasks')
    logs = db.relationship('EmailLog', back_populates='task', cascade='all, delete-orphan')


class EmailLog(db.Model):
    """邮件发送记录表 - 记录所有邮件发送历史"""
    __tablename__ = 'email_logs'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('email_tasks.id', ondelete='SET NULL'))
    email_config_id = db.Column(db.Integer, db.ForeignKey('email_configs.id', ondelete='SET NULL'))
    
    # 邮件内容
    subject = db.Column(db.String(255), nullable=False, comment="邮件主题")
    body = db.Column(db.Text, comment="邮件内容")
    recipients = db.Column(db.JSON, nullable=False, comment="收件人列表")
    cc_recipients = db.Column(db.JSON, comment="抄送列表")
    bcc_recipients = db.Column(db.JSON, comment="密送列表")
    
    # 发送状态
    status = db.Column(db.Enum(EmailStatusEnum), default=EmailStatusEnum.PENDING, comment="发送状态")
    error_message = db.Column(db.Text, comment="错误信息")
    retry_count = db.Column(db.Integer, default=0, comment="重试次数")
    
    # 时间记录
    scheduled_at = db.Column(db.DateTime, comment="计划发送时间")
    sent_at = db.Column(db.DateTime, comment="实际发送时间")
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # 关系
    task = db.relationship('EmailTask', back_populates='logs')
    email_config = db.relationship('EmailConfig', back_populates='logs')


# 添加索引
Index('idx_email_logs_task_id', EmailLog.task_id)
Index('idx_email_logs_status', EmailLog.status)
Index('idx_email_logs_created_at', EmailLog.created_at)
Index('idx_email_tasks_next_run_at', EmailTask.next_run_at)
