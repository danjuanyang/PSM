# PSM/app/setup.py
import click
from . import db
from .models import Permission, RolePermission, RoleEnum

# --- 定义应用所需的所有权限 ---
# 格式：{'name': '权限名称', 'description': '权限描述'}
PERMISSIONS = [
    # 用户和角色管理
    {'name': 'view_users', 'description': '查看用户列表和详情'},
    {'name': 'edit_user_role', 'description': '修改用户的角色'},
    {'name': 'manage_permissions', 'description': '管理用户和角色的特定权限'},
    {'name': 'manage_roles', 'description': '查看和修改角色的默认权限'},
    # 日志查看
    {'name': 'view_activity_logs', 'description': '查看用户活动日志'},
    {'name': 'view_session_logs', 'description': '查看用户登录会话'},
    # 其他模块的权限 (可以后续添加)
    {'name': 'manage_projects', 'description': '创建、编辑和删除项目'},
    {'name': 'view_reports', 'description': '查看人力资源报告'},
    # 项目模块权限
    {'name': 'manage_projects', 'description': '创建和编辑项目'},
    {'name': 'delete_projects', 'description': '删除项目'},
    {'name': 'manage_subprojects', 'description': '创建和编辑子项目'},
    {'name': 'delete_subprojects', 'description': '删除子项目'},
    {'name': 'manage_stages', 'description': '创建和编辑阶段'},
    {'name': 'delete_stages', 'description': '删除阶段'},
    {'name': 'manage_tasks', 'description': '创建和编辑任务'},
    {'name': 'delete_tasks', 'description': '删除任务'},
    {'name': 'assign_tasks', 'description': '为任务分配成员'},
    {'name': 'update_task_progress', 'description': '更新自己的任务进度和状态'},
    # HR
    {'name': 'manage_teams', 'description': '分配组长和组员'},
    {'name': 'view_clock_in_reports', 'description': '查看补卡记录报告'},
    {'name': 'view_progress_reports', 'description': '查看任务进度更新报告'},

]

# --- 定义各角色的默认权限 ---
# 将上面定义的权限名称分配给不同的角色
# SUPER 角色默认拥有所有权限，无需在此定义
ROLE_DEFAULT_PERMISSIONS = {
    RoleEnum.SUPER: ['all'],  # 特殊标记，拥有所有权限
    RoleEnum.ADMIN: [
        'view_users',
        'edit_user_role',
        'manage_permissions',
        'manage_roles',
        'view_activity_logs',
        'view_session_logs',
        'manage_projects',
        'view_reports',
        # 项目模块权限
        'manage_projects', 'delete_projects',
        'manage_subprojects', 'delete_subprojects',
        'manage_stages', 'delete_stages',
        'manage_tasks', 'delete_tasks',
        'assign_tasks',
        # 人力资源部分
        'manage_teams',
        'view_clock_in_reports',
        'view_progress_reports',
    ],
    RoleEnum.LEADER: [
        'view_users',
        'manage_projects',
        'view_reports',
        # 项目模块权限
        'manage_subprojects', 'delete_subprojects',  # 负责人可以管理自己项目下的子项目
        'manage_stages', 'delete_stages',
        'manage_tasks', 'delete_tasks',
        'assign_tasks',
        'update_task_progress',
    ],
    RoleEnum.MEMBER: [
        # 普通成员默认可能只有查看自己项目的权限，那些通常是基于对象ID判断，而不是通用权限
        'update_task_progress',
    ]
}


def register_commands(app):
    @app.cli.command('seed')
    def seed():
        """
        初始化数据库，创建权限和角色权限。
        这个命令是幂等的，可以安全地多次运行。
        """
        # --- 1. 创建所有权限 ---
        click.echo('正在创建权限...')
        for perm_info in PERMISSIONS:
            perm = Permission.query.filter_by(name=perm_info['name']).first()
            if not perm:
                perm = Permission(name=perm_info['name'], description=perm_info['description'])
                db.session.add(perm)
                click.echo(f"  Created 权限： {perm.name}")
        db.session.commit()
        click.echo('权限创建成功。')

        # --- 2. 为角色分配默认权限 ---
        click.echo('\n正在为角色分配默认权限...')
        for role, perm_names in ROLE_DEFAULT_PERMISSIONS.items():
            for perm_name in perm_names:
                # 检查角色-权限关系是否已存在
                role_perm_exists = db.session.query(RolePermission).join(Permission).filter(
                    RolePermission.role == role,
                    Permission.name == perm_name
                ).first()

                if not role_perm_exists:
                    permission = Permission.query.filter_by(name=perm_name).first()
                    if permission:
                        rp = RolePermission(role=role, permission_id=permission.id)
                        db.session.add(rp)
                        click.echo(f" 批予'{perm_name}' 目标角色 '{role.name}'")
        db.session.commit()
        click.echo('已成功分配角色权限。')
