# PSM/app/auth/routes.py
import re
import os
from datetime import datetime
from flask import request, jsonify, session, g, current_app
from flask_login import login_user, logout_user, current_user, login_required

from . import auth_bp
from ..decorators import log_activity
from ..models import User, RoleEnum, Permission, UserPermission, RolePermission, UserSession
from .. import db, bcrypt


@auth_bp.route('/register', methods=['POST'])
@log_activity('注册')
def register():
    """
    用户注册API端点。
    接收JSON格式的用户数据并创建新用户。
    """
    if not current_app.config['ALLOW_REGISTRATION']:
        return jsonify({"error": "注册功能当前已关闭", "code": "REGISTRATION_DISABLED"}), 403
    # 1. 检查是否是JSON请求
    if not request.is_json:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    # 2. 如果用户已登录，则不允许注册
    if current_user.is_authenticated:
        return jsonify({"error": "您已登录，无法注册新账户"}), 400

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')

    # 3. 验证输入数据
    if not all([username, password, email]):
        return jsonify({"error": "缺少必要字段：username, password, email"}), 400

    # 4. 检查用户名是否已存在
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "该用户名已被使用"}), 409  # 409 冲突

    # 5. 创建并保存新用户
    try:
        new_user = User(
            username=username,
            email=email,
            role=RoleEnum.MEMBER  # 默认角色
        )
        # 使用 set_password 方法加密密码
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        # 返回成功响应，201 Created
        return jsonify({
            "message": "用户注册成功",
            "user": {
                "id": new_user.id,
                "username": new_user.username,
                "email": new_user.email
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"服务器内部错误: {str(e)}"}), 500


@auth_bp.route('/login', methods=['POST'])
@log_activity('登录', action_detail_template='用户 {username} 登录系统')
def login():
    """
    用户登录API端点。
    验证用户凭据并创建会话。
    如果用户已登录，会先将其登出。
    """
    # 如果一个已登录用户尝试再次登录，先将他登出
    if current_user.is_authenticated:
        # 先执行登出逻辑以关闭旧会话
        session_id = session.get('user_session_id')
        if session_id:
            user_session = UserSession.query.get(session_id)
            if user_session and user_session.is_active:
                user_session.is_active = False
                user_session.logout_time = datetime.now()
                if user_session.login_time:
                    duration = user_session.logout_time - user_session.login_time
                    user_session.session_duration = int(duration.total_seconds())
                db.session.commit()
        logout_user()
        session.clear()

    if not request.is_json:
        return jsonify({"error": "请求必须是JSON格式", "code": "INVALID_REQUEST_FORMAT"}), 415
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    g.log_info = {'username': username}
    if not username or not password:
        return jsonify({"error": "缺少用户名或密码", "code": "MISSING_CREDENTIALS"}), 400
    user = User.query.filter_by(username=username).first()
    # 1. 检查用户是否存在
    if not user:
        return jsonify({"error": "请检查用户名，用户不存在", "code": "USER_NOT_FOUND"}), 401
    # 2. 检查密码是否正确
    if not user.check_password(password):
        return jsonify({"error": "登录错误：密码错误", "code": "INVALID_PASSWORD"}), 401
    # 3. (可选) 检查用户是否被禁用
    # 假设您的 User 模型有一个 is_active 字段
    if hasattr(user, 'is_active') and not user.is_active:
        return jsonify({"error": "该账户已被禁用，请联系管理员", "code": "USER_DISABLED"}), 403

    # 验证通过，登录用户
    session.permanent = True
    login_user(user)

    # 创建新的 UserSession 记录
    try:
        new_user_session = UserSession(
            user_id=user.id,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string,
            login_time=datetime.now()
        )
        db.session.add(new_user_session)
        db.session.flush()  # 获取新会话的ID
        session['user_session_id'] = new_user_session.id
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # 在实践中，这里应该使用 app.logger.error()
        print(f"Error creating user session: {e}")
        return jsonify({"error": "创建用户会话失败"}), 500

    return jsonify({
        "message": "登录成功",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role.name
        }
    }), 200


@auth_bp.route('/logout', methods=['POST'])
@login_required
@log_activity('退出系统', action_detail_template='用户 {username} 登出系统')
def logout():
    """
    用户登出API端点。
    """
    g.log_info = {'username': current_user.username}
    
    # 关闭 UserSession
    session_id = session.get('user_session_id')
    if session_id:
        user_session = UserSession.query.get(session_id)
        if user_session and user_session.is_active:
            user_session.is_active = False
            user_session.logout_time = datetime.now()
            if user_session.login_time:
                duration = user_session.logout_time - user_session.login_time
                user_session.session_duration = int(duration.total_seconds())
            db.session.commit()

    logout_user()
    session.clear() # 清理所有会话数据
    return jsonify({"message": "登出成功"}), 200


