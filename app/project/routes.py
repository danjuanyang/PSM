# app/project/routes.py

from flask import Blueprint, request, jsonify, g, current_app
from flask_login import current_user, login_required
from sqlalchemy import func

from . import project_bp
from .. import db
from ..models import Project, User, RoleEnum, Subproject, ProjectStage, StageTask, StatusEnum, TaskProgressUpdate,     subproject_members, UserEntityActivity
from ..decorators import permission_required, log_activity
from datetime import datetime, timezone


# --- 辅助函数 (Helper Functions) ---

def _track_entity_activity(entity, entity_type_str):
    """
    辅助函数，用于追踪实体的编辑活动。
    期望前端在请求的JSON中提供 'startTime' 字段 (ISO 8601 格式的字符串)。
    """
    data = request.get_json()
    start_time_str = data.get('startTime')

    if not start_time_str:
        # 如果前端没有提供 startTime，则不执行任何操作
        return

    try:
        # 解析带时区信息的ISO格式时间字符串
        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
        
        # 确保它是UTC时间
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        # 确保时长是合理的，例如，不为负数且不超过某个阈值（比如12小时）
        if 0 <= duration < 43200:
            duration_seconds = int(duration)
            
            # 1. 创建详细的活动记录
            activity_log = UserEntityActivity(
                user_id=current_user.id,
                entity_type=entity_type_str,
                entity_id=entity.id,
                duration_seconds=duration_seconds
            )
            db.session.add(activity_log)
            
            # 2. 更新实体上的统计字段
            current_count = getattr(entity, 'edit_count', 0)
            current_duration = getattr(entity, 'total_edit_duration', 0)
            
            setattr(entity, 'edit_count', (current_count or 0) + 1)
            setattr(entity, 'total_edit_duration', (current_duration or 0) + duration_seconds)

    except (ValueError, TypeError) as e:
        # 如果时间格式错误或数据有问题，则记录错误但不要让请求失败
        print(f"Error processing startTime for activity tracking: {e}")


def project_to_json(project):
    """将Project对象转换为JSON格式"""
    subprojects = project.subprojects.all()

    if not subprojects:
        progress = 0
    else:
        total_progress = sum(sp.progress for sp in subprojects)
        progress = round(total_progress / len(subprojects), 2) if len(subprojects) > 0 else 0
    project.progress = progress
    return {
        "id": project.id, "name": project.name, "description": project.description,
        "employee_id": project.employee_id,
        "employee_name": project.employee.username if project.employee else None,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "progress": progress, "status": project.status.value if project.status else None,
        "subproject_count": len(subprojects)
    }


def subproject_to_json(subproject):
    stages = subproject.stages.all()
    if not stages:
        progress = 0
    else:
        total_progress = sum(s.progress for s in stages)
        progress = round(total_progress / len(stages), 2) if len(stages) > 0 else 0
    subproject.progress = progress
    return {
        "id": subproject.id, "project_id": subproject.project_id, "name": subproject.name,
        "description": subproject.description,
        # 多对多
        "member_ids": [member.id for member in subproject.members],  # 新增
        "member_names": [member.username for member in subproject.members],  # 新增
        "start_date": subproject.start_date.isoformat() if subproject.start_date else None,
        "deadline": subproject.deadline.isoformat() if subproject.deadline else None,
        "progress": progress, "status": subproject.status.value if subproject.status else None,
        "created_at": subproject.created_at.isoformat(), "updated_at": subproject.updated_at.isoformat()
    }


def stage_to_json(stage):
    tasks = stage.tasks.all()
    if not tasks:
        progress = 0
    else:
        total_progress = sum(t.progress for t in tasks)
        progress = round(total_progress / len(tasks), 2) if len(tasks) > 0 else 0
    stage.progress = progress
    return {
        "id": stage.id, "project_id": stage.project_id, "subproject_id": stage.subproject_id,
        "name": stage.name, "description": stage.description,
        "start_date": stage.start_date.isoformat() if stage.start_date else None,
        "end_date": stage.end_date.isoformat() if stage.end_date else None,
        "progress": progress, "status": stage.status.value if stage.status else None,
        "tasks": [task_to_json(t) for t in tasks]
    }


