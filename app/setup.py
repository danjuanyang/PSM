# PSM/app/setup.py
import click
import os
from . import db
from .alerts.routes import generate_system_alerts_for_user
from .models import Permission, RolePermission, RoleEnum, ProjectFile, FileContent, User
from .files.routes import extract_text_from_file

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
    # 公告权限
    {'name': 'manage_announcements', 'description': '发布和管理公告'},
    {'name': 'view_announcement_stats', 'description': '查看公告阅读统计'},
    # 培训权限
    {'name': 'training_manage', 'description': '培训管理'},
    # 其他权限
    {'name': 'view_ai_setting', 'description': 'AI配置'},
    # 用户活跃
    {'name': 'view_user_activity', 'description': '查看用户活跃度'},
    # 配置系统数据
    {'name': 'manage_system_settings', 'description': '管理系统配置项'},
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
        # 公告权限
        'manage_announcements',
        'view_announcement_stats',
        'training_manage',
        # AI配置
        'view_ai_setting',
        # 用户活跃
        'view_user_activity',
        # 系统配置
        'manage_system_settings',
    ],
    RoleEnum.LEADER: [
        'view_users',
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

    @app.cli.command('index')
    @click.option('--reindex', is_flag=True, help='强制为所有文件重新建立索引')
    def index(reindex):
        """为已上传的文件创建或更新全文搜索索引。"""
        query = ProjectFile.query
        if not reindex:
            query = query.filter_by(text_extracted=False)

        files_to_index = query.all()
        if not files_to_index:
            click.echo('没有需要索引的文件。')
            return

        click.echo(f'开始为 {len(files_to_index)} 个文件建立索引...')
        with click.progressbar(files_to_index) as bar:
            for project_file in bar:
                if not os.path.exists(project_file.file_path):
                    continue

                file_ext = project_file.file_type
                extracted_text = extract_text_from_file(project_file.file_path, file_ext)

                if extracted_text:
                    # 查找现有的FileContent记录
                    file_content = FileContent.query.filter_by(file_id=project_file.id).first()
                    if file_content:
                        # 更新内容
                        file_content.content = extracted_text
                    else:
                        # 创建新记录
                        file_content = FileContent(file_id=project_file.id, content=extracted_text)
                        db.session.add(file_content)

                    project_file.text_extracted = True
                    db.session.add(project_file)

        db.session.commit()
        click.echo('索引建立完成。')

    @app.cli.command('generate-alerts')
    def generate_alerts_command():
        """扫描所有用户并生成系统提醒。"""
        click.echo('开始为所有用户生成提醒...')
        users = User.query.all()
        with click.progressbar(users) as bar:
            for user in bar:
                generate_system_alerts_for_user(user)
        click.echo('所有用户的提醒生成完毕。')