# app/hr/routes.py
from flask import Blueprint, request, jsonify, g
from flask_login import current_user, login_required
from sqlalchemy import extract, func
from datetime import datetime, timedelta

from sqlalchemy.orm import aliased

from . import hr_bp
from .. import db
from ..models import User, RoleEnum, ReportClockin, ReportClockinDetail, TaskProgressUpdate, StageTask, Project, \
    Subproject, ProjectStage
from ..decorators import permission_required, log_activity


def user_to_json_with_leader(user):
    """将User对象转换为带组长信息的JSON"""
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role.name,
        'team_leader_id': user.team_leader_id,
        'leader_name': user.leader.username if user.leader else None
    }


def clockin_detail_to_json(detail):
    """将ReportClockinDetail对象转换为JSON"""
    return {
        'id': detail.id,
        'report_id': detail.report_id,
        'employee_id': detail.report.employee_id,
        'employee_name': detail.report.employee.username,
        'clockin_date': detail.clockin_date.isoformat(),
        'weekday': detail.weekday,
        'remarks': detail.remarks,
        'created_at': detail.created_at.isoformat()
    }


def progress_update_to_json(update):
    """将TaskProgressUpdate对象转换为带完整上下文的JSON"""
    task = update.task
    stage = task.stage
    subproject = stage.subproject
    project = subproject.project
    return {
        'id': update.id,
        'progress': update.progress,
        'description': update.description,
        'created_at': update.created_at.isoformat(),
        'recorder_id': update.recorder_id,
        'recorder_name': update.recorder.username if update.recorder else None,
        'task_info': {
            'id': task.id,
            'name': task.name,
            'stage': stage.name,
            'subproject': subproject.name,
            'project': project.name
        }
    }


# --- 1. 团队管理接口 ---

@hr_bp.route('/team-overview', methods=['GET'])
@login_required
@permission_required('manage_teams')  # 确保只有具备团队管理权限的用户可以访问
def get_team_overview():
    """
    获取所有用户的列表，并包含他们的团队领导信息。
    这是为HR团队管理面板专门设计的接口。
    """
    all_users = User.query.order_by(User.id).all()
    # 使用 user_to_json_with_leader 函数来确保包含了 leader_name
    return jsonify([user_to_json_with_leader(u) for u in all_users])


@hr_bp.route('/users/<int:user_id>/assign-leader', methods=['PUT'])
@login_required
@permission_required('manage_teams')
def assign_leader_to_user(user_id):
    """
    为指定组员(MEMBER)分配或移除一个组长(LEADER)。
    如果 leader_id 为 null, 则表示移除组长。
    """
    member = User.query.get_or_404(user_id)
    if member.role != RoleEnum.MEMBER:
        return jsonify({"error": "该用户不是组员，无法分配组长"}), 400

    data = request.get_json()

    # 检查 'leader_id' 键是否存在于请求中
    if 'leader_id' not in data:
        return jsonify({"error": "请求体中缺少 leader_id 键"}), 400

    leader_id = data.get('leader_id')

    # 如果 leader_id 是 null/None，表示移除组长
    if leader_id is None:
        member.team_leader_id = None
        db.session.commit()
        return jsonify(user_to_json_with_leader(member)), 200

    # 如果 leader_id 不是 null，则执行分配逻辑
    leader = User.query.get(leader_id)
    if not leader:
        return jsonify({"error": f"ID为 {leader_id} 的组长不存在"}), 404
    if leader.role != RoleEnum.LEADER:
        return jsonify({"error": "指定的用户不是组长"}), 400

    member.team_leader_id = leader_id
    db.session.commit()
    return jsonify(user_to_json_with_leader(member)), 200


@hr_bp.route('/users/<int:user_id>/promote-to-leader', methods=['PUT'])
@login_required
@permission_required('manage_teams')
def promote_to_leader(user_id):
    """
    将一个用户提升为组长(LEADER)，并清空其 team_leader_id
    """
    user = User.query.get_or_404(user_id)
    user.role = RoleEnum.LEADER
    user.team_leader_id = None
    db.session.commit()
    return jsonify(user_to_json_with_leader(user)), 200


