# PSM/app/files/merge_tasks.py
import os
import uuid
import time
import shutil
import tempfile
from datetime import datetime
from flask import current_app
import PyPDF2
from PIL import Image
import fitz  # PyMuPDF
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from concurrent.futures import ThreadPoolExecutor
from .. import db
from ..models import FileMergeTask, FileMergeTaskStatusEnum, ProjectFile, Project


def register_font(font_name='simsun.ttf'):
    """根据给定的字体文件名注册字体，并返回用于ReportLab的字体名称"""
    if not font_name:
        font_name = 'simsun.ttf'

    font_display_name = os.path.splitext(font_name)[0]

    # 如果字体已经注册，直接返回显示名称
    if font_display_name in pdfmetrics.getRegisteredFontNames():
        return font_display_name

    font_path = os.path.join(current_app.root_path, 'fonts', font_name)
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(font_display_name, font_path))
            current_app.logger.info(f"字体 '{font_display_name}' 从 {font_path} 注册成功。")
            return font_display_name
        except Exception as e:
            current_app.logger.error(f"注册字体 '{font_name}' 失败: {e}")
            # 注册失败，尝试回退到默认字体
            pass

    # 如果指定字体不存在或注册失败，则注册并使用默认的宋体
    current_app.logger.warning(f"无法加载指定字体 '{font_name}'，将使用默认字体 'SimSun'。")
    default_font_name = 'SimSun'
    if default_font_name not in pdfmetrics.getRegisteredFontNames():
        default_font_path = os.path.join(current_app.root_path, 'fonts', 'simsun.ttf')
        if os.path.exists(default_font_path):
            pdfmetrics.registerFont(TTFont(default_font_name, default_font_path))
            current_app.logger.info("默认后备字体 'SimSun' 注册成功。")
            return default_font_name

    # 如果连宋体都失败了
    return None


# 创建线程池用于异步执行任务
task_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf_merge")


# 简化版本：使用线程池执行任务
class AsyncTaskResult:
    """模拟异步任务结果"""

    def __init__(self, task_id, future):
        self.id = task_id
        self.future = future

    @property
    def status(self):
        if self.future.done():
            if self.future.exception():
                return 'FAILURE'
            return 'SUCCESS'
        return 'PENDING'


def async_task(func):
    """装饰器：将函数转换为异步任务"""

    def wrapper(*args, **kwargs):
        from flask import current_app
        # 获取当前应用实例，传递给线程
        app = current_app._get_current_object()

        def run_with_context():
            with app.app_context():
                return func(*args, **kwargs)

        task_id = str(uuid.uuid4())
        future = task_executor.submit(run_with_context)
        return AsyncTaskResult(task_id, future)

    # 添加delay方法以兼容原有代码
    def delay(*args, **kwargs):
        return wrapper(*args, **kwargs)

    wrapper.delay = delay
    return wrapper


def update_task_progress(task_id, progress, status=None, status_message=None, error_message=None):
    """更新任务进度"""
    try:
        merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if merge_task:
            merge_task.progress = progress
            if status:
                merge_task.status = status
            if status_message:
                merge_task.status_message = status_message
            if error_message:
                merge_task.error_message = error_message
            merge_task.updated_at = datetime.now()

            if status in [FileMergeTaskStatusEnum.COMPLETED, FileMergeTaskStatusEnum.FAILED]:
                merge_task.completed_at = datetime.now()

            db.session.commit()
            print(f"任务进度更新成功: {task_id}, 进度: {progress}%, 状态: {status}")
        else:
            print(f"任务不存在: {task_id}")
    except Exception as e:
        print(f"更新任务进度失败: {e}")
        db.session.rollback()


def get_file_pages_count(file_path):
    """获取PDF文件页数"""
    try:
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            return len(pdf_reader.pages)
    except Exception as e:
        current_app.logger.error(f"获取PDF页数失败: {e}")
        return 0


