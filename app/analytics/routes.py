# PSM/app/analytics/routes.py
from flask import request, jsonify
from flask_login import login_required
from sqlalchemy import func, case
from datetime import datetime, timedelta

from . import analytics_bp
from ..models import db, User, UserSession, UserActivityLog
from ..decorators import permission_required

# 定义一个空闲阈值（例如，15分钟）
IDLE_THRESHOLD = timedelta(minutes=15)

@analytics_bp.route('/overview', methods=['GET'])
@login_required
# @permission_required('view_analytics') # 假设需要这个权限
def get_overview_stats():
    """提供实时概览统计数据。"""
    
    # 1. 当前在线用户
    # 定义一个时间窗口，例如过去5分钟内有活动的会话
    five_minutes_ago = datetime.now() - timedelta(minutes=5)
    online_users_count = UserSession.query.filter(
        UserSession.is_active == True,
        UserSession.last_activity_time >= five_minutes_ago
    ).count()

    # 2. 今日活跃用户 (DAU)
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    dau_count = db.session.query(func.count(UserSession.user_id.distinct())).filter(
        UserSession.login_time >= today_start
    ).scalar()

    # 3. 平均会话时长
    avg_duration_query = db.session.query(func.avg(UserSession.session_duration)).filter(
        UserSession.session_duration.isnot(None)
    ).scalar()
    avg_duration = int(avg_duration_query) if avg_duration_query else 0

    # 4. 最热门模块 (基于总停留时间)
    # 注意：这是一个简化的计算，精确计算见 /module-stats
    top_module_query = db.session.query(
        UserActivityLog.module,
        func.count(UserActivityLog.id).label('activity_count')
    ).filter(UserActivityLog.module.isnot(None))\
     .group_by(UserActivityLog.module)\
     .order_by(func.count(UserActivityLog.id).desc())\
     .first()
    
    top_module = top_module_query[0] if top_module_query else "N/A"

    return jsonify({
        "online_users": online_users_count,
        "dau_today": dau_count,
        "avg_session_duration_seconds": avg_duration,
        "most_frequent_module": top_module
    })

@analytics_bp.route('/online-users', methods=['GET'])
@login_required
# @permission_required('view_analytics')
def get_online_users():
    """获取当前在线用户列表。"""
    five_minutes_ago = datetime.now() - timedelta(minutes=5)
    
    online_sessions = UserSession.query.join(User).filter(
        UserSession.is_active == True,
        UserSession.last_activity_time >= five_minutes_ago
    ).order_by(UserSession.last_activity_time.desc()).all()

    users_data = [{
        "session_id": s.id,
        "user_id": s.user.id,
        "username": s.user.username,
        "login_time": s.login_time.isoformat(),
        "last_activity_time": s.last_activity_time.isoformat(),
        "ip_address": s.ip_address
    } for s in online_sessions]

    return jsonify(users_data)

@analytics_bp.route('/sessions', methods=['GET'])
@login_required
# @permission_required('view_analytics')
def get_session_history():
    """获取经过筛选和分页的会话历史记录。"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    user_id = request.args.get('userId', type=int)
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')

    query = UserSession.query.join(User).order_by(UserSession.login_time.desc())

    if user_id:
        query = query.filter(UserSession.user_id == user_id)
    if start_date_str:
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).replace(hour=0, minute=0, second=0)
        query = query.filter(UserSession.login_time >= start_date)
    if end_date_str:
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).replace(hour=23, minute=59, second=59)
        query = query.filter(UserSession.login_time <= end_date)

    paginated_sessions = query.paginate(page=page, per_page=per_page, error_out=False)

    sessions_data = [{
        "session_id": s.id,
        "user_id": s.user.id,
        "username": s.user.username,
        "login_time": s.login_time.isoformat() if s.login_time else None,
        "logout_time": s.logout_time.isoformat() if s.logout_time else None,
        "duration_seconds": s.session_duration,
        "ip_address": s.ip_address,
        "is_active": s.is_active
    } for s in paginated_sessions.items]

    return jsonify({
        "items": sessions_data,
        "total": paginated_sessions.total,
        "pages": paginated_sessions.pages,
        "current_page": paginated_sessions.page
    })

@analytics_bp.route('/session-details/<int:session_id>', methods=['GET'])
@login_required
# @permission_required('view_analytics')
def get_session_details(session_id):
    """获取单个会话的详细活动日志时间线。"""
    logs = UserActivityLog.query.filter_by(session_id=session_id).order_by(UserActivityLog.timestamp.asc()).all()
    
    details_data = [{
        "timestamp": log.timestamp.isoformat(),
        "action": log.action_type,
        "module": log.module,
        "endpoint": log.endpoint
    } for log in logs]

    return jsonify(details_data)

@analytics_bp.route('/module-stats', methods=['GET'])
@login_required
# @permission_required('view_analytics')
def get_module_stats():
    """计算并返回模块停留时间统计。"""
    user_id = request.args.get('userId', type=int)
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')

    query = UserActivityLog.query.order_by(UserActivityLog.user_id, UserActivityLog.session_id, UserActivityLog.timestamp.asc())

    # 应用筛选
    if user_id:
        query = query.filter(UserActivityLog.user_id == user_id)
    if start_date_str:
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).replace(hour=0, minute=0, second=0)
        query = query.filter(UserActivityLog.timestamp >= start_date)
    if end_date_str:
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).replace(hour=23, minute=59, second=59)
        query = query.filter(UserActivityLog.timestamp <= end_date)

    all_logs = query.all()
    
    module_time = {}
    
    if not all_logs:
        return jsonify([])

    # 遍历日志计算时间
    for i in range(len(all_logs) - 1):
        current_log = all_logs[i]
        next_log = all_logs[i+1]

        # 确保是同一个用户的同一个会话
        if current_log.session_id != next_log.session_id:
            continue
        
        # 确保当前日志有模块信息
        if not current_log.module:
            continue

        time_diff = next_log.timestamp - current_log.timestamp
        
        # 如果时间差小于空闲阈值，则计入停留时间
        if time_diff < IDLE_THRESHOLD:
            module_name = current_log.module
            if module_name not in module_time:
                module_time[module_name] = 0
            module_time[module_name] += time_diff.total_seconds()

    # 格式化为前端期望的数组格式
    formatted_stats = [
        {"module": name, "duration_seconds": int(time)}
        for name, time in module_time.items()
    ]
    
    # 按时间降序排序
    formatted_stats.sort(key=lambda x: x['duration_seconds'], reverse=True)

    return jsonify(formatted_stats)