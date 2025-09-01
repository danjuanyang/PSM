# app/alert/routes.py
from flask import Blueprint, jsonify, g
from flask_login import current_user, login_required
from datetime import datetime, timedelta
from sqlalchemy import and_, func, extract
from . import alert_bp
from .. import db
from ..decorators import log_activity
from ..models import Alert, User, Project, Subproject, ProjectStage, StageTask, Announcement, AnnouncementReadStatus, \
    ProjectFile, Training, ReportClockin
from ..models import StatusEnum


# --- 核心服务函数: 生成通知 ---

def _create_alert_if_not_exists(user, message, alert_type, related_key, related_url=None):
    """一个辅助函数，用于在检查不存在重复提醒后创建新提醒"""
    exists = Alert.query.filter_by(related_key=related_key).first()
    if not exists:
        alert = Alert(
            user_id=user.id,
            message=message,
            alert_type=alert_type,
            related_key=related_key,
            related_url=related_url
        )
        db.session.add(alert)
        return True
    return False


def generate_system_alerts_for_user(user):
    """
    为指定用户生成并协调所有系统性通知。
    该函数会创建新提醒，并自动将已解决的旧提醒标记为已读。
    """
    today = datetime.now().date()
    
    # 存储当前检查周期中所有有效的提醒key
    valid_alert_keys = set()

    # 获取此函数管理的所有类型的、当前未读的提醒
    managed_alert_types = [
        'project_deadline', 'subproject_deadline', 'stage_deadline', 'task_deadline',
        'task_no_file', 'unread_announcement', 'training_no_material', 'hr_no_clockin'
    ]
    existing_unread_alerts = Alert.query.filter(
        Alert.user_id == user.id,
        Alert.is_read == False,
        Alert.alert_type.in_(managed_alert_types)
    ).all()
    existing_unread_keys = {alert.related_key for alert in existing_unread_alerts}

    # --- 检查规则并填充 valid_alert_keys ---

    # 规则1: 项目和子项目到期提醒
    for project in Project.query.filter(Project.employee_id == user.id, Project.status != StatusEnum.COMPLETED).all():
        if project.deadline and (project.deadline.date() - today).days <= 7:
            key = f'project_deadline_{project.id}'
            valid_alert_keys.add(key)
            _create_alert_if_not_exists(user, f"项目 \"{project.name}\" 将在7天内到期。", 'project_deadline', key, f'/project/detail/{project.id}')

    for subproject in Subproject.query.filter(Subproject.members.any(id=user.id), Subproject.status != StatusEnum.COMPLETED).all():
        if subproject.deadline and (subproject.deadline.date() - today).days <= 7:
            key = f'subproject_deadline_{subproject.id}'
            valid_alert_keys.add(key)
            # 指向父项目的详情页
            _create_alert_if_not_exists(user, f"子项目 \"{subproject.name}\" 将在7天内到期。", 'subproject_deadline', key, f'/project/detail/{subproject.project_id}')

    # 规则2: 阶段到期提醒
    for stage in ProjectStage.query.join(Subproject).filter(Subproject.members.any(id=user.id), ProjectStage.status != StatusEnum.COMPLETED).all():
        if stage.end_date and (stage.end_date - today).days <= 3:
            key = f'stage_deadline_{stage.id}'
            valid_alert_keys.add(key)
            # 指向父项目的详情页
            _create_alert_if_not_exists(user, f"阶段 \"{stage.name}\" 将在3天内到期。", 'stage_deadline', key, f'/project/detail/{stage.subproject.project_id}')

    # 规则3: 任务到期提醒
    for task in StageTask.query.join(ProjectStage).join(Subproject).filter(Subproject.members.any(id=user.id), StageTask.status != StatusEnum.COMPLETED).all():
        if task.due_date and (task.due_date - today).days <= 1:
            key = f'task_deadline_{task.id}'
            valid_alert_keys.add(key)
            # 指向父项目的详情页
            _create_alert_if_not_exists(user, f"任务 \"{task.name}\" 将在1天内到期。", 'task_deadline', key, f'/project/detail/{task.stage.subproject.project_id}')

    # 规则4: 任务进度100%但未上传文件
    completed_tasks_without_files = StageTask.query.filter(
        StageTask.progress == 100,
        StageTask.stage.has(ProjectStage.subproject.has(Subproject.members.any(id=user.id)))
    ).outerjoin(ProjectFile, StageTask.id == ProjectFile.task_id).group_by(StageTask.id).having(func.count(ProjectFile.id) == 0).all()
    for task in completed_tasks_without_files:
        key = f'task_no_file_{task.id}'
        valid_alert_keys.add(key)
        # 指向父项目的详情页
        _create_alert_if_not_exists(user, f"任务 \"{task.name}\" 已完成，但尚未上传任何相关文件。", 'task_no_file', key, f'/project/detail/{task.stage.subproject.project_id}')

    # 规则5: 未读公告提醒
    unread_announcements = Announcement.query.filter(
        Announcement.is_active == True,
        ~Announcement.read_statuses.any(and_(
            AnnouncementReadStatus.user_id == user.id,
            AnnouncementReadStatus.is_read == True
        ))
    ).all()
    for ann in unread_announcements:
        key = f'unread_announcement_{ann.id}_user_{user.id}'
        valid_alert_keys.add(key)
        # 指向公告列表页
        _create_alert_if_not_exists(user, f"您有一条新的公告 \"{ann.title}\" 待查看。", 'unread_announcement', key, '/announcement/index')

    # 规则6: 培训无课件提醒
    assigned_trainings_without_material = Training.query.filter(Training.assignee_id == user.id, Training.material_path == None).all()
    for training in assigned_trainings_without_material:
        key = f'training_no_material_{training.id}'
        valid_alert_keys.add(key)
        # 指向培训列表页
        _create_alert_if_not_exists(user, f"您被分配的培训 \"{training.title}\" 尚未上传课件。", 'training_no_material', key, '/training/index')

    # 规则7: 未提交补卡提醒
    if today.day > 25:
        this_month = today.replace(day=1)
        has_submitted_clockin = ReportClockin.query.filter(
            ReportClockin.employee_id == user.id,
            extract('year', ReportClockin.report_date) == this_month.year,
            extract('month', ReportClockin.report_date) == this_month.month
        ).first()
        if not has_submitted_clockin:
            key = f'hr_no_clockin_{user.id}_{this_month.strftime("%Y-%m")}'
            valid_alert_keys.add(key)
            # 指向补卡填报页
            _create_alert_if_not_exists(user, f"您尚未提交本月的补卡申请。", 'hr_no_clockin', key, '/hr/clock-in-apply')

    # --- 协调：将已解决的提醒标记为已读 ---
    resolved_keys = existing_unread_keys - valid_alert_keys
    if resolved_keys:
        Alert.query.filter(
            Alert.user_id == user.id,
            Alert.is_read == False,
            Alert.related_key.in_(resolved_keys)
        ).update({'is_read': True}, synchronize_session=False)

    db.session.commit()