def task_to_json(task):
    return {
        "id": task.id, "stage_id": task.stage_id, "name": task.name,
        "description": task.description, "due_date": task.due_date.isoformat() if task.due_date else None,
        "progress": task.progress, "status": task.status.value if task.status else None,
        "created_at": task.created_at.isoformat(), "updated_at": task.updated_at.isoformat()
    }


# --- 状态级联更新辅助函数 ---

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
        return db.session.query(subproject_members).filter(
            subproject_members.c.subproject_id == item.id,
            subproject_members.c.user_id == current_user.id
        ).first() is not None

    if isinstance(item, ProjectStage):
        return db.session.query(subproject_members).filter(
            subproject_members.c.subproject_id == item.subproject_id,
            subproject_members.c.user_id == current_user.id
        ).first() is not None

    if isinstance(item, StageTask):
        return db.session.query(subproject_members).filter(
            subproject_members.c.subproject_id == item.stage.subproject_id,
            subproject_members.c.user_id == current_user.id
        ).first() is not None

    return False



# --- 新增：获取特定角色的用户 ---

@project_bp.route('/users/by-role/<role_name>', methods=['GET'])
@login_required
def get_users_by_role_name(role_name):
    try:
        role_enum = RoleEnum[role_name.upper()]
        query = User.query.filter_by(role=role_enum)

        # 如果请求者是Leader并且正在查找Member，则只返回他自己的组员
        leader_id = request.args.get('leader_id', type=int)
        if role_enum == RoleEnum.MEMBER and leader_id:
            if current_user.role == RoleEnum.LEADER and current_user.id == leader_id:
                query = query.filter_by(team_leader_id=leader_id)
            else:
                # 防止非leader用户或非自己的leader_id请求
                return jsonify({"error": "权限不足"}), 403

        users = query.all()
        return jsonify([{"id": u.id, "username": u.username} for u in users])
    except KeyError:
        return jsonify({"error": "无效的角色名称"}), 400


# --- REVISED: 状态级联更新辅助函数 ---

def update_parent_statuses(child_object):
    stage = None
    if isinstance(child_object, StageTask):
        stage = child_object.stage
    elif isinstance(child_object, ProjectStage):
        stage = child_object
    if not stage: return
    if stage.status == StatusEnum.PENDING:
        stage.status = StatusEnum.IN_PROGRESS
    subproject = stage.subproject
    if not subproject: return
    if subproject.status == StatusEnum.PENDING:
        subproject.status = StatusEnum.IN_PROGRESS
    project = subproject.project
    if not project: return
    if project.status == StatusEnum.PENDING:
        project.status = StatusEnum.IN_PROGRESS


# --- 项目路由 (Project Routes) ---
@project_bp.route('/projects', methods=['POST'])
@login_required
@log_activity('创建项目', action_detail_template='创建项目')
@permission_required('manage_projects')
def create_project():
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({"error": "项目名称不能为空"}), 400
    if data.get('employee_id'):
        leader = User.query.get(data.get('employee_id'))
        if not leader or leader.role != RoleEnum.LEADER:
            return jsonify({"error": "负责人必须是组长"}), 400
    new_project = Project(
        name=data['name'], description=data.get('description'),
        employee_id=data.get('employee_id'),
        start_date=datetime.fromisoformat(data['start_date']) if data.get('start_date') else None,
        deadline=datetime.fromisoformat(data['deadline']) if data.get('deadline') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_project)
    db.session.commit()
    return jsonify(project_to_json(new_project)), 201


@project_bp.route('/projects/<int:project_id>', methods=['PUT'])
@log_activity('更新项目信息', action_detail_template='更新项目信息')
@login_required
@permission_required('manage_projects')
def update_project(project_id):
    project = Project.query.get_or_404(project_id)
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
    
    _track_entity_activity(project, 'project')
    db.session.commit()
    return jsonify(project_to_json(project)), 200


