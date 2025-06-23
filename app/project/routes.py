from flask import Blueprint, request, jsonify
from flask_login import current_user, login_required

from . import project_bp
from .. import db
from ..models import Project, User, RoleEnum, Subproject, ProjectStage, StageTask, StatusEnum
from ..decorators import permission_required, log_activity
from datetime import datetime


# --- 辅助函数 (Helper Functions) ---
def project_to_json(project):
    """将Project对象转换为JSON格式"""
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "employee_id": project.employee_id,
        "employee_name": project.employee.username if project.employee else None,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "progress": project.progress,
        "status": project.status.value if project.status else None,
        "subproject_count": project.subprojects.count()
    }


def subproject_to_json(subproject):
    """将Subproject对象转换为JSON格式"""
    return {
        "id": subproject.id,
        "project_id": subproject.project_id,
        "name": subproject.name,
        "description": subproject.description,
        "employee_id": subproject.employee_id,
        "employee_name": subproject.employee.username if subproject.employee else None,
        "start_date": subproject.start_date.isoformat() if subproject.start_date else None,
        "deadline": subproject.deadline.isoformat() if subproject.deadline else None,
        "progress": subproject.progress,
        "status": subproject.status.value if subproject.status else None,
        "created_at": subproject.created_at.isoformat(),
        "updated_at": subproject.updated_at.isoformat()
    }


def stage_to_json(stage):
    """将ProjectStage对象转换为JSON格式"""
    return {
        "id": stage.id,
        "project_id": stage.project_id,
        "subproject_id": stage.subproject_id,
        "name": stage.name,
        "description": stage.description,
        "start_date": stage.start_date.isoformat() if stage.start_date else None,
        "end_date": stage.end_date.isoformat() if stage.end_date else None,
        "progress": stage.progress,
        "status": stage.status.value if stage.status else None,
    }


def task_to_json(task):
    """将StageTask对象转换为JSON格式"""
    return {
        "id": task.id,
        "stage_id": task.stage_id,
        "name": task.name,
        "description": task.description,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "progress": task.progress,
        "status": task.status.value if task.status else None,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat()
    }


def can_manage_project_item(item):
    """检查当前用户是否有权管理指定的项目条目(项目/子项目/阶段/任务)"""
    if current_user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        return True

    project = None
    if isinstance(item, Project):
        project = item
    elif isinstance(item, Subproject) or isinstance(item, ProjectStage):
        project = item.project
    elif isinstance(item, StageTask):
        project = item.stage.project

    if project and project.employee_id == current_user.id:
        return True  # 项目负责人

    if isinstance(item, Subproject) and item.employee_id == current_user.id:
        return True  # 子项目负责人

    # 对于任务，分配的成员也有权限
    if isinstance(item, StageTask) and item.stage.subproject.employee_id == current_user.id:
        return True  # 任务所在子项目的负责人

    return False


# --- 项目路由 (Project Routes) ---

