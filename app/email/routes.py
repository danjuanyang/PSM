# PSM/app/email/routes.py
from flask import request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, time
import json
import re
import logging
from . import email_bp
from .. import db
from ..models import (
    EmailConfig, EmailTemplate, EmailTask, EmailRecipientGroup, EmailLog,
    EmailTemplateTypeEnum, EmailTaskFrequencyEnum, EmailStatusEnum,
    RoleEnum
)
from ..decorators import role_required
from .service import EmailService, EmailEncryption
from .scheduler import email_scheduler

logger = logging.getLogger(__name__)


# ============= 邮件配置管理 =============

@email_bp.route('/configs', methods=['GET'])
@login_required
@role_required(RoleEnum.ADMIN)
def get_email_configs():
    """获取邮件配置列表"""
    configs = EmailConfig.query.all()
    return jsonify({
        'configs': [
            {
                'id': c.id,
                'name': c.name,
                'smtp_host': c.smtp_host,
                'smtp_port': c.smtp_port,
                'smtp_use_tls': c.smtp_use_tls,
                'smtp_use_ssl': c.smtp_use_ssl,
                'sender_email': c.sender_email,
                'sender_name': c.sender_name,
                'username': c.username,
                'is_active': c.is_active,
                'is_default': c.is_default,
                'created_at': c.created_at.isoformat() if c.created_at else None
            }
            for c in configs
        ]
    })


