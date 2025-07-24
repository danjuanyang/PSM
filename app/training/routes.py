# /app/training/routes.py
import os

from flask import request, jsonify, current_app, send_from_directory, send_file, g
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from .. import db
from ..decorators import permission_required, log_activity
from ..models import Training, Comment, Reply
from . import training_bp
from ..utils.preview import generate_file_preview


# 1. 获取所有培训
@training_bp.route('', methods=['GET'])
@login_required
def get_trainings():
    trainings = Training.query.order_by(Training.training_month.desc()).all()
    return jsonify([{
        'id': t.id,
        'title': t.title,
        'description': t.description,  # 添加 description
        'training_month': t.training_month,
        'trainer': t.trainer.username if t.trainer else None,
        'status': t.status,
        'assignee_id': t.assignee_id,
        'assignee_name': t.assignee.username if t.assignee else None,
        'file_path': t.material_path,
        'file_name': os.path.basename(t.material_path) if t.material_path else None,
    } for t in trainings])


# 2. 创建新培训
@training_bp.route('/', methods=['POST'])
@login_required
@log_activity('创建培训', '{username}创建培训')
@permission_required('training_manage')
def create_training():
    g.log_info = {'useername': current_user}
    data = request.get_json()
    if not all([data.get('training_month'), data.get('assignee_id'), data.get('title')]):
        return jsonify({'message': '缺少必要的字段（月份、标题、分配用户）。'}), 400

    if Training.query.filter_by(training_month=data['training_month']).first():
        return jsonify({'message': '本月已经分配了培训。'}), 400

    new_training = Training(
        title=data['title'],
        description=data.get('description'),  # 添加 description
        training_month=data['training_month'],
        assignee_id=data['assignee_id'],
        trainer_id=current_user.id,
        status='pending'
    )
    db.session.add(new_training)
    db.session.commit()
    return jsonify({'message': '培训创建成功。', 'id': new_training.id}), 201


# 3. 获取特定培训的详细信息
@training_bp.route('/<int:id>', methods=['GET'])
@login_required
def get_training_details(id):
    training = Training.query.options(
        joinedload(Training.comments).joinedload(Comment.user),
        joinedload(Training.comments).joinedload(Comment.replies).joinedload(Reply.user)
    ).get_or_404(id)

    def serialize_reply(reply):
        return {
            'id': reply.id,
            'content': reply.content,
            'create_time': reply.create_time.isoformat(),
            'user': {
                'id': reply.user.id,
                'username': reply.user.username
            } if reply.user else None
        }

    def serialize_comment(comment):
        return {
            'id': comment.id,
            'content': comment.content,
            'create_time': comment.create_time.isoformat(),
            'user': {
                'id': comment.user.id,
                'username': comment.user.username
            } if comment.user else None,
            'replies': [serialize_reply(r) for r in comment.replies]
        }

    return jsonify({
        'id': training.id,
        'title': training.title,
        'description': training.description,
        'training_month': training.training_month,
        'trainer': training.trainer.username if training.trainer else None,
        'assignee_id': training.assignee_id,
        'assignee_name': training.assignee.username if training.assignee else None,
        'status': training.status,
        'file_path': training.material_path,
        'file_name': os.path.basename(training.material_path) if training.material_path else None,
        'comments': [serialize_comment(c) for c in training.comments]
    })


# 4. 更新培训
@training_bp.route('/<int:id>', methods=['PUT'])
@login_required
@log_activity('更新培训', '{username}更新了培训')
@permission_required('training_manage')
def update_training(id):
    g.log_info = {'useername': current_user}
    training = Training.query.get_or_404(id)
    data = request.get_json()
    training.title = data.get('title', training.title)
    training.description = data.get('description', training.description)  # 添加 description
    training.assignee_id = data.get('assignee_id', training.assignee_id)
    db.session.commit()
    return jsonify({'message': '训练已成功更新。'})


# 5. 删除培训
@training_bp.route('/<int:id>', methods=['DELETE'])
@login_required
@permission_required('training_manage')
def delete_training(id):
    training = Training.query.get_or_404(id)
    if training.material_path and os.path.exists(training.material_path):
        try:
            os.remove(training.material_path)
        except OSError as e:
            current_app.logger.error(f"删除文件时出错 {training.material_path}: {e}")

    db.session.delete(training)
    db.session.commit()
    return jsonify({'message': '此培训已成功删除。'}), 200


# 新增：允许被分配者更新描述
@training_bp.route('/<int:id>/description', methods=['PUT'])
@login_required
def update_training_description(id):
    training = Training.query.get_or_404(id)
    if training.assignee_id != current_user.id:
        return jsonify({'message': '您无权修改此培训的描述。'}), 403

    data = request.get_json()
    training.description = data.get('description')
    db.session.commit()
    return jsonify({'message': '描述更新成功。'})


# 6. 预览培训材料
@training_bp.route('/<int:id>/preview', methods=['GET'])
@login_required
def preview_material(id):
    training = Training.query.get_or_404(id)
    if not training.material_path or not os.path.exists(training.material_path):
        return jsonify({'message': '没有可用的材料或文件不存在。'}), 404

    # 统一使用通用的预览/发送函数
    return generate_file_preview(training.material_path)


# 下载
@training_bp.route('/<int:id>/download', methods=['GET'])
@login_required
def download_material(id):
    training = Training.query.get_or_404(id)
    if not training.material_path or not os.path.exists(training.material_path):
        return jsonify({'message': '没有可用的材料或文件不存在。'}), 404

    # 统一使用通用的预览/发送函数
    return send_file(training.material_path, as_attachment=True)


# 7. 添加评论
@training_bp.route('/<int:id>/comments', methods=['POST'])
@login_required
def add_comment(id):
    training = Training.query.get_or_404(id)
    data = request.get_json()
    comment = Comment(
        content=data['content'],
        user_id=current_user.id,
        training_id=training.id
    )
    db.session.add(comment)
    db.session.commit()
    return jsonify({'message': '添加了评论', 'comment_id': comment.id}), 201


# 8. 回复评论
@training_bp.route('/comments/<int:comment_id>/replies', methods=['POST'])
@login_required
def add_reply(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    data = request.get_json()
    reply = Reply(
        content=data['content'],
        user_id=current_user.id,
        comment_id=comment.id,
        parent_id=data.get('parent_id')
    )
    db.session.add(reply)
    db.session.commit()
    return jsonify({'message': '回复已添加', 'reply_id': reply.id}), 201


# 9. 删除评论或回复
@training_bp.route('/comments/<int:comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if comment.user_id != current_user.id and not current_user.can('training_manage'):
        return jsonify({'message': '无权删除此评论。'}), 403
    db.session.delete(comment)
    db.session.commit()
    return jsonify({'message': '评论已删除。'})


@training_bp.route('/replies/<int:reply_id>', methods=['DELETE'])
@login_required
def delete_reply(reply_id):
    reply = Reply.query.get_or_404(reply_id)
    if reply.user_id != current_user.id and not current_user.can('training_manage'):
        return jsonify({'message': '无权删除此回复.'}), 403
    db.session.delete(reply)
    db.session.commit()
    return jsonify({'message': '回复已删除。'})
