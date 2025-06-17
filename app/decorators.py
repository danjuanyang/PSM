# PSM/app/decorators.py

from functools import wraps
from flask import jsonify
from flask_login import current_user


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

