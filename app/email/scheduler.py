# PSM/app/email/scheduler.py
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from flask import current_app
from .. import db
from ..models import EmailTask, EmailTaskFrequencyEnum
from .service import EmailService

logger = logging.getLogger(__name__)


class EmailScheduler:
    """邮件任务调度器"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.email_service = EmailService()
        self.scheduler.start()
        logger.info("Email scheduler started")
    
    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Email scheduler stopped")
    
    def init_tasks(self):
        """初始化所有活动的邮件任务"""
        try:
            with current_app.app_context():
                # 检查表是否存在
                from sqlalchemy import inspect
                inspector = inspect(db.engine)
                if 'email_tasks' not in inspector.get_table_names():
                    logger.info("Email tasks table does not exist yet, skipping initialization")
                    return
                    
                tasks = EmailTask.query.filter_by(is_active=True).all()
                for task in tasks:
                    self.schedule_task(task)
                logger.info(f"Initialized {len(tasks)} email tasks")
        except Exception as e:
            logger.warning(f"Could not initialize email tasks: {e}")
    
    def schedule_task(self, task: EmailTask):
        """
        调度单个任务
        
        Args:
            task: 邮件任务
        """
        job_id = f"email_task_{task.id}"
        
        # 如果任务已存在，先移除
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        
        if not task.is_active:
            return
        
        # 根据频率类型创建触发器
        trigger = self._create_trigger(task)
        
        if trigger:
            self.scheduler.add_job(
                func=self._execute_task,
                trigger=trigger,
                args=[task.id],
                id=job_id,
                name=task.name,
                replace_existing=True
            )
            
            # 更新下次运行时间
            job = self.scheduler.get_job(job_id)
            if job:
                task.next_run_at = job.next_run_time
                db.session.commit()
            
            logger.info(f"Scheduled task: {task.name} (ID: {task.id})")
    
    def _create_trigger(self, task: EmailTask):
        """
        根据任务配置创建触发器
        
        Args:
            task: 邮件任务
        
        Returns:
            APScheduler触发器
        """
        # 如果有Cron表达式，优先使用
        if task.cron_expression:
            try:
                return CronTrigger.from_crontab(task.cron_expression)
            except Exception as e:
                logger.error(f"Invalid cron expression for task {task.id}: {e}")
        
        # 根据频率类型创建触发器
        if task.frequency == EmailTaskFrequencyEnum.ONCE:
            # 一次性任务
            if task.send_time:
                run_date = datetime.combine(datetime.now().date(), task.send_time)
                if run_date < datetime.now():
                    run_date += timedelta(days=1)
                return DateTrigger(run_date=run_date)
        
        elif task.frequency == EmailTaskFrequencyEnum.DAILY:
            # 每天定时
            if task.send_time:
                return CronTrigger(
                    hour=task.send_time.hour,
                    minute=task.send_time.minute
                )
        
        elif task.frequency == EmailTaskFrequencyEnum.WEEKLY:
            # 每周定时
            if task.send_time and task.send_day_of_week is not None:
                return CronTrigger(
                    day_of_week=task.send_day_of_week,
                    hour=task.send_time.hour,
                    minute=task.send_time.minute
                )
        
        elif task.frequency == EmailTaskFrequencyEnum.MONTHLY:
            # 每月定时
            if task.send_time and task.send_day_of_month:
                return CronTrigger(
                    day=task.send_day_of_month,
                    hour=task.send_time.hour,
                    minute=task.send_time.minute
                )
        
        return None
    
    def _execute_task(self, task_id: int):
        """
        执行邮件任务
        
        Args:
            task_id: 任务ID
        """
        with current_app.app_context():
            try:
                logger.info(f"Executing email task {task_id}")
                success = self.email_service.send_task_email(task_id)
                
                if success:
                    logger.info(f"Email task {task_id} executed successfully")
                else:
                    logger.error(f"Email task {task_id} execution failed")
                
                # 如果是一次性任务，执行后禁用
                task = EmailTask.query.get(task_id)
                if task and task.frequency == EmailTaskFrequencyEnum.ONCE:
                    task.is_active = False
                    db.session.commit()
                    self.remove_task(task_id)
                    
            except Exception as e:
                logger.error(f"Error executing email task {task_id}: {e}")
    
    def remove_task(self, task_id: int):
        """
        移除任务
        
        Args:
            task_id: 任务ID
        """
        job_id = f"email_task_{task_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed task {task_id} from scheduler")
    
    def pause_task(self, task_id: int):
        """
        暂停任务
        
        Args:
            task_id: 任务ID
        """
        job_id = f"email_task_{task_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.pause_job(job_id)
            logger.info(f"Paused task {task_id}")
    
    def resume_task(self, task_id: int):
        """
        恢复任务
        
        Args:
            task_id: 任务ID
        """
        job_id = f"email_task_{task_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.resume_job(job_id)
            logger.info(f"Resumed task {task_id}")
    
    def run_task_now(self, task_id: int):
        """
        立即执行任务
        
        Args:
            task_id: 任务ID
        """
        self._execute_task(task_id)


# 全局调度器实例
email_scheduler = EmailScheduler()