# PSM/app/knowledge_base/routes.py
import os
from datetime import datetime

from flask import request, jsonify, current_app
from flask_login import current_user, login_required
from sqlalchemy import or_, and_

from . import kb_bp
from ..models import (KnowledgeBaseItem, KBItemTypeEnum, KBNamespaceEnum, MarkdownDocument, 
                     MindMap, MindMapNodeLink, ProjectFile, User, RoleEnum)
from .. import db


@kb_bp.route('/items', methods=['GET'])
@login_required
def list_items():
    """
    获取知识库条目列表。
    可以按 parent_id, namespace 进行过滤。
    """
    parent_id = request.args.get('parent_id', None)
    namespace_str = request.args.get('namespace', 'personal') # 默认为个人空间

    # 如果 parent_id 是 'root' 或 'null'，则视为根目录
    if parent_id in ['root', 'null', None]:
        parent_id = None
    else:
        try:
            parent_id = int(parent_id)
        except (ValueError, TypeError):
            return jsonify({"error": "无效的 parent_id"}), 400

    try:
        namespace = KBNamespaceEnum(namespace_str)
    except ValueError:
        return jsonify({"error": "无效的 namespace"}), 400

    query = KnowledgeBaseItem.query.filter_by(parent_id=parent_id)

    if namespace == KBNamespaceEnum.PERSONAL:
        # 用户只能看到自己的个人空间内容，或者管理员可以看到所有个人空间内容
        if current_user.can('manage_knowledge_base'):
            # 管理员可以查看所有个人空间内容
            query = query.filter_by(namespace=KBNamespaceEnum.PERSONAL)
        else:
            # 普通用户只能查看自己的个人空间内容
            query = query.filter_by(namespace=KBNamespaceEnum.PERSONAL, owner_id=current_user.id)
    else: # KBNamespaceEnum.PUBLIC
        # 所有用户都可以看到公共空间内容
        query = query.filter_by(namespace=KBNamespaceEnum.PUBLIC)

    items = query.order_by(KnowledgeBaseItem.item_type, KnowledgeBaseItem.name).all()

    # 序列化结果
    result = [{
        'id': item.id,
        'name': item.name,
        'item_type': item.item_type.value,
        'namespace': item.namespace.value,
        'parent_id': item.parent_id,
        'owner_id': item.owner_id,
        'created_at': item.created_at.isoformat(),
        'updated_at': item.updated_at.isoformat(),
    } for item in items]

    return jsonify(result)


