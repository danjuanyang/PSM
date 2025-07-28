# PSM/app/files/routes.py


import os
import uuid
from datetime import datetime
import pdfplumber
import docx

from flask import Blueprint, request, jsonify, current_app, send_from_directory, g
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from . import files_bp
from .. import db
from ..models import ProjectFile, StageTask, StatusEnum, RoleEnum, Subproject, User, FileContent, Training, \
    ProjectStage, Project
from ..decorators import permission_required, log_activity

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'zip',
                      'rar'}
# 增加对PDF的特殊允许列表
ALLOWED_TRAINING_EXTENSIONS = {'pdf'}

MIME_TYPE_MAPPING = {
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pdf': 'application/pdf',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'txt': 'text/plain'
}


def allowed_file(filename, allowed_extensions=ALLOWED_EXTENSIONS):
    """检查文件扩展名是否在允许列表中"""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in allowed_extensions


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
        elif file_ext in ['xls', 'xlsx']:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                for row in ws.rows:
                    row_text = ' '.join(str(cell.value) for cell in row if cell.value is not None)
                    if row_text.strip():
                        text += row_text + '\n'
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




def highlight_text(text, query):
    """
    在文本中为搜索关键词添加高亮标记
    使用特殊标记 {{highlight}} 和 {{/highlight}} 包裹匹配文本
    """
    if not text or not query:
        return text

    try:
        # 不区分大小写
        query_lower = query.lower()
        # 分割文本以保留原始大小写
        parts = []
        last_idx = 0
        text_lower = text.lower()

        while True:
            idx = text_lower.find(query_lower, last_idx)
            if idx == -1:
                parts.append(text[last_idx:])
                break

            parts.append(text[last_idx:idx])
            parts.append("{{highlight}}" + text[idx:idx + len(query)] + "{{/highlight}}")
            last_idx = idx + len(query)

        return "".join(parts)
    except Exception as e:
        current_app.logger.error(f"高亮处理错误: {str(e)}")
        return text