@project_bp.route('/projects', methods=['POST'])
@login_required
@log_activity('创建项目',action_detail_template='创建项目')
@permission_required('manage_projects')
def create_project():
    # 获取请求中的JSON数据
    data = request.get_json()
    # 如果没有数据或者没有项目名称，则返回错误信息
    if not data or not data.get('name'):
        return jsonify({"error": "项目名称不能为空"}), 400
    # 创建新项目
    new_project = Project(
        name=data['name'],
        description=data.get('description'),
        employee_id=data.get('employee_id'),
        start_date=datetime.fromisoformat(data['start_date']) if data.get('start_date') else None,
        deadline=datetime.fromisoformat(data['deadline']) if data.get('deadline') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    # 将新项目添加到数据库会话中
    db.session.add(new_project)
    # 提交数据库会话
    db.session.commit()
    # 返回新项目的JSON表示，状态码为201
    return jsonify(project_to_json(new_project)), 201


@project_bp.route('/projects', methods=['GET'])
@log_activity('获取所有项目','获取所有项目')
@login_required
def get_all_projects():
    user = current_user
    query = Project.query
    if user.role == RoleEnum.LEADER:
        query = query.filter(Project.employee_id == user.id)
    elif user.role == RoleEnum.MEMBER:
        subquery = db.session.query(Subproject.project_id).filter(Subproject.employee_id == user.id).distinct()
        query = query.filter(Project.id.in_(subquery))
    projects = query.order_by(Project.id.desc()).all()
    return jsonify([project_to_json(p) for p in projects]), 200


@project_bp.route('/projects/<int:project_id>', methods=['GET'])
@log_activity('获取项目详细信息',action_detail_template='获取项目详细信息')
@login_required
def get_project(project_id):
    project = Project.query.get_or_404(project_id)
    return jsonify(project_to_json(project)), 200


# 定义一个路由，用于更新项目信息
@project_bp.route('/projects/<int:project_id>', methods=['PUT'])
@log_activity('更新项目信息',action_detail_template='更新项目信息')
# 需要登录
@login_required
# 需要有管理项目的权限
@permission_required('manage_projects')
def update_project(project_id):
    # 根据项目id获取项目信息
    project = Project.query.get_or_404(project_id)
    # 判断当前用户是否有权限管理该项目
    if not can_manage_project_item(project):
        # 如果没有权限，返回错误信息
        return jsonify({"error": "权限不足"}), 403
    # 获取请求中的json数据
    data = request.get_json()
    # 更新项目名称
    project.name = data.get('name', project.name)
    # 更新项目描述
    project.description = data.get('description', project.description)
    # 更新项目员工id
    project.employee_id = data.get('employee_id', project.employee_id)
    # 更新项目开始日期
    project.start_date = datetime.fromisoformat(data['start_date']) if data.get('start_date') else project.start_date
    # 更新项目截止日期
    project.deadline = datetime.fromisoformat(data['deadline']) if data.get('deadline') else project.deadline
    # 更新项目进度
    project.progress = data.get('progress', project.progress)
    # 更新项目状态
    if data.get('status'):
        project.status = StatusEnum[data.get('status').upper()]
    # 提交更改
    db.session.commit()
    return jsonify(project_to_json(project)), 200


@project_bp.route('/projects/<int:project_id>', methods=['DELETE'])
@login_required
@log_activity('删除项目',action_detail_template='删除项目')
@permission_required('delete_projects')
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    if not current_user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]:
        return jsonify({"error": "权限不足"}), 403
    db.session.delete(project)
    db.session.commit()
    return jsonify({"message": "项目已删除"}), 200


# --- 子项目路由 (Subproject Routes) ---

@project_bp.route('/projects/<int:project_id>/subprojects', methods=['POST'])
@login_required
@log_activity('创建子项目',action_detail_template='创建子项目')
@permission_required('manage_subprojects')
def create_subproject(project_id):
    project = Project.query.get_or_404(project_id)
    if not can_manage_project_item(project):
        return jsonify({"error": "权限不足"}), 403
    data = request.get_json()
    new_subproject = Subproject(
        project_id=project_id,
        name=data['name'],
        description=data.get('description'),
        employee_id=data.get('employee_id'),
        start_date=datetime.fromisoformat(data['start_date']) if data.get('start_date') else None,
        deadline=datetime.fromisoformat(data['deadline']) if data.get('deadline') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_subproject)
    db.session.commit()
    return jsonify(subproject_to_json(new_subproject)), 201


@project_bp.route('/projects/<int:project_id>/subprojects', methods=['GET'])
@login_required
@log_activity('获取项目下的所有子项目',action_detail_template='获取项目下的所有子项目')
def get_subprojects_for_project(project_id):
    Project.query.get_or_404(project_id)
    subprojects = Subproject.query.filter_by(project_id=project_id).all()
    return jsonify([subproject_to_json(sp) for sp in subprojects]), 200


@project_bp.route('/subprojects/<int:subproject_id>', methods=['PUT'])
@login_required
@log_activity('更新子项目信息',action_detail_template='更新子项目信息')
@permission_required('manage_subprojects')
def update_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    if not can_manage_project_item(subproject):
        return jsonify({"error": "权限不足"}), 403
    data = request.get_json()
    subproject.name = data.get('name', subproject.name)
    subproject.description = data.get('description', subproject.description)
    subproject.employee_id = data.get('employee_id', subproject.employee_id)
    if data.get('status'):
        subproject.status = StatusEnum[data.get('status').upper()]
    subproject.updated_at = datetime.now()
    db.session.commit()
    return jsonify(subproject_to_json(subproject)), 200


@project_bp.route('/subprojects/<int:subproject_id>', methods=['DELETE'])
@login_required
@permission_required('delete_subprojects')
@log_activity('删除子项目',action_detail_template='删除子项目')
def delete_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    if not can_manage_project_item(subproject.project):
        return jsonify({"error": "权限不足"}), 403
    db.session.delete(subproject)
    db.session.commit()
    return jsonify({"message": "子项目已删除"}), 200


# --- 阶段路由 (Stage Routes) ---

