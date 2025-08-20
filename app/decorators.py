# PSM/app/decorators.py

from functools import wraps
from flask import jsonify, request, g
from flask_login import current_user
from .models import UserSession
from . import db
from .models import UserActivityLog


def permission_required(permission_name):
    """
    自定义权限检查装饰器。
    检查当前登录的用户是否具有所需的权限。
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({'error': '需要身份验证！请登录'}), 401
            if not current_user.can(permission_name):
                return jsonify({'error': '您没有执行此操作的权限'}), 403
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def role_required(role):
    """
    检查用户是否属于特定角色。
    (注意: 推荐优先使用基于权限的 permission_required)
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({'error': '需要身份验证'}), 401
            if current_user.role != role:
                return jsonify({'error': '角色权限不足'}), 403
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def log_activity(action_type, action_detail_template=""):
    """
    一个记录用户活动的装饰器（增强版）。
    它会自动记录模块、会话ID，并格式化日志详情。
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user_before_logout = None
            if request.endpoint == 'auth.logout' and current_user.is_authenticated:
                user_before_logout = current_user._get_current_object()

            response = f(*args, **kwargs)

            try:
                log_user = user_before_logout or current_user
                if not log_user or not log_user.is_authenticated:
                    return response

                # 提取模块名
                module = "default"
                if request.endpoint:
                    module = request.endpoint.split('.')[0]

                # 获取当前用户的活动会话ID
                user_session = UserSession.query.filter_by(
                    user_id=log_user.id,
                    is_active=True
                ).order_by(UserSession.login_time.desc()).first()
                session_id = user_session.id if user_session else None

                # 格式化日志详情
                format_data = kwargs.copy()
                if hasattr(g, 'log_info') and isinstance(g.log_info, dict):
                    format_data.update(g.log_info)
                detail = action_detail_template.format(**format_data)

                status_code = response.status_code if hasattr(response, 'status_code') else 200

                log = UserActivityLog(
                    user_id=log_user.id,
                    session_id=session_id,
                    action_type=action_type,
                    action_detail=detail,
                    status_code=status_code,
                    request_method=request.method,
                    endpoint=request.endpoint,
                    ip_address=request.remote_addr,
                    module=module,
                    resource_type=g.get('resource_type'),
                    resource_id=g.get('resource_id')
                )
                db.session.add(log)
                db.session.commit()

            except Exception as e:
                print(f"错误：记录活动日志失败 - {e}")
                db.session.rollback()
            finally:
                if hasattr(g, 'log_info'):
                    del g.log_info
                if hasattr(g, 'resource_type'):
                    del g.resource_type
                if hasattr(g, 'resource_id'):
                    del g.resource_id

            return response

        return wrapper

    return decorator