def create_cover_page(options):
    """创建封面页PDF"""
    try:
        # 从选项中提取参数
        title = options.get('title', '默认标题')
        subtitle = options.get('subtitle', '')
        author = options.get('author', '')
        date = options.get('date', '')
        title_font_name = options.get('title_font', 'simsun.ttf')
        title_font_size = options.get('title_font_size', 32)
        subtitle_font_name = options.get('subtitle_font', 'simsun.ttf')
        subtitle_font_size = options.get('subtitle_font_size', 24)
        text_font_name = options.get('text_font', 'simsun.ttf') # 用于作者和日期

        # 注册所有需要的字体
        registered_title_font = register_font(title_font_name)
        registered_subtitle_font = register_font(subtitle_font_name)
        registered_text_font = register_font(text_font_name)

        if not all([registered_title_font, registered_subtitle_font, registered_text_font]):
            raise Exception("无法注册所有需要的字体。")

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        doc = SimpleDocTemplate(temp_file.name, pagesize=A4)

        # 样式
        title_style = ParagraphStyle(
            'CustomTitle',
            fontName=registered_title_font,
            fontSize=title_font_size,
            spaceAfter=30,
            alignment=1  # 居中
        )

        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            fontName=registered_subtitle_font,
            fontSize=subtitle_font_size,
            spaceAfter=20,
            alignment=1
        )

        normal_style = ParagraphStyle(
            'CustomNormal',
            fontName=registered_text_font,
            fontSize=12,
            spaceAfter=12,
            alignment=1
        )

        # 内容
        story = []
        story.append(Spacer(1, 2 * inch))
        story.append(Paragraph(title, title_style))

        if subtitle:
            story.append(Paragraph(subtitle, subtitle_style))

        story.append(Spacer(1, 1 * inch))

        if author:
            story.append(Paragraph(f"作者: {author}", normal_style))

        if date:
            story.append(Paragraph(f"日期: {date}", normal_style))

        doc.build(story)
        return temp_file.name

    except Exception as e:
        current_app.logger.error(f"创建封面页失败: {e}")
        return None


def create_toc_page(file_list, options):
    """创建层级目录页PDF"""
    try:
        # 从选项中提取参数
        font_name = options.get('font', 'simsun.ttf')
        font_size = options.get('font_size', 12)
        level = options.get('level', 2) # 默认为2级目录
        title_font_size = font_size + 6

        registered_font_name = register_font(font_name)
        if not registered_font_name:
            raise Exception("无法注册目录字体。")

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        doc = SimpleDocTemplate(temp_file.name, pagesize=A4, leftMargin=inch, rightMargin=inch)

        # 样式
        title_style = ParagraphStyle('TOCTitle', fontName=registered_font_name, fontSize=title_font_size, spaceAfter=20, alignment=1)
        
        # 为不同层级创建样式
        level_styles = []
        for i in range(4):
            style = ParagraphStyle(
                f'TOCLevel{i}',
                fontName=registered_font_name,
                fontSize=font_size,
                leftIndent=i * 20, # 逐级缩进
                spaceAfter=6
            )
            level_styles.append(style)

        story = [Paragraph("目 录", title_style)]
        
        current_page = 2 # 封面页占1页
        if options.get('enabled', True):
             current_page += 1 # 目录页本身占1页

        last_hierarchy = {}
        
        for file_info in file_list:
            hierarchy = file_info.get('hierarchy', {})
            
            # 根据level决定显示哪些层级
            hierarchy_levels = ['project', 'subproject', 'stage', 'task']
            
            for i in range(level):
                level_key = hierarchy_levels[i]
                current_level_name = hierarchy.get(level_key)
                
                if current_level_name and current_level_name != last_hierarchy.get(level_key):
                    # 添加层级标题
                    story.append(Paragraph(current_level_name, level_styles[i]))
                    last_hierarchy[level_key] = current_level_name
            
            # 添加文件名和页码
            file_name = file_info.get('original_name', '未知文件')
            # 使用一个表格来对齐文件名和页码
            toc_entry_table = Table(
                [[Paragraph(file_name, level_styles[level-1]), str(current_page)]],
                colWidths=['*', 0.5*inch]
            )
            toc_entry_table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ]))
            story.append(toc_entry_table)

            current_page += file_info.get('pages_count', 1)
            
            # 清除较低层级的last_hierarchy，以确保它们在下一个文件中能被重新打印
            for i in range(level, 4):
                level_key = hierarchy_levels[i]
                if level_key in last_hierarchy:
                    del last_hierarchy[level_key]

        doc.build(story)
        return temp_file.name

    except Exception as e:
        current_app.logger.error(f"创建层级目录页失败: {e}", exc_info=True)
        return None


