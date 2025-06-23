# app/hr/routes.py
from flask import Blueprint, request, jsonify
from flask_login import current_user, login_required
from sqlalchemy import extract
from datetime import datetime, timedelta
from .. import db
from ..models import User, RoleEnum, ReportClockin, ReportClockinDetail, TaskProgressUpdate, StageTask
from ..decorators import permission_required, log_activity

# 创建蓝图
hr_bp = Blueprint('hr', __name__, url_prefix='/api/hr')


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

@hr_bp.route('/users/<int:user_id>/assign-leader', methods=['PUT'])
@login_required
@permission_required('manage_teams')
def assign_leader_to_user(user_id):
    """
    为指定组员(MEMBER)分配一个组长(LEADER)
    """
    member = User.query.get_or_404(user_id)
    if member.role != RoleEnum.MEMBER:
        return jsonify({"error": "该用户不是组员，无法分配组长"}), 400

    data = request.get_json()
    leader_id = data.get('leader_id')
    if not leader_id:
        return jsonify({"error": "请求体中缺少 leader_id"}), 400

    leader = User.query.get_or_404(leader_id)
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
def get_clock_in_records():
    """
    查询补卡记录，支持按用户和月份过滤
    """
    query = ReportClockinDetail.query.join(ReportClockin)

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
    查询任务进度更新记录，支持按用户和时间段(天/周/月)过滤
    """
    query = TaskProgressUpdate.query

    # 按用户过滤
    recorder_id = request.args.get('recorder_id', type=int)
    if recorder_id:
        query = query.filter(TaskProgressUpdate.recorder_id == recorder_id)

    # 按时间段过滤
    period = request.args.get('period')  # e.g., '天', '周', '月'
    today = datetime.now().date()

    start_date = None
    end_date = None

    if period == 'day':
        start_date = datetime.combine(today, datetime.min.time())
        end_date = start_date + timedelta(days=1)
    elif period == 'week':
        start_of_week = today - timedelta(days=today.weekday())
        start_date = datetime.combine(start_of_week, datetime.min.time())
        end_date = start_date + timedelta(weeks=1)
    elif period == 'month':
        start_of_month = today.replace(day=1)
        start_date = datetime.combine(start_of_month, datetime.min.time())
        # 计算下个月的第一天
        if start_of_month.month == 12:
            end_of_month = start_of_month.replace(year=start_of_month.year + 1, month=1)
        else:
            end_of_month = start_of_month.replace(month=start_of_month.month + 1)
        end_date = datetime.combine(end_of_month, datetime.min.time())

    if start_date and end_date:
        query = query.filter(TaskProgressUpdate.created_at >= start_date, TaskProgressUpdate.created_at < end_date)

    updates = query.order_by(TaskProgressUpdate.created_at.desc()).all()
    return jsonify([progress_update_to_json(u) for u in updates]), 200
