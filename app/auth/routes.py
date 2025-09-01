# PSM/app/auth/routes.py
import re
import os
from datetime import datetime
from flask import request, jsonify, session, g, current_app
from flask_login import login_user, logout_user, current_user, login_required

from . import auth_bp
from ..decorators import log_activity
from ..models import User, RoleEnum, Permission, UserPermission, RolePermission, UserSession, Project, Alert, \
    AnnouncementReadStatus, StageTask, StatusEnum, Announcement, SystemConfig, ProjectStage
from .. import db, bcrypt


@auth_bp.route('/register', methods=['POST'])
@log_activity('注册')
def register():
    """
    用户注册API端点。
    接收JSON格式的用户数据并创建新用户。
    """
    # 确保从最新的配置中读取
    allow_reg_config = SystemConfig.query.filter_by(key='ALLOW_REGISTRATION').first()
    allow_registration = allow_reg_config.value.lower() == 'true' if allow_reg_config else current_app.config.get('ALLOW_REGISTRATION', False)

    if not allow_registration:
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
    # 模型有一个 is_active 字段
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
    if not re.match(r"[^@]+@[^@]+\\.[^@]+", new_email):
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


@auth_bp.route('/public/registration-status', methods=['GET'])
def public_registration_status():
    """
    公开的API端点，用于检查是否允许用户注册。
    此端点无需认证。
    """
    config_entry = SystemConfig.query.filter_by(key='ALLOW_REGISTRATION').first()
    if config_entry:
        allow_reg = config_entry.value.lower() == 'true'
    else:
        allow_reg = current_app.config.get('ALLOW_REGISTRATION', False)
    
    response = jsonify({"allow_registration": allow_reg})
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@auth_bp.route('/settings/registration', methods=['POST'])
@login_required
def update_registration_settings():
    """
    更新用户注册设置 (需要管理员权限)。
    """
    # 权限检查
    if current_user.role not in [RoleEnum.ADMIN, RoleEnum.SUPER]:
        return jsonify({"error": "权限不足"}), 403

    data = request.get_json()
    if data is None:
        return jsonify({"error": "请求必须是JSON格式"}), 415
    
    allow = data.get('allow_registration')
    if not isinstance(allow, bool):
        return jsonify({"error": "无效的数据格式，'allow_registration' 必须是布尔值"}), 400

    try:
        # 更新或创建数据库中的配置项
        config_entry = SystemConfig.query.filter_by(key='ALLOW_REGISTRATION').first()
        if config_entry:
            config_entry.value = str(allow)
        else:
            config_entry = SystemConfig(
                key='ALLOW_REGISTRATION',
                value=str(allow),
                description='是否允许新用户注册'
            )
            db.session.add(config_entry)
        
        db.session.commit()

        # 实时更新当前应用的配置
        current_app.config['ALLOW_REGISTRATION'] = allow
        
        return jsonify({"message": "注册设置已更新", "allow_registration": allow}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新注册设置失败: {e}")
        return jsonify({"error": "更新设置时发生服务器错误"}), 500


@auth_bp.route('/dashboard_stats', methods=['GET'])
@login_required
def dashboard_stats():
    """
    为Dashboard提供关键指标的统计数据。
    """
    try:
        # 1. 进行中的项目数 (仅限当前用户)
        in_progress_projects = Project.query.filter_by(
            status=StatusEnum.IN_PROGRESS,
            employee_id=current_user.id
        ).count()

        # 2. 待办任务数
        #    为了准确，我们应该连接到用户负责的项目
        pending_tasks = db.session.query(StageTask.id).join(ProjectStage).join(Project).filter(
            Project.employee_id == current_user.id,
            StageTask.status.in_([StatusEnum.PENDING, StatusEnum.IN_PROGRESS])
        ).count()


        # 3. 未读公告数
        unread_announcements = db.session.query(Announcement.id).outerjoin(
            AnnouncementReadStatus,
            (Announcement.id == AnnouncementReadStatus.announcement_id) &
            (AnnouncementReadStatus.user_id == current_user.id)
        ).filter(
            (AnnouncementReadStatus.is_read == None) | (AnnouncementReadStatus.is_read == False)
        ).count()

        # 4. 未读提醒数
        unread_alerts = Alert.query.filter_by(user_id=current_user.id, is_read=False).count()

        # 5. 最近的项目更新 (仅限当前用户)
        recent_projects = Project.query.filter_by(employee_id=current_user.id).order_by(Project.start_date.desc()).limit(5).all()
        recent_projects_data = [{
            'id': p.id,
            'name': p.name,
            'employee_name': p.employee.username if p.employee else 'N/A',
            'progress': p.progress,
            'status': p.status.value if p.status else None
        } for p in recent_projects]

        # 6. 最近的动态 (公告和提醒)
        recent_alerts = Alert.query.filter_by(user_id=current_user.id).order_by(Alert.created_at.desc()).limit(5).all()
        
        # 获取所有公告，并在Python中判断是否已读
        all_announcements = Announcement.query.order_by(Announcement.created_at.desc()).limit(10).all()
        read_announcement_ids = {item.announcement_id for item in AnnouncementReadStatus.query.filter_by(user_id=current_user.id, is_read=True).all()}

        recent_announcements_data = [{
            'id': a.id,
            'title': a.title,
            'created_at': a.created_at.isoformat(),
            'is_read_by_current_user': a.id not in read_announcement_ids
        } for a in all_announcements]


        return jsonify({
            "data": {
                "stats": {
                    "in_progress_projects": in_progress_projects,
                    "pending_tasks": pending_tasks,
                    "unread_announcements": unread_announcements,
                    "unread_alerts": unread_alerts,
                },
                "recent_projects": recent_projects_data,
                "recent_announcements": recent_announcements_data,
                "recent_alerts": [{
                    'id': a.id,
                    'message': a.message,
                    'created_at': a.created_at.isoformat()
                } for a in recent_alerts]
            }
        })

    except Exception as e:
        current_app.logger.error(f"获取Dashboard统计数据失败: {e}")
        return jsonify({"error": "获取Dashboard统计数据失败"}), 500