def create_separator_page(stage_name, task_name, options):
    """创建居中任务分隔页（使用更可靠的Spacer布局）"""
    try:
        font_name = options.get('font', 'simsun.ttf')
        task_font_size = options.get('font_size', 16)
        stage_font_size = task_font_size - 2

        registered_font_name = register_font(font_name)
        if not registered_font_name:
            raise Exception("无法注册分隔页字体。")

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        doc = SimpleDocTemplate(temp_file.name, pagesize=A4, topMargin=inch, bottomMargin=inch)

        stage_style = ParagraphStyle(
            'StageSeparatorStyle',
            fontName=registered_font_name,
            fontSize=stage_font_size,
            alignment=1,
            leading=stage_font_size * 1.2
        )
        task_style = ParagraphStyle(
            'TaskSeparatorStyle',
            fontName=registered_font_name,
            fontSize=task_font_size,
            alignment=1,
            leading=task_font_size * 1.2
        )

        stage_p = Paragraph(stage_name, stage_style)
        task_p = Paragraph(task_name, task_style)

        # 计算两个段落的实际高度
        w, h1 = stage_p.wrap(doc.width, doc.height)
        w, h2 = task_p.wrap(doc.width, doc.height)
        total_content_height = h1 + h2

        # 计算将内容推到中心的Spacer高度
        top_spacer_height = (doc.height - total_content_height) / 2

        story = [
            Spacer(1, top_spacer_height),
            stage_p,
            task_p
        ]
        
        doc.build(story)
        return temp_file.name

    except Exception as e:
        current_app.logger.error(f"创建分隔页失败: {e}", exc_info=True)
        return None


def merge_pdfs(file_paths_list, output_path, pages_to_delete=None):
    """合并PDF文件"""
    try:
        pdf_writer = PyPDF2.PdfWriter()
        pages_to_delete = set(pages_to_delete or [])
        current_page_index = 0

        for file_path in file_paths_list:
            if not os.path.exists(file_path):
                current_app.logger.warning(f"文件不存在，跳过: {file_path}")
                continue
                
            with open(file_path, 'rb') as pdf_file:
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                for page_num, page in enumerate(pdf_reader.pages):
                    if current_page_index not in pages_to_delete:
                        pdf_writer.add_page(page)
                    current_page_index += 1

        # 写入输出文件
        with open(output_path, 'wb') as output_file:
            pdf_writer.write(output_file)

        return True

    except Exception as e:
        current_app.logger.error(f"合并PDF失败: {e}", exc_info=True)
        return False


def get_ordered_pdf_files(project_id, selected_file_ids):
    """获取按照选择顺序排序的PDF文件列表"""
    if not selected_file_ids:
        # 如果没有指定文件，按默认顺序获取项目下所有PDF文件
        files = ProjectFile.query.filter(
            ProjectFile.project_id == project_id,
            ProjectFile.file_type == 'pdf'
        ).order_by(ProjectFile.upload_date.asc()).all()
    else:
        # 按照选择的顺序获取文件 - 关键：保持用户选择的顺序
        files = []
        for file_id in selected_file_ids:
            file_obj = ProjectFile.query.filter(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project_id,
                ProjectFile.file_type == 'pdf'
            ).first()
            if file_obj and os.path.exists(file_obj.file_path):
                files.append(file_obj)

    return files


def convert_pdf_to_images_with_order(pdf_path, output_dir, dpi=150):
    """将PDF转换为图片，保持页面顺序信息 (使用 PyMuPDF)"""
    try:
        doc = fitz.open(pdf_path)
        image_paths = []

        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            image_filename = f"page_{i + 1}.png"
            image_path = os.path.join(output_dir, image_filename)
            pix.save(image_path)
            image_paths.append({
                'page_number': i + 1,
                'page_index': i,  # 0-based索引，用于删除操作
                'filename': image_filename,
                'url': f"/api/files/merge/temp_preview_image/{os.path.basename(output_dir)}/{image_filename}"
            })

        doc.close()
        return image_paths

    except Exception as e:
        current_app.logger.error(f"PDF转图片失败 (PyMuPDF): {e}")
        return []


def convert_pdf_to_images(pdf_path, output_dir, dpi=150):
    """将PDF转换为图片 (使用 PyMuPDF)"""
    try:
        doc = fitz.open(pdf_path)
        image_paths = []

        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            image_filename = f"page_{i + 1}.png"
            image_path = os.path.join(output_dir, image_filename)
            pix.save(image_path)
            image_paths.append({
                'page_number': i + 1,
                'filename': image_filename,
                'url': f"/api/files/temp_preview_image/{os.path.basename(output_dir)}/{image_filename}"
            })

        doc.close()
        return image_paths

    except Exception as e:
        current_app.logger.error(f"PDF转图片失败 (PyMuPDF): {e}")
        return []


