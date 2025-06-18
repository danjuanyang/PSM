# PSM/app/log/routes.py
from flask import request, jsonify
from .. import db
from ..admin import admin_bp
from ..models import UserActivityLog, UserSession, User
from ..decorators import permission_required
from datetime import datetime


# ------------------- 日志查看 API -------------------

@admin_bp.route('/activities', methods=['GET'])
@permission_required('view_activity_logs')  # 假设 'view_activity_logs' 是查看活动日志的权限
def get_activity_logs():
    """
    获取用户活动日志列表，支持过滤和分页
    可过滤字段: user_id, action_type, start_date, end_date
    """
    query = UserActivityLog.query

    # 根据查询参数进行过滤
    user_id = request.args.get('user_id', type=int)
    if user_id:
        query = query.filter(UserActivityLog.user_id == user_id)

    action_type = request.args.get('action_type', type=str)
    if action_type:
        query = query.filter(UserActivityLog.action_type.ilike(f'%{action_type}%'))

    start_date_str = request.args.get('start_date')
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str)
            query = query.filter(UserActivityLog.timestamp >= start_date)
        except ValueError:
            return jsonify({'error': 'start_date格式无效。使用 ISO 格式，如 YYYY-MM-DDTHH：MM：SS'}), 400

    end_date_str = request.args.get('end_date')
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str)
            query = query.filter(UserActivityLog.timestamp <= end_date)
        except ValueError:
            return jsonify({'error': 'end_date格式无效。使用 ISO 格式，如 YYYY-MM-DDTHH：MM：SS'}), 400

    # 排序：默认按时间倒序
    query = query.order_by(UserActivityLog.timestamp.desc())

    # 分页
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items

    return jsonify({
        'logs': [
            {
                'id': log.id,
                'user_id': log.user_id,
                'username': User.query.get(log.user_id).username if log.user_id and User.query.get(
                    log.user_id) else 'N/A',
                'action_type': log.action_type,
                'action_detail': log.action_detail,
                'endpoint': log.endpoint,
                'ip_address': log.ip_address,
                'timestamp': log.timestamp.isoformat()
            } for log in logs
        ],
        'total_pages': pagination.pages,
        'current_page': pagination.page,
        'total_logs': pagination.total
    })


@admin_bp.route('/sessions', methods=['GET'])
@permission_required('view_session_logs')  # 假设 'view_session_logs' 是查看会话日志的权限
def get_session_logs():
    """获取用户会话日志列表，支持过滤和分页"""
    query = UserSession.query

    # 过滤
    user_id = request.args.get('user_id', type=int)
    if user_id:
        query = query.filter(UserSession.user_id == user_id)

    # 排序
    query = query.order_by(UserSession.login_time.desc())

    # 分页
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    sessions = pagination.items

    return jsonify({
        'sessions': [
            {
                'id': s.id,
                'user_id': s.user_id,
                'username': User.query.get(s.user_id).username if s.user_id and User.query.get(s.user_id) else 'N/A',
                'ip_address': s.ip_address,
                'user_agent': s.user_agent,
                'login_time': s.login_time.isoformat() if s.login_time else None,
                'logout_time': s.logout_time.isoformat() if s.logout_time else None,
                'duration_seconds': s.session_duration,
                'is_active': s.is_active
            } for s in sessions
        ],
        'total_pages': pagination.pages,
        'current_page': pagination.page,
        'total_sessions': pagination.total
    })

