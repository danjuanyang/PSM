# PSM/app/decorators.py

from functools import wraps
from flask import jsonify, request, g
from flask_login import current_user

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
    一个记录用户活动的装饰器。
    它可以使用URL关键字参数(如{user_id})和存储在g.log_info中的自定义数据(如{username})来格式化日志模板。
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # --- 优化点 1: 为登出操作预先捕获用户信息 ---
            # 因为在logout之后, current_user会变为匿名用户
            user_before_logout = None
            if request.endpoint == 'auth.logout' and current_user.is_authenticated:
                user_before_logout = current_user._get_current_object()

            # --- 改造核心: 先执行视图函数，以便能从 g 对象中获取动态数据 ---
            response = f(*args, **kwargs)

            # --- 优化点 2: 统一日志记录逻辑 ---
            try:
                # 确定记录日志时要使用的用户对象
                log_user = user_before_logout or current_user

                # 如果用户未认证，则不记录日志（例如登录失败）
                if not log_user or not log_user.is_authenticated:
                    return response

                # 准备一个字典，用于格式化模板字符串
                format_data = kwargs.copy()  # 1. 包含所有URL关键字参数
                if hasattr(g, 'log_info') and isinstance(g.log_info, dict):
                    format_data.update(g.log_info)  # 2. 合并来自g.log_info的动态数据

                # 格式化日志详情
                detail = action_detail_template.format(**format_data)

                # 获取响应状态码
                status_code = response.status_code if hasattr(response, 'status_code') else 200

                # 创建并保存日志条目
                log = UserActivityLog(
                    user_id=log_user.id,
                    action_type=action_type,
                    action_detail=detail,
                    status_code=status_code,
                    request_method=request.method,
                    endpoint=request.endpoint,
                    ip_address=request.remote_addr,
                )
                db.session.add(log)
                db.session.commit()

            except Exception as e:
                # 即使日志记录失败，也不应影响正常的请求响应
                print(f"错误：记录活动日志失败 - {e}")
                db.session.rollback()
            finally:
                # 清理 g 对象，避免数据污染下一次请求
                if hasattr(g, 'log_info'):
                    del g.log_info

            return response

        return wrapper

    return decorator