@project_bp.route('/subprojects/<int:subproject_id>/stages', methods=['POST'])
@login_required
@permission_required('manage_stages')
@log_activity('创建阶段',action_detail_template='创建阶段')
def create_stage(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    if not can_manage_project_item(subproject):
        return jsonify({"error": "权限不足"}), 403
    data = request.get_json()
    new_stage = ProjectStage(
        project_id=subproject.project_id, subproject_id=subproject_id, name=data['name'],
        description=data.get('description'), status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_stage)
    db.session.commit()
    return jsonify(stage_to_json(new_stage)), 201


@project_bp.route('/subprojects/<int:subproject_id>/stages', methods=['GET'])
@login_required
@log_activity('获取子项目下的所有阶段',action_detail_template='获取子项目下的所有阶段')
def get_stages_for_subproject(subproject_id):
    Subproject.query.get_or_404(subproject_id)
    stages = ProjectStage.query.filter_by(subproject_id=subproject_id).all()
    return jsonify([stage_to_json(s) for s in stages]), 200


@project_bp.route('/stages/<int:stage_id>', methods=['PUT'])
@login_required
@permission_required('manage_stages')
@log_activity('更新阶段信息',action_detail_template='更新阶段信息')
def update_stage(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    if not can_manage_project_item(stage):
        return jsonify({"error": "权限不足"}), 403
    data = request.get_json()
    stage.name = data.get('name', stage.name)
    stage.description = data.get('description', stage.description)
    if data.get('status'):
        stage.status = StatusEnum[data.get('status').upper()]
    db.session.commit()
    return jsonify(stage_to_json(stage)), 200


@project_bp.route('/stages/<int:stage_id>', methods=['DELETE'])
@login_required
@log_activity('删除阶段',action_detail_template='删除阶段')
@permission_required('delete_stages')
def delete_stage(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    if not can_manage_project_item(stage.subproject):
        return jsonify({"error": "权限不足"}), 403
    db.session.delete(stage)
    db.session.commit()
    return jsonify({"message": "阶段已删除"}), 200


# --- 任务路由 (Task Routes) ---

@project_bp.route('/stages/<int:stage_id>/tasks', methods=['POST'])
@login_required
@log_activity('创建任务',action_detail_template='创建任务')
@permission_required('manage_tasks')
def create_task(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    if not can_manage_project_item(stage):
        return jsonify({"error": "权限不足"}), 403
    data = request.get_json()
    new_task = StageTask(
        stage_id=stage_id, name=data['name'], description=data.get('description'),
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_task)
    db.session.commit()
    return jsonify(task_to_json(new_task)), 201


@project_bp.route('/stages/<int:stage_id>/tasks', methods=['GET'])
@login_required
@log_activity('获取阶段下的所有任务',action_detail_template='获取阶段下的所有任务')
def get_tasks_for_stage(stage_id):
    ProjectStage.query.get_or_404(stage_id)
    tasks = StageTask.query.filter_by(stage_id=stage_id).all()
    return jsonify([task_to_json(t) for t in tasks]), 200


@project_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@login_required
@log_activity('更新任务信息',action_detail_template='更新任务信息')
@permission_required('update_task_progress')  # 使用特定权限控制
def update_task(task_id):
    task = StageTask.query.get_or_404(task_id)

    # 负责人或被指派者可以更新
    is_manager = can_manage_project_item(task.stage)
    # 此处假设task有employee_id字段，根据你的models.py，需要关联查询
    # employee_id 在 Subproject上, 这里简化为负责人可修改
    if not is_manager:
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json()
    # 普通成员只能更新进度和状态
    if current_user.role == RoleEnum.MEMBER and not is_manager:
        if 'name' in data or 'description' in data:
            return jsonify({"error": "权限不足以修改任务名称或描述"}), 403

    task.name = data.get('name', task.name)
    task.description = data.get('description', task.description)
    task.progress = data.get('progress', task.progress)
    if data.get('status'):
        task.status = StatusEnum[data.get('status').upper()]
    task.updated_at = datetime.now()
    db.session.commit()
    return jsonify(task_to_json(task)), 200


@project_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@login_required
@log_activity('删除任务',action_detail_template='删除任务')
@permission_required('delete_tasks')
def delete_task(task_id):
    task = StageTask.query.get_or_404(task_id)
    if not can_manage_project_item(task.stage):
        return jsonify({"error": "权限不足"}), 403
    db.session.delete(task)
    db.session.commit()
    return jsonify({"message": "任务已删除"}), 200