@auth_bp.route('/status', methods=['GET'])
def status():
    """
    检查当前用户的登录状态并返回完整的用户信息。
    包含角色和权限数据以支持前端动态路由生成。
    """
    g.log_info = {'username': current_user.username if current_user.is_authenticated else 'anonymous'}

    if current_user.is_authenticated:
        # 权限计算逻辑重构
        final_permissions = {}

        # 1. SUPER用户拥有所有激活的权限
        if current_user.role == RoleEnum.SUPER:
            all_active_permissions = Permission.query.filter_by(is_active=True).all()
            for p in all_active_permissions:
                final_permissions[p.name] = True
        else:
            # 2. 首先加载角色的所有有效权限
            role_permissions = RolePermission.query.join(Permission).filter(
                RolePermission.role == current_user.role,
                RolePermission.is_allowed == True,
                Permission.is_active == True
            ).all()
            for rp in role_permissions:
                final_permissions[rp.permission.name] = True

            # 3. 获取用户的特定权限设置并用其覆盖角色权限
            specific_permissions = UserPermission.query.join(Permission).filter(
                UserPermission.user_id == current_user.id,
                Permission.is_active == True
            ).all()
            for up in specific_permissions:
                final_permissions[up.permission.name] = up.is_allowed

        # 4. 格式化最终的权限列表，只包括被允许的权限
        user_permissions = [{'name': name} for name, allowed in final_permissions.items() if allowed]

        return jsonify({
            "logged_in": True,
            "data": {
                "user": {
                    "id": current_user.id,
                    "username": current_user.username,
                    "email": current_user.email,
                    "create_at":current_user.created_at,
                    "role": current_user.role.name  # 枚举的name属性
                },
                "roles": [current_user.role.name],
                "permissions": user_permissions
            }
        }), 200
    else:
        return jsonify({"logged_in": False}), 200


# 修改密码
@auth_bp.route('/change_password', methods=['POST'])
@login_required
@log_activity('修改密码', action_detail_template='用户 {username} 修改密码')
def change_password():
    """
    用户修改密码API端点。
    :return:
    """
    g.log_info = {'username': current_user.username}
    if not request.is_json:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    data = request.get_json()
    old_password = data.get('old_password')
    new_password = data.get('new_password')

    if not old_password or not new_password:
        return jsonify({"error": "缺少旧密码或新密码"}), 400

    if not current_user.check_password(old_password):
        return jsonify({"error": "旧密码不正确"}), 401

    current_user.set_password(new_password)
    db.session.commit()

    return jsonify({
        "message": "用户修改密码成功",
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email
        }
    }), 201


# 更改用户名
@auth_bp.route('/change_username', methods=['POST'])
@login_required
@log_activity('修改用户名', action_detail_template='用户 {username} 修改用户名')
def change_username():
    """
    用户修改用户名API端点。
    :return:
    """
    g.log_info = {'username': current_user.username}
    if not request.is_json:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    data = request.get_json()
    new_username = data.get('new_username')

    if not new_username:
        return jsonify({"error": "缺少新用户名"}), 400

    # 检查用户名是否已存在
    existing_user = User.query.filter_by(username=new_username).first()
    if existing_user:
        return jsonify("用户名已存在"), 400
    current_user.username = new_username
    db.session.commit()
    return jsonify({"message": "用户名修改成功"}), 201


# 更改邮箱
@auth_bp.route('/change_email', methods=['POST'])
@login_required
@log_activity('修改邮箱', action_detail_template='用户 {username} 修改邮箱')
def change_email():
    """
    用户修改邮箱API端点。
    需要提供新邮箱和当前密码进行验证。
    """
    g.log_info = {'username': current_user.username}
    if not request.is_json:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    data = request.get_json()
    new_email = data.get('new_email')
    password = data.get('password')

    if not new_email or not password:
        return jsonify({"error": "缺少新邮箱或密码"}), 400

    # 验证邮箱格式
    if not re.match(r"[^@]+@[^@]+\.[^@]+", new_email):
        return jsonify({"error": "无效的邮箱格式"}), 400

    # 验证当前密码
    if not current_user.check_password(password):
        return jsonify({"error": "密码不正确，无法完成操作"}), 401

    # 检查新邮箱是否已被其他用户注册
    existing_user = User.query.filter(User.email == new_email, User.id != current_user.id).first()
    if existing_user:
        return jsonify({"error": "该邮箱已被注册"}), 409

    current_user.email = new_email
    db.session.commit()

    return jsonify({
        "message": "用户修改邮箱成功",
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email
        }
    }), 200


@auth_bp.route('/settings/registration', methods=['GET', 'POST'])
def registration_settings():
    if request.method == 'GET':
        response = jsonify({"allow_registration": current_app.config['ALLOW_REGISTRATION']})
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    if request.method == 'POST':
        # POST请求需要管理员权限
        if not current_user.is_authenticated or current_user.role != RoleEnum.ADMIN:
            return jsonify({"error": "权限不足"}), 403

        data = request.get_json()
        if data is None:
            return jsonify({"error": "请求必须是JSON格式"}), 415
        
        allow = data.get('allow_registration')
        if not isinstance(allow, bool):
            return jsonify({"error": "无效的数据格式"}), 400

        # 这部分比较棘手，因为我们不能直接修改app.config
        # 最好的方法是存储在数据库或一个配置文件中
        # 这里为了简单，我们直接修改.env文件
        # 注意：在生产环境中，这通常不是一个好主意
        env_path = os.path.join(os.path.dirname(current_app.root_path), '.env')
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
            
            with open(env_path, 'w') as f:
                found = False
                for line in lines:
                    if line.strip().startswith('ALLOW_REGISTRATION'):
                        f.write(f'ALLOW_REGISTRATION={str(allow)}\n')
                        found = True
                    else:
                        f.write(line)
                if not found:
                    f.write(f'\nALLOW_REGISTRATION={str(allow)}\n')
            current_app.config['ALLOW_REGISTRATION'] = allow
            return jsonify({"message": "设置已更新"}), 200
        else:
            return jsonify({"error": ".env 文件未找到"}), 500
