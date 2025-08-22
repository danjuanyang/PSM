# PSM/app/knowledge_base/init_permissions.py
"""
知识库模块权限初始化脚本
"""

from .. import db
from ..models import Permission, RolePermission, RoleEnum


def init_knowledge_base_permissions():
    """
    初始化知识库模块的权限。
    """
    
    # 定义知识库权限
    kb_permissions = [
        {
            'name': 'view_knowledge_base',
            'description': '查看知识库内容'
        },
        {
            'name': 'create_kb_item',
            'description': '创建知识库条目'
        },
        {
            'name': 'edit_own_kb_item',
            'description': '编辑自己的知识库条目'
        },
        {
            'name': 'delete_own_kb_item',
            'description': '删除自己的知识库条目'
        },
        {
            'name': 'manage_knowledge_base',
            'description': '管理所有知识库内容（包括其他用户的）'
        },
        {
            'name': 'sync_training_files',
            'description': '同步培训文件到知识库'
        },
        {
            'name': 'sync_public_files',
            'description': '同步公开文件到知识库'
        },
        {
            'name': 'create_public_kb_item',
            'description': '在公共空间创建知识库条目'
        }
    ]
    
    # 创建权限
    created_permissions = {}
    for perm_data in kb_permissions:
        permission = Permission.query.filter_by(name=perm_data['name']).first()
        if not permission:
            permission = Permission(
                name=perm_data['name'],
                description=perm_data['description'],
                is_active=True
            )
            db.session.add(permission)
            db.session.flush()
        created_permissions[perm_data['name']] = permission
    
    # 定义角色权限映射
    role_permission_mapping = {
        RoleEnum.SUPER: [
            'view_knowledge_base',
            'create_kb_item', 
            'edit_own_kb_item',
            'delete_own_kb_item',
            'manage_knowledge_base',
            'sync_training_files',
            'sync_public_files',
            'create_public_kb_item'
        ],
        RoleEnum.ADMIN: [
            'view_knowledge_base',
            'create_kb_item',
            'edit_own_kb_item', 
            'delete_own_kb_item',
            'manage_knowledge_base',
            'sync_training_files',
            'sync_public_files',
            'create_public_kb_item'
        ],
        RoleEnum.LEADER: [
            'view_knowledge_base',
            'create_kb_item',
            'edit_own_kb_item',
            'delete_own_kb_item',
            'create_public_kb_item'
        ],
        RoleEnum.MEMBER: [
            'view_knowledge_base',
            'create_kb_item',
            'edit_own_kb_item',
            'delete_own_kb_item'
        ]
    }
    
    # 创建角色权限关联
    for role, permission_names in role_permission_mapping.items():
        for perm_name in permission_names:
            permission = created_permissions[perm_name]
            
            # 检查是否已存在
            existing = RolePermission.query.filter_by(
                role=role,
                permission_id=permission.id
            ).first()
            
            if not existing:
                role_permission = RolePermission(
                    role=role,
                    permission_id=permission.id,
                    is_allowed=True
                )
                db.session.add(role_permission)
    
    try:
        db.session.commit()
        print("知识库权限初始化成功！")
        
        # 打印权限统计
        total_perms = len(kb_permissions)
        total_role_perms = sum(len(perms) for perms in role_permission_mapping.values())
        print(f"创建了 {total_perms} 个权限")
        print(f"创建了 {total_role_perms} 个角色权限关联")
        
    except Exception as e:
        db.session.rollback()
        print(f"权限初始化失败: {e}")
        raise


def init_default_kb_structure():
    """
    初始化默认的知识库结构（创建系统文件夹）。
    """
    from ..models import KnowledgeBaseItem, KBItemTypeEnum, KBNamespaceEnum, User
    
    # 获取管理员用户
    admin_user = User.query.filter_by(role=RoleEnum.SUPER).first()
    if not admin_user:
        admin_user = User.query.first()
    
    if not admin_user:
        print("警告：没有找到用户，无法创建默认知识库结构")
        return
    
    # 创建系统文件夹
    system_folders = ['培训', '公开文件']
    
    for folder_name in system_folders:
        existing = KnowledgeBaseItem.query.filter_by(
            name=folder_name,
            item_type=KBItemTypeEnum.FOLDER,
            namespace=KBNamespaceEnum.PUBLIC,
            parent_id=None
        ).first()
        
        if not existing:
            folder = KnowledgeBaseItem(
                name=folder_name,
                item_type=KBItemTypeEnum.FOLDER,
                namespace=KBNamespaceEnum.PUBLIC,
                owner_id=admin_user.id,
                parent_id=None
            )
            db.session.add(folder)
    
    try:
        db.session.commit()
        print("默认知识库结构初始化成功！")
        print(f"创建了系统文件夹: {', '.join(system_folders)}")
        
    except Exception as e:
        db.session.rollback()
        print(f"默认知识库结构初始化失败: {e}")
        raise


if __name__ == '__main__':
    # 独立运行脚本时的初始化
    from ... import create_app
    
    app = create_app()
    with app.app_context():
        print("开始初始化知识库权限...")
        init_knowledge_base_permissions()
        
        print("\n开始初始化默认知识库结构...")
        init_default_kb_structure()
        
        print("\n知识库模块初始化完成！")