@email_bp.route('/configs', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def create_email_config():
    """创建邮件配置"""
    data = request.get_json()
    
    # 验证必填字段
    required_fields = ['name', 'smtp_host', 'sender_email', 'username', 'password']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400
    
    # 加密密码
    encryption = EmailEncryption()
    encrypted_password = encryption.encrypt_password(data['password'])
    
    # 如果设置为默认，取消其他配置的默认状态
    if data.get('is_default'):
        EmailConfig.query.update({'is_default': False})
    
    config = EmailConfig(
        name=data['name'],
        smtp_host=data['smtp_host'],
        smtp_port=data.get('smtp_port', 587),
        smtp_use_tls=data.get('smtp_use_tls', True),
        smtp_use_ssl=data.get('smtp_use_ssl', False),
        sender_email=data['sender_email'],
        sender_name=data.get('sender_name'),
        username=data['username'],
        password=encrypted_password,
        is_active=data.get('is_active', True),
        is_default=data.get('is_default', False)
    )
    
    db.session.add(config)
    db.session.commit()
    
    return jsonify({
        'message': 'Email config created successfully',
        'config_id': config.id
    })


@email_bp.route('/configs/<int:config_id>', methods=['PUT'])
@login_required
@role_required(RoleEnum.ADMIN)
def update_email_config(config_id):
    """更新邮件配置"""
    config = EmailConfig.query.get_or_404(config_id)
    data = request.get_json()
    
    # 更新字段
    if 'name' in data:
        config.name = data['name']
    if 'smtp_host' in data:
        config.smtp_host = data['smtp_host']
    if 'smtp_port' in data:
        config.smtp_port = data['smtp_port']
    if 'smtp_use_tls' in data:
        config.smtp_use_tls = data['smtp_use_tls']
    if 'smtp_use_ssl' in data:
        config.smtp_use_ssl = data['smtp_use_ssl']
    if 'sender_email' in data:
        config.sender_email = data['sender_email']
    if 'sender_name' in data:
        config.sender_name = data['sender_name']
    if 'username' in data:
        config.username = data['username']
    if 'password' in data:
        encryption = EmailEncryption()
        config.password = encryption.encrypt_password(data['password'])
    if 'is_active' in data:
        config.is_active = data['is_active']
    if 'is_default' in data and data['is_default']:
        EmailConfig.query.filter(EmailConfig.id != config_id).update({'is_default': False})
        config.is_default = True
    
    config.updated_at = datetime.now()
    db.session.commit()
    
    return jsonify({'message': 'Email config updated successfully'})


@email_bp.route('/configs/<int:config_id>', methods=['DELETE'])
@login_required
@role_required(RoleEnum.ADMIN)
def delete_email_config(config_id):
    """删除邮件配置"""
    config = EmailConfig.query.get_or_404(config_id)
    
    # 检查是否有任务在使用此配置
    tasks_using = EmailTask.query.filter_by(email_config_id=config_id).count()
    if tasks_using > 0:
        return jsonify({'error': f'Cannot delete config, {tasks_using} tasks are using it'}), 400
    
    db.session.delete(config)
    db.session.commit()
    
    return jsonify({'message': 'Email config deleted successfully'})


@email_bp.route('/configs/<int:config_id>/test', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def test_email_config(config_id):
    """测试邮件配置"""
    try:
        config = EmailConfig.query.get_or_404(config_id)
        data = request.get_json()
        
        # 获取测试邮箱地址
        test_email = data.get('test_email')
        if not test_email:
            # 如果没有提供测试邮箱，尝试使用当前用户的邮箱
            if hasattr(current_user, 'email') and current_user.email:
                test_email = current_user.email
            else:
                return jsonify({'error': 'Test email address is required'}), 400
        
        # 验证邮箱格式
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', test_email):
            return jsonify({'error': 'Invalid email address format'}), 400
        
        service = EmailService()
        result = service.send_email(
            config=config,
            recipients=[test_email],
            subject='Email Configuration Test',
            body_text='This is a test email to verify your email configuration.',
            body_html='<p>This is a <strong>test email</strong> to verify your email configuration.</p>'
        )
        
        if result['success']:
            return jsonify({'message': 'Test email sent successfully'})
        else:
            return jsonify({'error': result.get('error', 'Failed to send test email')}), 500
    except Exception as e:
        logger.error(f"Test email failed: {str(e)}", exc_info=True)
        return jsonify({'error': f'Test failed: {str(e)}'}), 500


# ============= 邮件模板管理 =============

@email_bp.route('/templates', methods=['GET'])
@login_required
def get_email_templates():
    """获取邮件模板列表"""
    templates = EmailTemplate.query.filter_by(is_active=True).all()
    return jsonify({
        'templates': [
            {
                'id': t.id,
                'name': t.name,
                'template_type': t.template_type.value,
                'subject': t.subject,
                'description': t.description,
                'variables': t.variables,
                'created_by': t.creator.username if t.creator else None,
                'created_at': t.created_at.isoformat() if t.created_at else None
            }
            for t in templates
        ]
    })


@email_bp.route('/templates', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def create_email_template():
    """创建邮件模板"""
    data = request.get_json()
    
    # 验证必填字段
    required_fields = ['name', 'template_type', 'subject']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400
    
    # 验证模板类型
    try:
        template_type = EmailTemplateTypeEnum[data['template_type']]
    except KeyError:
        return jsonify({'error': 'Invalid template type'}), 400
    
    template = EmailTemplate(
        name=data['name'],
        template_type=template_type,
        subject=data['subject'],
        body_html=data.get('body_html'),
        body_text=data.get('body_text'),
        variables=data.get('variables'),
        description=data.get('description'),
        is_active=data.get('is_active', True),
        created_by=current_user.id
    )
    
    db.session.add(template)
    db.session.commit()
    
    return jsonify({
        'message': 'Email template created successfully',
        'template_id': template.id
    })


@email_bp.route('/templates/<int:template_id>', methods=['PUT'])
@login_required
@role_required(RoleEnum.ADMIN)
def update_email_template(template_id):
    """更新邮件模板"""
    template = EmailTemplate.query.get_or_404(template_id)
    data = request.get_json()
    
    # 更新字段
    if 'name' in data:
        template.name = data['name']
    if 'template_type' in data:
        try:
            template.template_type = EmailTemplateTypeEnum[data['template_type']]
        except KeyError:
            return jsonify({'error': 'Invalid template type'}), 400
    if 'subject' in data:
        template.subject = data['subject']
    if 'body_html' in data:
        template.body_html = data['body_html']
    if 'body_text' in data:
        template.body_text = data['body_text']
    if 'variables' in data:
        template.variables = data['variables']
    if 'description' in data:
        template.description = data['description']
    if 'is_active' in data:
        template.is_active = data['is_active']
    
    template.updated_at = datetime.now()
    db.session.commit()
    
    return jsonify({'message': 'Email template updated successfully'})


@email_bp.route('/templates/<int:template_id>', methods=['DELETE'])
@login_required
@role_required(RoleEnum.ADMIN)
def delete_email_template(template_id):
    """删除邮件模板"""
    template = EmailTemplate.query.get_or_404(template_id)
    
    # 检查是否有任务在使用此模板
    tasks_using = EmailTask.query.filter_by(template_id=template_id).count()
    if tasks_using > 0:
        return jsonify({'error': f'Cannot delete template, {tasks_using} tasks are using it'}), 400
    
    db.session.delete(template)
    db.session.commit()
    
    return jsonify({'message': 'Email template deleted successfully'})


@email_bp.route('/templates/<int:template_id>/preview', methods=['POST'])
@login_required
def preview_email_template(template_id):
    """预览邮件模板"""
    template = EmailTemplate.query.get_or_404(template_id)
    data = request.get_json()
    context = data.get('context', {})
    
    service = EmailService()
    try:
        rendered = service.render_template(template, context)
        return jsonify({
            'subject': rendered['subject'],
            'body_html': rendered['body_html'],
            'body_text': rendered['body_text']
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ============= 收件人组管理 =============

@email_bp.route('/recipient-groups', methods=['GET'])
@login_required
def get_recipient_groups():
    """获取收件人组列表"""
    groups = EmailRecipientGroup.query.filter_by(is_active=True).all()
    return jsonify({
        'groups': [
            {
                'id': g.id,
                'name': g.name,
                'description': g.description,
                'recipient_roles': g.recipient_roles,
                'recipient_user_ids': g.recipient_user_ids,
                'recipient_emails': g.recipient_emails,
                'created_at': g.created_at.isoformat() if g.created_at else None
            }
            for g in groups
        ]
    })


@email_bp.route('/recipient-groups', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def create_recipient_group():
    """创建收件人组"""
    data = request.get_json()
    
    if not data.get('name'):
        return jsonify({'error': 'Group name is required'}), 400
    
    # 检查名称是否已存在
    if EmailRecipientGroup.query.filter_by(name=data['name']).first():
        return jsonify({'error': 'Group name already exists'}), 400
    
    group = EmailRecipientGroup(
        name=data['name'],
        description=data.get('description'),
        recipient_roles=data.get('recipient_roles', []),
        recipient_user_ids=data.get('recipient_user_ids', []),
        recipient_emails=data.get('recipient_emails', []),
        is_active=data.get('is_active', True)
    )
    
    db.session.add(group)
    db.session.commit()
    
    return jsonify({
        'message': 'Recipient group created successfully',
        'group_id': group.id
    })


@email_bp.route('/recipient-groups/<int:group_id>', methods=['PUT'])
@login_required
@role_required(RoleEnum.ADMIN)
def update_recipient_group(group_id):
    """更新收件人组"""
    group = EmailRecipientGroup.query.get_or_404(group_id)
    data = request.get_json()
    
    # 更新字段
    if 'name' in data:
        # 检查名称是否已存在
        existing = EmailRecipientGroup.query.filter(
            EmailRecipientGroup.name == data['name'],
            EmailRecipientGroup.id != group_id
        ).first()
        if existing:
            return jsonify({'error': 'Group name already exists'}), 400
        group.name = data['name']
    if 'description' in data:
        group.description = data['description']
    if 'recipient_roles' in data:
        group.recipient_roles = data['recipient_roles']
    if 'recipient_user_ids' in data:
        group.recipient_user_ids = data['recipient_user_ids']
    if 'recipient_emails' in data:
        group.recipient_emails = data['recipient_emails']
    if 'is_active' in data:
        group.is_active = data['is_active']
    
    group.updated_at = datetime.now()
    db.session.commit()
    
    return jsonify({'message': 'Recipient group updated successfully'})


@email_bp.route('/recipient-groups/<int:group_id>', methods=['DELETE'])
@login_required
@role_required(RoleEnum.ADMIN)
def delete_recipient_group(group_id):
    """删除收件人组"""
    group = EmailRecipientGroup.query.get_or_404(group_id)
    
    # 检查是否有任务在使用此组
    tasks_using = EmailTask.query.filter_by(recipient_group_id=group_id).count()
    if tasks_using > 0:
        return jsonify({'error': f'Cannot delete group, {tasks_using} tasks are using it'}), 400
    
    db.session.delete(group)
    db.session.commit()
    
    return jsonify({'message': 'Recipient group deleted successfully'})


# ============= 邮件任务管理 =============

@email_bp.route('/tasks', methods=['GET'])
@login_required
def get_email_tasks():
    """获取邮件任务列表"""
    tasks = EmailTask.query.all()
    return jsonify({
        'tasks': [
            {
                'id': t.id,
                'name': t.name,
                'description': t.description,
                'template_id': t.template_id,
                'template_name': t.template.name if t.template else None,
                'email_config_id': t.email_config_id,
                'config_name': t.email_config.name if t.email_config else None,
                'recipient_group_id': t.recipient_group_id,
                'group_name': t.recipient_group.name if t.recipient_group else None,
                'frequency': t.frequency.value,
                'cron_expression': t.cron_expression,
                'send_time': t.send_time.isoformat() if t.send_time else None,
                'send_day_of_week': t.send_day_of_week,
                'send_day_of_month': t.send_day_of_month,
                'is_active': t.is_active,
                'last_run_at': t.last_run_at.isoformat() if t.last_run_at else None,
                'next_run_at': t.next_run_at.isoformat() if t.next_run_at else None,
                'created_by': t.creator.username if t.creator else None,
                'created_at': t.created_at.isoformat() if t.created_at else None
            }
            for t in tasks
        ]
    })


@email_bp.route('/tasks', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def create_email_task():
    """创建邮件任务"""
    data = request.get_json()
    
    # 验证必填字段
    required_fields = ['name', 'template_id', 'email_config_id', 'frequency']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400
    
    # 验证频率类型
    try:
        frequency = EmailTaskFrequencyEnum[data['frequency']]
    except KeyError:
        return jsonify({'error': 'Invalid frequency type'}), 400
    
    # 解析时间
    send_time = None
    if data.get('send_time'):
        try:
            time_parts = data['send_time'].split(':')
            send_time = time(int(time_parts[0]), int(time_parts[1]))
        except:
            return jsonify({'error': 'Invalid send_time format'}), 400
    
    task = EmailTask(
        name=data['name'],
        description=data.get('description'),
        template_id=data['template_id'],
        email_config_id=data['email_config_id'],
        recipient_group_id=data.get('recipient_group_id'),
        frequency=frequency,
        cron_expression=data.get('cron_expression'),
        send_time=send_time,
        send_day_of_week=data.get('send_day_of_week'),
        send_day_of_month=data.get('send_day_of_month'),
        data_query_config=data.get('data_query_config'),
        additional_recipients=data.get('additional_recipients'),
        is_active=data.get('is_active', True),
        created_by=current_user.id
    )
    
    db.session.add(task)
    db.session.commit()
    
    # 如果任务激活，添加到调度器
    if task.is_active:
        email_scheduler.schedule_task(task)
    
    return jsonify({
        'message': 'Email task created successfully',
        'task_id': task.id
    })


@email_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@login_required
@role_required(RoleEnum.ADMIN)
def update_email_task(task_id):
    """更新邮件任务"""
    task = EmailTask.query.get_or_404(task_id)
    data = request.get_json()
    
    # 更新字段
    if 'name' in data:
        task.name = data['name']
    if 'description' in data:
        task.description = data['description']
    if 'template_id' in data:
        task.template_id = data['template_id']
    if 'email_config_id' in data:
        task.email_config_id = data['email_config_id']
    if 'recipient_group_id' in data:
        task.recipient_group_id = data['recipient_group_id']
    if 'frequency' in data:
        try:
            task.frequency = EmailTaskFrequencyEnum[data['frequency']]
        except KeyError:
            return jsonify({'error': 'Invalid frequency type'}), 400
    if 'cron_expression' in data:
        task.cron_expression = data['cron_expression']
    if 'send_time' in data:
        if data['send_time']:
            try:
                time_parts = data['send_time'].split(':')
                task.send_time = time(int(time_parts[0]), int(time_parts[1]))
            except:
                return jsonify({'error': 'Invalid send_time format'}), 400
        else:
            task.send_time = None
    if 'send_day_of_week' in data:
        task.send_day_of_week = data['send_day_of_week']
    if 'send_day_of_month' in data:
        task.send_day_of_month = data['send_day_of_month']
    if 'data_query_config' in data:
        task.data_query_config = data['data_query_config']
    if 'additional_recipients' in data:
        task.additional_recipients = data['additional_recipients']
    if 'is_active' in data:
        task.is_active = data['is_active']
    
    task.updated_at = datetime.now()
    db.session.commit()
    
    # 重新调度任务
    if task.is_active:
        email_scheduler.schedule_task(task)
    else:
        email_scheduler.remove_task(task_id)
    
    return jsonify({'message': 'Email task updated successfully'})


@email_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@login_required
@role_required(RoleEnum.ADMIN)
def delete_email_task(task_id):
    """删除邮件任务"""
    task = EmailTask.query.get_or_404(task_id)
    
    # 从调度器中移除
    email_scheduler.remove_task(task_id)
    
    db.session.delete(task)
    db.session.commit()
    
    return jsonify({'message': 'Email task deleted successfully'})


@email_bp.route('/tasks/<int:task_id>/run', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def run_email_task(task_id):
    """立即执行邮件任务"""
    task = EmailTask.query.get_or_404(task_id)
    
    email_scheduler.run_task_now(task_id)
    
    return jsonify({'message': 'Email task triggered successfully'})


@email_bp.route('/tasks/<int:task_id>/toggle', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def toggle_email_task(task_id):
    """启用/禁用邮件任务"""
    task = EmailTask.query.get_or_404(task_id)
    
    task.is_active = not task.is_active
    task.updated_at = datetime.now()
    db.session.commit()
    
    if task.is_active:
        email_scheduler.schedule_task(task)
        message = 'Email task enabled'
    else:
        email_scheduler.remove_task(task_id)
        message = 'Email task disabled'
    
    return jsonify({
        'message': message,
        'is_active': task.is_active
    })


# ============= 邮件日志查询 =============

@email_bp.route('/logs', methods=['GET'])
@login_required
def get_email_logs():
    """获取邮件发送日志"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    task_id = request.args.get('task_id', type=int)
    status = request.args.get('status')
    
    query = EmailLog.query
    
    # 过滤条件
    if task_id:
        query = query.filter_by(task_id=task_id)
    if status:
        try:
            status_enum = EmailStatusEnum[status]
            query = query.filter_by(status=status_enum)
        except KeyError:
            pass
    
    # 分页查询
    pagination = query.order_by(EmailLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    logs = pagination.items
    
    return jsonify({
        'logs': [
            {
                'id': log.id,
                'task_id': log.task_id,
                'task_name': log.task.name if log.task else None,
                'subject': log.subject,
                'recipients': log.recipients,
                'cc_recipients': log.cc_recipients,
                'bcc_recipients': log.bcc_recipients,
                'status': log.status.value,
                'error_message': log.error_message,
                'retry_count': log.retry_count,
                'scheduled_at': log.scheduled_at.isoformat() if log.scheduled_at else None,
                'sent_at': log.sent_at.isoformat() if log.sent_at else None,
                'created_at': log.created_at.isoformat() if log.created_at else None
            }
            for log in logs
        ],
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'pages': pagination.pages
    })


@email_bp.route('/logs/<int:log_id>', methods=['GET'])
@login_required
def get_email_log_detail(log_id):
    """获取邮件日志详情"""
    log = EmailLog.query.get_or_404(log_id)
    
    return jsonify({
        'id': log.id,
        'task_id': log.task_id,
        'task_name': log.task.name if log.task else None,
        'email_config_id': log.email_config_id,
        'config_name': log.email_config.name if log.email_config else None,
        'subject': log.subject,
        'body': log.body,
        'recipients': log.recipients,
        'cc_recipients': log.cc_recipients,
        'bcc_recipients': log.bcc_recipients,
        'status': log.status.value,
        'error_message': log.error_message,
        'retry_count': log.retry_count,
        'scheduled_at': log.scheduled_at.isoformat() if log.scheduled_at else None,
        'sent_at': log.sent_at.isoformat() if log.sent_at else None,
        'created_at': log.created_at.isoformat() if log.created_at else None
    })


@email_bp.route('/logs/<int:log_id>/retry', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMIN)
def retry_email_log(log_id):
    """重试发送失败的邮件"""
    log = EmailLog.query.get_or_404(log_id)
    
    if log.status != EmailStatusEnum.FAILED:
        return jsonify({'error': 'Can only retry failed emails'}), 400
    
    # 更新状态为发送中
    log.status = EmailStatusEnum.SENDING
    log.retry_count += 1
    db.session.commit()
    
    # 重新发送
    service = EmailService()
    
    try:
        # 获取配置
        config = log.email_config or (log.task.email_config if log.task else None)
        if not config:
            raise ValueError("No email configuration found")
        
        # 发送邮件
        result = service.send_email(
            config=config,
            recipients=log.recipients,
            subject=log.subject,
            body_html=log.body if '<' in log.body else None,
            body_text=log.body if '<' not in log.body else None,
            cc_recipients=log.cc_recipients,
            bcc_recipients=log.bcc_recipients
        )
        
        # 更新状态
        if result['success']:
            log.status = EmailStatusEnum.SUCCESS
            log.sent_at = datetime.now()
            message = 'Email resent successfully'
        else:
            log.status = EmailStatusEnum.FAILED
            log.error_message = result.get('error', 'Unknown error')
            message = 'Failed to resend email'
        
        db.session.commit()
        
        return jsonify({
            'message': message,
            'status': log.status.value
        })
        
    except Exception as e:
        log.status = EmailStatusEnum.FAILED
        log.error_message = str(e)
        db.session.commit()
        return jsonify({'error': str(e)}), 500