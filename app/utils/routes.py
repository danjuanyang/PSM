import os
from flask import Blueprint, jsonify, current_app
from flask_login import current_user, login_required

from . import utils_bp
from .preview import generate_file_preview
from ..models import ProjectFile, AnnouncementAttachment, RoleEnum

# --- 权限检查辅助函数 ---
# 这些函数集中处理不同类型文件的访问权限

def _can_access_project_file(user, file_record):
    """检查用户是否有权访问指定的项目文件"""
    if file_record.is_public or user.role in [RoleEnum.SUPER, RoleEnum.ADMIN] or user.id == file_record.upload_user_id:
        return True
    if user.role == RoleEnum.LEADER and file_record.project.employee_id == user.id:
        return True
    # 在此可添加更复杂的项目成员权限检查
    return False


def _can_access_announcement_attachment(user, attachment_record):
    """检查用户是否有权访问指定的公告附件"""
    if attachment_record.announcement.is_active or user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        return True
    return False


# --- 模型和权限的映射表 ---
# 这使得端点可以轻松扩展，以支持未来更多模块的文件

MODEL_MAP = {
    'project': ProjectFile,
    'announcement': AnnouncementAttachment,
    # 未来可在此添加: 'training': TrainingFile
}

PERMISSION_CHECKERS = {
    'project': _can_access_project_file,
    'announcement': _can_access_announcement_attachment,
}


@utils_bp.route('/preview/<string:file_model_name>/<int:file_id>', methods=['GET'])
@login_required
def preview_file(file_model_name, file_id):
    """
    一个通用的文件预览端点。
    - file_model_name: 文件的模块类型 (e.g., 'project', 'announcement')
    - file_id: 文件在对应表中的ID
    """
    ModelClass = MODEL_MAP.get(file_model_name)
    permission_checker = PERMISSION_CHECKERS.get(file_model_name)

    if not ModelClass or not permission_checker:
        return jsonify({"error": "无效的文件类型"}), 404

    file_record = ModelClass.query.get_or_404(file_id)

    if not permission_checker(current_user, file_record):
        return jsonify({"error": "权限不足，无法预览此文件"}), 403

    # 根据不同模型获取文件路径
    file_path = ''
    if file_model_name == 'project':
        file_path = file_record.file_path
    elif file_model_name == 'announcement':
        # 根据公告附件的存储规则重构路径
        upload_time = file_record.uploaded_at
        year = str(upload_time.year)
        month = str(upload_time.month).zfill(2)
        # 注意: 'announcements' 是我们在上传时定义的子目录
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'announcements', year, month,
                                 file_record.stored_filename)

    return generate_file_preview(file_path)