# --- 通知API接口 ---

@alert_bp.route('', methods=['GET'])
@login_required
def get_user_alerts():
    """
    获取当前用户的通知列表。
    在获取前，会先为该用户触发一次通知生成检查。
    """
    # 1. 按需为当前用户生成最新的系统通知
    generate_system_alerts_for_user(current_user)

    # 2. 从数据库获取该用户所有未读的通知
    alerts = Alert.query.filter_by(user_id=current_user.id, is_read=False) \
        .order_by(Alert.created_at.desc()) \
        .all()

    return jsonify([
        {
            'id': alert.id,
            'message': alert.message,
            'related_url': alert.related_url,
            'created_at': alert.created_at.isoformat()
        } for alert in alerts])


@alert_bp.route('/<int:alert_id>/mark-as-read', methods=['POST'])
@login_required
def mark_as_read(alert_id):
    """将单条通知标记为已读"""
    alert = Alert.query.get_or_404(alert_id)
    if alert.user_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403

    alert.is_read = True
    db.session.commit()
    return jsonify({"message": "通知已标记为已读"}), 200


@alert_bp.route('/mark-all-as-read', methods=['POST'])
@login_required
@log_activity('标记所有通知为已读',action_detail_template='用户 {user} 标记了所有通知为已读')
def mark_all_as_read():
    """将所有通知标记为已读"""
    g.log_info = {'user': current_user.username}
    Alert.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({"message": "所有通知已标记为已读"}), 200


@alert_bp.route('/test-generation', methods=['GET'])
@login_required
def test_alert_generation():
    """
    一个仅用于测试的端点，手动为当前用户触发通知生成。
    """
    if not current_user.role == 'SUPER': # SUPER 是超级管理员
        return jsonify({"error": "仅超级管理员可访问"}), 403

    generate_system_alerts_for_user(current_user)
    return jsonify({"message": "已为当前用户触发通知生成检查。"}), 200

