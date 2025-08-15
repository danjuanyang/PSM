# PSM/app/email/service.py
import smtplib
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Dict, Any, Optional
import json
import re
from jinja2 import Template
import logging
from cryptography.fernet import Fernet
from .. import db
from ..models import (
    EmailConfig, EmailTemplate, EmailTask, EmailRecipientGroup, 
    EmailLog, EmailStatusEnum, EmailTemplateTypeEnum, EmailTaskFrequencyEnum,
    User, RoleEnum, Project, ReportClockinDetail, RequestTypeEnum, ProjectUpdate
)

logger = logging.getLogger(__name__)


class EmailEncryption:
    """邮件密码加密/解密服务"""
    
    @staticmethod
    def get_or_create_key():
        """获取或创建加密密钥"""
        key_file = os.environ.get('EMAIL_ENCRYPTION_KEY_FILE', '.email_key')
        
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            return key
    
    @staticmethod
    def encrypt_password(password: str) -> str:
        """加密密码"""
        key = EmailEncryption.get_or_create_key()
        f = Fernet(key)
        return f.encrypt(password.encode()).decode()
    
    @staticmethod
    def decrypt_password(encrypted_password: str) -> str:
        """解密密码"""
        key = EmailEncryption.get_or_create_key()
        f = Fernet(key)
        return f.decrypt(encrypted_password.encode()).decode()


