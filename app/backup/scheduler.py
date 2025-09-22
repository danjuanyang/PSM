
# PSM/app/backup/scheduler.py
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import current_app
from .. import db
from ..models import SystemConfig
from .service import BackupService

logger = logging.getLogger(__name__)


class BackupScheduler:
    """系统备份任务调度器"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.backup_service = BackupService()
        self.scheduler.start()
        logger.info("Backup scheduler started")

    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Backup scheduler stopped")

    def init_tasks(self):
        """从数据库加载并初始化备份任务"""
        try:
            with current_app.app_context():
                from sqlalchemy import inspect
                inspector = inspect(db.engine)
                if 'system_config' not in inspector.get_table_names():
                    logger.info("SystemConfig table does not exist yet, skipping backup task initialization")
                    return

                cron_config = SystemConfig.query.filter_by(key='AUTOBACKUP_CRON_SCHEDULE').first()
                if cron_config and cron_config.value:
                    self.schedule_task(cron_config.value)
                else:
                    logger.info("No AUTOBACKUP_CRON_SCHEDULE found in SystemConfig, no backup task scheduled.")
        except Exception as e:
            logger.warning(f"Could not initialize backup tasks: {e}")

    def schedule_task(self, cron_expression: str):
        """
        根据Cron表达式调度备份任务

        Args:
            cron_expression: Cron格式的调度字符串
        """
        job_id = "system_backup_task"

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        if not cron_expression:
            logger.info("Backup cron expression is empty, removing scheduled task.")
            return

        try:
            trigger = CronTrigger.from_crontab(cron_expression)
            self.scheduler.add_job(
                func=self._execute_backup_task,
                trigger=trigger,
                id=job_id,
                name="System Automatic Backup",
                replace_existing=True
            )
            job = self.scheduler.get_job(job_id)
            logger.info(f"Scheduled system backup with cron: '{cron_expression}'. Next run at: {job.next_run_time}")
        except Exception as e:
            logger.error(f"Invalid cron expression '{cron_expression}': {e}")

    def _execute_backup_task(self):
        """
        执行备份任务
        """
        with current_app.app_context():
            try:
                logger.info("Executing automatic system backup...")
                self.backup_service.create_backup_archive()
                logger.info("Automatic system backup finished successfully.")
            except Exception as e:
                logger.error(f"Error executing automatic system backup: {e}", exc_info=True)


# 全局调度器实例
backup_scheduler = BackupScheduler()