@async_task
def generate_preview_task(task_id, project_id, selected_file_ids, merge_config):
    """生成预览的任务 - 使用线程池异步执行"""
    print(f"开始执行预览任务: {task_id}")
    try:
        print(f"任务 {task_id}: 开始更新进度到5%")
        update_task_progress(task_id, 5, FileMergeTaskStatusEnum.GENERATING_PREVIEW, "开始生成预览...")

        # 获取项目
        print(f"任务 {task_id}: 查询项目 {project_id}")
        project = Project.query.get(project_id)
        if not project:
            print(f"任务 {task_id}: 项目不存在")
            raise Exception("项目不存在")

        # 按选择顺序获取PDF文件
        print(f"任务 {task_id}: 获取PDF文件列表")
        files = get_ordered_pdf_files(project_id, selected_file_ids)

        if not files:
            print(f"任务 {task_id}: 没有找到PDF文件")
            raise Exception("没有找到可合并的PDF文件")

        print(f"任务 {task_id}: 找到 {len(files)} 个PDF文件")
        for i, file_obj in enumerate(files):
            print(f"任务 {task_id}: 文件 {i + 1}: {file_obj.original_name}")

        update_task_progress(task_id, 15, status_message="创建临时目录...")
        print(f"任务 {task_id}: 更新进度到15%")

        # 创建临时目录 - 使用项目配置的TEMP_DIR
        temp_base_dir = current_app.config.get('TEMP_DIR', tempfile.gettempdir())
        preview_session_id = str(uuid.uuid4())
        image_output_dir = os.path.join(temp_base_dir, preview_session_id)
        os.makedirs(image_output_dir, exist_ok=True)
        print(f"任务 {task_id}: 创建临时目录 {image_output_dir}")

        update_task_progress(task_id, 25, status_message="创建封面和目录...")

        # 从配置中获取字体信息
        text_font_name = merge_config.get('text_font', 'simsun.ttf')

        # 创建封面页
        cover_path = None
        cover_options = merge_config.get('coverPage', {})
        if cover_options.get('enabled', False):
            print(f"任务 {task_id}: 创建封面页")
            # 确保将项目名称和默认日期传递给封面
            full_cover_options = {
                'title': project.name,
                'date': datetime.now().strftime('%Y-%m-%d'),
                **cover_options,
                'text_font': text_font_name, # 传递正文字体用于作者和日期
            }
            cover_path = create_cover_page(full_cover_options)

        # 创建目录页
        toc_path = None
        toc_options = merge_config.get('toc', {})
        if toc_options.get('enabled', False):
            print(f"任务 {task_id}: 创建目录页")
            file_info_list = []
            for file_obj in files:
                pages_count = get_file_pages_count(file_obj.file_path)
                hierarchy = {
                    'project': file_obj.project.name if file_obj.project else '',
                    'subproject': file_obj.subproject.name if file_obj.subproject else '',
                    'stage': file_obj.stage.name if file_obj.stage else '',
                    'task': file_obj.task.name if file_obj.task else ''
                }
                file_info_list.append({
                    'original_name': file_obj.original_name,
                    'pages_count': pages_count,
                    'hierarchy': hierarchy
                })
            toc_path = create_toc_page(file_info_list, options=toc_options)

        # 按任务ID对文件进行排序，以确保分隔页逻辑正确
        files.sort(key=lambda f: (f.task_id is None, f.task_id))

        update_task_progress(task_id, 40, status_message="合并PDF文件...")

        # 合并PDF
        merged_pdf_path = os.path.join(image_output_dir, 'merged_preview.pdf')
        
        # 构建包含分隔页的最终文件列表
        paths_to_merge = []
        temp_files_to_clean = []
        if cover_path:
            paths_to_merge.append(cover_path)
            temp_files_to_clean.append(cover_path)
        if toc_path:
            paths_to_merge.append(toc_path)
            temp_files_to_clean.append(toc_path)

        last_task_id = None
        for file_obj in files:
            current_task_id = file_obj.task_id
            # 如果任务ID非空且发生变化，则插入分隔页
            if current_task_id is not None and current_task_id != last_task_id:
                separator_pdf = create_separator_page(file_obj.task.stage.name, file_obj.task.name, toc_options)
                if separator_pdf:
                    paths_to_merge.append(separator_pdf)
                    temp_files_to_clean.append(separator_pdf)
                last_task_id = current_task_id
            
            paths_to_merge.append(file_obj.file_path)

        print(f"任务 {task_id}: 开始合并PDF")
        success = merge_pdfs(paths_to_merge, merged_pdf_path)
        if not success:
            raise Exception("PDF合并失败")

        update_task_progress(task_id, 60, status_message="生成预览图片...")

        # 转换为图片
        print(f"任务 {task_id}: 转换PDF为图片")
        image_info = convert_pdf_to_images_with_order(merged_pdf_path, image_output_dir)
        if not image_info:
            raise Exception("生成预览图片失败")

        update_task_progress(task_id, 80, status_message="保存预览信息...")

        # 更新任务信息
        merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if merge_task:
            merge_task.preview_session_id = preview_session_id
            merge_task.preview_image_urls = image_info

            # 保存文件信息
            file_source_info = []
            page_index = 0

            # 封面页信息
            if cover_path:
                file_source_info.append({
                    'page_range': [page_index],
                    'source_type': 'cover',
                    'source_name': '封面页'
                })
                page_index += 1

            # 目录页信息  
            if toc_path:
                file_source_info.append({
                    'page_range': [page_index],
                    'source_type': 'toc',
                    'source_name': '目录页'
                })
                page_index += 1

            # 文件页面信息
            for file_obj in files:
                pages_count = get_file_pages_count(file_obj.file_path)
                page_range = list(range(page_index, page_index + pages_count))
                file_source_info.append({
                    'page_range': page_range,
                    'source_type': 'file',
                    'source_name': file_obj.original_name,
                    'file_id': file_obj.id
                })
                page_index += pages_count

            merge_task.merge_config = {
                **merge_config,
                'file_source_info': file_source_info
            }

            db.session.commit()

        # 清理临时文件
        if cover_path and os.path.exists(cover_path):
            os.unlink(cover_path)
        if toc_path and os.path.exists(toc_path):
            os.unlink(toc_path)
        if os.path.exists(merged_pdf_path):
            os.unlink(merged_pdf_path)

        update_task_progress(task_id, 100, FileMergeTaskStatusEnum.PREVIEW_READY, "预览生成完成")
        print(f"任务 {task_id}: 预览生成完成")

        return {
            'preview_session_id': preview_session_id,
            'image_info': image_info,
            'temp_dir': temp_base_dir
        }

    except Exception as e:
        print(f"任务 {task_id}: 执行失败 - {e}")
        import traceback
        traceback.print_exc()
        update_task_progress(task_id, 100, FileMergeTaskStatusEnum.FAILED, "生成预览失败", str(e))
        raise


