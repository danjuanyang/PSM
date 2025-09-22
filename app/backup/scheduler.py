
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
        self.scheduler.add_job(
            func=self._reload_schedule_from_db,
            trigger="interval",
            minutes=5,
            id="backup_schedule_reloader",
            name="Backup Schedule Reloader"
        )
        self.scheduler.start()
        logger.info("Backup scheduler started with periodic reloader.")

    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Backup scheduler stopped")

    def _reload_schedule_from_db(self):
        """从数据库加载并初始化备份任务。此方法会由调度器周期性调用。"""
        with current_app.app_context():
            try:
                cron_config = SystemConfig.query.filter_by(key='AUTOBACKUP_CRON_SCHEDULE').first()
                cron_expression = cron_config.value if (cron_config and cron_config.value) else None
                self.schedule_backup_task(cron_expression)
            except Exception as e:
                logger.error(f"Could not reload backup schedule from DB: {e}", exc_info=True)

    def schedule_backup_task(self, cron_expression: str):
        """
        根据Cron表达式调度备份任务

        Args:
            cron_expression: Cron格式的调度字符串, 如果为None或空则移除任务
        """
        job_id = "system_backup_task"
        existing_job = self.scheduler.get_job(job_id)

        if not cron_expression:
            if existing_job:
                self.scheduler.remove_job(job_id)
                logger.info("Backup cron expression is empty, removed scheduled backup task.")
            return

        try:
            trigger = CronTrigger.from_crontab(cron_expression)
            if existing_job:
                existing_job.reschedule(trigger=trigger)
                logger.info(f"Rescheduled system backup with new cron: '{cron_expression}'. Next run at: {existing_job.next_run_time}")
            else:
                job = self.scheduler.add_job(
                    func=self._execute_backup_task,
                    trigger=trigger,
                    id=job_id,
                    name="System Automatic Backup",
                    replace_existing=True
                )
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
