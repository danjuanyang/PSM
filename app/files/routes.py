# PSM/app/files/routes.py

import os
import uuid
from datetime import datetime
import pdfplumber
import docx
import tempfile
from urllib.parse import quote

from flask import request, jsonify, current_app, send_from_directory, g, send_file, Response, stream_with_context
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from . import files_bp
from .. import db
from ..models import (
    ProjectFile, StageTask, StatusEnum, RoleEnum, Subproject, User, FileContent, Training,
    ProjectStage, Project, FileMergeTask, FileMergeTaskStatusEnum
)
from ..decorators import permission_required, log_activity
from .merge_tasks import generate_preview_task, generate_final_pdf_task, cleanup_temp_files

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {"txt", "pdf", "png", "jpg", "jpeg", "gif", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip",
                      "rar"}
ALLOWED_TRAINING_EXTENSIONS = {"pdf"}

MIME_TYPE_MAPPING = {
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pdf': 'application/pdf',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'txt': 'text/plain'
}


# --- 辅助函数 ---

def allowed_file(filename, allowed_extensions=ALLOWED_EXTENSIONS):
    """检查文件扩展名是否在允许列表中"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions


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
    """将ProjectFile对象转换为JSON"""
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
    """检查用户是否有权限访问（查看/下载）特定文件"""
    if file_record.is_public:
        return True
    if user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        return True
    if user.id == file_record.upload_user_id:
        return True
    if user.role == RoleEnum.LEADER:
        project = file_record.project
        if project and project.employee_id == user.id:
            return True
    if user.role == RoleEnum.MEMBER:
        user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
        if file_record.subproject_id in user_subproject_ids:
            return True
    return False


def highlight_text(text, query):
    """在文本中为搜索关键词添加高亮标记"""
    if not text or not query:
        return text
    try:
        query_lower = query.lower()
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
    """获取匹配内容的上下文预览"""
    if not content or not query:
        return None
    try:
        content_lower = content.lower()
        query_lower = query.lower()
        index = content_lower.find(query_lower)
        if index == -1:
            return None
        start = max(0, index - context_length // 2)
        end = min(len(content), index + len(query) + context_length // 2)
        while start > 0 and content[start - 1].isalnum():
            start -= 1
        while end < len(content) - 1 and content[end].isalnum():
            end += 1
        preview = content[start:end].strip()
        if start > 0:
            preview = f"...{preview}"
        if end < len(content):
            preview = f"{preview}..."
        return highlight_text(preview, query)
    except Exception as e:
        current_app.logger.error(f"生成内容预览时出错: {str(e)}")
        return None


# --- 文件核心路由 ---

@files_bp.route('/upload/training/<int:id>', methods=['POST'])
@login_required
def upload_training_material(id):
    # ... (代码无变化)
    training = Training.query.get_or_404(id)
    if not (current_user.id == training.trainer_id or current_user.id == training.assignee_id):
        return jsonify({'message': '只有指定的培训师或被分配者才能上传材料.'}), 403
    if 'file' not in request.files:
        return jsonify({'message': '无文件部分'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'message': '没有选定的文件'}), 400
    if file and allowed_file(file.filename, ALLOWED_TRAINING_EXTENSIONS):
        filename = secure_filename(file.filename)
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
    # ... (代码无变化)
    task = StageTask.query.get_or_404(task_id)
    g.log_info = {'task_name': task.name}
    if 'file' not in request.files:
        return jsonify({"error": "请求中未找到文件部分"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400
    if file and allowed_file(file.filename):
        original_filename = file.filename
        file_ext = original_filename.rsplit('.', 1)[1].lower()
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
        extracted_text = extract_text_from_file(file_path, file_ext)
        if extracted_text:
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
    # ... (代码无变化)
    task = StageTask.query.get_or_404(task_id)
    all_files = ProjectFile.query.filter_by(task_id=task.id).all()
    accessible_files = [f for f in all_files if can_access_file(current_user, f)]
    return jsonify([file_to_json(f) for f in accessible_files]), 200


@files_bp.route('/download/<int:file_id>', methods=['GET'])
@login_required
def download_file(file_id):
    # ... (代码无变化)
    file_record = ProjectFile.query.get_or_404(file_id)
    if not can_access_file(current_user, file_record):
        return jsonify({"error": "权限不足，无法下载此文件"}), 403
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
    # ... (代码无变化)
    file_record = ProjectFile.query.get_or_404(file_id)
    g.log_info = {'file_name': file_record.original_name, "username": current_user.username}
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


@files_bp.route('/public', methods=['GET'])
def get_public_files():
    # ... (代码无变化)
    files = ProjectFile.query.filter_by(is_public=True).all()
    return jsonify([file_to_json(f) for f in files]), 200


@files_bp.route('/', methods=['GET'])
@login_required
def get_all_files():
    # ... (代码无变化)
    query = ProjectFile.query
    project_id = request.args.get('project_id', type=int)
    subproject_id = request.args.get('subproject_id', type=int)
    stage_id = request.args.get('stage_id', type=int)
    task_id = request.args.get('task_id', type=int)
    is_public_str = request.args.get('is_public', type=str)
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
    user = current_user
    accessible_files = []
    if user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        accessible_files = all_files
    else:
        user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
        leader_project_ids = [p.id for p in user.projects]
        for f in all_files:
            if (
                    f.is_public or
                    f.upload_user_id == user.id or
                    f.subproject_id in user_subproject_ids or
                    f.project_id in leader_project_ids
            ):
                accessible_files.append(f)
    return jsonify([file_to_json(f) for f in accessible_files]), 200


@files_bp.route('/search', methods=['GET'])
@login_required
@log_activity('搜索文件', action_detail_template='搜索关键词: {query}')
def search_files():
    # ... (代码无变化)
    try:
        search_query = request.args.get('q', '').strip()
        visibility = request.args.get('visibility', '')
        subproject_id = request.args.get('subproject_id', type=int)
        project_id = request.args.get('project_id', type=int)
        g.log_info = {'query': search_query}
        if not search_query:
            return jsonify({'error': '搜索关键词不能为空'}), 400
        user = current_user
        user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
        leader_project_ids = [p.id for p in user.projects]
        base_query = ProjectFile.query.options(
            db.joinedload(ProjectFile.project),
            db.joinedload(ProjectFile.subproject),
            db.joinedload(ProjectFile.stage),
            db.joinedload(ProjectFile.task),
            db.joinedload(ProjectFile.upload_user),
            db.joinedload(ProjectFile.content)
        )
        if project_id:
            base_query = base_query.filter(ProjectFile.project_id == project_id)
        if subproject_id:
            base_query = base_query.filter(ProjectFile.subproject_id == subproject_id)
        if user.role not in [RoleEnum.SUPER, RoleEnum.ADMIN]:
            if visibility == 'public':
                base_query = base_query.filter(ProjectFile.is_public == True)
            elif visibility == 'private':
                base_query = base_query.filter(ProjectFile.upload_user_id == user.id)
            else:
                access_conditions = [
                    ProjectFile.upload_user_id == user.id,
                    ProjectFile.is_public == True
                ]
                if user_subproject_ids:
                    access_conditions.append(ProjectFile.subproject_id.in_(user_subproject_ids))
                if leader_project_ids:
                    access_conditions.append(ProjectFile.project_id.in_(leader_project_ids))
                base_query = base_query.filter(or_(*access_conditions))
        else:
            if visibility == 'public':
                base_query = base_query.filter(ProjectFile.is_public == True)
            elif visibility == 'private':
                base_query = base_query.filter(ProjectFile.is_public == False)
        search_conditions = [
            ProjectFile.original_name.ilike(f'%{search_query}%'),
            ProjectFile.file_name.ilike(f'%{search_query}%'),
            ProjectFile.file_type.ilike(f'%{search_query}%'),
        ]
        search_results = base_query \
            .outerjoin(Project).outerjoin(Subproject).outerjoin(ProjectStage).outerjoin(StageTask) \
            .join(User, ProjectFile.upload_user_id == User.id).outerjoin(FileContent) \
            .filter(or_(
            *search_conditions,
            Project.name.ilike(f'%{search_query}%'),
            Subproject.name.ilike(f'%{search_query}%'),
            ProjectStage.name.ilike(f'%{search_query}%'),
            StageTask.name.ilike(f'%{search_query}%'),
            User.username.ilike(f'%{search_query}%'),
            FileContent.content.ilike(f'%{search_query}%')
        )).order_by(ProjectFile.upload_date.desc()).all()
        results = []
        for file_record in search_results:
            try:
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
                if file_record.content and file_record.content.content:
                    preview = get_content_preview(file_record.content.content, search_query, context_length=150)
                    result['contentPreview'] = preview if preview else "无匹配内容"
                    result['hasContent'] = True
                else:
                    result['contentPreview'] = "未提取内容"
                    result['hasContent'] = False
                results.append(result)
            except Exception as e:
                current_app.logger.error(f"处理文件 {file_record.id} 时出错: {str(e)}")
                continue
        return jsonify({'results': results, 'total': len(results), 'query': search_query}), 200
    except Exception as e:
        current_app.logger.error(f"搜索文件时出错: {str(e)}")
        return jsonify({'error': f'搜索失败: {str(e)}'}), 500


@files_bp.route('/preview/<int:file_id>', methods=['GET'])
@login_required
def preview_file(file_id):
    # ... (代码无变化)
    file_record = ProjectFile.query.get_or_404(file_id)
    if not can_access_file(current_user, file_record):
        return jsonify({"error": "权限不足"}), 403
    file_ext = file_record.file_type
    file_path = file_record.file_path
    if not os.path.exists(file_path):
        return jsonify({"error": "文件未在服务器上找到"}), 404
    if file_record.content and file_record.content.content:
        content = file_record.content.content
    else:
        content = extract_text_from_file(file_path, file_ext)
        if content and not file_record.content:
            file_content = FileContent(file_id=file_record.id, content=' '.join(content.split()))
            db.session.add(file_content)
            file_record.text_extracted = True
            db.session.commit()
    mime_type = MIME_TYPE_MAPPING.get(file_ext, 'application/octet-stream')
    return jsonify({
        'id': file_record.id,
        'original_name': file_record.original_name,
        'file_type': file_ext,
        'mime_type': mime_type,
        'content': content,
        'can_download': True
    }), 200


# --- PDF 合并路由 ---
@files_bp.route('/merge/fonts', methods=['GET'])
@login_required
def get_available_fonts():
    """获取可用的字体列表"""
    try:
        font_dir = os.path.join(current_app.root_path, 'fonts')
        if not os.path.isdir(font_dir):
            current_app.logger.warning(f"字体目录不存在: {font_dir}")
            return jsonify({'fonts': []})

        supported_extensions = ('.ttf', '.ttc')
        font_files = [f for f in os.listdir(font_dir) if f.lower().endswith(supported_extensions)]

        fonts_data = []
        for font_file in font_files:
            fonts_data.append({
                'filename': font_file,
                'display_name': os.path.splitext(font_file)[0]
            })

        return jsonify({'fonts': fonts_data})
    except Exception as e:
        current_app.logger.error(f"获取字体列表失败: {e}")
        return jsonify({'error': '无法获取字体列表'}), 500


def can_merge_project_files(user, project):
    """检查用户是否可以合并项目文件"""
    if user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        return True
    if user.role == RoleEnum.LEADER and project.employee_id == user.id:
        return True
    user_subproject_ids = [sp.id for sp in user.assigned_subprojects]
    project_subproject_ids = [sp.id for sp in project.subprojects]
    if any(sp_id in user_subproject_ids for sp_id in project_subproject_ids):
        return True
    return False


@files_bp.route('/merge/start-preview', methods=['POST'])
@login_required
@log_activity('启动文件合并预览', action_detail_template='项目{project_name}启动文件合并预览')
def start_merge_preview():
    """启动文件合并预览任务"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '请求数据不能为空'}), 400

        project_id = data.get('project_id')
        selected_file_ids = data.get('selected_file_ids', [])
        merge_config = data.get('merge_config', {})
        if not project_id:
            return jsonify({'error': '项目ID不能为空'}), 400
        project = Project.query.get(project_id)
        if not project:
            return jsonify({'error': '项目不存在'}), 404
        if not can_merge_project_files(current_user, project):
            return jsonify({'error': '没有权限合并该项目的文件'}), 403
        # 检查是否有可合并的PDF文件
        file_query = ProjectFile.query.filter(ProjectFile.project_id == project_id, ProjectFile.file_type == 'pdf')
        if selected_file_ids:
            file_query = file_query.filter(ProjectFile.id.in_(selected_file_ids))
        if file_query.count() == 0:
            return jsonify({'error': '没有找到可合并的PDF文件'}), 400
        task_id = str(uuid.uuid4())
        # 直接使用从前端获取的完整 merge_config
        merge_task = FileMergeTask(
            task_id=task_id,
            project_id=project_id,
            user_id=current_user.id,
            status=FileMergeTaskStatusEnum.PENDING,
            merge_config=merge_config,  # 使用完整的配置
            selected_file_ids=selected_file_ids if selected_file_ids else None,
            status_message='任务已创建，等待处理...'
        )
        db.session.add(merge_task)
        db.session.commit()
        # 将完整的配置传递给异步任务
        async_task_result = generate_preview_task.delay(
            task_id=task_id,
            project_id=project_id,
            selected_file_ids=selected_file_ids,
            merge_config=merge_config
        )
        current_app.logger.info(f"启动文件合并预览任务: {task_id}, 异步任务ID:{async_task_result.id}")
        return jsonify({
            'task_id': task_id,
            'celery_task_id': async_task_result.id,  # 保持兼容性
            'message': '预览任务已启动'
        }), 200
    except Exception as e:
        current_app.logger.error(f"启动合并预览任务失败: {e}", exc_info=True)
        return jsonify({'error': f'启动预览任务失败: {str(e)}'}), 500