@project_bp.route('/projects', methods=['GET'])
@log_activity('获取所有项目', '获取所有项目')
@login_required
def get_all_projects():
    user = current_user
    query = Project.query

    # SUPER/ADMIN 或拥有 manage_projects 权限的用户可以查看所有项目
    if user.role in [RoleEnum.SUPER, RoleEnum.ADMIN] or user.can('manage_projects'):
        pass  # 不对 query 做任何限制
    elif user.role == RoleEnum.LEADER:
        # 组长可以看到自己负责的项目
        query = query.filter(Project.employee_id == user.id)
    elif user.role == RoleEnum.MEMBER:
        # 组员只能看到自己参与的项目
        subquery = db.session.query(Subproject.project_id).join(
            subproject_members
        ).filter(
            subproject_members.c.user_id == user.id
        ).distinct()
        query = query.filter(Project.id.in_(subquery))
    else:
        # 其他情况，返回空列表
        return jsonify([]), 200

    projects = query.order_by(Project.id.desc()).all()
    projects_json = [project_to_json(project) for project in projects]
    db.session.commit()
    return jsonify(projects_json), 200


@project_bp.route('/projects/<int:project_id>', methods=['GET'])
@log_activity('获取项目详细信息', action_detail_template='获取项目详细信息')
@login_required
def get_project(project_id):
    project = Project.query.get_or_404(project_id)
    user = current_user

    # 如果用户是管理员或拥有 manage_projects 权限，则直接允许访问
    if user.role in [RoleEnum.SUPER, RoleEnum.ADMIN] or user.can('manage_projects'):
        project_json = project_to_json(project)
        db.session.commit()
        return jsonify(project_json), 200

    # 否则，按现有逻辑检查是否为项目负责人或成员
    if user.role == RoleEnum.LEADER and project.employee_id != user.id:
        return jsonify({"error": "权限不足"}), 403

    if user.role == RoleEnum.MEMBER:
        # 检查该用户是否是该项目的任何子项目的成员
        is_assigned = db.session.query(subproject_members).filter(
            subproject_members.c.subproject_id == Subproject.id,
            Subproject.project_id == project_id,
            subproject_members.c.user_id == user.id
        ).first()

        if not is_assigned:
            return jsonify({"error": "权限不足"}), 403

    project_json = project_to_json(project)
    db.session.commit()
    return jsonify(project_json), 200

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
    if current_user.role != RoleEnum.LEADER or project.employee_id != current_user.id:
        return jsonify({"error": "权限不足，只有项目负责人(组长)可以创建子项目"}), 403
    data = request.get_json()
    new_subproject = Subproject(
        project_id=project_id, name=data['name'], description=data.get('description'),
        # employee_id=data.get('employee_id'),
        start_date=datetime.fromisoformat(data['start_date']) if data.get('start_date') else None,
        deadline=datetime.fromisoformat(data['deadline']) if data.get('deadline') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    # --- 处理多个成员 ---
    member_ids = data.get('member_ids', [])
    if member_ids:
        members = User.query.filter(User.id.in_(member_ids)).all()
        new_subproject.members.extend(members)

    db.session.add(new_subproject)
    db.session.flush()
    _track_entity_activity(new_subproject, 'subproject')
    db.session.commit()
    return jsonify(subproject_to_json(new_subproject)), 201


@project_bp.route('/projects/<int:project_id>/subprojects', methods=['GET'])
@login_required
@log_activity('获取项目下的所有子项目', action_detail_template=f'获取项目下的所有子项目')
def get_subprojects_for_project(project_id):
    # 确保项目存在
    Project.query.get_or_404(project_id)

    user = current_user
    query = Subproject.query.filter_by(project_id=project_id)
    # 组员只能看到分配给自己的子项目（通过中间表查询）
    if user.role == RoleEnum.MEMBER:
        subproject_ids = db.session.query(subproject_members.c.subproject_id).filter(
            subproject_members.c.user_id == user.id
        ).subquery()

        query = query.filter(Subproject.id.in_(subproject_ids))

    # 查询并返回结果
    subprojects = query.all()
    return jsonify([subproject_to_json(sp) for sp in subprojects]), 200


# REVISED: 更新子项目信息, Leader可以编辑自己项目下的所有子项目
@project_bp.route('/subprojects/<int:subproject_id>', methods=['PUT'])
@login_required
@log_activity('更新子项目信息', action_detail_template='更新子项目信息')
def update_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    if not (current_user.role == RoleEnum.LEADER and subproject.project.employee_id == current_user.id):
        return jsonify({"error": "权限不足, 只有项目负责人(组长)可以修改子项目"}), 403
    data = request.get_json()
    # --- 修改：只允许更新成员 ---
    if 'member_ids' in data:
        member_ids = data.get('member_ids', [])
        members = User.query.filter(User.id.in_(member_ids)).all()
        subproject.members = members  # 直接替换成员列表

    if data.get('status'):
        subproject.status = StatusEnum[data.get('status').upper()]
    subproject.updated_at = datetime.now()
    _track_entity_activity(subproject, 'subproject')
    db.session.commit()
    return jsonify(subproject_to_json(subproject)), 200


@project_bp.route('/subprojects/<int:subproject_id>', methods=['DELETE'])
@login_required
@log_activity('删除子项目', action_detail_template=f'{current_user}删除了子项目')
def delete_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    g.log_info('username', current_user.username)
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

    # 判断用户是否是子项目的成员（多对多）
    is_assigned_member = db.session.query(subproject_members).filter(
        subproject_members.c.subproject_id == subproject_id,
        subproject_members.c.user_id == current_user.id
    ).first() is not None

    is_project_leader = current_user.role == RoleEnum.LEADER and subproject.project.employee_id == current_user.id
    if not (is_assigned_member or is_project_leader):
        return jsonify({"error": "权限不足, 只有被分配的组员或项目负责人可以创建阶段"}), 403
    data = request.get_json()
    new_stage = ProjectStage(
        project_id=subproject.project_id, subproject_id=subproject_id, name=data['name'],
        description=data.get('description'),
        start_date=datetime.fromisoformat(data['start_date']) if data.get('start_date') else None,
        end_date=datetime.fromisoformat(data['end_date']) if data.get('end_date') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_stage)
    db.session.flush()  # Flush 用于填充 new_stage 上的关系
    update_parent_statuses(new_stage)
    _track_entity_activity(new_stage, 'stage')
    db.session.commit()  # 提交所有更改
    return jsonify(stage_to_json(new_stage)), 201


@project_bp.route('/subprojects/<int:subproject_id>/stages', methods=['GET'])
@login_required
@log_activity('获取子项目下的所有阶段', action_detail_template='获取子项目下的所有阶段')
def get_stages_for_subproject(subproject_id):
    subproject = Subproject.query.get_or_404(subproject_id)
    user = current_user

    # 权限检查
    can_manage = user.can('manage_projects') or user.can('manage_stages')
    is_admin_or_super = user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]
    is_project_leader = user.role == RoleEnum.LEADER and subproject.project.employee_id == user.id
    is_assigned_member = db.session.query(subproject_members).filter(
        subproject_members.c.subproject_id == subproject_id,
        subproject_members.c.user_id == user.id
    ).first() is not None

    if not (is_admin_or_super or is_project_leader or is_assigned_member or can_manage):
        return jsonify({"error": "权限不足，无法查看此子项目的阶段"}), 403

    stages = ProjectStage.query.filter_by(subproject_id=subproject_id).all()
    stages_json = [stage_to_json(s) for s in stages]
    db.session.commit()
    return jsonify(stages_json), 200


