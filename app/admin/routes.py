# PSM/app/admin/routes.py
from flask import request, jsonify
from . import admin_bp
from .. import db
from ..models import User, RoleEnum, Permission, UserPermission, RolePermission
from ..decorators import permission_required, log_activity


# ------------------- 用户管理 API -------------------

@admin_bp.route('/users', methods=['GET'])
@log_activity('用户列表',action_detail_template='用户列表')
@permission_required('view_users')  #'view_users' 是查看用户列表的权限
def get_users():
    """获取所有用户的列表 (分页)"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    pagination = User.query.paginate(page=page, per_page=per_page, error_out=False)
    users = pagination.items

    return jsonify({
        'users': [
            {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'role': user.role.name
            } for user in users
        ],
        'total_pages': pagination.pages,
        'current_page': pagination.page,
        'total_users': pagination.total
    })


@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@log_activity('用户的详细信息',action_detail_template='用户的详细信息')
@permission_required('view_users')
def get_user_details(user_id):
    """获取单个用户的详细信息，包括特定权限"""
    user = User.query.get_or_404(user_id)

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
@log_activity('用户的角色',action_detail_template=f'更新用户角色')
@permission_required('edit_user_role')
def update_user_role(user_id):
    """更新用户的角色"""
    user = User.query.get_or_404(user_id)
    data = request.get_json()

    if not data or 'role' not in data:
        return jsonify({'error': '请求正文中缺少角色'}), 400

    new_role_name = data['role'].upper()

    # 验证角色名称是否有效
    if new_role_name not in RoleEnum.__members__:
        return jsonify({'error': f'角色名称 无效： {new_role_name}'}), 400

    user.role = RoleEnum[new_role_name]
    db.session.commit()

    return jsonify({'message': f'用户 {user.username}\'s 角色已更新为{user.role.name}'})


# ------------------- 权限管理 API -------------------

@admin_bp.route('/permissions', methods=['GET'])
@log_activity('权限列表',action_detail_template='权限列表')
@permission_required('manage_permissions')
def get_permissions():
    """获取所有可用权限的列表"""
    permissions = Permission.query.all()
    return jsonify([
        {'id': p.id, 'name': p.name, 'description': p.description} for p in permissions
    ])


@admin_bp.route('/users/<int:user_id>/permissions', methods=['POST'])
@log_activity('设置用户的权限',action_detail_template='设置用户的权限')
@permission_required('manage_permissions')
def modify_user_permission(user_id):
    """为用户添加或移除特定权限"""
    user = User.query.get_or_404(user_id)
    data = request.get_json()
    log_activity.detail_kwargs = {'username': user.username}
    permission_name = data.get('permission_name')
    is_allowed = data.get('is_allowed', True)  # 默认为授予权限

    if not permission_name:
        return jsonify({'error': 'permission_name 是必需的'}), 400

    permission = Permission.query.filter_by(name=permission_name).first()
    if not permission:
        return jsonify({'error': f'权限 "{permission_name}"没找到'}), 404

    user_perm = UserPermission.query.filter_by(user_id=user.id, permission_id=permission.id).first()

    if user_perm:
        # 更新现有权限
        user_perm.is_allowed = is_allowed
    else:
        # 创建新的特定权限
        user_perm = UserPermission(user_id=user.id, permission_id=permission.id, is_allowed=is_allowed)
        db.session.add(user_perm)

    db.session.commit()
    action = "granted" if is_allowed else "revoked"
    return jsonify({'message': f'Permission "{permission_name}" has been {action} for user "{user.username}".'})


# ------------------- 角色管理 API -------------------

@admin_bp.route('/roles', methods=['GET'])
@log_activity('角色列表',action_detail_template='角色列表')
@permission_required('manage_roles')
def get_roles():
    """获取所有角色的列表"""
    roles = [{'name': role.name, 'value': role.value} for role in RoleEnum]
    return jsonify(roles)


@admin_bp.route('/roles/<role_name>/permissions', methods=['GET', 'PUT'])
@log_activity('角色的权限',action_detail_template='角色 {role_name} 的权限')
@permission_required('manage_roles')
def manage_role_permissions(role_name):
    """获取或更新一个角色的权限"""
    role_name_upper = role_name.upper()
    if role_name_upper not in RoleEnum.__members__:
        return jsonify({'error': f'角色 "{role_name}"没找到'}), 404

    role = RoleEnum[role_name_upper]

    if request.method == 'GET':
        # 获取该角色当前的所有权限
        role_perms = db.session.query(Permission.name, RolePermission.is_allowed).join(RolePermission).filter(
            RolePermission.role == role).all()
        return jsonify([{'name': name, 'allowed': allowed} for name, allowed in role_perms])

    if request.method == 'PUT':
        # 更新该角色的权限
        data = request.get_json()
        if not isinstance(data, list):
            return jsonify({'error': '请求正文必须是权限对象列表'}), 400

        # 先删除该角色所有旧的权限
        RolePermission.query.filter_by(role=role).delete()

        # 添加新的权限
        for perm_data in data:
            permission_name = perm_data.get('name')
            is_allowed = perm_data.get('is_allowed', True)
            permission = Permission.query.filter_by(name=permission_name).first()

            if permission:
                new_role_perm = RolePermission(role=role, permission_id=permission.id, is_allowed=is_allowed)
                db.session.add(new_role_perm)

        db.session.commit()
        return jsonify({'message': f'角色的权限"{role.name}"已更新。'})