@async_task
def generate_final_pdf_task(task_id, project_id, selected_file_ids, merge_config, pages_to_delete_indices):
    """生成最终PDF的任务 - 使用线程池异步执行"""
    cover_path, toc_path = None, None
    temp_files_to_clean = []
    
    try:
        update_task_progress(task_id, 5, FileMergeTaskStatusEnum.GENERATING_FINAL, "开始生成最终PDF...")

        project = Project.query.get(project_id)
        if not project:
            raise Exception("项目不存在")

        files = get_ordered_pdf_files(project_id, selected_file_ids)
        if not files:
            raise Exception("没有找到可合并的PDF文件")
        
        # 关键修复：按任务ID排序以确保分隔页逻辑正确
        files.sort(key=lambda f: (f.task_id is None, f.task_id))

        update_task_progress(task_id, 20, status_message="创建封面和目录...")

        header_page_count = 0
        text_font_name = merge_config.get('text_font', 'simsun.ttf')
        cover_options = merge_config.get('coverPage', {})
        if cover_options.get('enabled', False):
            full_cover_options = {'title': project.name, 'date': datetime.now().strftime('%Y-%m-%d'), **cover_options, 'text_font': text_font_name}
            cover_path = create_cover_page(full_cover_options)
            if cover_path:
                temp_files_to_clean.append(cover_path)
                header_page_count += 1

        toc_options = merge_config.get('toc', {})
        if toc_options.get('enabled', False):
            file_info_list = [{'original_name': f.original_name, 'pages_count': get_file_pages_count(f.file_path), 'hierarchy': {'project': f.project.name, 'subproject': f.subproject.name, 'stage': f.stage.name, 'task': f.task.name}} for f in files]
            toc_path = create_toc_page(file_info_list, options=toc_options)
            if toc_path:
                temp_files_to_clean.append(toc_path)
                header_page_count += 1

        update_task_progress(task_id, 40, status_message="准备合并文件列表...")
        
        paths_to_merge = []
        if cover_path: paths_to_merge.append(cover_path)
        if toc_path: paths_to_merge.append(toc_path)

        last_task_id = None
        for file_obj in files:
            current_task_id = file_obj.task_id
            if current_task_id is not None and current_task_id != last_task_id:
                separator_pdf = create_separator_page(file_obj.task.stage.name, file_obj.task.name, toc_options)
                if separator_pdf:
                    paths_to_merge.append(separator_pdf)
                    temp_files_to_clean.append(separator_pdf)
                last_task_id = current_task_id
            
            paths_to_merge.append(file_obj.file_path)

        output_dir = os.path.join(current_app.config.get('UPLOAD_FOLDER', '/tmp'), 'merged_files')
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        final_filename = f"{project.name}_merged_{timestamp}.pdf"
        final_file_path = os.path.join(output_dir, final_filename)

        update_task_progress(task_id, 60, status_message="正在合并PDF...")
        if not merge_pdfs(paths_to_merge, final_file_path, pages_to_delete=pages_to_delete_indices):
            raise Exception("无法合并最终PDF")

        update_task_progress(task_id, 80, status_message="正在添加页码...")
        font_for_numbering = toc_options.get('font', 'simsun.ttf')
        font_path_for_numbering = os.path.join(current_app.root_path, 'fonts', font_for_numbering)
        if not add_page_numbers_to_pdf(final_file_path, font_path_for_numbering, header_page_count=header_page_count):
            raise Exception("添加页码失败")

        merge_task = FileMergeTask.query.filter_by(task_id=task_id).first()
        if merge_task:
            merge_task.final_file_path = final_file_path
            merge_task.final_file_name = final_filename
            db.session.commit()

        update_task_progress(task_id, 100, FileMergeTaskStatusEnum.COMPLETED, "最终PDF生成成功")
        return {'final_file_path': final_file_path, 'final_filename': final_filename}

    except Exception as e:
        current_app.logger.error(f"最终PDF任务 {task_id} 失败: {e}", exc_info=True)
        update_task_progress(task_id, 100, FileMergeTaskStatusEnum.FAILED, "最终PDF生成失败", str(e))
    finally:
        for f in temp_files_to_clean:
            try:
                if os.path.exists(f):
                    os.unlink(f)
            except Exception as e:
                current_app.logger.error(f"清理临时文件失败 {f}: {e}")