def get_content_preview(content, query, context_length=150):
    """
    获取匹配内容的上下文预览，突出显示匹配的文本
    """
    if not content or not query:
        return None

    try:
        # 转换为小写进行不区分大小写的搜索
        content_lower = content.lower()
        query_lower = query.lower()

        # 查找匹配位置
        index = content_lower.find(query_lower)
        if index == -1:
            return None

        # 计算预览窗口的起始和结束位置
        start = max(0, index - context_length // 2)
        end = min(len(content), index + len(query) + context_length // 2)

        # 调整起始位置到单词边界（如果可能）
        while start > 0 and content[start - 1].isalnum():
            start -= 1

        # 调整结束位置到单词边界（如果可能）
        while end < len(content) - 1 and content[end].isalnum():
            end += 1

        # 构建预览文本
        preview = content[start:end].strip()

        # 添加省略号标记
        if start > 0:
            preview = f"...{preview}"
        if end < len(content):
            preview = f"{preview}..."

        # 为预览文本添加高亮
        return highlight_text(preview, query)

    except Exception as e:
        current_app.logger.error(f"生成内容预览时出错: {str(e)}")
        return None




@files_bp.route('/upload/training/<int:id>', methods=['POST'])
@login_required
def upload_training_material(id):
    """为培训上传材料"""
    training = Training.query.get_or_404(id)
    # 权限检查：只有指定的培训师或被分配者可以上传
    if not (current_user.id == training.trainer_id or current_user.id == training.assignee_id):
        return jsonify({'message': '只有指定的培训师或被分配者才能上传材料.'}), 403

    if 'file' not in request.files:
        return jsonify({'message': '无文件部分'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'message': '没有选定的文件'}), 400

    if file and allowed_file(file.filename, ALLOWED_TRAINING_EXTENSIONS):
        filename = secure_filename(file.filename)
        # 按模块/年份/月份分桶
        upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'training')
        os.makedirs(upload_folder, exist_ok=True)

        file_path = os.path.join(upload_folder, filename)
        file.save(file_path)

        training.material_path = file_path
        training.upload_time = datetime.now()
        training.status = 'completed'
        db.session.commit()
        return jsonify({'message': '文件上传成功。'})
    else:
        return jsonify({'message': '不允许的文件类型，仅支持PDF。'}), 400


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
            # 规范化空白字符，以优化FTS搜索
            processed_text = ' '.join(extracted_text.split())
            file_content = FileContent(file_id=new_file.id, content=processed_text)
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
@log_activity('搜索文件', action_detail_template='搜索关键词: {query}')
def search_files():
    """
    搜索文件功能，支持文件名、内容、项目信息等多维度搜索
    """
    try:
        # 获取搜索参数
        search_query = request.args.get('q', '').strip()
        visibility = request.args.get('visibility', '')
        subproject_id = request.args.get('subproject_id', type=int)
        project_id = request.args.get('project_id', type=int)

        # 设置日志信息
        g.log_info = {'query': search_query}

        if not search_query:
            return jsonify({'error': '搜索关键词不能为空'}), 400

        # 获取当前用户信息
        user = current_user
        user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
        leader_project_ids = [p.id for p in user.projects]

        # 构建基础查询
        base_query = ProjectFile.query.options(
            db.joinedload(ProjectFile.project),
            db.joinedload(ProjectFile.subproject),
            db.joinedload(ProjectFile.stage),
            db.joinedload(ProjectFile.task),
            db.joinedload(ProjectFile.upload_user),
            db.joinedload(ProjectFile.content)
        )

        # 应用过滤条件
        if project_id:
            base_query = base_query.filter(ProjectFile.project_id == project_id)

        if subproject_id:
            base_query = base_query.filter(ProjectFile.subproject_id == subproject_id)

        # 权限过滤
        if user.role not in [RoleEnum.SUPER, RoleEnum.ADMIN]:
            if visibility == 'public':
                base_query = base_query.filter(ProjectFile.is_public == True)
            elif visibility == 'private':
                base_query = base_query.filter(
                    ProjectFile.upload_user_id == user.id
                )
            else:
                # 默认：用户可以看到自己上传的、公开的、或参与子项目的文件
                access_conditions = [
                    ProjectFile.upload_user_id == user.id,
                    ProjectFile.is_public == True
                ]

                # 如果用户参与了子项目，可以查看这些子项目的文件
                if user_subproject_ids:
                    access_conditions.append(ProjectFile.subproject_id.in_(user_subproject_ids))

                # 如果用户是项目负责人，可以查看项目下的所有文件
                if leader_project_ids:
                    access_conditions.append(ProjectFile.project_id.in_(leader_project_ids))

                base_query = base_query.filter(or_(*access_conditions))
        else:
            # 管理员可以查看所有文件，但仍可应用可见性筛选器
            if visibility == 'public':
                base_query = base_query.filter(ProjectFile.is_public == True)
            elif visibility == 'private':
                base_query = base_query.filter(ProjectFile.is_public == False)

        # 构建搜索条件
        search_conditions = [
            ProjectFile.original_name.ilike(f'%{search_query}%'),
            ProjectFile.file_name.ilike(f'%{search_query}%'),
            ProjectFile.file_type.ilike(f'%{search_query}%'),
        ]

        # 执行搜索查询
        search_results = base_query \
            .outerjoin(Project) \
            .outerjoin(Subproject) \
            .outerjoin(ProjectStage) \
            .outerjoin(StageTask) \
            .join(User, ProjectFile.upload_user_id == User.id) \
            .outerjoin(FileContent) \
            .filter(or_(
            *search_conditions,
            Project.name.ilike(f'%{search_query}%'),
            Subproject.name.ilike(f'%{search_query}%'),
            ProjectStage.name.ilike(f'%{search_query}%'),
            StageTask.name.ilike(f'%{search_query}%'),
            User.username.ilike(f'%{search_query}%'),
            FileContent.content.ilike(f'%{search_query}%')
        )) \
            .order_by(ProjectFile.upload_date.desc()) \
            .all()

        # 处理搜索结果
        results = []
        for file_record in search_results:
            try:
                # 获取文件大小
                file_size = 0
                if file_record.file_path and os.path.exists(file_record.file_path):
                    file_size = os.path.getsize(file_record.file_path)

                result = {
                    'id': file_record.id,
                    'fileName': file_record.file_name,
                    'originalName': highlight_text(file_record.original_name, search_query),
                    'fileType': file_record.file_type,
                    'fileSize': file_size,
                    'uploadTime': file_record.upload_date.isoformat(),
                    'uploader': highlight_text(file_record.upload_user.username, search_query),
                    'projectName': highlight_text(file_record.project.name if file_record.project else None,
                                                  search_query),
                    'subprojectName': highlight_text(file_record.subproject.name if file_record.subproject else None,
                                                     search_query),
                    'stageName': highlight_text(file_record.stage.name if file_record.stage else None, search_query),
                    'taskName': highlight_text(file_record.task.name if file_record.task else None, search_query),
                    'is_public': file_record.is_public,
                    'uploader_id': file_record.upload_user_id,
                    'task_id': file_record.task_id,
                    'stage_id': file_record.stage_id,
                    'subproject_id': file_record.subproject_id,
                    'project_id': file_record.project_id
                }

                # 添加内容预览
                if file_record.content and file_record.content.content:
                    preview = get_content_preview(
                        file_record.content.content,
                        search_query,
                        context_length=150
                    )
                    result['contentPreview'] = preview if preview else "无匹配内容"
                    result['hasContent'] = True
                else:
                    result['contentPreview'] = "未提取内容"
                    result['hasContent'] = False

                results.append(result)
            except Exception as e:
                current_app.logger.error(f"处理文件 {file_record.id} 时出错: {str(e)}")
                continue

        return jsonify({
            'results': results,
            'total': len(results),
            'query': search_query
        }), 200

    except Exception as e:
        current_app.logger.error(f"搜索文件时出错: {str(e)}")
        return jsonify({'error': f'搜索失败: {str(e)}'}), 500