# PSM/app/files/routes.py


import os
import uuid
from datetime import datetime
import pdfplumber
import docx

from flask import Blueprint, request, jsonify, current_app, send_from_directory, g
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from . import files_bp
from .. import db
from ..models import ProjectFile, StageTask, StatusEnum, RoleEnum, Subproject, User, FileContent, FileContentFts
from ..decorators import permission_required, log_activity

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'zip',
                      'rar'}


def allowed_file(filename):
    """检查文件扩展名是否在允许列表中"""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text_from_file(file_path, file_ext):
    """从文件中提取文本"""
    text = ""
    try:
        if file_ext == 'pdf':
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
        elif file_ext == 'docx':
            doc = docx.Document(file_path)
            for para in doc.paragraphs:
                text += para.text + '\n'
        elif file_ext == 'txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
    except Exception as e:
        current_app.logger.error(f"Error extracting text from {file_path}: {e}")
    return text

def file_to_json(file_record, snippet=None):
    """将ProjectFile对象转换为JSON，可选择性地包含一个代码片段"""
    data = {
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
    if snippet:
        data['snippet'] = snippet
    return data


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
@log_activity('上传文件', action_detail_template='为任务{task_name}上传文件')
def upload_file_for_task(task_id):
    """
    为已完成的任务上传文件
    """
    task = StageTask.query.get_or_404(task_id)
    # 修复：使用 g 对象传递任务名称
    g.log_info = {'task_name': task.name}

    # --- 修复：明确检查 request.files ---
    if 'file' not in request.files:
        return jsonify({"error": "请求中未找到文件部分"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400

    if file and allowed_file(file.filename):
        original_filename = file.filename
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

        # 提取文件内容并存储
        extracted_text = extract_text_from_file(file_path, file_ext)
        if extracted_text:
            file_content = FileContent(file_id=new_file.id, content=extracted_text)
            db.session.add(file_content)
            db.session.commit()
            new_file.text_extracted = True
            db.session.commit()

        return jsonify({"message": "文件上传成功", "file": file_to_json(new_file)}), 201

    return jsonify({"error": "文件类型不允许"}), 400


@files_bp.route('/tasks/<int:task_id>/files', methods=['GET'])
@login_required
@log_activity('查看文件列表', action_detail_template='查看任务{task_name}已上传文件列表')
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
@log_activity('删除文件', action_detail_template='{username}删除文件{file_name}')
def delete_file(file_id):
    """删除文件记录和物理文件，增加细分的权限检查"""
    file_record = ProjectFile.query.get_or_404(file_id)
    g.log_info = {'file_name': file_record.original_name,"username":current_user.username}
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


@files_bp.route('/', methods=['GET'])
@login_required
def get_all_files():
    """
    获取文件列表，支持基于项目、子项目、阶段、任务和公开状态的动态过滤。
    - 超级/管理员可以查看所有文件。
    - 项目负责人可以查看其项目下的所有文件。
    - 普通用户可以查看自己上传的、公开的、或参与的子项目下的文件。
    """
    query = ProjectFile.query

    # 提取查询参数
    project_id = request.args.get('project_id', type=int)
    subproject_id = request.args.get('subproject_id', type=int)
    stage_id = request.args.get('stage_id', type=int)
    task_id = request.args.get('task_id', type=int)
    is_public_str = request.args.get('is_public', type=str)

    # 根据参数构建查询
    if project_id:
        query = query.filter(ProjectFile.project_id == project_id)
    if subproject_id:
        query = query.filter(ProjectFile.subproject_id == subproject_id)
    if stage_id:
        query = query.filter(ProjectFile.stage_id == stage_id)
    if task_id:
        query = query.filter(ProjectFile.task_id == task_id)

    if is_public_str is not None:
        if is_public_str.lower() == 'true':
            query = query.filter(ProjectFile.is_public == True)
        elif is_public_str.lower() == 'false':
            query = query.filter(ProjectFile.is_public == False)

    all_files = query.order_by(ProjectFile.upload_date.desc()).all()

    # 根据用户角色进行权限过滤
    user = current_user
    accessible_files = []

    if user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        accessible_files = all_files
    else:
        user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
        leader_project_ids = [p.id for p in user.projects]

        for f in all_files:
            # 检查是否满足任一条件
            if (f.is_public or
                    f.upload_user_id == user.id or
                    f.subproject_id in user_subproject_ids or
                    f.project_id in leader_project_ids):
                accessible_files.append(f)

    return jsonify([file_to_json(f) for f in accessible_files]), 200


@files_bp.route('/search', methods=['GET'])
@login_required
def search_files():
    """
    通过FTS5搜索文件内容和文件名，并返回带有上下文片段的结果。
    """
    query_str = request.args.get('q', '')
    if not query_str:
        return jsonify({"error": "请输入搜索关键词"}), 400

    from sqlalchemy import text, column

    # --- 1. 内容搜索 (FTS) ---
    sql_fts = text("""
        SELECT 
            pf.*, 
            snippet(file_contents_fts, 0, '<b>', '</b>', '...', 15) as snippet
        FROM project_files pf
        JOIN file_contents fc ON pf.id = fc.file_id
        JOIN file_contents_fts fts ON fc.id = fts.content_rowid
        WHERE fts.content MATCH :query
        ORDER BY rank;
    """)
    content_results = db.session.query(ProjectFile, column("snippet")).from_statement(sql_fts).params(query=query_str).all()

    # --- 2. 文件名搜索 (LIKE) ---
    like_query = f"%{query_str}%"
    filename_results = ProjectFile.query.filter(ProjectFile.original_name.like(like_query)).all()

    # --- 3. 合并结果并去重 ---
    merged_results = {}
    # 首先添加内容搜索结果，它们有snippet
    for file_record, snippet in content_results:
        merged_results[file_record.id] = (file_record, snippet)
    
    # 然后添加文件名搜索结果，如果它们不存在于merged_results中
    for file_record in filename_results:
        if file_record.id not in merged_results:
            merged_results[file_record.id] = (file_record, None) # 没有snippet

    # --- 4. 权限过滤 ---
    user = current_user
    accessible_files_json = []
    user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
    leader_project_ids = [p.id for p in user.projects]

    for file_record, snippet in merged_results.values():
        is_accessible = (
            file_record.is_public or
            user.role in [RoleEnum.SUPER, RoleEnum.ADMIN] or
            file_record.upload_user_id == user.id or
            (file_record.subproject_id and file_record.subproject_id in user_subproject_ids) or
            (file_record.project_id and file_record.project_id in leader_project_ids)
        )

        if is_accessible:
            accessible_files_json.append(file_to_json(file_record, snippet))

    return jsonify(accessible_files_json), 200
