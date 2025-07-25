# PSM/app/activity/routes.py
from flask import request, session, jsonify
from flask_login import current_user, login_required
from datetime import datetime

from . import activity_bp
from ..models import db, UserSession, UserActivityLog

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