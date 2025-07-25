# PSM/app/ai/routes.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, desc
from openai import OpenAI  # 导入 OpenAI

from . import ai_bp
from .. import db
from ..decorators import permission_required
from ..models import User, AIApi, AIConversation, AIMessage, SystemConfig, RoleEnum


def get_api_key():
    """获取当前用户可用的API Key。优先使用用户自己的Key，其次是系统Key。"""
    user_key = AIApi.query.filter_by(user_id=current_user.id).first()
    if user_key and user_key.api_key:
        return user_key.api_key

    system_key = SystemConfig.query.filter_by(key='DEEPSEEK_API_KEY').first()
    if system_key and system_key.value:
        return system_key.value

    return None


# ------------------- Admin Routes -------------------

@ai_bp.route('/admin/system_key', methods=['POST'])
@login_required
def set_system_api_key():
    """管理员设置系统全局的DeepSeek API Key。"""
    data = request.get_json()
    api_key = data.get('api_key')
    if not api_key:
        return jsonify({"error": "需要 API 密钥"}), 400

    config = SystemConfig.query.filter_by(key='DEEPSEEK_API_KEY').first()
    if config:
        config.value = api_key
    else:
        config = SystemConfig(key='DEEPSEEK_API_KEY', value=api_key, description='System-wide DeepSeek API Key')
        db.session.add(config)

    db.session.commit()
    return jsonify({"message": "系统 API 密钥设置成功。"}), 200


@ai_bp.route('/admin/system_key', methods=['GET'])
@login_required
@permission_required('view_ai_setting')
def get_system_api_key():
    """管理员获取系统全局的DeepSeek API Key。"""
    config = SystemConfig.query.filter_by(key='DEEPSEEK_API_KEY').first()
    if config and config.value:
        return jsonify({"api_key": config.value}), 200
    return jsonify({"api_key": None}), 404


