# PSM/app/auth/routes.py
from flask import request, jsonify, session
from flask_login import login_user, logout_user, current_user, login_required

from . import auth_bp
from ..models import User, RoleEnum
from .. import db, bcrypt


@auth_bp.route('/register', methods=['POST'])
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
        return jsonify({"error": "该用户名已被使用"}), 409  # 409 Conflict

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
def login():
    """
    用户登录API端点。
    验证用户凭据并创建会话。
    """
    if not request.is_json:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    if current_user.is_authenticated:
        return jsonify({"message": "用户已登录"}), 200

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"error": "缺少用户名或密码"}), 400

    user = User.query.filter_by(username=username).first()

    # 验证用户存在且密码正确
    if user and user.check_password(password):
        # 在 login_user 之前，将会话标记为“永久”
        session.permanent = True
        # 使用 flask-login 创建会话 (基于Cookie)
        # login_user(user, remember=True)  # remember=True 保持会话持久
        login_user(user)
        return jsonify({
            "message": "登录成功",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role.name  # 返回角色名称
            }
        }), 200
    else:
        return jsonify({"error": "用户名或密码无效"}), 401  # 401 Unauthorized


@auth_bp.route('/logout', methods=['POST'])
@login_required  # 确保只有登录的用户才能登出
def logout():
    """
    用户登出API端点。
    """
    logout_user()
    return jsonify({"message": "登出成功"}), 200


@auth_bp.route('/status', methods=['GET'])
def status():
    """
    检查当前用户的登录状态。
    前端可以用这个接口来判断是否需要显示登录页。
    """
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