def cleanup_temp_files(temp_dir):
    """清理临时文件"""
    try:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            current_app.logger.info(f"清理了临时目录： {temp_dir}")

    except Exception as e:
        current_app.logger.error(f"无法清理临时目录 {temp_dir}: {e}")


def add_page_numbers_to_pdf(pdf_path, font_path, header_page_count=0, font_size=10):
    """为PDF的每一页添加页码, 可选择跳过头部页面"""
    try:
        doc = fitz.open(pdf_path)
        temp_output_path = pdf_path + ".numbered.pdf"
        
        with open(font_path, "rb") as f:
            font_buffer = f.read()
        font = fitz.Font(fontbuffer=font_buffer)
        font_alias = font.name.replace(" ", "")

        for i, page in enumerate(doc):
            # 跳过封面和目录页
            if i < header_page_count:
                continue

            page.insert_font(fontname=font_alias, fontbuffer=font_buffer)
            
            # 页码从1开始计数
            page_num_text = f"- {i - header_page_count + 1} -"
            page_rect = page.rect
            
            text_width = font.text_length(page_num_text, fontsize=font_size)
            p = fitz.Point(page_rect.width / 2 - text_width / 2, page_rect.height - 30)
            
            page.insert_text(
                p,
                page_num_text,
                fontsize=font_size,
                fontname=font_alias,
                color=(0, 0, 0)
            )

        doc.save(temp_output_path, garbage=4, deflate=True)
        doc.close()
        
        os.replace(temp_output_path, pdf_path)
        current_app.logger.info(f"成功为 {pdf_path} 添加页码。")
        return True
        
    except Exception as e:
        current_app.logger.error(f"为PDF添加页码失败: {e}", exc_info=True)
        if 'temp_output_path' in locals() and os.path.exists(temp_output_path):
            os.unlink(temp_output_path)
        return False