class EmailService:
    """邮件服务核心类"""
    
    def __init__(self):
        self.encryption = EmailEncryption()
    
    def send_email(
        self,
        config: EmailConfig,
        recipients: List[str],
        subject: str,
        body_html: Optional[str] = None,
        body_text: Optional[str] = None,
        cc_recipients: Optional[List[str]] = None,
        bcc_recipients: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        发送邮件
        
        Args:
            config: 邮件配置
            recipients: 收件人列表
            subject: 邮件主题
            body_html: HTML格式内容
            body_text: 纯文本内容
            cc_recipients: 抄送列表
            bcc_recipients: 密送列表
            attachments: 附件列表 [{'filename': 'file.pdf', 'content': bytes}]
        
        Returns:
            发送结果
        """
        logger.info(f"尝试将电子邮件发送至{recipients} 使用配置'{config.name}'")
        try:
            # 创建邮件消息
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{config.sender_name or config.sender_email} <{config.sender_email}>"
            msg['To'] = ', '.join(recipients)
            
            if cc_recipients:
                msg['Cc'] = ', '.join(cc_recipients)
            if bcc_recipients:
                msg['Bcc'] = ', '.join(bcc_recipients)
            
            # 添加邮件内容
            if body_text:
                msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
            if body_html:
                msg.attach(MIMEText(body_html, 'html', 'utf-8'))
            
            # 添加附件
            if attachments:
                for attachment in attachments:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment['content'])
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename= {attachment["filename"]}'
                    )
                    msg.attach(part)
            
            # 连接SMTP服务器并发送
            if config.smtp_use_ssl:
                server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port)
            else:
                server = smtplib.SMTP(config.smtp_host, config.smtp_port)
                if config.smtp_use_tls:
                    server.starttls()
            
            # 登录认证
            if config.password:
                try:
                    password = self.encryption.decrypt_password(config.password)
                except Exception as decrypt_error:
                    logger.error(f"密码解密失败： {str(decrypt_error)}")
                    # 如果解密失败，尝试使用原始密码（可能还未加密）
                    password = config.password
            else:
                return {
                    'success': False,
                    'error': 'SMTP password not configured'
                }
            
            server.login(config.username, password)
            
            # 发送邮件
            all_recipients = recipients + (cc_recipients or []) + (bcc_recipients or [])
            server.send_message(msg, config.sender_email, all_recipients)
            server.quit()
            
            logger.info("通过 SMTP 成功发送电子邮件。")
            return {
                'success': True,
                'message': '电子邮件发送成功'
            }
            
        except Exception as e:
            logger.error(f"SMTP 错误：无法发送电子邮件。原因： {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def render_template(self, template: EmailTemplate, context: Dict[str, Any]) -> Dict[str, str]:
        """
        渲染邮件模板
        
        Args:
            template: 邮件模板
            context: 模板变量上下文
        
        Returns:
            渲染后的主题和内容
        """
        try:
            # 渲染主题
            subject_template = Template(template.subject)
            rendered_subject = subject_template.render(**context)
            
            # 渲染HTML内容
            rendered_html = None
            if template.body_html:
                html_template = Template(template.body_html)
                rendered_html = html_template.render(**context)
            
            # 渲染纯文本内容
            rendered_text = None
            if template.body_text:
                text_template = Template(template.body_text)
                rendered_text = text_template.render(**context)
            
            return {
                'subject': rendered_subject,
                'body_html': rendered_html,
                'body_text': rendered_text
            }
        except Exception as e:
            logger.error(f"无法渲染模板： {str(e)}")
            raise
    
    def get_recipients_from_group(self, group: EmailRecipientGroup) -> List[str]:
        """
        从收件人组获取收件人邮箱列表
        
        Args:
            group: 收件人组
        
        Returns:
            邮箱列表
        """
        recipients = []
        
        # 添加外部邮箱
        if group.recipient_emails:
            recipients.extend(group.recipient_emails)
        
        # 根据角色获取用户
        if group.recipient_roles:
            for role_str in group.recipient_roles:
                try:
                    role = RoleEnum[role_str]
                    users = User.query.filter_by(role=role).all()
                    recipients.extend([u.email for u in users if u.email])
                except KeyError:
                    logger.warning(f"角色无效： {role_str}")
        
        # 根据用户ID获取邮箱
        if group.recipient_user_ids:
            users = User.query.filter(User.id.in_(group.recipient_user_ids)).all()
            recipients.extend([u.email for u in users if u.email])
        
        # 去重
        return list(set(recipients))
    
    def prepare_email_data(self, task: EmailTask) -> Dict[str, Any]:
        """
        准备邮件数据
        
        Args:
            task: 邮件任务
        
        Returns:
            模板渲染所需的数据
        """
        context = {
            'current_date': datetime.now().strftime('%Y-%m-%d'),
            'current_time': datetime.now().strftime('%H:%M:%S'),
            'task_name': task.name
        }
        
        # 根据模板类型准备数据
        if task.template and task.template.template_type == EmailTemplateTypeEnum.WEEKLY_REPORT:
            context.update(self._prepare_weekly_report_data())
        elif task.template and task.template.template_type == EmailTemplateTypeEnum.MONTHLY_REPORT:
            context.update(self._prepare_monthly_report_data())
        elif task.template and task.template.template_type == EmailTemplateTypeEnum.CLOCK_IN_SUMMARY:
            context.update(self._prepare_clock_in_summary_data())
        elif task.template and task.template.template_type == EmailTemplateTypeEnum.PROJECT_DEADLINE:
            context.update(self._prepare_project_deadline_data())
        
        # 添加自定义查询配置的数据
        if task.data_query_config:
            context.update(self._execute_custom_query(task.data_query_config))
        
        return context
    
    def _prepare_weekly_report_data(self) -> Dict[str, Any]:
        """准备周报数据"""
        start_date = datetime.now() - timedelta(days=7)
        
        # 查询本周有更新的项目
        projects = Project.query.join(Project.updates).filter(
            ProjectUpdate.created_at >= start_date
        ).distinct().all()
        
        return {
            'week_start': start_date.strftime('%Y-%m-%d'),
            'week_end': datetime.now().strftime('%Y-%m-%d'),
            'projects': [
                {
                    'name': p.name,
                    'progress': p.progress,
                    'status': p.status.value if p.status else '',
                    'employee': p.employee.username if p.employee else ''
                }
                for p in projects
            ],
            'total_projects': len(projects)
        }
    
    def _prepare_monthly_report_data(self) -> Dict[str, Any]:
        """准备月报数据"""
        start_date = datetime.now().replace(day=1)
        
        # 查询本月项目数据
        projects = Project.query.filter(
            Project.start_date >= start_date
        ).all()
        
        completed_projects = [p for p in projects if p.status and p.status.value == 'completed']
        
        return {
            'month': datetime.now().strftime('%Y-%m'),
            'total_projects': len(projects),
            'completed_projects': len(completed_projects),
            'completion_rate': round(len(completed_projects) / len(projects) * 100, 2) if projects else 0
        }
    
    def _prepare_clock_in_summary_data(self) -> Dict[str, Any]:
        """准备补卡汇总数据"""
        start_date = datetime.now().replace(day=1)
        
        # 查询本月补卡记录
        clock_ins = ReportClockinDetail.query.filter(
            ReportClockinDetail.created_at >= start_date,
            ReportClockinDetail.request_type == RequestTypeEnum.CLOCK_IN
        ).all()
        
        # 按用户统计
        user_stats = {}
        for record in clock_ins:
            if record.report and record.report.employee:
                username = record.report.employee.username
                if username not in user_stats:
                    user_stats[username] = []
                user_stats[username].append({
                    'date': record.clockin_date.strftime('%Y-%m-%d'),
                    'weekday': record.weekday,
                    'remarks': record.remarks
                })
        
        return {
            'month': datetime.now().strftime('%Y-%m'),
            'total_clock_ins': len(clock_ins),
            'user_statistics': [
                {
                    'username': username,
                    'count': len(records),
                    'records': records
                }
                for username, records in user_stats.items()
            ]
        }
    
    def _prepare_project_deadline_data(self) -> Dict[str, Any]:
        """准备项目到期提醒数据"""
        # 查询15天内到期的项目
        deadline_date = datetime.now() + timedelta(days=15)
        
        projects = Project.query.filter(
            Project.deadline <= deadline_date,
            Project.deadline >= datetime.now(),
            Project.status != 'completed'
        ).all()
        
        return {
            'deadline_projects': [
                {
                    'name': p.name,
                    'deadline': p.deadline.strftime('%Y-%m-%d'),
                    'days_remaining': (p.deadline - datetime.now()).days,
                    'progress': p.progress,
                    'employee': p.employee.username if p.employee else ''
                }
                for p in projects
            ],
            'total_deadline_projects': len(projects)
        }
    
    def _execute_custom_query(self, query_config: Dict) -> Dict[str, Any]:
        """执行自定义查询配置"""
        # 这里可以根据配置执行自定义的数据查询
        # query_config 可以包含SQL查询、模型查询等配置
        return {}
    
    def send_task_email(self, task_id: int) -> bool:
        """
        发送任务邮件
        
        Args:
            task_id: 任务ID
        
        Returns:
            是否成功
        """
        logger.info(f"开始处理电子邮件任务 {task_id} ('{EmailTask.query.get(task_id).name}')")
        task = EmailTask.query.get(task_id)
        if not task or not task.is_active:
            logger.warning(f"任务 {task_id} 不活跃或不存在。中止.")
            return False
        
        log = None  # 初始化log变量
        try:
            # 1. 获取收件人
            recipients = []
            if task.recipient_group:
                recipients = self.get_recipients_from_group(task.recipient_group)
            if task.additional_recipients:
                recipients.extend(task.additional_recipients)
            
            if not recipients:
                raise ValueError("未找到任务的收件人")
            
            logger.info(f"任务 {task_id}: 收件人解析为： {recipients}")
            
            # 2. 准备数据并渲染模板
            logger.info(f"任务 {task_id}:准备和渲染模板...")
            context = self.prepare_email_data(task)
            rendered = self.render_template(task.template, context)
            logger.info(f"任务 {task_id}: 模板渲染成功。主题： '{rendered['subject']}'")

            # 3. 创建并填充完整的日志记录
            log = EmailLog(
                task_id=task.id,
                email_config_id=task.email_config_id,
                status=EmailStatusEnum.SENDING,
                scheduled_at=datetime.now(),
                subject=rendered['subject'],
                body=rendered['body_html'] or rendered['body_text'],
                recipients=recipients
            )
            db.session.add(log)
            db.session.commit()
            
            # 4. 发送邮件
            logger.info(f"任务 {task_id}: 继续通过 SMTP 服务发送电子邮件.")
            result = self.send_email(
                config=task.email_config,
                recipients=recipients,
                subject=rendered['subject'],
                body_html=rendered['body_html'],
                body_text=rendered['body_text']
            )
            
            # 5. 根据发送结果更新日志状态
            if result['success']:
                log.status = EmailStatusEnum.SUCCESS
                log.sent_at = datetime.now()
                task.last_run_at = datetime.now()
                logger.info(f"任务 {task_id}: 发送电子邮件并将日志更新为成功.")
            else:
                log.status = EmailStatusEnum.FAILED
                error_msg = result.get('error', 'Unknown error')
                log.error_message = error_msg
                logger.error(f"Email sending failed for task {task_id}. Reason: {error_msg}")
            
            db.session.commit()
            return result['success']
            
        except Exception as e:
            logger.error(f"An unexpected error occurred in send_task_email for task {task_id}: {str(e)}", exc_info=True)
            # 如果在日志创建后发生异常，也要更新日志
            if log and log.id: # 确保log对象已提交到数据库
                log.status = EmailStatusEnum.FAILED
                log.error_message = str(e)
                db.session.commit()
            elif not log: # 如果在创建log之前就失败了
                # 创建一个失败的日志条目
                try:
                    fail_log = EmailLog(
                        task_id=task.id,
                        email_config_id=task.email_config_id,
                        subject=f"[Task Failed] {task.name}",
                        recipients=[],
                        status=EmailStatusEnum.FAILED,
                        error_message=str(e)
                    )
                    db.session.add(fail_log)
                    db.session.commit()
                except Exception as log_e:
                    logger.error(f"Could not even create a failure log for task {task_id}: {log_e}")
            return False