# PSM/app/auth/routes.py
from flask import request, jsonify, session, g
from flask_login import login_user, logout_user, current_user, login_required

from . import auth_bp
from ..decorators import log_activity
from ..models import User, RoleEnum
from .. import db, bcrypt


@auth_bp.route('/register', methods=['POST'])
@log_activity('注册')
def register():
    """
    用户注册API端点。
    接收JSON格式的用户数据并创建新用户。
    """
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
        logout_user()

    if not request.is_json:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    g.log_info = {'username': username}
    if not username or not password:
        return jsonify({"error": "缺少用户名或密码"}), 400

    user = User.query.filter_by(username=username).first()

    # 验证用户存在且密码正确
    if user and user.check_password(password):
        session.permanent = True
        login_user(user)  # 不使用 remember=True 来确保会话过期功能正常
        return jsonify({
            "message": "登录成功",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role.name
            }
        }), 200
    else:
        # 凭证无效，返回 401 Unauthorized
        return jsonify({"error": "用户名或密码无效"}), 401


@auth_bp.route('/logout', methods=['POST'])
@login_required
@log_activity('退出系统', action_detail_template='用户 {username} 登出系统')
def logout():
    """
    用户登出API端点。
    """
    # 在修饰器执行前，current_user是有效的。
    # 修饰器会在 logout_user() 执行前捕获它。
    g.log_info = {'username': current_user.username}
    logout_user()
    return jsonify({"message": "登出成功"}), 200


@auth_bp.route('/status', methods=['GET'])
# @log_activity('检查登录状态', action_detail_template='用户 {username} 检查登录状态')
# 没必要记录这个操作，要获取用户是否离线，会频繁请求
def status():
    """
    检查当前用户的登录状态。
    前端可以用这个接口来判断是否需要显示登录页。
    """
    g.log_info = {'username': current_user.username}
    if current_user.is_authenticated:
        return jsonify({
            "logged_in": True,
            "user": {
                "id": current_user.id,
                "username": current_user.username,
                "email": current_user.email,
                "role": current_user.role.name
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