@files_bp.route('/merge/finalize', methods=['POST'])
@login_required
@log_activity('生成最终合并文件', action_detail_template='项目{project_name}生成最终合并文件')
def finalize_merge():
    """生成最终合并文件"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '请求数据不能为空'}), 400
        task_id = data.get('task_id')
        pages_to_delete_indices = data.get('pages_to_delete_indices', [])
        if not task_id:
            return jsonify({'error': '任务ID不能为空'}), 400
        preview_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if not preview_task:
            return jsonify({'error': '预览任务不存在'}), 404
        if preview_task.user_id != current_user.id:
            project = Project.query.get(preview_task.project_id)
            if not can_merge_project_files(current_user, project):
                return jsonify({'error': '没有权限操作该任务'}), 403
        if preview_task.status != FileMergeTaskStatusEnum.PREVIEW_READY:
            return jsonify({'error': '预览任务未就绪，无法生成最终文件'}), 400
        final_task_id = str(uuid.uuid4())
        final_task = FileMergeTask(
            task_id=final_task_id,
            project_id=preview_task.project_id,
            user_id=current_user.id,
            status=FileMergeTaskStatusEnum.PENDING,
            merge_config=preview_task.merge_config,
            selected_file_ids=preview_task.selected_file_ids,
            pages_to_delete_indices=pages_to_delete_indices,
            status_message='正在生成最终文件...'
        )
        db.session.add(final_task)
        db.session.commit()
        celery_task = generate_final_pdf_task.delay(
            task_id=final_task_id,
            project_id=preview_task.project_id,
            selected_file_ids=preview_task.selected_file_ids,
            merge_config=preview_task.merge_config,
            pages_to_delete_indices=pages_to_delete_indices
        )
        current_app.logger.info(f"启动最终合并任务: {final_task_id}, Celery任务ID: {celery_task.id}")
        return jsonify({
            'task_id': final_task_id,
            'celery_task_id': celery_task.id,
            'message': '最终合并任务已启动'
        }), 200
    except Exception as e:
        current_app.logger.error(f"启动最终合并任务失败: {e}")
        return jsonify({'error': f'启动最终合并任务失败: {str(e)}'}), 500


@files_bp.route('/merge/progress/<task_id>', methods=['GET'])
@login_required
def get_task_progress(task_id):
    """获取任务进度"""
    try:
        merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if not merge_task:
            return jsonify({'error': '任务不存在'}), 404
        if merge_task.user_id != current_user.id:
            project = Project.query.get(merge_task.project_id)
            if not can_merge_project_files(current_user, project):
                return jsonify({'error': '没有权限查看该任务'}), 403
        response_data = {
            'task_id': merge_task.task_id,
            'status': merge_task.status.value,
            'progress': merge_task.progress,
            'status_message': merge_task.status_message,
            'created_at': merge_task.created_at.isoformat(),
            'updated_at': merge_task.updated_at.isoformat()
        }
        if merge_task.error_message:
            response_data['error_message'] = merge_task.error_message
        if merge_task.preview_session_id:
            response_data['preview_session_id'] = merge_task.preview_session_id
        if merge_task.preview_image_urls:
            response_data['preview_images'] = merge_task.preview_image_urls
        if merge_task.final_file_path and merge_task.status == FileMergeTaskStatusEnum.COMPLETED:
            response_data['download_ready'] = True
            response_data['final_filename'] = merge_task.final_file_name
        return jsonify(response_data), 200
    except Exception as e:
        current_app.logger.error(f"获取任务进度失败: {e}")
        return jsonify({'error': f'获取任务进度失败: {str(e)}'}), 500


@files_bp.route('/merge/download/<task_id>', methods=['GET'])
@login_required
def download_merged_file(task_id):
    """下载合并后的PDF文件"""
    try:
        merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if not merge_task:
            return jsonify({'error': '任务不存在'}), 404
        if merge_task.user_id != current_user.id:
            project = Project.query.get(merge_task.project_id)
            if not can_merge_project_files(current_user, project):
                return jsonify({'error': '没有权限下载该文件'}), 403
        if merge_task.status != FileMergeTaskStatusEnum.COMPLETED:
            return jsonify({'error': '文件尚未生成完成'}), 400
        if not merge_task.final_file_path or not os.path.exists(merge_task.final_file_path):
            return jsonify({'error': '文件不存在'}), 404

        def generate_file_stream():
            try:
                with open(merge_task.final_file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            except Exception as e:
                current_app.logger.error(f"读取文件流失败: {e}")
                raise

        filename = merge_task.final_file_name or f"merged_{task_id}.pdf"
        encoded_filename = quote(filename)
        response = Response(
            stream_with_context(generate_file_stream()),
            mimetype='application/pdf'
        )
        response.headers["Content-Disposition"] = (
            f"attachment; filename=\"{filename.encode('latin-1', 'replace').decode('latin-1')}\"; "
            f"filename*=UTF-8''{encoded_filename}"
        )
        current_app.logger.info(f"用户 {current_user.username} 下载合并文件: {filename}")
        return response
    except Exception as e:
        current_app.logger.error(f"下载合并文件失败: {e}")
        return jsonify({'error': f'下载文件失败: {str(e)}'}), 500


@files_bp.route('/merge/temp_preview_image/<session_id>/<image_filename>', methods=['GET'])
@login_required
def serve_temp_preview_image(session_id, image_filename):
    """提供临时预览图片"""
    try:
        if ".." in image_filename or image_filename.startswith("/") or ".." in session_id:
            return jsonify({'error': '无效的参数'}), 400
        merge_task = FileMergeTask.query.filter_by(preview_session_id=session_id).first()
        if not merge_task:
            return jsonify({'error': '预览会话不存在'}), 404
        if merge_task.user_id != current_user.id:
            project = Project.query.get(merge_task.project_id)
            if not can_merge_project_files(current_user, project):
                return jsonify({'error': '没有权限访问该预览'}), 403
        temp_base_dir = current_app.config.get('TEMP_DIR', tempfile.gettempdir())
        temp_dir = os.path.join(temp_base_dir, session_id)
        image_path = os.path.join(temp_dir, image_filename)
        if not os.path.exists(image_path):
            return jsonify({'error': '图片不存在'}), 404
        return send_file(image_path, mimetype='image/png')
    except Exception as e:
        current_app.logger.error(f"提供预览图片失败: {e}")
        return jsonify({'error': '获取预览图片失败'}), 500


@files_bp.route('/tasks', methods=['GET'])
@login_required
def get_user_merge_tasks():
    """获取用户的合并任务列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        status_filter = request.args.get('status')
        query = FileMergeTask.query.filter_by(user_id=current_user.id)
        if status_filter:
            try:
                status_enum = FileMergeTaskStatusEnum(status_filter)
                query = query.filter_by(status=status_enum)
            except ValueError:
                return jsonify({'error': '无效的状态筛选参数'}), 400
        query = query.order_by(FileMergeTask.created_at.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        tasks_data = []
        for task in pagination.items:
            task_data = {
                'task_id': task.task_id,
                'project_id': task.project_id,
                'project_name': task.project.name if task.project else None,
                'status': task.status.value,
                'progress': task.progress,
                'status_message': task.status_message,
                'created_at': task.created_at.isoformat(),
                'updated_at': task.updated_at.isoformat()
            }
            if task.completed_at:
                task_data['completed_at'] = task.completed_at.isoformat()
            if task.error_message:
                task_data['error_message'] = task.error_message
            if task.final_file_name and task.status == FileMergeTaskStatusEnum.COMPLETED:
                task_data['download_available'] = True
                task_data['final_filename'] = task.final_file_name
            tasks_data.append(task_data)
        return jsonify({
            'tasks': tasks_data,
            'pagination': {
                'page': pagination.page,
                'per_page': pagination.per_page,
                'total': pagination.total,
                'pages': pagination.pages,
                'has_prev': pagination.has_prev,
                'has_next': pagination.has_next
            }
        }), 200
    except Exception as e:
        current_app.logger.error(f"获取用户合并任务列表失败: {e}")
        return jsonify({'error': '获取任务列表失败'}), 500


@files_bp.route('/task/<task_id>', methods=['DELETE'])
@login_required
def delete_merge_task(task_id):
    """删除合并任务（仅删除记录，不删除生成的文件）"""
    try:
        merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if not merge_task:
            return jsonify({'error': '任务不存在'}), 404
        if merge_task.user_id != current_user.id:
            return jsonify({'error': '没有权限删除该任务'}), 403
        if merge_task.preview_session_id:
            temp_base_dir = current_app.config.get('TEMP_DIR', tempfile.gettempdir())
            temp_dir = os.path.join(temp_base_dir, merge_task.preview_session_id)
            cleanup_temp_files(temp_dir)
        db.session.delete(merge_task)
        db.session.commit()
        current_app.logger.info(f"用户 {current_user.username} 删除合并任务: {task_id}")
        return jsonify({'message': '任务已删除'}), 200
    except Exception as e:
        current_app.logger.error(f"删除合并任务失败: {e}")
        return jsonify({'error': '删除任务失败'}), 500
