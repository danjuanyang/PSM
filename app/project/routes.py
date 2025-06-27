from flask import Blueprint, request, jsonify
from flask_login import current_user, login_required
from sqlalchemy import func

from . import project_bp
from .. import db
from ..models import Project, User, RoleEnum, Subproject, ProjectStage, StageTask, StatusEnum
from ..decorators import permission_required, log_activity
from datetime import datetime



# --- 辅助函数 (Helper Functions) ---
def project_to_json(project):
    """将Project对象转换为JSON格式"""
    # 计算项目进度
    subprojects = project.subprojects.all()
    if not subprojects:
        progress = 0
    else:
        total_progress = sum(sp.progress for sp in subprojects)
        progress = round(total_progress / len(subprojects), 2)

    project.progress = progress
    db.session.commit()

    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "employee_id": project.employee_id,
        "employee_name": project.employee.username if project.employee else None,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "progress": progress,
        "status": project.status.value if project.status else None,
        "subproject_count": len(subprojects)
    }


def subproject_to_json(subproject):
    """将Subproject对象转换为JSON格式"""
    stages = subproject.stages.all()
    if not stages:
        progress = 0
    else:
        total_progress = sum(s.progress for s in stages)
        progress = round(total_progress / len(stages), 2)

    subproject.progress = progress
    db.session.commit()

    return {
        "id": subproject.id,
        "project_id": subproject.project_id,
        "name": subproject.name,
        "description": subproject.description,
        "employee_id": subproject.employee_id,
        "employee_name": subproject.employee.username if subproject.employee else None,
        "start_date": subproject.start_date.isoformat() if subproject.start_date else None,
        "deadline": subproject.deadline.isoformat() if subproject.deadline else None,
        "progress": progress,
        "status": subproject.status.value if subproject.status else None,
        "created_at": subproject.created_at.isoformat(),
        "updated_at": subproject.updated_at.isoformat()
    }


