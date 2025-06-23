# PSM/app/files/routes.py


import os
import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app, send_from_directory
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
from .. import db
from ..models import ProjectFile, StageTask, StatusEnum, RoleEnum, Subproject, User
from ..decorators import permission_required

# 创建蓝图
files_bp = Blueprint('files', __name__, url_prefix='/api/files')

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'zip',
                      'rar'}


def allowed_file(filename):
    """检查文件扩展名是否在允许列表中"""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def file_to_json(file_record):
    """将ProjectFile对象转换为JSON"""
    return {
        'id': file_record.id,
        'original_name': file_record.original_name,
        'file_name': file_record.file_name,
        'file_path': file_record.file_path,
        'file_type': file_record.file_type,
        'upload_date': file_record.upload_date.isoformat(),
        'is_public': file_record.is_public,
        'uploader_id': file_record.upload_user_id,
        'uploader_name': file_record.upload_user.username if file_record.upload_user else None,
        'task_id': file_record.task_id,
        'stage_id': file_record.stage_id,
        'subproject_id': file_record.subproject_id,
        'project_id': file_record.project_id
    }


def can_access_file(user, file_record):
    """
    检查用户是否有权限访问（查看/下载）特定文件
    """
    # 规则1: 公开文件，所有人可访问
    if file_record.is_public:
        return True

    # 规则2: 超管和管理员可访问所有文件
    if user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        return True

    # 规则3: 文件上传者本人可访问
    if user.id == file_record.upload_user_id:
        return True

    # 规则4: 项目负责人(LEADER)可访问其项目下的所有文件
    if user.role == RoleEnum.LEADER:
        project = file_record.project
        if project and project.employee_id == user.id:
            return True

    # 规则5: 组员(MEMBER)可以访问自己参与的子项目中的所有文件
    if user.role == RoleEnum.MEMBER:
        # 查询用户参与的子项目ID列表
        user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
        if file_record.subproject_id in user_subproject_ids:
            return True

    return False


@files_bp.route('/tasks/<int:task_id>/upload', methods=['POST'])
@login_required
@permission_required('update_task_progress')
def upload_file_for_task(task_id):
    """
    为已完成的任务上传文件
    """
    task = StageTask.query.get_or_404(task_id)

    if task.status != StatusEnum.COMPLETED:
        return jsonify({"error": "任务尚未完成，无法上传文件"}), 403

    if 'file' not in request.files:
        return jsonify({"error": "请求中未找到文件部分"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400

    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        file_ext = original_filename.rsplit('.', 1)[1].lower()

        # 优化存储路径：按模块/年份/月份分桶
        module = 'projects'
        year = str(datetime.now().year)
        month = str(datetime.now().month).zfill(2)
        unique_filename = f"{uuid.uuid4()}.{file_ext}"

        upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], module, year, month)
        os.makedirs(upload_folder, exist_ok=True)

        file_path = os.path.join(upload_folder, unique_filename)
        file.save(file_path)

        is_public = request.form.get('is_public', 'false').lower() == 'true'

        stage = task.stage
        subproject = stage.subproject
        project = subproject.project

        new_file = ProjectFile(
            project_id=project.id, subproject_id=subproject.id, stage_id=stage.id,
            task_id=task.id, upload_user_id=current_user.id, original_name=original_filename,
            file_name=unique_filename, file_path=file_path, file_type=file_ext, is_public=is_public
        )
        db.session.add(new_file)
        db.session.commit()

        return jsonify({"message": "文件上传成功", "file": file_to_json(new_file)}), 201

    return jsonify({"error": "文件类型不允许"}), 400


@files_bp.route('/tasks/<int:task_id>/files', methods=['GET'])
@login_required
def get_task_files(task_id):
    """获取指定任务下有权限查看的文件列表"""
    task = StageTask.query.get_or_404(task_id)
    all_files = ProjectFile.query.filter_by(task_id=task.id).all()

    # 根据权限过滤文件列表
    accessible_files = [f for f in all_files if can_access_file(current_user, f)]

    return jsonify([file_to_json(f) for f in accessible_files]), 200


@files_bp.route('/download/<int:file_id>', methods=['GET'])
@login_required
def download_file(file_id):
    """下载文件，增加细分的权限检查"""
    file_record = ProjectFile.query.get_or_404(file_id)

    # 使用统一的权限检查函数
    if not can_access_file(current_user, file_record):
        return jsonify({"error": "权限不足，无法下载此文件"}), 403

    # 注意：send_from_directory的directory参数需要是绝对路径
    # file_path存储的是绝对路径，所以我们需要它的目录部分
    directory = os.path.dirname(file_record.file_path)
    filename = os.path.basename(file_record.file_path)

    try:
        return send_from_directory(
            directory=directory,
            path=filename,
            as_attachment=True,
            download_name=file_record.original_name
        )
    except FileNotFoundError:
        return jsonify({"error": "文件未在服务器上找到"}), 404


@files_bp.route('/<int:file_id>', methods=['DELETE'])
@login_required
def delete_file(file_id):
    """删除文件记录和物理文件，增加细分的权限检查"""
    file_record = ProjectFile.query.get_or_404(file_id)

    # 权限检查：只有上传者或管理员/项目负责人可以删除
    is_uploader = file_record.upload_user_id == current_user.id

    is_manager = False
    if current_user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        is_manager = True
    elif current_user.role == RoleEnum.LEADER:
        project = file_record.project
        if project and project.employee_id == current_user.id:
            is_manager = True

    if not (is_uploader or is_manager):
        return jsonify({"error": "权限不足"}), 403

    try:
        os.remove(file_record.file_path)
        db.session.delete(file_record)
        db.session.commit()
        return jsonify({"message": f"文件 '{file_record.original_name}' 已成功删除"}), 200
    except FileNotFoundError:
        db.session.delete(file_record)
        db.session.commit()
        return jsonify({"message": "文件已从数据库删除，但物理文件未找到"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"删除文件时出错: {str(e)}"}), 500


# 获取所有公开文件
@files_bp.route('/public', methods=['GET'])
def get_public_files():
    """获取所有公开文件"""
    files = ProjectFile.query.filter_by(is_public=True).all()
    return jsonify([file_to_json(f) for f in files]), 200