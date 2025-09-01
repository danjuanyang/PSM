# PSM/app/log/routes.py
from flask import request, jsonify
from sqlalchemy import func, and_, or_, desc, asc
from .. import db
from ..admin import admin_bp
from ..models import UserActivityLog, UserSession, User, RoleEnum
from ..decorators import permission_required
from datetime import datetime, timedelta
from flask_login import current_user


from sqlalchemy.orm import joinedload

# ------------------- 日志查看 API -------------------

@admin_bp.route('/activities', methods=['GET'])
@permission_required('view_activity_logs')
def get_activity_logs():
    """
    获取用户活动日志列表（增强版）
    - 动态计算每条日志的停留时间
    """
    # 1. 创建子查询，使用 LEAD 窗口函数计算下一条记录的时间戳
    next_timestamp_subquery = db.session.query(
        UserActivityLog.id,
        func.lead(UserActivityLog.timestamp).over(
            partition_by=UserActivityLog.user_id,
            order_by=UserActivityLog.timestamp
        ).label('next_timestamp')
    ).subquery()

    # 2. 主查询，关联原始表和子查询
    query = db.session.query(
        UserActivityLog, # 选择整个日志对象
        # 计算时间差（秒）
        (func.julianday(next_timestamp_subquery.c.next_timestamp) - func.julianday(UserActivityLog.timestamp)) * 86400
    ).outerjoin(
        next_timestamp_subquery, UserActivityLog.id == next_timestamp_subquery.c.id
    ).options(joinedload(UserActivityLog.user))

    # --- 以下是过滤逻辑，保持不变 ---
    exclude_heartbeat = request.args.get('exclude_heartbeat', 'true').lower() == 'true'
    if exclude_heartbeat:
        query = query.filter(UserActivityLog.action_type != 'HEARTBEAT')

    user_id = request.args.get('user_id', type=int)
    if user_id:
        query = query.filter(UserActivityLog.user_id == user_id)

    session_id = request.args.get('session_id', type=int)
    if session_id:
        query = query.filter(UserActivityLog.session_id == session_id)

    action_type = request.args.get('action_type', type=str)
    if action_type:
        query = query.filter(UserActivityLog.action_type.ilike(f'%{action_type}%'))

    module = request.args.get('module', type=str)
    if module:
        query = query.filter(UserActivityLog.module == module)

    status_code = request.args.get('status_code', type=int)
    if status_code:
        query = query.filter(UserActivityLog.status_code == status_code)

    resource_type = request.args.get('resource_type', type=str)
    if resource_type:
        query = query.filter(UserActivityLog.resource_type == resource_type)
    
    resource_id = request.args.get('resource_id', type=int)
    if resource_id:
        query = query.filter(UserActivityLog.resource_id == resource_id)

    start_date_str = request.args.get('start_date')
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str)
            query = query.filter(UserActivityLog.timestamp >= start_date)
        except ValueError:
            return jsonify({'error': 'start_date格式无效，请使用ISO格式'}), 400

    end_date_str = request.args.get('end_date')
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str)
            query = query.filter(UserActivityLog.timestamp <= end_date)
        except ValueError:
            return jsonify({'error': 'end_date格式无效，请使用ISO格式'}), 400

    errors_only = request.args.get('errors_only', 'false').lower() == 'true'
    if errors_only:
        query = query.filter(UserActivityLog.status_code >= 400)

    # --- 排序逻辑需要调整，因为我们不能直接在复杂查询上按动态字段排序 ---
    # 暂时只支持按时间戳排序
    sort_by = request.args.get('sort_by', 'timestamp')
    sort_order = request.args.get('sort_order', 'desc')
    if sort_by == 'timestamp':
        query = query.order_by(desc(UserActivityLog.timestamp) if sort_order == 'desc' else asc(UserActivityLog.timestamp))
    
    # 3. 分页
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    results = pagination.items

    # 4. 构建最终的JSON响应
    logs_with_duration = []
    for log, duration in results:
        log_dict = log.to_dict() # 使用我们之前定义的 to_dict 方法
        # 如果停留时间超过5分钟（300秒），可能表示用户空闲或已离开，记为0
        calculated_duration = int(duration) if duration and 0 < duration < 300 else 0
        log_dict['duration_seconds'] = calculated_duration
        logs_with_duration.append(log_dict)

    return jsonify({
        'logs': logs_with_duration,
        'pagination': {
            'total_pages': pagination.pages,
            'current_page': pagination.page,
            'total_items': pagination.total,
            'per_page': per_page
        }
    })


@admin_bp.route('/sessions', methods=['GET'])
@permission_required('view_session_logs')  # 'view_session_logs' 是查看会话日志的权限
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

@admin_bp.route('/activities/modules', methods=['GET'])
@permission_required('view_activity_logs')
def get_activity_modules():
    """获取所有不重复的模块列表"""
    modules = db.session.query(UserActivityLog.module).filter(UserActivityLog.module.isnot(None)).distinct().all()
    return jsonify([module[0] for module in modules])