@kb_bp.route('/items', methods=['POST'])
@login_required
def create_item():
    """
    创建一个新的知识库条目 (文件夹, Markdown, 思维导图).
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    # --- 参数校验 ---
    name = data.get('name')
    item_type_str = data.get('item_type')
    parent_id = data.get('parent_id')
    namespace_str = data.get('namespace', 'personal')

    if not name or not item_type_str:
        return jsonify({"error": "缺少必要参数: name, item_type"}), 400

    try:
        item_type = KBItemTypeEnum(item_type_str)
        namespace = KBNamespaceEnum(namespace_str)
    except ValueError:
        return jsonify({"error": "无效的 item_type 或 namespace"}), 400

    # --- 权限和父文件夹校验 ---
    parent_item = None
    if parent_id:
        parent_item = KnowledgeBaseItem.query.get(parent_id)
        if not parent_item or parent_item.item_type != KBItemTypeEnum.FOLDER:
            return jsonify({"error": "父文件夹不存在或类型错误"}), 404
        # 检查用户是否有权在父文件夹下创建
        if parent_item.owner_id != current_user.id and parent_item.namespace != KBNamespaceEnum.PUBLIC:
             return jsonify({"error": "权限不足，无法在此位置创建"}), 403

    # 用户只能在自己的个人空间创建
    if namespace == KBNamespaceEnum.PERSONAL and (parent_item and parent_item.owner_id != current_user.id):
        return jsonify({"error": "无法在其他用户的个人空间创建文件"}), 403
    
    # (未来) 在此添加公共空间创建权限检查

    # --- 创建条目 ---
    try:
        # 检查同名文件
        if KnowledgeBaseItem.query.filter_by(parent_id=parent_id, name=name).first():
            return jsonify({"error": "同目录下已存在同名条目"}), 409

        new_item = KnowledgeBaseItem(
            name=name,
            item_type=item_type,
            parent_id=parent_id,
            owner_id=current_user.id,
            namespace=namespace
        )
        db.session.add(new_item)

        # 如果是文档或思维导图，创建关联内容
        if item_type == KBItemTypeEnum.MARKDOWN:
            md_doc = MarkdownDocument(kb_item=new_item, content=f'# {name}\n')
            db.session.add(md_doc)
        elif item_type == KBItemTypeEnum.MINDMAP:
            # 创建一个包含根节点的默认思维导图
            default_mindmap_data = {
                'nodes': [{'id': 'root', 'label': name, 'x': 0, 'y': 0}],
                'edges': []
            }
            mindmap = MindMap(kb_item=new_item, data=default_mindmap_data)
            db.session.add(mindmap)

        db.session.commit()

        # 返回新创建的条目信息
        return jsonify({
            'id': new_item.id,
            'name': new_item.name,
            'item_type': new_item.item_type.value,
            'parent_id': new_item.parent_id,
            'owner_id': new_item.owner_id,
            'created_at': new_item.created_at.isoformat(),
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建知识库条目失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/items/<int:item_id>', methods=['PUT'])
@login_required
def update_item(item_id):
    """
    更新一个知识库条目 (重命名, 更新内容等).
    """
    item = KnowledgeBaseItem.query.get_or_404(item_id)
    data = request.get_json()
    if not data:
        return jsonify({"error": "请求必须是JSON格式"}), 415

    # 权限检查：只有所有者或有管理权限的用户才能修改
    if item.owner_id != current_user.id and not current_user.can('manage_knowledge_base'):
        return jsonify({"error": "权限不足"}), 403

    try:
        # 1. 更新名称
        if 'name' in data:
            new_name = data['name']
            # 检查在同一目录下是否存在同名条目
            name_exists = KnowledgeBaseItem.query.filter(
                KnowledgeBaseItem.parent_id == item.parent_id,
                KnowledgeBaseItem.name == new_name,
                KnowledgeBaseItem.id != item_id
            ).first()
            if name_exists:
                return jsonify({"error": "同目录下已存在同名条目"}), 409
            item.name = new_name

        # 2. 更新Markdown内容
        if 'content' in data and item.item_type == KBItemTypeEnum.MARKDOWN:
            if item.markdown_document:
                item.markdown_document.content = data['content']
            else:
                # 如果没有关联的文档，就创建一个
                md_doc = MarkdownDocument(kb_item=item, content=data['content'])
                db.session.add(md_doc)

        # 3. 更新思维导图数据
        if 'data' in data and item.item_type == KBItemTypeEnum.MINDMAP:
            mindmap_data = data['data']
            current_app.logger.info(f"更新思维导图数据，条目ID: {item_id}, 数据类型: {type(mindmap_data)}")
            
            if item.mindmap:
                item.mindmap.data = mindmap_data
            else:
                mindmap = MindMap(kb_item=item, data=mindmap_data)
                db.session.add(mindmap)

        item.updated_at = datetime.now()
        db.session.commit()

        return jsonify({"message": "更新成功"}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新知识库条目失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/items/<int:item_id>', methods=['DELETE'])
@login_required
def delete_item(item_id):
    """
    删除一个知识库条目。
    """
    item = KnowledgeBaseItem.query.get_or_404(item_id)

    # 权限检查：只有所有者或有管理权限的用户才能删除
    if item.owner_id != current_user.id and not current_user.can('manage_knowledge_base'):
        return jsonify({"error": "权限不足"}), 403

    try:
        # 如果是文件夹，检查是否为空
        if item.item_type == KBItemTypeEnum.FOLDER:
            if item.children.first():
                return jsonify({"error": "文件夹不为空，无法删除"}), 400

        # 删除条目 (由于设置了cascade, 关联的md/mindmap也会被删除)
        db.session.delete(item)
        db.session.commit()

        return jsonify({"message": "删除成功"}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除知识库条目失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """
    上传文件到知识库并创建一个FILE类型的条目。
    """
    if 'file' not in request.files:
        return jsonify({"error": "没有文件部分"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "没有选择文件"}), 400

    parent_id = request.form.get('parent_id', None)
    namespace_str = request.form.get('namespace', 'personal')

    # --- 校验逻辑 (与 create_item 类似) ---
    parent_item = None
    if parent_id and parent_id != 'null':
        parent_item = KnowledgeBaseItem.query.get(int(parent_id))
        if not parent_item or parent_item.item_type != KBItemTypeEnum.FOLDER:
            return jsonify({"error": "父文件夹不存在或类型错误"}), 404
        if parent_item.owner_id != current_user.id:
            return jsonify({"error": "权限不足"}), 403
        parent_id = parent_item.id
    else:
        parent_id = None

    try:
        namespace = KBNamespaceEnum(namespace_str)
    except ValueError:
        return jsonify({"error": "无效的 namespace"}), 400

    if file:
        from werkzeug.utils import secure_filename
        import os
        from flask import current_app

        filename = secure_filename(file.filename)
        # 检查同名文件
        if KnowledgeBaseItem.query.filter_by(parent_id=parent_id, name=filename).first():
            return jsonify({"error": "同目录下已存在同名条目"}), 409

        # 保存文件
        upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'kb_files')
        os.makedirs(upload_folder, exist_ok=True)
        file_path = os.path.join(upload_folder, filename)
        file.save(file_path)

        # 创建 ProjectFile 记录
        project_file = ProjectFile(
            original_name=file.filename,
            file_name=filename,
            file_path=file_path,
            file_type=file.mimetype,
            upload_user_id=current_user.id
        )
        db.session.add(project_file)
        db.session.flush() # 获取 project_file.id

        # 创建知识库条目
        kb_item = KnowledgeBaseItem(
            name=filename,
            item_type=KBItemTypeEnum.FILE,
            parent_id=parent_id,
            owner_id=current_user.id,
            namespace=namespace,
            project_file_id=project_file.id
        )
        db.session.add(kb_item)
        db.session.commit()

        return jsonify({
            'message': '文件上传成功',
            'item': {
                'id': kb_item.id,
                'name': kb_item.name,
                'item_type': kb_item.item_type.value
            }
        }), 201

    return jsonify({"error": "文件上传失败"}), 400


@kb_bp.route('/items/<int:item_id>', methods=['GET'])
@login_required
def get_item_details(item_id):
    """
    获取单个知识库条目的详细信息，包括其内容。
    """
    item = KnowledgeBaseItem.query.get_or_404(item_id)

    # 权限检查：所有者、公共空间的条目或有管理权限的用户可查看
    if item.namespace == KBNamespaceEnum.PERSONAL:
        # 个人空间条目：只有所有者或有管理权限的用户可以查看
        if item.owner_id != current_user.id and not current_user.can('manage_knowledge_base'):
            return jsonify({"error": "权限不足"}), 403
    # 公共空间条目所有人都可以查看，无需额外检查

    # 基础信息
    response_data = {
        'id': item.id,
        'name': item.name,
        'item_type': item.item_type.value,
        'namespace': item.namespace.value,
        'parent_id': item.parent_id,
        'owner_id': item.owner_id,
        'created_at': item.created_at.isoformat(),
        'updated_at': item.updated_at.isoformat(),
        'content': None, # 用于md或mindmap
        'file_info': None, # 用于文件
        # 权限信息
        'can_edit': (item.owner_id == current_user.id or 
                     current_user.can('manage_knowledge_base')),
        'can_delete': (item.owner_id == current_user.id or 
                       current_user.can('manage_knowledge_base'))
    }

    # 添加特定类型的内容
    if item.item_type == KBItemTypeEnum.MARKDOWN and item.markdown_document:
        response_data['content'] = item.markdown_document.content
    elif item.item_type == KBItemTypeEnum.MINDMAP and item.mindmap:
        mindmap_data = item.mindmap.data
        if isinstance(mindmap_data, dict) and 'nodeExtraData' in mindmap_data:
            node_extra_data = mindmap_data.get('nodeExtraData', {})
            # 收集所有需要查询的关联条目ID
            all_linked_item_ids = set()
            for node_id, extra in node_extra_data.items():
                for f in extra.get('attachedFiles', []):
                    all_linked_item_ids.add(f.get('id'))
                for f in extra.get('attachedFolders', []):
                    all_linked_item_ids.add(f.get('id'))
            
            # 一次性查询所有关联条目的信息
            if all_linked_item_ids:
                linked_items = KnowledgeBaseItem.query.filter(KnowledgeBaseItem.id.in_(all_linked_item_ids)).all()
                linked_items_map = {item.id: item for item in linked_items}
                
                # "充实" nodeExtraData
                for node_id, extra in node_extra_data.items():
                    # 充实文件
                    if 'attachedFiles' in extra:
                        enriched_files = []
                        for f in extra['attachedFiles']:
                            linked_item = linked_items_map.get(f.get('id'))
                            if linked_item:
                                enriched_files.append({
                                    'id': linked_item.id,
                                    'name': linked_item.name,
                                    'item_type': linked_item.item_type.value
                                })
                        extra['attachedFiles'] = enriched_files
                    
                    # 充实文件夹
                    if 'attachedFolders' in extra:
                        enriched_folders = []
                        for f in extra['attachedFolders']:
                            linked_item = linked_items_map.get(f.get('id'))
                            if linked_item:
                                enriched_folders.append({
                                    'id': linked_item.id,
                                    'name': linked_item.name,
                                    'item_type': linked_item.item_type.value
                                })
                        extra['attachedFolders'] = enriched_folders

        response_data['content'] = mindmap_data
    elif item.item_type == KBItemTypeEnum.FILE and item.project_file:
        response_data['file_info'] = {
            'id': item.project_file.id,
            'original_name': item.project_file.original_name,
            'file_type': item.project_file.file_type,
            'upload_date': item.project_file.upload_date.isoformat()
        }

    return jsonify(response_data)


@kb_bp.route('/preview/<int:project_file_id>', methods=['GET'])
@login_required
def preview_kb_file(project_file_id):
    """
    为知识库中的文件生成预览。
    """
    from ..models import ProjectFile
    from ..utils.preview import generate_file_preview

    # 1. 查找文件
    project_file = ProjectFile.query.get_or_404(project_file_id)

    # 2. 权限检查
    #    文件必须关联到一个KB Item，并且用户有权访问该Item
    if not project_file.kb_item:
        return jsonify({"error": "此文件未关联到知识库"}), 404
    
    kb_item = project_file.kb_item[0] # backref is a list
    
    # 检查权限：所有者、公共空间文件或有管理权限的用户都可以访问
    if kb_item.namespace == KBNamespaceEnum.PERSONAL:
        # 个人空间文件：只有所有者或有管理权限的用户可以访问
        if kb_item.owner_id != current_user.id and not current_user.can('manage_knowledge_base'):
            return jsonify({"error": "权限不足"}), 403
    # 公共空间文件所有人都可以访问，无需额外检查

    # 3. 生成预览
    return generate_file_preview(project_file.file_path)


@kb_bp.route('/search', methods=['GET'])
@login_required
def search_items():
    """
    搜索知识库条目。
    """
    query_text = request.args.get('q', '').strip()
    namespace_str = request.args.get('namespace', 'personal')
    
    if not query_text:
        return jsonify({"error": "搜索关键词不能为空"}), 400
    
    try:
        namespace = KBNamespaceEnum(namespace_str)
    except ValueError:
        return jsonify({"error": "无效的 namespace"}), 400
    
    # 基础查询
    query = KnowledgeBaseItem.query.filter(
        KnowledgeBaseItem.name.contains(query_text)
    )
    
    # 权限过滤
    if namespace == KBNamespaceEnum.PERSONAL:
        if current_user.can('manage_knowledge_base'):
            query = query.filter_by(namespace=KBNamespaceEnum.PERSONAL)
        else:
            query = query.filter_by(namespace=KBNamespaceEnum.PERSONAL, owner_id=current_user.id)
    else:
        query = query.filter_by(namespace=KBNamespaceEnum.PUBLIC)
    
    items = query.order_by(KnowledgeBaseItem.updated_at.desc()).limit(50).all()
    
    # 序列化结果
    result = [{
        'id': item.id,
        'name': item.name,
        'item_type': item.item_type.value,
        'namespace': item.namespace.value,
        'parent_id': item.parent_id,
        'owner_id': item.owner_id,
        'created_at': item.created_at.isoformat(),
        'updated_at': item.updated_at.isoformat(),
        'path': _get_item_path(item)
    } for item in items]
    
    return jsonify(result)


@kb_bp.route('/items/<int:item_id>/move', methods=['POST'])
@login_required
def move_item(item_id):
    """
    移动知识库条目到新的父文件夹。
    """
    item = KnowledgeBaseItem.query.get_or_404(item_id)
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "请求必须是JSON格式"}), 415
    
    # 权限检查：只有所有者或有管理权限的用户才能移动
    if item.owner_id != current_user.id and not current_user.can('manage_knowledge_base'):
        return jsonify({"error": "权限不足"}), 403
    
    new_parent_id = data.get('parent_id')
    
    # 验证新父文件夹
    if new_parent_id:
        new_parent = KnowledgeBaseItem.query.get(new_parent_id)
        if not new_parent or new_parent.item_type != KBItemTypeEnum.FOLDER:
            return jsonify({"error": "目标文件夹不存在或类型错误"}), 404
        if new_parent.owner_id != current_user.id:
            return jsonify({"error": "无权移动到目标文件夹"}), 403
    else:
        new_parent_id = None
    
    # 防止循环引用（如果移动的是文件夹）
    if item.item_type == KBItemTypeEnum.FOLDER and new_parent_id:
        if _is_ancestor_or_self(item.id, new_parent_id):
            return jsonify({"error": "不能移动文件夹到其子文件夹"}), 400
    
    # 检查目标位置是否已有同名文件
    existing = KnowledgeBaseItem.query.filter_by(
        parent_id=new_parent_id,
        name=item.name
    ).filter(KnowledgeBaseItem.id != item_id).first()
    
    if existing:
        return jsonify({"error": "目标位置已存在同名条目"}), 409
    
    try:
        item.parent_id = new_parent_id
        item.updated_at = datetime.now()
        db.session.commit()
        
        return jsonify({"message": "移动成功"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"移动知识库条目失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/items/<int:item_id>/copy', methods=['POST'])
@login_required
def copy_item(item_id):
    """
    复制知识库条目。
    """
    item = KnowledgeBaseItem.query.get_or_404(item_id)
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "请求必须是JSON格式"}), 415
    
    # 权限检查：只能复制自己的、公开的条目或有管理权限的用户可以复制任何条目
    if item.namespace == KBNamespaceEnum.PERSONAL:
        if item.owner_id != current_user.id and not current_user.can('manage_knowledge_base'):
            return jsonify({"error": "权限不足"}), 403
    
    target_parent_id = data.get('parent_id')
    new_name = data.get('name', f"{item.name}_副本")
    
    # 验证目标父文件夹
    if target_parent_id:
        target_parent = KnowledgeBaseItem.query.get(target_parent_id)
        if not target_parent or target_parent.item_type != KBItemTypeEnum.FOLDER:
            return jsonify({"error": "目标文件夹不存在或类型错误"}), 404
        if target_parent.owner_id != current_user.id:
            return jsonify({"error": "无权复制到目标文件夹"}), 403
    else:
        target_parent_id = None
    
    # 检查目标位置是否已有同名文件
    existing = KnowledgeBaseItem.query.filter_by(
        parent_id=target_parent_id,
        name=new_name
    ).first()
    
    if existing:
        return jsonify({"error": "目标位置已存在同名条目"}), 409
    
    try:
        # 创建新条目
        new_item = KnowledgeBaseItem(
            name=new_name,
            item_type=item.item_type,
            parent_id=target_parent_id,
            owner_id=current_user.id,
            namespace=KBNamespaceEnum.PERSONAL  # 复制的条目总是放在个人空间
        )
        db.session.add(new_item)
        db.session.flush()  # 获取ID
        
        # 复制内容
        if item.item_type == KBItemTypeEnum.MARKDOWN and item.markdown_document:
            new_md = MarkdownDocument(
                kb_item=new_item,
                content=item.markdown_document.content
            )
            db.session.add(new_md)
        elif item.item_type == KBItemTypeEnum.MINDMAP and item.mindmap:
            new_mindmap = MindMap(
                kb_item=new_item,
                data=item.mindmap.data
            )
            db.session.add(new_mindmap)
        
        db.session.commit()
        
        return jsonify({
            'message': '复制成功',
            'item': {
                'id': new_item.id,
                'name': new_item.name,
                'item_type': new_item.item_type.value
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"复制知识库条目失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/batch-upload', methods=['POST'])
@login_required
def batch_upload_files():
    """
    批量上传文件到知识库。
    """
    if 'files' not in request.files:
        return jsonify({"error": "没有文件部分"}), 400
    
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "没有选择文件"}), 400
    
    parent_id = request.form.get('parent_id', None)
    namespace_str = request.form.get('namespace', 'personal')
    
    # 验证父文件夹
    parent_item = None
    if parent_id and parent_id != 'null':
        parent_item = KnowledgeBaseItem.query.get(int(parent_id))
        if not parent_item or parent_item.item_type != KBItemTypeEnum.FOLDER:
            return jsonify({"error": "父文件夹不存在或类型错误"}), 404
        if parent_item.owner_id != current_user.id:
            return jsonify({"error": "权限不足"}), 403
        parent_id = parent_item.id
    else:
        parent_id = None
    
    try:
        namespace = KBNamespaceEnum(namespace_str)
    except ValueError:
        return jsonify({"error": "无效的 namespace"}), 400
    
    from werkzeug.utils import secure_filename
    import os
    
    uploaded_files = []
    errors = []
    
    upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'kb_files')
    os.makedirs(upload_folder, exist_ok=True)
    
    for file in files:
        if file.filename == '':
            continue
            
        try:
            filename = secure_filename(file.filename)
            
            # 检查同名文件
            if KnowledgeBaseItem.query.filter_by(parent_id=parent_id, name=filename).first():
                errors.append(f"{filename}: 同目录下已存在同名条目")
                continue
            
            # 保存文件
            file_path = os.path.join(upload_folder, filename)
            file.save(file_path)
            
            # 创建 ProjectFile 记录
            project_file = ProjectFile(
                original_name=file.filename,
                file_name=filename,
                file_path=file_path,
                file_type=file.mimetype,
                upload_user_id=current_user.id
            )
            db.session.add(project_file)
            db.session.flush()
            
            # 创建知识库条目
            kb_item = KnowledgeBaseItem(
                name=filename,
                item_type=KBItemTypeEnum.FILE,
                parent_id=parent_id,
                owner_id=current_user.id,
                namespace=namespace,
                project_file_id=project_file.id
            )
            db.session.add(kb_item)
            
            uploaded_files.append({
                'id': kb_item.id,
                'name': kb_item.name,
                'item_type': kb_item.item_type.value
            })
            
        except Exception as e:
            current_app.logger.error(f"上传文件 {file.filename} 失败: {e}")
            errors.append(f"{file.filename}: 上传失败")
    
    try:
        db.session.commit()
        
        return jsonify({
            'message': f'成功上传 {len(uploaded_files)} 个文件',
            'uploaded_files': uploaded_files,
            'errors': errors
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"批量上传提交失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/mindmap/<int:mindmap_id>/links', methods=['GET'])
@login_required
def get_mindmap_links(mindmap_id):
    """
    获取思维导图的节点链接。
    """
    from ..models import MindMap, MindMapNodeLink
    
    mindmap = MindMap.query.get_or_404(mindmap_id)
    
    # 权限检查
    if mindmap.kb_item.namespace == KBNamespaceEnum.PERSONAL and mindmap.kb_item.owner_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403
    
    links = MindMapNodeLink.query.filter_by(mindmap_id=mindmap_id).all()
    
    result = [{
        'id': link.id,
        'node_id': link.node_id,
        'linked_kb_item_id': link.linked_kb_item_id,
        'linked_item_name': KnowledgeBaseItem.query.get(link.linked_kb_item_id).name if KnowledgeBaseItem.query.get(link.linked_kb_item_id) else None
    } for link in links]
    
    return jsonify(result)


@kb_bp.route('/mindmap/<int:mindmap_id>/links', methods=['POST'])
@login_required
def create_mindmap_link(mindmap_id):
    """
    创建思维导图节点链接。
    """
    from ..models import MindMap, MindMapNodeLink
    
    mindmap = MindMap.query.get_or_404(mindmap_id)
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "请求必须是JSON格式"}), 415
    
    # 权限检查：只有所有者才能创建链接
    if mindmap.kb_item.owner_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403
    
    node_id = data.get('node_id')
    linked_kb_item_id = data.get('linked_kb_item_id')
    
    if not node_id or not linked_kb_item_id:
        return jsonify({"error": "缺少必要参数: node_id, linked_kb_item_id"}), 400
    
    # 验证被链接的条目
    linked_item = KnowledgeBaseItem.query.get(linked_kb_item_id)
    if not linked_item:
        return jsonify({"error": "被链接的条目不存在"}), 404
    
    # 权限检查：只能链接到自己的或公开的条目
    if linked_item.namespace == KBNamespaceEnum.PERSONAL and linked_item.owner_id != current_user.id:
        return jsonify({"error": "无权链接到该条目"}), 403
    
    # 检查是否已存在该链接
    existing_link = MindMapNodeLink.query.filter_by(
        mindmap_id=mindmap_id,
        node_id=node_id
    ).first()
    
    if existing_link:
        return jsonify({"error": "该节点已存在链接"}), 409
    
    try:
        new_link = MindMapNodeLink(
            mindmap_id=mindmap_id,
            node_id=node_id,
            linked_kb_item_id=linked_kb_item_id
        )
        db.session.add(new_link)
        db.session.commit()
        
        return jsonify({
            'message': '链接创建成功',
            'link': {
                'id': new_link.id,
                'node_id': new_link.node_id,
                'linked_kb_item_id': new_link.linked_kb_item_id,
                'linked_item_name': linked_item.name
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建思维导图链接失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/mindmap/links/<int:link_id>', methods=['DELETE'])
@login_required
def delete_mindmap_link(link_id):
    """
    删除思维导图节点链接。
    """
    from ..models import MindMapNodeLink
    
    link = MindMapNodeLink.query.get_or_404(link_id)
    mindmap = link.mindmap if hasattr(link, 'mindmap') else db.session.query(MindMap).filter_by(id=link.mindmap_id).first()
    
    # 权限检查：只有所有者才能删除链接
    if mindmap.kb_item.owner_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403
    
    try:
        db.session.delete(link)
        db.session.commit()
        
        return jsonify({"message": "链接删除成功"}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除思维导图链接失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/mindmap/<int:mindmap_id>/links/<string:node_id>', methods=['PUT'])
@login_required
def update_mindmap_link(mindmap_id, node_id):
    """
    更新思维导图节点链接。
    """
    from ..models import MindMap, MindMapNodeLink
    
    mindmap = MindMap.query.get_or_404(mindmap_id)
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "请求必须是JSON格式"}), 415
    
    # 权限检查：只有所有者才能更新链接
    if mindmap.kb_item.owner_id != current_user.id:
        return jsonify({"error": "权限不足"}), 403
    
    link = MindMapNodeLink.query.filter_by(
        mindmap_id=mindmap_id,
        node_id=node_id
    ).first()
    
    if not link:
        return jsonify({"error": "链接不存在"}), 404
    
    new_linked_kb_item_id = data.get('linked_kb_item_id')
    
    if not new_linked_kb_item_id:
        return jsonify({"error": "缺少必要参数: linked_kb_item_id"}), 400
    
    # 验证被链接的条目
    linked_item = KnowledgeBaseItem.query.get(new_linked_kb_item_id)
    if not linked_item:
        return jsonify({"error": "被链接的条目不存在"}), 404
    
    # 权限检查：只能链接到自己的或公开的条目
    if linked_item.namespace == KBNamespaceEnum.PERSONAL and linked_item.owner_id != current_user.id:
        return jsonify({"error": "无权链接到该条目"}), 403
    
    try:
        link.linked_kb_item_id = new_linked_kb_item_id
        db.session.commit()
        
        return jsonify({
            'message': '链接更新成功',
            'link': {
                'id': link.id,
                'node_id': link.node_id,
                'linked_kb_item_id': link.linked_kb_item_id,
                'linked_item_name': linked_item.name
            }
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新思维导图链接失败: {e}")
        return jsonify({"error": "服务器内部错误"}), 500


@kb_bp.route('/sync/training-files', methods=['POST'])
@login_required
def sync_training_files():
    """
    将培训模块的文件同步到知识库的"培训"文件夹。
    """
    if not current_user.can('manage_knowledge_base'):
        return jsonify({"error": "权限不足"}), 403
    
    from ..models import Training
    import os
    
    try:
        # 确保"培训"文件夹存在
        training_folder = _ensure_system_folder('培训')
        
        # 获取所有有材料路径的培训
        trainings = Training.query.filter(Training.material_path.isnot(None)).all()
        
        synced_count = 0
        errors = []
        
        for training in trainings:
            if training.material_path and os.path.exists(training.material_path):
                try:
                    filename = os.path.basename(training.material_path)
                    
                    # 检查是否已存在
                    existing = KnowledgeBaseItem.query.filter_by(
                        parent_id=training_folder.id,
                        name=filename
                    ).first()
                    
                    if existing:
                        continue  # 跳过已存在的文件
                    
                    # 创建 ProjectFile 记录
                    project_file = ProjectFile(
                        original_name=filename,
                        file_name=filename,
                        file_path=training.material_path,
                        file_type='application/pdf',  # 假设大部分是PDF
                        upload_user_id=training.trainer_id,
                        is_public=True
                    )
                    db.session.add(project_file)
                    db.session.flush()
                    
                    # 创建知识库条目
                    kb_item = KnowledgeBaseItem(
                        name=f"{training.title} - {filename}",
                        item_type=KBItemTypeEnum.FILE,
                        parent_id=training_folder.id,
                        owner_id=training.trainer_id,
                        namespace=KBNamespaceEnum.PUBLIC,
                        project_file_id=project_file.id
                    )
                    db.session.add(kb_item)
                    synced_count += 1
                    
                except Exception as e:
                    current_app.logger.error(f"同步培训文件 {training.material_path} 失败: {e}")
                    errors.append(f"培训 '{training.title}': 同步失败")
        
        db.session.commit()
        
        return jsonify({
            'message': f'成功同步 {synced_count} 个培训文件',
            'synced_count': synced_count,
            'errors': errors
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"同步培训文件失败: {e}")
        return jsonify({"error": "同步失败"}), 500


@kb_bp.route('/sync/public-files', methods=['POST'])
@login_required
def sync_public_files():
    """
    将文件管理模块的公开文件同步到知识库的"公开文件"文件夹。
    """
    if not current_user.can('manage_knowledge_base'):
        return jsonify({"error": "权限不足"}), 403
    
    try:
        # 确保"公开文件"文件夹存在
        public_folder = _ensure_system_folder('公开文件')
        
        # 获取所有公开的项目文件
        public_files = ProjectFile.query.filter_by(is_public=True).all()
        
        synced_count = 0
        errors = []
        
        for file in public_files:
            if not file.file_path or not os.path.exists(file.file_path):
                continue
                
            try:
                # 检查是否已存在
                existing = KnowledgeBaseItem.query.filter_by(
                    parent_id=public_folder.id,
                    name=file.original_name
                ).first()
                
                if existing:
                    continue  # 跳过已存在的文件
                
                # 创建知识库条目
                kb_item = KnowledgeBaseItem(
                    name=file.original_name,
                    item_type=KBItemTypeEnum.FILE,
                    parent_id=public_folder.id,
                    owner_id=file.upload_user_id or 1,  # 默认管理员
                    namespace=KBNamespaceEnum.PUBLIC,
                    project_file_id=file.id
                )
                db.session.add(kb_item)
                synced_count += 1
                
            except Exception as e:
                current_app.logger.error(f"同步公开文件 {file.file_path} 失败: {e}")
                errors.append(f"文件 '{file.original_name}': 同步失败")
        
        db.session.commit()
        
        return jsonify({
            'message': f'成功同步 {synced_count} 个公开文件',
            'synced_count': synced_count,
            'errors': errors
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"同步公开文件失败: {e}")
        return jsonify({"error": "同步失败"}), 500


@kb_bp.route('/namespaces', methods=['GET'])
@login_required
def get_namespaces():
    """
    获取用户可访问的命名空间列表。
    """
    namespaces = [
        {
            'value': 'personal',
            'label': '个人空间',
            'description': '只有自己可以访问的私人知识库'
        },
        {
            'value': 'public',
            'label': '公共空间',
            'description': '所有用户都可以查看的公共知识库'
        }
    ]
    
    return jsonify(namespaces)


@kb_bp.route('/stats', methods=['GET'])
@login_required
def get_kb_stats():
    """
    获取知识库统计信息。
    """
    from sqlalchemy import func
    
    # 个人空间统计
    personal_stats = db.session.query(
        KnowledgeBaseItem.item_type,
        func.count(KnowledgeBaseItem.id).label('count')
    ).filter_by(
        namespace=KBNamespaceEnum.PERSONAL,
        owner_id=current_user.id
    ).group_by(KnowledgeBaseItem.item_type).all()
    
    # 公共空间统计（如果有权限查看）
    public_stats = db.session.query(
        KnowledgeBaseItem.item_type,
        func.count(KnowledgeBaseItem.id).label('count')
    ).filter_by(
        namespace=KBNamespaceEnum.PUBLIC
    ).group_by(KnowledgeBaseItem.item_type).all()
    
    # 最近更新的条目
    recent_items = KnowledgeBaseItem.query.filter(
        or_(
            and_(
                KnowledgeBaseItem.namespace == KBNamespaceEnum.PERSONAL,
                KnowledgeBaseItem.owner_id == current_user.id
            ),
            KnowledgeBaseItem.namespace == KBNamespaceEnum.PUBLIC
        )
    ).order_by(KnowledgeBaseItem.updated_at.desc()).limit(5).all()
    
    personal_count_by_type = {stat.item_type.value: stat.count for stat in personal_stats}
    public_count_by_type = {stat.item_type.value: stat.count for stat in public_stats}
    
    return jsonify({
        'personal': {
            'total': sum(personal_count_by_type.values()),
            'by_type': personal_count_by_type
        },
        'public': {
            'total': sum(public_count_by_type.values()),
            'by_type': public_count_by_type
        },
        'recent_items': [{
            'id': item.id,
            'name': item.name,
            'item_type': item.item_type.value,
            'namespace': item.namespace.value,
            'updated_at': item.updated_at.isoformat()
        } for item in recent_items]
    })


@kb_bp.route('/admin/init-permissions', methods=['POST'])
@login_required
def init_permissions():
    """
    初始化知识库权限（仅管理员可用）。
    """
    if not current_user.can('manage_knowledge_base') and current_user.role != RoleEnum.SUPER:
        return jsonify({"error": "权限不足"}), 403
    
    try:
        from .init_permissions import init_knowledge_base_permissions, init_default_kb_structure
        
        # 初始化权限
        init_knowledge_base_permissions()
        
        # 初始化默认结构
        init_default_kb_structure()
        
        return jsonify({
            'message': '知识库权限和默认结构初始化成功',
            'details': {
                'permissions_initialized': True,
                'default_structure_created': True
            }
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"初始化知识库失败: {e}")
        return jsonify({"error": "初始化失败"}), 500


def _ensure_system_folder(folder_name):
    """
    确保系统文件夹存在，如果不存在则创建。
    """
    # 查找管理员用户（假设ID为1或SUPER角色的第一个用户）
    from ..models import RoleEnum
    admin_user = User.query.filter_by(role=RoleEnum.SUPER).first()
    if not admin_user:
        admin_user = User.query.first()  # 如果没有SUPER用户，使用第一个用户
    
    folder = KnowledgeBaseItem.query.filter_by(
        name=folder_name,
        item_type=KBItemTypeEnum.FOLDER,
        namespace=KBNamespaceEnum.PUBLIC,
        parent_id=None
    ).first()
    
    if not folder:
        folder = KnowledgeBaseItem(
            name=folder_name,
            item_type=KBItemTypeEnum.FOLDER,
            namespace=KBNamespaceEnum.PUBLIC,
            owner_id=admin_user.id,
            parent_id=None
        )
        db.session.add(folder)
        db.session.flush()
    
    return folder


def _get_item_path(item):
    """获取条目的完整路径"""
    path_parts = []
    current = item
    while current:
        path_parts.append(current.name)
        current = current.parent
    return ' / '.join(reversed(path_parts))


def _is_ancestor_or_self(folder_id, target_id):
    """检查target_id是否是folder_id的祖先或自身"""
    if folder_id == target_id:
        return True
    
    target = KnowledgeBaseItem.query.get(target_id)
    while target and target.parent_id:
        if target.parent_id == folder_id:
            return True
        target = target.parent
    return False