# --- 2. 记录查询接口 ---

@hr_bp.route('/clock-in-records', methods=['GET'])
@login_required
@permission_required('view_clock_in_reports')  # 权限已细分
@log_activity('查看打卡记录', action_detail_template='{username}查看了打卡记录')
def get_clock_in_records():
    """
    查询补卡记录，支持按用户和月份过滤
    """
    query = ReportClockinDetail.query.join(ReportClockin)
    g.log_info = {'username': current_user.username}
    user_id = request.args.get('user_id', type=int)
    if user_id:
        query = query.filter(ReportClockin.employee_id == user_id)

    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    if year and month:
        query = query.filter(
            extract('year', ReportClockinDetail.clockin_date) == year,
            extract('month', ReportClockinDetail.clockin_date) == month
        )

    records = query.order_by(ReportClockinDetail.clockin_date.desc()).all()
    return jsonify([clockin_detail_to_json(r) for r in records]), 200


# --- 3. 任务进度历史接口 ---
@hr_bp.route('/task-progress-updates', methods=['GET'])
@login_required
@permission_required('view_progress_reports')
def get_task_progress_updates():
    """
    获取任务进度更新记录，并计算每次更新与上一次的进度差。
    """
    # 使用子查询和窗口函数来获取上一次的进度
    prev_update = aliased(TaskProgressUpdate)
    subquery = db.session.query(
        TaskProgressUpdate.id,
        func.lag(TaskProgressUpdate.progress, 1, 0).over(
            partition_by=TaskProgressUpdate.task_id,
            order_by=TaskProgressUpdate.created_at
        ).label('previous_progress')
    ).subquery()

    # 主查询，关联任务、阶段、项目等信息，并左连接子查询以获取 'previous_progress'
    query = db.session.query(
        TaskProgressUpdate,
        subquery.c.previous_progress
    ).join(
        subquery, TaskProgressUpdate.id == subquery.c.id
    ).join(
        StageTask, TaskProgressUpdate.task_id == StageTask.id
    ).join(
        ProjectStage, StageTask.stage_id == ProjectStage.id
    ).join(
        Subproject, ProjectStage.subproject_id == Subproject.id
    ).join(
        Project, Subproject.project_id == Project.id
    ).join(
        User, TaskProgressUpdate.recorder_id == User.id
    ).order_by(TaskProgressUpdate.created_at.desc())

    # 应用筛选
    recorder_id = request.args.get('recorder_id', type=int)
    if recorder_id:
        query = query.filter(TaskProgressUpdate.recorder_id == recorder_id)

    period = request.args.get('period')
    if period:
        today = datetime.now().date()
        start_date = None
        if period == 'day':
            start_date = today
        elif period == 'week':
            start_date = today - timedelta(days=today.weekday())
        elif period == 'month':
            start_date = today.replace(day=1)

        if start_date:
            end_date = start_date + timedelta(days=1) if period == 'day' else (
                start_date + timedelta(weeks=1) if period == 'week' else (
                    start_date.replace(month=start_date.month % 12 + 1,
                                       day=1) if start_date.month < 12 else start_date.replace(year=start_date.year + 1,
                                                                                               month=1, day=1)))
            query = query.filter(TaskProgressUpdate.created_at.between(start_date, end_date))

    results = query.all()

    # 序列化结果
    updates_json = []
    for update, previous_progress in results:
        updates_json.append({
            'id': update.id,
            'progress': update.progress,
            'previous_progress': previous_progress,  # 新增字段
            'description': update.description,
            'created_at': update.created_at.isoformat(),
            'recorder_id': update.recorder_id,
            'recorder_name': update.recorder.username if update.recorder else None,
            'task_info': {
                'id': update.task.id,
                'name': update.task.name,
                'stage': update.task.stage.name,
                'subproject': update.task.stage.subproject.name,
                'project': update.task.stage.project.name
            }
        })

    return jsonify(updates_json)


