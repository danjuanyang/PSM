# PSM/app/admin/routes.py
import random
import string
from flask import request, jsonify, g
from flask_bcrypt import generate_password_hash
from flask_login import login_required, current_user

from . import admin_bp
from .. import db
from ..models import User, RoleEnum, Permission, UserPermission, RolePermission, Training
from ..decorators import permission_required, log_activity


# --- 辅助函数 ---
def generate_random_password(length=10):
    """生成指定长度的随机字母和数字密码"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for i in range(length))


# ------------------- 用户管理 API -------------------

@admin_bp.route('/users', methods=['GET'])
@log_activity('用户列表', action_detail_template='查看用户列表')
@permission_required('view_users')
def get_users():
    """
    获取所有用户的列表 (不分页)
    - 永远不显示 SUPER 角色的用户。
    - 根据当前用户权限过滤列表，低权限管理员无法看到高权限或同级管理员。
    """
    # 基础查询，始终排除 SUPER 用户
    query = User.query.filter(User.role != RoleEnum.SUPER)

    # 如果操作者不是 SUPER, 则增加额外的过滤，防止其看到比自己等级高或同级的用户
    if current_user.role != RoleEnum.SUPER:
        current_user_role_value = current_user.role.value
        # 找出所有权限更高或同级的角色 (数值更小或相等)
        excluded_roles = [r for r in RoleEnum if r.value <= current_user_role_value]
        # 从查询中排除这些角色的用户
        query = query.filter(User.role.notin_(excluded_roles))

    users = query.all()
    return jsonify({
        'users': [
            {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'role': user.role.name
            } for user in users
        ]
    })


@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@log_activity('查看用户详细信息', action_detail_template='用户{username}的详细信息')
@permission_required('view_users')
def get_user_details(user_id):
    """获取单个用户的详细信息，包括特定权限"""
    user = User.query.get_or_404(user_id)
    g.log_info = {'username': user.username}
    # 获取用户的特定权限
    specific_perms = db.session.query(Permission.name, UserPermission.is_allowed).join(UserPermission).filter(
        UserPermission.user_id == user.id).all()

    return jsonify({
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role.name,
        'specific_permissions': [
            {'name': name, 'allowed': allowed} for name, allowed in specific_perms
        ]
    })


@admin_bp.route('/users/<int:user_id>/role', methods=['PUT'])
@permission_required('edit_user_role')
@log_activity('用户的角色', action_detail_template='更新用户 {username} 角色')
def update_user_role(user_id):
    """更新用户的角色"""
    target_user = User.query.get_or_404(user_id)
    g.log_info = {'username': target_user.username}

    # 安全检查：禁止操作者修改同级或更高级别的用户角色
    if current_user.role.value >= target_user.role.value:
        return jsonify({'error': '权限不足：无法修改同级或更高级别用户的角色。'}), 403

    data = request.get_json()
    if not data or 'role' not in data:
        return jsonify({'error': '请求正文中缺少角色'}), 400

    new_role_name = data['role'].upper()
    if new_role_name not in RoleEnum.__members__:
        return jsonify({'error': f'角色名称 无效： {new_role_name}'}), 400

    # 安全检查：禁止将任何用户提升到与操作者同级或更高级别
    new_role_value = RoleEnum[new_role_name].value
    if new_role_value <= current_user.role.value:
        return jsonify({'error': '权限不足：无法将用户提升到同级或更高级别。'}), 403

    target_user.role = RoleEnum[new_role_name]
    db.session.commit()

    return jsonify({'message': f'用户 {target_user.username} 的角色已更新为 {target_user.role.name}'})


@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@permission_required('reset_user_password')
@log_activity('重置用户密码', action_detail_template='重置了用户 {username} (id:{id})的密码')
def reset_user_password(user_id):
    """
    为指定用户重置密码，并返回新密码
    """
    user = User.query.get_or_404(user_id)
    new_password = generate_random_password()
    g.log_info = {'username': user.username, 'id': user.id}
    # 使用在 User model 中定义的 set_password 方法
    user.set_password(new_password)
    db.session.commit()

    return jsonify({
        'message': f"用户 '{user.username}' 的密码已重置",
        'new_password': new_password
    })


# ------------------- 权限管理 API -------------------

@admin_bp.route('/permissions', methods=['GET'])
@log_activity('权限列表', action_detail_template='权限列表')
@permission_required('manage_permissions')
def get_permissions():
    """获取所有可用权限的列表"""
    permissions = Permission.query.all()
    return jsonify([
        {'id': p.id, 'name': p.name, 'description': p.description} for p in permissions
    ])


@admin_bp.route('/users/<int:user_id>/permissions', methods=['POST'])
@log_activity('设置用户的权限', action_detail_template='设置用户{username}的权限')
@permission_required('manage_permissions')
def modify_user_permission(user_id):
    """为用户添加或移除特定权限"""
    target_user = User.query.get_or_404(user_id)
    g.log_info = {'username': target_user.username}

    # 安全检查：禁止操作者修改同级或更高级别的用户权限
    if current_user.role.value >= target_user.role.value:
        return jsonify({'error': '权限不足：无法修改同级或更高级别用户的权限。'}), 403

    data = request.get_json()
    permission_name = data.get('permission_name')
    is_allowed = data.get('is_allowed', True)

    if not permission_name:
        return jsonify({'error': 'permission_name 是必需的'}), 400

    permission = Permission.query.filter_by(name=permission_name).first()
    if not permission:
        return jsonify({'error': f'权限 "{permission_name}" 没找到'}), 404

    user_perm = UserPermission.query.filter_by(user_id=target_user.id, permission_id=permission.id).first()

    if user_perm:
        user_perm.is_allowed = is_allowed
    else:
        user_perm = UserPermission(user_id=target_user.id, permission_id=permission.id, is_allowed=is_allowed)
        db.session.add(user_perm)

    db.session.commit()
    action = "granted" if is_allowed else "revoked"
    return jsonify({'message': f'Permission "{permission_name}" has been {action} for user "{target_user.username}".'})


# ------------------- 角色管理 API -------------------

@admin_bp.route('/roles', methods=['GET'])
@log_activity('角色列表', action_detail_template='获取所有角色列表')
@permission_required('manage_roles')
def get_roles():
    """获取所有角色的列表"""
    roles = [{'name': role.name, 'value': role.value} for role in RoleEnum]
    return jsonify(roles)


@admin_bp.route('/roles/<role_name>/permissions', methods=['GET', 'PUT'])
@log_activity('角色的权限', action_detail_template='角色 {role_name} 的权限')
@permission_required('manage_roles')
def manage_role_permissions(role_name):
    """获取或更新一个角色的权限"""
    role_name_upper = role_name.upper()
    if role_name_upper not in RoleEnum.__members__:
        return jsonify({'error': f'角色 "{role_name}"没找到'}), 404

    target_role = RoleEnum[role_name_upper]

    # 安全检查：禁止操作者修改同级或更高级别的角色权限
    if current_user.role.value >= target_role.value:
        return jsonify({'error': '权限不足：无法修改同级或更高级别角色的权限。'}), 403

    if request.method == 'GET':
        role_perms = db.session.query(Permission.name, RolePermission.is_allowed).join(RolePermission).filter(
            RolePermission.role == target_role).all()
        return jsonify([{'name': name, 'allowed': allowed} for name, allowed in role_perms])

    if request.method == 'PUT':
        data = request.get_json()
        if not isinstance(data, list):
            return jsonify({'error': '请求正文必须是权限对象列表'}), 400

        # --- 优化点：一次性查询所有需要的权限 ---
        perm_names_from_request = [p.get('name') for p in data if p.get('name')]
        permissions_in_db = Permission.query.filter(Permission.name.in_(perm_names_from_request)).all()
        permission_map = {p.name: p for p in permissions_in_db}
        # --- 优化结束 ---

        # 使用事务，确保操作的原子性
        try:
            # 1. 先删除该角色所有旧的权限
            RolePermission.query.filter_by(role=target_role).delete()

            # 2. 添加新的权限
            for perm_data in data:
                permission_name = perm_data.get('name')
                permission = permission_map.get(permission_name)

                if permission:
                    is_allowed = perm_data.get('is_allowed', True)
                    new_role_perm = RolePermission(role=target_role, permission_id=permission.id, is_allowed=is_allowed)
                    db.session.add(new_role_perm)

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': '更新权限时出错', 'details': str(e)}), 500

        return jsonify({'message': f'角色的权限"{target_role.name}"已更新。'})

# 培训功能，管理员分配培训任务，每个月分配一个用户


# 删除用户，后续实现，先做内容
# 2025年6月24日15:51:18