# REVISED: 更新阶段信息, Leader可以编辑自己项目下的所有阶段
@project_bp.route('/stages/<int:stage_id>', methods=['PUT'])
@login_required
@log_activity('更新阶段信息', action_detail_template='更新阶段信息')
def update_stage(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    # 检查用户是否是该子项目的成员（多对多）
    is_assigned_member = db.session.query(subproject_members).filter(
        subproject_members.c.subproject_id == stage.subproject_id,
        subproject_members.c.user_id == current_user.id
    ).first() is not None
    is_project_leader = current_user.role == RoleEnum.LEADER and stage.project.employee_id == current_user.id
    if not (is_assigned_member or is_project_leader):
        return jsonify({"error": "权限不足, 只有被分配的组员或项目负责人可以编辑"}), 403
    data = request.get_json()
    stage.name = data.get('name', stage.name)
    stage.description = data.get('description', stage.description)
    stage.start_date = datetime.fromisoformat(data['start_date']) if data.get('start_date') else stage.start_date
    stage.end_date = datetime.fromisoformat(data['end_date']) if data.get('end_date') else stage.end_date
    if data.get('status'):
        stage.status = StatusEnum[data.get('status').upper()]
    _track_entity_activity(stage, 'stage')
    db.session.commit()
    return jsonify(stage_to_json(stage)), 200


# --- 任务路由 (Task Routes) ---
@project_bp.route('/stages/<int:stage_id>/tasks', methods=['POST'])
@login_required
@log_activity('创建任务', action_detail_template='创建任务')
def create_task(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    # 检查用户是否是该子项目的成员（多对多）
    is_assigned_member = db.session.query(subproject_members).filter(
        subproject_members.c.subproject_id == stage.subproject_id,
        subproject_members.c.user_id == current_user.id
    ).first() is not None
    is_project_leader = current_user.role == RoleEnum.LEADER and stage.project.employee_id == current_user.id
    if not (is_assigned_member or is_project_leader):
        return jsonify({"error": "权限不足"}), 403
    data = request.get_json()
    new_task = StageTask(
        stage_id=stage_id, name=data['name'], description=data.get('description'),
        due_date=datetime.fromisoformat(data['due_date']) if data.get('due_date') else None,
        status=StatusEnum[data.get('status', 'PENDING').upper()]
    )
    db.session.add(new_task)
    db.session.flush()  # Flush 用于填充 new_task 上的关系
    update_parent_statuses(new_task)
    _track_entity_activity(new_task, 'task')
    db.session.commit()  # 提交所有更改
    return jsonify(task_to_json(new_task)), 201


@project_bp.route('/stages/<int:stage_id>/tasks', methods=['GET'])
@login_required
@log_activity('获取阶段下的所有任务', action_detail_template='获取阶段下的所有任务')
def get_tasks_for_stage(stage_id):
    stage = ProjectStage.query.get_or_404(stage_id)
    subproject = stage.subproject
    is_admin_or_super = current_user.role in [RoleEnum.SUPER, RoleEnum.ADMIN]
    is_project_leader = current_user.role == RoleEnum.LEADER and subproject.project.employee_id == current_user.id
    # 检查用户是否是该子项目的成员（多对多）
    is_assigned_member = db.session.query(subproject_members).filter(
        subproject_members.c.subproject_id == subproject.id,
        subproject_members.c.user_id == current_user.id
    ).first() is not None

    if not (is_admin_or_super or is_project_leader or is_assigned_member):
        return jsonify({"error": "权限不足，无法查看此阶段的任务"}), 403
    tasks = StageTask.query.filter_by(stage_id=stage_id).all()
    tasks_json = [task_to_json(t) for t in tasks]
    db.session.commit()
    return jsonify(tasks_json), 200


# REVISED: 更新任务信息, Leader可以编辑自己项目下的所有任务
@project_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@login_required
@log_activity('更新任务信息', action_detail_template='更新任务信息')
def update_task(task_id):
    task = StageTask.query.get_or_404(task_id)
    # 检查用户是否是该子项目的成员（多对多）
    is_assigned_member = db.session.query(subproject_members).filter(
        subproject_members.c.subproject_id == task.stage.subproject_id,
        subproject_members.c.user_id == current_user.id
    ).first() is not None

    is_project_leader = current_user.role == RoleEnum.LEADER and task.stage.project.employee_id == current_user.id

    if not (is_assigned_member or is_project_leader or current_user.role in [RoleEnum.ADMIN, RoleEnum.SUPER]):
        return jsonify({"error": "权限不足"}), 403

    # Member只能在TaskUpdateModal中更新进度，此处阻止其直接修改任务核心内容
    if current_user.role == RoleEnum.MEMBER:
        return jsonify({"error": "请通过'更新进度'按钮来修改任务"}), 403

    data = request.get_json()
    task.name = data.get('name', task.name)
    task.description = data.get('description', task.description)
    task.due_date = datetime.fromisoformat(data['due_date']) if data.get('due_date') else task.due_date
    _track_entity_activity(task, 'task')
    db.session.commit()
    return jsonify(task_to_json(task)), 200


# --- NEW: 任务进度更新路由 ---
@project_bp.route('/tasks/<int:task_id>/progress-updates', methods=['POST'])
@login_required
@log_activity('更新任务进度', action_detail_template='{current_user}更新任务进度')
def create_task_progress_update(task_id):
    task = StageTask.query.get_or_404(task_id)
    g.log_info=("username",current_user)
    # 检查用户是否是该子项目的成员（多对多）
    is_assigned_member = db.session.query(subproject_members).filter(
        subproject_members.c.subproject_id == task.stage.subproject_id,
        subproject_members.c.user_id == current_user.id
    ).first() is not None

    is_project_leader = current_user.role == RoleEnum.LEADER and task.stage.project.employee_id == current_user.id

    if not (is_assigned_member or is_project_leader):
        return jsonify({"error": "权限不足，无法更新此任务进度"}), 403

    data = request.get_json()
    new_progress = data.get('progress')
    description = data.get('description')

    if new_progress is None:
        return jsonify({"error": "进度值为必填项"}), 400
    if not description:
        return jsonify({"error": "更新说明为必填项"}), 400

    # 进度不允许回退
    if new_progress < task.progress:
        return jsonify({"error": f"进度不允许回退 (当前: {task.progress}%, 提交: {new_progress}%)"}), 400

    # 创建更新记录
    update_record = TaskProgressUpdate(
        task_id=task_id,
        recorder_id=current_user.id,
        progress=new_progress,
        description=description
    )
    db.session.add(update_record)

    # 更新任务本身的状态和进度
    task.progress = new_progress
    if task.progress == 100:
        task.status = StatusEnum.COMPLETED
    elif task.progress > 0 and task.status == StatusEnum.PENDING:
        task.status = StatusEnum.IN_PROGRESS
        update_parent_statuses(task)  # 状态级联

    task.updated_at = datetime.now()

    # 提交所有更改
    db.session.commit()

    # 返回更新后的任务信息
    return jsonify(task_to_json(task)), 201


@project_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@login_required
@log_activity('删除任务', action_detail_template='{username} 删除任务')
def delete_task(task_id):
    task = StageTask.query.get_or_404(task_id)
    g.log_info=('username', current_user)
    if not can_manage_project_item(task.stage):
        return jsonify({"error": "权限不足"}), 403

    # **新增逻辑**：如果任务进度为100%，则禁止删除
    if task.progress == 100:
        return jsonify({"error": "不能删除已完成的任务"}), 400

    db.session.delete(task)
    db.session.commit()
    return jsonify({"message": "任务已删除"}), 200


@project_bp.route('/<int:project_id>/generate_mindmap', methods=['POST'])
@login_required
@log_activity('生成项目思维导图', action_detail_template='为项目 {project_name} 生成思维导图')
def generate_project_mindmap(project_id):
    from ..models import KnowledgeBaseItem, KBItemTypeEnum, KBNamespaceEnum, MindMap

    project = Project.query.get_or_404(project_id)
    g.log_info = {'project_name': project.name}

    # 权限检查：只有能访问该项目的人才能为其创建导图
    # (这里的权限逻辑可能需要根据 get_project 的逻辑进行调整，暂时简化)
    if not can_manage_project_item(project):
         return jsonify({"error": "权限不足，无法访问此项目"}), 403

    nodes = []
    edges = []
    node_extra_data = {}

    # 1. 项目作为根节点
    project_node_id = f"proj_{project.id}"
    nodes.append({'id': project_node_id, 'label': project.name})
    node_extra_data[project_node_id] = {
        'description': project.description or '',
        'attachedFiles': [],
        'attachedFolders': []
    }

    # 2. 关联项目文件到根节点
    # 注意：这里需要一个方法来把 ProjectFile 转换为 KBItem，如果它们还没有在知识库中的话
    # 这是一个简化逻辑，假设我们只关联已存在的KBItem
    # 一个更完整的实现需要检查并可能创建KBItem

    # 3. 遍历子项目、阶段和任务
    for subproject in project.subprojects:
        subproject_node_id = f"sub_{subproject.id}"
        nodes.append({'id': subproject_node_id, 'label': subproject.name})
        edges.append({'source': project_node_id, 'target': subproject_node_id})
        node_extra_data[subproject_node_id] = {'description': subproject.description or ''}

        for stage in subproject.stages:
            stage_node_id = f"stage_{stage.id}"
            nodes.append({'id': stage_node_id, 'label': stage.name})
            edges.append({'source': subproject_node_id, 'target': stage_node_id})
            node_extra_data[stage_node_id] = {'description': stage.description or ''}

            for task in stage.tasks:
                task_node_id = f"task_{task.id}"
                nodes.append({'id': task_node_id, 'label': task.name})
                edges.append({'source': stage_node_id, 'target': task_node_id})
                node_extra_data[task_node_id] = {'description': task.description or ''}

    mindmap_data = {
        'nodes': nodes,
        'edges': edges,
        'nodeExtraData': node_extra_data
    }

    try:
        # 在用户的个人空间创建思维导图
        kb_item_name = f"{project.name} - 项目导图"
        # 检查是否已存在同名导图
        existing_item = KnowledgeBaseItem.query.filter_by(
            owner_id=current_user.id,
            namespace=KBNamespaceEnum.PERSONAL,
            name=kb_item_name,
            parent_id=None # 根目录
        ).first()

        if existing_item:
            return jsonify({"error": f'名为 "{kb_item_name}" 的思维导图已存在于您的个人空间'}), 409

        new_kb_item = KnowledgeBaseItem(
            name=kb_item_name,
            item_type=KBItemTypeEnum.MINDMAP,
            namespace=KBNamespaceEnum.PERSONAL,
            owner_id=current_user.id
        )
        db.session.add(new_kb_item)

        new_mindmap = MindMap(kb_item=new_kb_item, data=mindmap_data)
        db.session.add(new_mindmap)

        db.session.commit()

        return jsonify({
            "message": "项目思维导图生成成功！",
            "mindmap_id": new_kb_item.id
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"生成项目思维导图失败: {e}")
        return jsonify({"error": "生成思维导图时发生服务器错误"}), 500


@project_bp.route('/subprojects/<int:subproject_id>/generate_mindmap', methods=['POST'])
@login_required
@log_activity('生成子项目思维导图', action_detail_template='为子项目 {subproject_name} 生成思维导图')
def generate_subproject_mindmap(subproject_id):
    from ..models import KnowledgeBaseItem, KBItemTypeEnum, KBNamespaceEnum, MindMap

    subproject = Subproject.query.get_or_404(subproject_id)
    g.log_info = {'subproject_name': subproject.name}

    # 权限检查：项目负责人或子项目成员可以生成
    is_project_leader = current_user.id == subproject.project.employee_id
    is_member = current_user in subproject.members

    if not (is_project_leader or is_member or current_user.role in [RoleEnum.ADMIN, RoleEnum.SUPER]):
        return jsonify({"error": "权限不足，无法访问此子项目"}), 403

    nodes = []
    edges = []
    node_extra_data = {}

    # 1. 子项目作为根节点
    subproject_node_id = f"sub_{subproject.id}"
    nodes.append({'id': subproject_node_id, 'label': subproject.name})
    node_extra_data[subproject_node_id] = {'description': subproject.description or ''}

    # 2. 遍历阶段和任务
    for stage in subproject.stages:
        stage_node_id = f"stage_{stage.id}"
        nodes.append({'id': stage_node_id, 'label': stage.name})
        edges.append({'source': subproject_node_id, 'target': stage_node_id})
        node_extra_data[stage_node_id] = {'description': stage.description or ''}

        for task in stage.tasks:
            task_node_id = f"task_{task.id}"
            nodes.append({'id': task_node_id, 'label': task.name})
            edges.append({'source': stage_node_id, 'target': task_node_id})
            node_extra_data[task_node_id] = {'description': task.description or ''}

    mindmap_data = {
        'nodes': nodes,
        'edges': edges,
        'nodeExtraData': node_extra_data
    }

    try:
        kb_item_name = f"{subproject.name} - 子项目导图"
        existing_item = KnowledgeBaseItem.query.filter_by(
            owner_id=current_user.id,
            namespace=KBNamespaceEnum.PERSONAL,
            name=kb_item_name,
            parent_id=None
        ).first()

        if existing_item:
            return jsonify({"error": f'名为 "{kb_item_name}" 的思维导图已存在于您的个人空间'}), 409

        new_kb_item = KnowledgeBaseItem(
            name=kb_item_name,
            item_type=KBItemTypeEnum.MINDMAP,
            namespace=KBNamespaceEnum.PERSONAL,
            owner_id=current_user.id
        )
        db.session.add(new_kb_item)

        new_mindmap = MindMap(kb_item=new_kb_item, data=mindmap_data)
        db.session.add(new_mindmap)

        db.session.commit()

        return jsonify({
            "message": "子项目思维导图生成成功！",
            "mindmap_id": new_kb_item.id
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"生成子项目思维导图失败: {e}")
        return jsonify({"error": "生成思维导图时发生服务器错误"}), 500