# --- 3. 新增：补卡填报接口 ---
@hr_bp.route('/clock-in-records', methods=['POST'])
@login_required
@log_activity('提交补卡记录', action_detail_template='{username}提交了补卡记录')
def submit_clock_in_record():
    """
    员工提交一个月度的补卡记录.
    """
    data = request.get_json()
    if not data or 'year' not in data or 'month' not in data or 'details' not in data:
        return jsonify({"error": "请求数据不完整"}), 400

    year = data['year']
    month = data['month']
    details = data['details']
    g.log_info = {"username": current_user.username}
    # 检查本月是否已提交过
    existing_report = ReportClockin.query.filter(
        ReportClockin.employee_id == current_user.id,
        db.extract('year', ReportClockin.report_date) == year,
        db.extract('month', ReportClockin.report_date) == month
    ).first()

    if existing_report:
        return jsonify({"error": f"{year}年{month}月的补卡记录已提交，不可重复提交。"}), 409

    # 创建主记录
    new_report = ReportClockin(
        employee_id=current_user.id,
        report_date=datetime(year, month, 1)
    )
    db.session.add(new_report)
    db.session.flush()

    # 创建详细记录
    for detail in details:
        clockin_date_str = detail.get('date')
        remarks = detail.get('remarks')
        if not clockin_date_str or not remarks:
            continue

        clockin_date = datetime.strptime(clockin_date_str, '%Y-%m-%d').date()
        weekday_str = clockin_date.strftime('%A')

        new_detail = ReportClockinDetail(
            report_id=new_report.id,
            clockin_date=clockin_date,
            weekday=weekday_str,
            remarks=remarks
        )
        db.session.add(new_detail)

    db.session.commit()
    return jsonify({"message": "补卡记录提交成功"}), 201


# --- 新增：员工查询自己当月的提交记录 ---
@hr_bp.route('/clock-in-records/my-current-month', methods=['GET'])
@login_required
@log_activity('查询补卡记录', action_detail_template='{username}查询了补卡记录')
def get_my_current_month_records():
    """
    获取当前登录用户在本月的补卡提交记录。
    """
    today = datetime.now()
    year = today.year
    month = today.month
    g.log_info = {"username": current_user.username}
    records = ReportClockinDetail.query.join(ReportClockin).filter(
        ReportClockin.employee_id == current_user.id,
        extract('year', ReportClockin.report_date) == year,
        extract('month', ReportClockin.report_date) == month
    ).all()

    if not records:
        return jsonify([])

    return jsonify([clockin_detail_to_json(r) for r in records])


@hr_bp.route('/clock-in/check', methods=['GET'])
@login_required
def check_clock_in_report():
    """
    检查当前用户在指定月份是否已提交过补卡报告。
    如果已提交，则直接返回记录详情。
    接收一个 'month' 查询参数, 格式为 'YYYY-MM'.
    """
    month_str = request.args.get('month')
    if not month_str:
        return jsonify({"error": "缺少 'month' 查询参数"}), 400

    try:
        year, month = map(int, month_str.split('-'))
    except ValueError:
        return jsonify({"error": "月份格式无效，请使用 'YYYY-MM' 格式"}), 400

    # CORRECTED: Use the database-agnostic `extract` function for consistency and correctness.
    records = ReportClockinDetail.query.join(ReportClockin).filter(
        ReportClockin.employee_id == current_user.id,
        db.extract('year', ReportClockin.report_date) == year,
        db.extract('month', ReportClockin.report_date) == month
    ).order_by(ReportClockinDetail.clockin_date).all()

    if records:
        # 如果记录存在，序列化并返回
        return jsonify({
            "exists": True,
            "records": [clockin_detail_to_json(r) for r in records]
        })
    else:
        # 如果记录不存在
        return jsonify({
            "exists": False,
            "records": []
        })