def stage_to_json(stage):
    """将ProjectStage对象转换为JSON格式"""
    tasks = stage.tasks.all()
    if not tasks:
        progress = 0
    else:
        total_progress = sum(t.progress for t in tasks)
        progress = round(total_progress / len(tasks), 2)

    stage.progress = progress
    db.session.commit()

    return {
        "id": stage.id,
        "project_id": stage.project_id,
        "subproject_id": stage.subproject_id,
        "name": stage.name,
        "description": stage.description,
        "start_date": stage.start_date.isoformat() if stage.start_date else None,
        "end_date": stage.end_date.isoformat() if stage.end_date else None,
        "progress": progress,
        "status": stage.status.value if stage.status else None,
        "tasks": [task_to_json(t) for t in tasks]
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
        return True  # 项目负责人 (Leader)

    # 组员只能管理分配给自己的子项目下的内容
    if isinstance(item, Subproject):
        # 组长可以管理他负责的项目下的所有子项目
        return item.project.employee_id == current_user.id
    if isinstance(item, ProjectStage):
        # 组员可以管理分配给自己的子项目下的阶段
        return item.subproject.employee_id == current_user.id
    if isinstance(item, StageTask):
        # 组员可以管理分配给自己的子项目下的任务
        return item.stage.subproject.employee_id == current_user.id

    return False


# --- 新增：获取特定角色的用户 ---
@project_bp.route('/users/by-role/<role_name>', methods=['GET'])
@login_required
@permission_required('manage_projects')  # 只有能管理项目的才能看
def get_users_by_role_name(role_name):
    try:
        role_enum = RoleEnum[role_name.upper()]
        users = User.query.filter_by(role=role_enum).all()
        return jsonify([{"id": u.id, "username": u.username} for u in users])
    except KeyError:
        return jsonify({"error": "无效的角色名称"}), 400


# --- 项目路由 (Project Routes) ---

@project_bp.route('/projects', methods=['POST'])
@login_required
@log_activity('创建项目', action_detail_template='创建项目')
@permission_required('manage_projects')
def create_project():
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({"error": "项目名称不能为空"}), 400

    # 确保负责人是 LEADER
    if data.get('employee_id'):
        leader = User.query.get(data.get('employee_id'))
        if not leader or leader.role != RoleEnum.LEADER:
            return jsonify({"error": "负责人必须是组长"}), 400

    new_project = Project(
        name=data['name'],
        description=data.get('description'),
        employee_id=data.get('employee_id'),
        start_date=datetime.fromisoformat(data['start_date']) if data.get('start_date') else None,
        deadline=datetime.fromisoformat(data['deadline']) if data.get('deadline') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_project)
    db.session.commit()
    return jsonify(project_to_json(new_project)), 201


@project_bp.route('/projects', methods=['GET'])
@log_activity('获取所有项目', '获取所有项目')
@login_required
def get_all_projects():
    user = current_user
    query = Project.query

    if user.role == RoleEnum.LEADER:
        # 组长看自己负责的
        query = query.filter(Project.employee_id == user.id)
    elif user.role == RoleEnum.MEMBER:
        # 组员看自己参与的
        subquery = db.session.query(Subproject.project_id).filter(Subproject.employee_id == user.id).distinct()
        query = query.filter(Project.id.in_(subquery))

    projects = query.order_by(Project.id.desc()).all()
    return jsonify([project_to_json(p) for p in projects]), 200


@project_bp.route('/projects/<int:project_id>', methods=['GET'])
@log_activity('获取项目详细信息', action_detail_template='获取项目详细信息')
@login_required
def get_project(project_id):
    project = Project.query.get_or_404(project_id)
    # 权限检查：确保用户有权查看
    if current_user.role == RoleEnum.LEADER and project.employee_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403
    if current_user.role == RoleEnum.MEMBER:
        is_assigned = Subproject.query.filter_by(project_id=project_id, employee_id=current_user.id).first()
        if not is_assigned:
            return jsonify({"error": "权限不足"}), 403

    return jsonify(project_to_json(project)), 200


@project_bp.route('/projects/<int:project_id>', methods=['PUT'])
@log_activity('更新项目信息', action_detail_template='更新项目信息')
@login_required
@permission_required('manage_projects')
def update_project(project_id):
    project = Project.query.get_or_404(project_id)
    if not can_manage_project_item(project):
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json()
    project.name = data.get('name', project.name)
    project.description = data.get('description', project.description)

    if 'employee_id' in data:
        leader_id = data.get('employee_id')
        if leader_id:
            leader = User.query.get(leader_id)
            if not leader or leader.role != RoleEnum.LEADER:
                return jsonify({"error": "负责人必须是组长"}), 400
        project.employee_id = leader_id

    project.start_date = datetime.fromisoformat(data['start_date']) if data.get('start_date') else project.start_date
    project.deadline = datetime.fromisoformat(data['deadline']) if data.get('deadline') else project.deadline
    if data.get('status'):
        project.status = StatusEnum[data.get('status').upper()]

    db.session.commit()
    return jsonify(project_to_json(project)), 200


@project_bp.route('/projects/<int:project_id>', methods=['DELETE'])
@login_required
@log_activity('删除项目', action_detail_template='删除项目')
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
@log_activity('创建子项目', action_detail_template='创建子项目')
def create_subproject(project_id):
    project = Project.query.get_or_404(project_id)
    # 只有项目负责人（组长）可以创建子项目
    if project.employee_id != current_user.id:
        return jsonify({"error": "权限不足，只有项目负责人可以创建子项目"}), 403

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
@log_activity('获取项目下的所有子项目', action_detail_template='获取项目下的所有子项目')
def get_subprojects_for_project(project_id):
    Project.query.get_or_404(project_id)
    query = Subproject.query.filter_by(project_id=project_id)

    # 组员只能看到分配给自己的子项目
    if current_user.role == RoleEnum.MEMBER:
        query = query.filter_by(employee_id=current_user.id)

    subprojects = query.all()
    return jsonify([subproject_to_json(sp) for sp in subprojects]), 200


@project_bp.route('/subprojects/<int:subproject_id>', methods=['PUT'])
@login_required
@log_activity('更新子项目信息', action_detail_template='更新子项目信息')
def update_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    # 只有项目负责人（组长）可以修改子项目
    if subproject.project.employee_id != current_user.id:
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
@log_activity('删除子项目', action_detail_template='删除子项目')
def delete_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    # 只有项目负责人（组长）可以删除
    if subproject.project.employee_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403
    db.session.delete(subproject)
    db.session.commit()
    return jsonify({"message": "子项目已删除"}), 200


# --- 阶段路由 (Stage Routes) ---

@project_bp.route('/subprojects/<int:subproject_id>/stages', methods=['POST'])
@login_required
@log_activity('创建阶段', action_detail_template='创建阶段')
def create_stage(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    # 只有子项目负责人（组员）可以创建
    if subproject.employee_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json()
    new_stage = ProjectStage(
        project_id=subproject.project_id, subproject_id=subproject_id, name=data['name'],
        description=data.get('description'),
        start_date=datetime.fromisoformat(data['start_date']) if data.get('start_date') else None,
        end_date=datetime.fromisoformat(data['end_date']) if data.get('end_date') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_stage)
    db.session.commit()
    return jsonify(stage_to_json(new_stage)), 201


@project_bp.route('/subprojects/<int:subproject_id>/stages', methods=['GET'])
@login_required
@log_activity('获取子项目下的所有阶段', action_detail_template='获取子项目下的所有阶段')
def get_stages_for_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    # 检查权限
    if not can_manage_project_item(subproject):
        return jsonify({"error": "权限不足"}), 403
    stages = ProjectStage.query.filter_by(subproject_id=subproject_id).all()
    return jsonify([stage_to_json(s) for s in stages]), 200


@project_bp.route('/stages/<int:stage_id>', methods=['PUT'])
@login_required
@log_activity('更新阶段信息', action_detail_template='更新阶段信息')
def update_stage(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    # 只有子项目负责人（组员）可以修改
    if stage.subproject.employee_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json()
    stage.name = data.get('name', stage.name)
    stage.description = data.get('description', stage.description)
    stage.start_date = datetime.fromisoformat(data['start_date']) if data.get('start_date') else stage.start_date
    stage.end_date = datetime.fromisoformat(data['end_date']) if data.get('end_date') else stage.end_date
    if data.get('status'):
        stage.status = StatusEnum[data.get('status').upper()]
    db.session.commit()
    return jsonify(stage_to_json(stage)), 200


@project_bp.route('/stages/<int:stage_id>', methods=['DELETE'])
@login_required
@log_activity('删除阶段', action_detail_template='删除阶段')
def delete_stage(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    # 只有子项目负责人（组员）可以删除
    if stage.subproject.employee_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403

    # **新增逻辑**：如果阶段下有任何任务进度为100%，则禁止删除
    completed_task_exists = StageTask.query.filter_by(stage_id=stage.id, progress=100).first()
    if completed_task_exists:
        return jsonify({"error": "该阶段下有已完成的任务，无法删除"}), 400

    db.session.delete(stage)
    db.session.commit()
    return jsonify({"message": "阶段已删除"}), 200


# --- 任务路由 (Task Routes) ---

@project_bp.route('/stages/<int:stage_id>/tasks', methods=['POST'])
@login_required
@log_activity('创建任务', action_detail_template='创建任务')
def create_task(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    if not can_manage_project_item(stage):
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json()
    new_task = StageTask(
        stage_id=stage_id,
        name=data['name'],
        description=data.get('description'),
        due_date=datetime.fromisoformat(data['due_date']) if data.get('due_date') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_task)
    db.session.commit()

    # 更新阶段状态
    if stage.status == StatusEnum.PENDING:
        stage.status = StatusEnum.IN_PROGRESS
        db.session.commit()

    return jsonify(task_to_json(new_task)), 201


@project_bp.route('/stages/<int:stage_id>/tasks', methods=['GET'])
@login_required
@log_activity('获取阶段下的所有任务', action_detail_template='获取阶段下的所有任务')
def get_tasks_for_stage(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    if not can_manage_project_item(stage):
        return jsonify({"error": "权限不足"}), 403
    tasks = StageTask.query.filter_by(stage_id=stage_id).all()
    return jsonify([task_to_json(t) for t in tasks]), 200


@project_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@login_required
@log_activity('更新任务信息', action_detail_template='更新任务信息')
def update_task(task_id):
    task = StageTask.query.get_or_404(task_id)

    if not can_manage_project_item(task):
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json()

    # 组员只能更新进度
    if current_user.role == RoleEnum.MEMBER:
        if any(k in data for k in ['name', 'description', 'due_date', 'status']):
            # 允许进度驱动状态变化
            if 'progress' in data and 'status' in data and len(data) == 2:
                pass
            else:
                return jsonify({"error": "权限不足，只能更新任务进度"}), 403

        task.progress = data.get('progress', task.progress)
        if task.progress == 100:
            task.status = StatusEnum.COMPLETED
        elif task.progress > 0:
            task.status = StatusEnum.IN_PROGRESS
        else:
            task.status = StatusEnum.PENDING

    # 组长或管理员可以更新所有字段
    else:
        task.name = data.get('name', task.name)
        task.description = data.get('description', task.description)
        task.due_date = datetime.fromisoformat(data['due_date']) if data.get('due_date') else task.due_date
        task.progress = data.get('progress', task.progress)
        if data.get('status'):
            task.status = StatusEnum[data.get('status').upper()]

    task.updated_at = datetime.now()
    db.session.commit()

    # 触及父级进度更新
    stage_to_json(task.stage)

    return jsonify(task_to_json(task)), 200


@project_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@login_required
@log_activity('删除任务', action_detail_template='删除任务')
def delete_task(task_id):
    task = StageTask.query.get_or_404(task_id)
    if not can_manage_project_item(task.stage):
        return jsonify({"error": "权限不足"}), 403

    # **新增逻辑**：如果任务进度为100%，则禁止删除
    if task.progress == 100:
        return jsonify({"error": "不能删除已完成的任务"}), 400

    db.session.delete(task)
    db.session.commit()
    return jsonify({"message": "任务已删除"}), 200
