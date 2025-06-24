#app/alerts/routes.py

from flask import Blueprint, jsonify, g
from flask_login import current_user, login_required
from datetime import datetime, timedelta
from sqlalchemy import and_

from .. import db
from ..decorators import log_activity
from ..models import Alert, User, Project, Subproject, ProjectStage, StageTask, Announcement, AnnouncementReadStatus, \
    ProjectFile
from ..models import StatusEnum

# 创建蓝图
alert_bp = Blueprint('alert', __name__, url_prefix='/alert')


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
    """为指定用户生成所有系统性的、基于检查的通知"""
    today = datetime.now().date()

    # 规则1: 任务进度100%但未上传文件
    completed_tasks = StageTask.query.filter(
        StageTask.progress == 100,
        StageTask.stage.has(ProjectStage.subproject.has(Subproject.employee_id == user.id))
    ).outerjoin(ProjectFile, StageTask.id == ProjectFile.task_id).group_by(StageTask.id).having(
        db.func.count(ProjectFile.id) == 0).all()

    for task in completed_tasks:
        _create_alert_if_not_exists(
            user,
            f"任务 \"{task.name}\" 已完成，但尚未上传任何相关文件。",
            'task_no_file',
            f'task_no_file_{task.id}'
        )

    # 规则2: 项目/子项目/阶段到期提醒
    deadlines = [15, 7, 3, 0]  # 提醒天数
    items_to_check = [
        ('项目', Project.query.filter(Project.employee_id == user.id, Project.status != StatusEnum.COMPLETED).all()),
        ('子项目',
         Subproject.query.filter(Subproject.employee_id == user.id, Subproject.status != StatusEnum.COMPLETED).all()),
        ('阶段', ProjectStage.query.join(Subproject).filter(Subproject.employee_id == user.id,
                                                            ProjectStage.status != StatusEnum.COMPLETED).all())
    ]
    for item_type, items in items_to_check:
        for item in items:
            if not item.deadline: continue
            days_left = (item.deadline.date() - today).days
            for d in deadlines:
                if days_left <= d:
                    key_part = f'overdue_{item.id}' if days_left < 0 else f'deadline_{d}_days_{item.id}'
                    msg = f'{item_type} "{item.name}" 已逾期 {-days_left} 天。' if days_left < 0 else f'{item_type} "{item.name}" 将在 {days_left} 天后到期。'

                    _create_alert_if_not_exists(
                        user, msg, f'{item_type}_deadline', f'{item_type}_{key_part}'
                    )
                    break  # 匹配到最紧急的提醒后即停止

    # 规则4: 公告未读提醒
    unread_announcements = Announcement.query.filter(
        Announcement.is_active == True,
        ~Announcement.read_statuses.any(and_(
            AnnouncementReadStatus.user_id == user.id,
            AnnouncementReadStatus.is_read == True
        ))
    ).all()
    for ann in unread_announcements:
        _create_alert_if_not_exists(
            user, f"您有一条新的公告 \"{ann.title}\" 待查看。", 'unread_announcement',
            f'unread_announcement_{ann.id}_user_{user.id}'
        )

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

    return jsonify([{
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

