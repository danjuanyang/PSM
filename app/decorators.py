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

    用法:
    @app.route('/some_route/<int:item_id>')
    @log_activity('VIEW_ITEM', action_detail_template='User viewed item with id {item_id}')
    def some_view_function(item_id):
        # ... 路由处理逻辑
        pass

    :param action_type: str, 必需, 动作的类型 (e.g., 'LOGIN_SUCCESS', 'CREATE_PROJECT').
    :param action_detail_template: str, 可选, 动作描述的模板.
                                  可以使用路由中的变量，如 '{item_id}'.
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 首先，执行原始的路由函数来获取其响应
            try:
                response = f(*args, **kwargs)
                # 从响应中提取 status_code
                if isinstance(response, tuple):
                    status_code = response[1]
                elif hasattr(response, 'status_code'):
                    status_code = response.status_code
                else:
                    # 如果返回的不是标准响应对象，默认为 200 OK
                    status_code = 200
            except Exception as e:
                # 如果路由函数本身抛出异常，我们也应该记录
                status_code = 500  # 内部服务器错误
                # 在重新抛出异常前记录日志
                _log_db(action_type, action_detail_template, status_code, kwargs)
                # 重新抛出异常，让Flask的错误处理器接管
                raise e

            # 正常执行后记录日志
            _log_db(action_type, action_detail_template, status_code, kwargs)

            return response

        return decorated_function

    return decorator


def _log_db(action_type, action_detail_template, status_code, route_kwargs):
    """
    内部辅助函数，执行实际的数据库写入操作，以避免代码重复。
    """
    # 只有认证通过的用户才记录活动
    if not current_user.is_authenticated:
        return

    action_detail = None
    if action_detail_template:
        try:
            # 使用路由的关键字参数 (e.g., user_id) 格式化描述
            action_detail = action_detail_template.format(**route_kwargs)
        except KeyError:
            # 如果模板中的变量在路由参数中找不到，就直接使用模板字符串
            action_detail = action_detail_template

    try:
        log_entry = UserActivityLog(
            user_id=current_user.id,
            session_id=None,  # 可以扩展为记录会话ID
            action_type=action_type,
            action_detail=action_detail,
            status_code=status_code,
            request_method=request.method,
            endpoint=request.endpoint,
            ip_address=request.remote_addr,
            # resource_type 和 resource_id 对于通用修饰器来说比较难获取
            # 如果需要记录这些，可以在路由中手动调用一个辅助函数来记录
            resource_type=None,
            resource_id=None
        )
        db.session.add(log_entry)
        db.session.commit()
    except Exception as e:
        # 如果日志记录失败，不应影响主流程
        print(f"错误记录活动： {e}")
        db.session.rollback()
