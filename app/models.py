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


class User(UserMixin,db.Model):
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

    employee = db.relationship('User', backref=db.backref('projects', passive_deletes=True))
    subprojects = db.relationship('Subproject', back_populates='project', lazy='dynamic', cascade='all, delete-orphan')
    updates = db.relationship('ProjectUpdate', back_populates='project', cascade='all, delete-orphan')
    stages = db.relationship('ProjectStage', back_populates='project', lazy='dynamic', cascade='all, delete-orphan')
    files = db.relationship('ProjectFile', back_populates='project', lazy='dynamic')


class Subproject(db.Model):
    __tablename__ = 'subprojects'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    start_date = db.Column(db.DateTime, default=datetime.now)
    deadline = db.Column(db.DateTime)
    progress = db.Column(db.Float, default=0.0)
    status = db.Column(db.Enum(StatusEnum), default=StatusEnum.PENDING)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    project = db.relationship('Project', back_populates='subprojects')
    employee = db.relationship('User', backref='assigned_subprojects')
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


# ------------------- 人力资源相关模型 (HR Models) -------------------

class ReportClockin(db.Model):
    __tablename__ = 'report_clockins'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    report_date = db.Column(db.DateTime, default=datetime.now)
    created_at = db.Column(db.DateTime, default=datetime.now)

    employee = db.relationship('User', backref=db.backref('report_clockins', cascade='all, delete-orphan'))
    details = db.relationship('ReportClockinDetail', back_populates='report', cascade='all, delete-orphan')


class ReportClockinDetail(db.Model):
    __tablename__ = 'report_clockin_details'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('report_clockins.id', ondelete='CASCADE'), nullable=False)
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
    training_month = db.Column(db.String(7), nullable=False, comment="格式: 'YYYY-MM'")
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    material_path = db.Column(db.String(255))
    upload_time = db.Column(db.DateTime)
    create_time = db.Column(db.DateTime, default=datetime.now)

    trainer = db.relationship('User', backref='trainings')
    comments = db.relationship('Comment', back_populates='training', lazy='dynamic', cascade='all, delete-orphan')


class Comment(db.Model):
    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    training_id = db.Column(db.Integer, db.ForeignKey('trainings.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)

    training = db.relationship('Training', back_populates='comments')
    user = db.relationship('User', backref='comments')
    replies = db.relationship('Reply', back_populates='comment', lazy='dynamic', cascade='all, delete-orphan')


class Reply(db.Model):
    __tablename__ = 'replies'
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comments.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.now)

    comment = db.relationship('Comment', back_populates='replies')
    user = db.relationship('User', backref='replies')


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
    user = db.relationship('User', backref='activity_logs')
    session = db.relationship('UserSession', backref='activity_logs')


# ------------------- AI 功能模型 (AI Models) -------------------

class AIApi(db.Model):
    __tablename__ = 'ai_api'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    ai_model = db.Column(db.String(50))
    api_key = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    user = db.relationship('User', backref=db.backref('ai_apis', lazy='dynamic', cascade='all, delete-orphan'))


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
    tokens_used = db.Column(db.Integer, default=0)
    model_version = db.Column(db.String(50))

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
