# PSM/app/decorators.py

from functools import wraps
from flask import jsonify, request
from flask_login import current_user

from app import db
from app.models import UserActivityLog


def permission_required(permission_name):
    """
    自定义权限检查修饰器。
    检查当前登录的用户是否具有所需的权限。
    - 如果用户未登录，返回401 Unauthorized。
    - 如果用户没有权限，返回403 Forbidden。

    用法:
    @app.route('/some_route')
    @permission_required('edit_project')
    def some_view_function():
        # 路由处理逻辑
        pass
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 1. 检查用户是否已登录
            if not current_user.is_authenticated:
                return jsonify({'error': '需要身份验证！请登录'}), 401

            # 2. 检查用户是否拥有指定权限
            #    这里调用了您在models.py中定义的 can 方法
            if not current_user.can(permission_name):
                return jsonify({'error': '您没有执行此作的权限'}), 403

            # 3. 如果所有检查通过，执行原始的路由函数
            return f(*args, **kwargs)

        return decorated_function

    return decorator


# 您也可以根据需要，创建基于角色的修饰器

def role_required(role):
    """检查用户是否属于特定角色"""

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


def log_activity(action_type, action_detail_template=None):
    """
    一个作为修饰器的函数，用于在路由执行后自动记录用户活动。
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 为登出操作预先捕获用户信息
            user_before_logout = None
            if request.endpoint == 'auth.logout' and current_user.is_authenticated:
                user_before_logout = current_user._get_current_object()

            try:
                response = f(*args, **kwargs)
                if isinstance(response, tuple):
                    status_code = response[1]
                else:
                    status_code = getattr(response, 'status_code', 200)
            except Exception as e:
                status_code = 500
                _log_db(action_type, action_detail_template, status_code, kwargs, user=user_before_logout)
                raise e

            _log_db(action_type, action_detail_template, status_code, kwargs, user=user_before_logout)

            return response
        return decorated_function
    return decorator

def _log_db(action_type, action_detail_template, status_code, route_kwargs, user=None):
    """
    内部辅助函数，执行实际的数据库写入操作。
    :param user: 可选参数，用于在用户登出后记录其信息。
    """
    # 如果没有显式传递user，就使用current_user
    log_user = user or current_user

    # 只有认证通过的用户才记录活动
    if not log_user or not log_user.is_authenticated:
        return

    action_detail = None
    if action_detail_template:
        try:
            # 在模板中添加 current_user，方便记录操作者
            format_kwargs = route_kwargs.copy()
            format_kwargs['current_user'] = log_user
            action_detail = action_detail_template.format(**format_kwargs)
        except (KeyError, IndexError):
            action_detail = action_detail_template

    try:
        log_entry = UserActivityLog(
            user_id=log_user.id,
            action_type=action_type,
            action_detail=action_detail,
            status_code=status_code,
            request_method=request.method,
            endpoint=request.endpoint,
            ip_address=request.remote_addr,
        )
        db.session.add(log_entry)
        db.session.commit()
    except Exception as e:
        print(f"错误记录活动：{e}")
        db.session.rollback()