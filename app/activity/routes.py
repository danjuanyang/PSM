# PSM/app/activity/routes.py
from flask import request, session, jsonify
from flask_login import current_user, login_required
from datetime import datetime

from . import activity_bp
from ..decorators import permission_required
from ..models import db, UserSession, UserActivityLog, User, RoleEnum


@activity_bp.route('/heartbeat', methods=['POST'])
@login_required
def heartbeat():
    """
    接收前端的心跳请求，更新会话的最后活动时间，并记录活动日志。
    """
    session_id = session.get('user_session_id')
    if not session_id:
        return jsonify({"status": "error", "message": "会话不存在或已过期"}), 400

    user_session = UserSession.query.get(session_id)
    if not user_session:
        return jsonify({"status": "error", "message": "会话记录未找到"}), 400

    # 更新心跳
    user_session.last_activity_time = datetime.now()

    # 记录活动日志
    data = request.get_json() or {}
    module = data.get('module')

    activity_log = UserActivityLog(
        user_id=current_user.id,
        session_id=session_id,
        action_type='HEARTBEAT',
        endpoint=request.endpoint,
        module=module,
        status_code=200,
        ip_address=request.remote_addr
    )
    db.session.add(activity_log)

    try:
        db.session.commit()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        db.session.rollback()
        # 在实践中，这里应该使用 app.logger.error()
        print(f"Error in heartbeat: {e}")
        return jsonify({"status": "error", "message": "数据库操作失败"}), 500


@activity_bp.route('/unload', methods=['POST'])
@login_required
def unload():
    """
    接收前端在页面卸载时发送的信号，以正常结束会话。
    """
    session_id = session.get('user_session_id')
    if not session_id:
        return jsonify({"status": "ok", "message": "会话已结束"}), 200

    user_session = UserSession.query.get(session_id)
    if user_session and user_session.is_active:
        user_session.is_active = False
        user_session.logout_time = datetime.now()
        if user_session.login_time:
            duration = user_session.logout_time - user_session.login_time
            user_session.session_duration = int(duration.total_seconds())

        try:
            db.session.commit()
            # 从会话中移除，以防万一
            session.pop('user_session_id', None)
        except Exception as e:
            db.session.rollback()
            print(f"Error in unload: {e}")
            return jsonify({"status": "error", "message": "数据库操作失败"}), 500

    return jsonify({"status": "ok"}), 200


@activity_bp.route('/stats', methods=['GET'])
@permission_required('view_user_activity')
@login_required
def get_activity_stats():
    """
    获取用户活动统计数据，主要是总在线时长。
    """
    # 查询每个用户的总会话时长
    user_stats = db.session.query(
        User.username,
        db.func.sum(UserSession.session_duration).label('total_duration')
    ).join(UserSession, User.id == UserSession.user_id).filter(UserSession.session_duration.isnot(None)).group_by(
        User.username).order_by(db.desc('total_duration')).filter(User.role != RoleEnum.SUPER)

    # 格式化结果
    stats_data = [
        {'username': username, 'total_duration': total_duration}
        for username, total_duration in user_stats
    ]

    return jsonify(stats_data)


@activity_bp.route('/module_stats', methods=['GET'])
@permission_required('view_user_activity')
@login_required
def get_module_activity_stats():
    """
    计算并返回每个模块的用户总停留时间。
    支持通过查询参数进行筛选：
    - user_ids: 用户ID列表 (e.g., '1,2,3')
    - start_date: 开始日期 (e.g., '2025-01-01')
    - end_date: 结束日期 (e.g., '2025-01-31')
    """
    from sqlalchemy import func, desc, cast, Date
    from datetime import datetime

    # --- 获取筛选参数 ---
    user_ids_str = request.args.get('user_ids')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    # --- 构建基础查询 ---
    base_query = db.session.query(
        UserActivityLog.module,
        UserActivityLog.user_id,
        UserActivityLog.timestamp
    ).filter(
        UserActivityLog.module.isnot(None)
    )

    # --- 应用筛选条件 ---
    if user_ids_str:
        try:
            user_ids = [int(uid) for uid in user_ids_str.split(',')]
            if user_ids:
                base_query = base_query.filter(UserActivityLog.user_id.in_(user_ids))
        except ValueError:
            return jsonify({"status": "error", "message": "无效的用户ID格式"}), 400

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            base_query = base_query.filter(cast(UserActivityLog.timestamp, Date) >= start_date.date())
        except ValueError:
            return jsonify({"status": "error", "message": "无效的开始日期格式, 请使用 YYYY-MM-DD"}), 400

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            base_query = base_query.filter(cast(UserActivityLog.timestamp, Date) <= end_date.date())
        except ValueError:
            return jsonify({"status": "error", "message": "无效的结束日期格式, 请使用 YYYY-MM-DD"}), 400

    # --- 使用窗口函数计算停留时间 ---
    lead_func = func.lead(UserActivityLog.timestamp, 1).over(
        partition_by=UserActivityLog.user_id,
        order_by=UserActivityLog.timestamp
    )

    # 使用 julianday (SQLite) 计算秒数差
    duration_calc = (func.julianday(lead_func) - func.julianday(UserActivityLog.timestamp)) * 86400.0

    # 将基础查询构建为子查询，并计算时长
    activity_with_duration = base_query.add_columns(duration_calc.label('duration')).subquery()

    # --- 聚合最终结果 ---
    module_stats = db.session.query(
        activity_with_duration.c.module,
        func.sum(activity_with_duration.c.duration).label('total_duration_seconds')
    ).filter(
        activity_with_duration.c.duration.isnot(None),
        activity_with_duration.c.duration > 0,
        activity_with_duration.c.duration < 1800  # 过滤掉大于30分钟的记录
    ).group_by(
        activity_with_duration.c.module
    ).order_by(
        desc('total_duration_seconds')
    ).all()

    # 格式化结果
    stats_data = [
        {
            'module': module,
            'duration_seconds': int(total_duration) if total_duration else 0
        }
        for module, total_duration in module_stats
    ]

    return jsonify(stats_data)