@ai_bp.route('/admin/usage', methods=['GET'])
@login_required
@permission_required('view_ai_setting')
def get_all_usage_stats():
    """管理员查看所有用户的使用情况和Token统计。"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)

    # 统计每个用户的总Token和消息数
    user_stats = db.session.query(
        User.id.label('user_id'),
        User.username.label('username'),
        func.sum(AIMessage.total_tokens).label('total_tokens'),
        func.count(AIMessage.id).label('message_count')
    ).join(AIConversation, User.id == AIConversation.user_id) \
        .join(AIMessage, AIConversation.id == AIMessage.conversation_id) \
        .group_by(User.id, User.username) \
        .order_by(desc('total_tokens')) \
        .paginate(page=page, per_page=per_page, error_out=False)

    results = {
        "total": user_stats.total,
        "pages": user_stats.pages,
        "current_page": user_stats.page,
        "per_page": user_stats.per_page,
        "users": [
            {
                "user_id": stat.user_id,
                "username": stat.username,
                "total_tokens": int(stat.total_tokens or 0),
                "message_count": stat.message_count
            } for stat in user_stats.items
        ]
    }

    return jsonify(results), 200


# ------------------- User Routes -------------------

@ai_bp.route('/apikey', methods=['POST'])
@login_required
def set_user_api_key():
    """用户设置自己的DeepSeek API Key。"""
    data = request.get_json()
    api_key = data.get('api_key')
    if not api_key:
        return jsonify({"error": "需要 API 密钥"}), 400

    user_api = AIApi.query.filter_by(user_id=current_user.id).first()
    if user_api:
        user_api.api_key = api_key
    else:
        user_api = AIApi(user_id=current_user.id, api_key=api_key)
        db.session.add(user_api)

    db.session.commit()
    return jsonify({"message": "API 密钥设置成功。"}), 200


@ai_bp.route('/apikey', methods=['GET'])
@login_required
def get_user_api_key():
    """用户获取自己的DeepSeek API Key。"""
    user_api = AIApi.query.filter_by(user_id=current_user.id).first()
    if user_api and user_api.api_key:
        return jsonify({"api_key": user_api.api_key}), 200
    return jsonify({"api_key": None}), 404


@ai_bp.route('/conversations', methods=['POST'])
@login_required
def create_conversation():
    """创建新的AI对话。"""
    data = request.get_json()
    title = data.get('title', 'New Conversation')

    conversation = AIConversation(user_id=current_user.id, title=title)
    db.session.add(conversation)
    db.session.commit()

    return jsonify({
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat()
    }), 201


@ai_bp.route('/conversations', methods=['GET'])
@login_required
def get_conversations():
    """获取当前用户的对话列表。"""
    conversations = AIConversation.query.filter_by(user_id=current_user.id) \
        .order_by(AIConversation.updated_at.desc()).all()

    return jsonify([
        {
            "id": conv.id,
            "title": conv.title,
            "created_at": conv.created_at.isoformat(),
            "updated_at": conv.updated_at.isoformat()
        } for conv in conversations
    ]), 200


@ai_bp.route('/conversations/<int:conv_id>', methods=['DELETE'])
@login_required
def delete_conversation(conv_id):
    """删除一个对话及其所有消息。"""
    conversation = AIConversation.query.filter_by(id=conv_id, user_id=current_user.id).first_or_404()

    db.session.delete(conversation)
    db.session.commit()

    return jsonify({"message": "Conversation deleted successfully."}), 200


@ai_bp.route('/conversations/<int:conv_id>/messages', methods=['GET'])
@login_required
def get_messages_in_conversation(conv_id):
    """获取指定对话的所有消息。"""
    conversation = AIConversation.query.filter_by(id=conv_id, user_id=current_user.id).first_or_404()
    messages = AIMessage.query.filter_by(conversation_id=conversation.id).order_by(AIMessage.created_at).all()

    return jsonify([
        {
            "id": msg.id,
            "content": msg.content,
            "role": msg.role,
            "created_at": msg.created_at.isoformat(),
            "total_tokens": msg.total_tokens
        } for msg in messages
    ]), 200



@ai_bp.route('/conversations/<int:conv_id>/chat', methods=['POST'])
@login_required
def chat(conv_id):
    """在指定对话中与AI聊天。"""
    conversation = AIConversation.query.filter_by(id=conv_id, user_id=current_user.id).first_or_404()

    data = request.get_json()
    user_message_content = data.get('message')
    if not user_message_content:
        return jsonify({"error": "消息内容为必填项"}), 400

    api_key = get_api_key()
    if not api_key:
        return jsonify({"error": "未配置 API 密钥。请联系管理员或自行设置。"}), 400

    # 先构建上下文，但不立即保存用户消息
    messages_history = AIMessage.query.filter_by(conversation_id=conversation.id).order_by(AIMessage.created_at).all()
    history_for_api = [{"role": msg.role, "content": msg.content} for msg in messages_history]
    # 将当前用户消息添加到待发送列表
    history_for_api.append({"role": "user", "content": user_message_content})

    try:
        # 使用OpenAI库与DeepSeek API交互
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
        
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=history_for_api
        )

        ai_response_content = response.choices[0].message.content
        usage = response.usage

        # 在API调用成功后，一次性保存用户消息和AI回复
        user_message = AIMessage(
            conversation_id=conversation.id,
            content=user_message_content,
            role='user'
        )
        db.session.add(user_message)

        ai_message = AIMessage(
            conversation_id=conversation.id,
            content=ai_response_content,
            role='assistant',
            model_version=response.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens
        )
        db.session.add(ai_message)
        
        # 更新对话的最后更新时间
        conversation.updated_at = db.func.now()
        
        db.session.commit()

        return jsonify({
            "reply": ai_response_content,
            "tokens_used": usage.total_tokens
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error during AI chat completion: {e}")
        return jsonify({"error": f"无法与 AI 服务通信： {str(e)}"}), 500
