import os
import uuid
from flask import Blueprint, request, jsonify, current_app, send_from_directory, g
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
from datetime import datetime

from . import announcement_bp
from .. import db
from ..models import Announcement, AnnouncementAttachment, AnnouncementReadStatus, User, RoleEnum
from ..decorators import permission_required, log_activity

# --- 辅助函数 (Helper Functions) ---

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'zip',
                      'rar'}


def allowed_file(filename):
    """检查文件扩展名"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def announcement_to_json(announcement, user_id=None):
    """将Announcement对象转换为JSON"""
    attachments_json = [{
        'id': att.id,
        'original_filename': att.original_filename,
        'file_size': att.file_size,
        'uploaded_at': att.uploaded_at.isoformat()
    } for att in announcement.attachments]

    is_read = False
    if user_id:
        read_status = AnnouncementReadStatus.query.filter_by(
            announcement_id=announcement.id,
            user_id=user_id
        ).first()
        if read_status and read_status.is_read:
            is_read = True

    return {
        'id': announcement.id,
        'title': announcement.title,
        'content': announcement.content,
        'priority': announcement.priority,
        'is_active': announcement.is_active,
        'created_at': announcement.created_at.isoformat(),
        'updated_at': announcement.updated_at.isoformat(),
        'creator_id': announcement.created_by,
        'creator_name': announcement.creator.username if announcement.creator else None,
        'attachments': attachments_json,
        'is_read_by_current_user': is_read,  # 特定于当前用户的阅读状态
    }


# --- 公告管理接口 ---

@announcement_bp.route('', methods=['POST'])
@login_required
@log_activity('创建公告', f'{current_user}创建公告')
@permission_required('manage_announcements')
def create_announcement():
    """
    发布新公告，支持同时上传多个附件
    """
    if 'title' not in request.form or 'content' not in request.form:
        return jsonify({"error": "标题和内容不能为空"}), 400

    new_announcement = Announcement(
        title=request.form['title'],
        content=request.form['content'],
        priority=request.form.get('priority', 0, type=int),
        created_by=current_user.id
    )
    db.session.add(new_announcement)

    # 处理文件上传
    files = request.files.getlist('attachments')
    for file in files:
        if file and allowed_file(file.filename):
            original_filename = secure_filename(file.filename)
            file_ext = original_filename.rsplit('.', 1)[1].lower()
            stored_filename = f"{uuid.uuid4()}.{file_ext}"

            # 使用分桶存储
            upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'announcements', str(datetime.now().year),
                                         str(datetime.now().month).zfill(2))
            os.makedirs(upload_folder, exist_ok=True)

            file_path = os.path.join(upload_folder, stored_filename)
            file.save(file_path)

            attachment = AnnouncementAttachment(
                announcement=new_announcement,
                original_filename=original_filename,
                stored_filename=stored_filename,
                file_size=os.path.getsize(file_path),
                file_type=file_ext
            )
            db.session.add(attachment)

    db.session.commit()
    return jsonify(announcement_to_json(new_announcement)), 201


@announcement_bp.route('/<int:announcement_id>', methods=['PUT'])
@login_required
@log_activity('更新公告', f'{current_user}更新公告')
@permission_required('manage_announcements')
def update_announcement(announcement_id):
    """
    编辑公告，并重置所有用户的阅读状态
    """
    announcement = Announcement.query.get_or_404(announcement_id)
    data = request.form

    announcement.title = data.get('title', announcement.title)
    announcement.content = data.get('content', announcement.content)
    announcement.priority = data.get('priority', announcement.priority, type=int)
    announcement.updated_at = datetime.now()

    # 重置阅读状态
    AnnouncementReadStatus.query.filter_by(announcement_id=announcement.id).delete()

    # 可以在此处添加更复杂的附件更新逻辑（如删除旧附件）

    db.session.commit()
    return jsonify(announcement_to_json(announcement, current_user.id)), 200


@announcement_bp.route('/<int:announcement_id>/toggle-status', methods=['PUT'])
@login_required
@log_activity('上线/下线公告', f' {current_user}上线/下线公告')
@permission_required('manage_announcements')
def toggle_announcement_status(announcement_id):
    """
    上线或下线公告
    """
    announcement = Announcement.query.get_or_404(announcement_id)
    announcement.is_active = not announcement.is_active
    db.session.commit()
    return jsonify({
        "message": f"公告 '{announcement.title}' 已成功 {'上线' if announcement.is_active else '下线'}",
        "is_active": announcement.is_active
    }), 200


# --- 用户查看接口 ---

@announcement_bp.route('', methods=['GET'])
@login_required
@log_activity('查看公告列表', '{current_user.username}查看公告列表')
def get_announcements():
    """
    获取公告列表
    - 管理员可查看所有公告
    - 普通用户只能查看已上线的公告
    """
    query = Announcement.query
    if current_user.role not in [RoleEnum.ADMIN, RoleEnum.SUPER]:
        query = query.filter_by(is_active=True)

    announcements = query.order_by(Announcement.priority.desc(), Announcement.created_at.desc()).all()

    # 为每个公告附上当前用户的阅读状态
    return jsonify([announcement_to_json(a, current_user.id) for a in announcements]), 200


@announcement_bp.route('/<int:announcement_id>', methods=['GET'])
@login_required
@log_activity('查看公告详情', '{current_user.username}查看公告详情,标记已读')
def get_announcement_detail(announcement_id):
    """
    查看公告详情，并标记为已读
    """
    announcement = Announcement.query.get_or_404(announcement_id)

    # 权限检查：非管理员只能看已上线的公告
    if not announcement.is_active and current_user.role not in [RoleEnum.ADMIN, RoleEnum.SUPER]:
        return jsonify({"error": "公告未上线"}), 404

    # 标记为已读
    read_status = AnnouncementReadStatus.query.filter_by(
        announcement_id=announcement.id,
        user_id=current_user.id
    ).first()
    if not read_status:
        read_status = AnnouncementReadStatus(
            announcement_id=announcement.id,
            user_id=current_user.id,
        )
    read_status.is_read = True
    read_status.read_at = datetime.now()
    db.session.add(read_status)
    db.session.commit()

    return jsonify(announcement_to_json(announcement, current_user.id)), 200


# --- 统计与附件接口 ---

@announcement_bp.route('/<int:announcement_id>/read-status', methods=['GET'])
@login_required
@log_activity('查看公告阅读状态统计', '{current_user.username}查看公告阅读状态统计')
@permission_required('view_announcement_stats')
def get_read_statistics(announcement_id):
    """
    获取指定公告的阅读状态统计
    """
    announcement = Announcement.query.get_or_404(announcement_id)
    all_users = User.query.all()
    read_statuses = {rs.user_id: rs for rs in announcement.read_statuses}

    read_users = []
    unread_users = []

    for user in all_users:
        if user.id in read_statuses and read_statuses[user.id].is_read:
            read_users.append({'id': user.id, 'username': user.username})
        else:
            unread_users.append({'id': user.id, 'username': user.username})

    return jsonify({
        'announcement_id': announcement.id,
        'title': announcement.title,
        'read_count': len(read_users),
        'unread_count': len(unread_users),
        'read_users': read_users,
        'unread_users': unread_users
    }), 200


# @announcement_bp.route('/attachments/<int:attachment_id>/download', methods=['GET'])
# @login_required
# @log_activity('下载附件', '{current_user.username}下载了附件')
# def download_attachment(attachment_id):
#     """
#     下载公告附件
#     """
#     attachment = AnnouncementAttachment.query.get_or_404(attachment_id)
#     announcement = attachment.announcement
#
#     # 权限检查：非管理员只能下载已上线公告的附件
#     if not announcement.is_active and current_user.role not in [RoleEnum.ADMIN, RoleEnum.SUPER]:
#         return jsonify({"error": "无法下载未上线公告的附件"}), 404
#
#     # 从存储路径中解析出目录和文件名
#     directory = os.path.dirname(attachment.file_path)
#     filename = os.path.basename(attachment.file_path)
#
#     try:
#         return send_from_directory(
#             directory=directory,
#             path=filename,
#             as_attachment=True,
#             download_name=attachment.original_filename
#         )
#     except FileNotFoundError:
#         return jsonify({"error": "附件未在服务器上找到"}), 404


# --- 附件下载接口 (关键修复) ---
@announcement_bp.route('/attachments/<int:attachment_id>/download', methods=['GET'])
@login_required
@log_activity('下载附件', '{current_user.username}下载了附件')
def download_attachment(attachment_id):
    """
    下载公告附件
    """
    attachment = AnnouncementAttachment.query.get_or_404(attachment_id)
    announcement = attachment.announcement
    g.log_info = f"{current_user.username}下载了附件"
    # 权限检查
    if not announcement.is_active and current_user.role not in [RoleEnum.ADMIN, RoleEnum.SUPER]:
        return jsonify({"error": "无法下载未上线公告的附件"}), 404

    # --- 修复：根据上传逻辑重新构建文件路径 ---
    upload_time = attachment.uploaded_at
    year = str(upload_time.year)
    month = str(upload_time.month).zfill(2)

    # 获取基础上传目录
    base_upload_folder = current_app.config['UPLOAD_FOLDER']

    # 拼接成完整的目录路径
    directory = os.path.join(base_upload_folder, 'announcements', year, month)

    try:
        return send_from_directory(
            directory=directory,
            path=attachment.stored_filename,  # 使用正确的 stored_filename 字段
            as_attachment=True,
            download_name=attachment.original_filename
        )
    except FileNotFoundError:
        return jsonify({"error": "附件未在服务器上找到"}), 404
