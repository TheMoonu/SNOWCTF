from django.contrib import admin
from django.utils.html import format_html
from quiz.models import Question, Option, Quiz, QuizQuestion, QuizRecord, Answer, QuizRegistration


class OptionInline(admin.TabularInline):
    """选项内联编辑"""
    model = Option
    extra = 4  # 默认显示4个空表单（用于选择题）
    max_num = 4  # 最多4个选项
    fields = ['order', 'content', 'is_correct']


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    """题目管理"""
    list_display = [
        'id',
        'question_type_display',
        'content_short',
        'score',
        'difficulty',
        'category',
        'is_active',
        'created_at'
    ]
    list_filter = ['question_type', 'difficulty', 'category', 'is_active', 'created_at']
    search_fields = ['id', 'content', 'category', 'standard_answer']
    list_editable = ['is_active']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'created_at'
    
    # 启用自动完成搜索
    autocomplete_fields = []
    
    def get_fieldsets(self, request, obj=None):
        """根据题型动态返回字段集"""
        # 判断是否为主观题
        is_subjective = obj and obj.question_type in ['fill_blank', 'essay']
        
        if is_subjective:
            # 主观题显示standard_answer
            return (
                ('基本信息', {
                    'fields': ('question_type', 'content', 'category', 'is_active')
                }),
                ('主观题答案', {
                    'fields': ('standard_answer',),
                    'description': '填空题和简答题的标准答案（用于批改参考）'
                }),
                ('详细配置', {
                    'fields': ('score', 'difficulty', 'explanation')
                }),
                ('时间信息', {
                    'fields': ('created_at', 'updated_at'),
                    'classes': ('collapse',)
                }),
            )
        else:
            # 客观题不显示standard_answer
            return (
                ('基本信息', {
                    'fields': ('question_type', 'content', 'category', 'is_active')
                }),
                ('详细配置', {
                    'fields': ('score', 'difficulty', 'explanation')
                }),
                ('时间信息', {
                    'fields': ('created_at', 'updated_at'),
                    'classes': ('collapse',)
                }),
            )
    
    def get_inlines(self, request, obj=None):
        """根据题型动态返回inlines"""
        # 如果是填空题或简答题，不显示选项编辑
        if obj and obj.question_type in ['fill_blank', 'essay']:
            return []
        # 其他题型显示选项编辑
        return [OptionInline]
    
    def question_type_display(self, obj):
        """题目类型显示"""
        colors = {
            'single': '#28a745',
            'multiple': '#007bff',
            'judge': '#ffc107',
            'fill_blank': '#17a2b8',
            'essay': '#dc3545'
        }
        color = colors.get(obj.question_type, '#6c757d')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_question_type_display()
        )
    question_type_display.short_description = '题目类型'
    
    def content_short(self, obj):
        """题目内容简短显示"""
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_short.short_description = '题目内容'
    
    def save_model(self, request, obj, form, change):
        """保存模型时的额外处理"""
        super().save_model(request, obj, form, change)
        
        # 如果是判断题，确保只有2个选项
        if obj.question_type == 'judge':
            options = obj.options.all()
            if options.count() > 2:
                # 只保留前两个选项
                for option in options[2:]:
                    option.delete()
    
    def save_related(self, request, form, formsets, change):
        """保存关联对象（选项）后进行验证"""
        super().save_related(request, form, formsets, change)
        
        obj = form.instance
        
        # 填空题和简答题不需要验证选项
        if obj.question_type in ['fill_blank', 'essay']:
            return
        
        # 调用模型的验证方法
        errors = obj.validate_options()
        
        if errors:
            from django.contrib import messages
            for error in errors:
                messages.warning(request, f'⚠️ {error}')
            messages.info(request, '题目已保存，但请注意以上警告信息')


@admin.register(Option)
class OptionAdmin(admin.ModelAdmin):
    """选项管理"""
    list_display = ['id', 'question_short', 'order', 'content_short', 'is_correct_display']
    list_filter = ['is_correct', 'order']
    search_fields = ['content', 'question__content']
    raw_id_fields = ['question']
    
    def question_short(self, obj):
        """题目简短显示"""
        return obj.question.content[:30] + '...'
    question_short.short_description = '所属题目'
    
    def content_short(self, obj):
        """选项内容简短显示"""
        return obj.content[:40] + '...' if len(obj.content) > 40 else obj.content
    content_short.short_description = '选项内容'
    
    def is_correct_display(self, obj):
        """正确答案显示"""
        if obj.is_correct:
            return format_html('<span style="color: green; font-weight: bold;">✓ 正确</span>')
        return format_html('<span style="color: gray;">✗ 错误</span>')
    is_correct_display.short_description = '是否正确'


class QuizQuestionInline(admin.TabularInline):
    """竞赛题目内联编辑"""
    model = QuizQuestion
    extra = 1  # 显示1个空行，方便添加
    can_delete = True  # 显式启用删除功能
    show_change_link = True  # 显示编辑链接
    fields = ['order', 'question', 'question_type_display', 'question_content_display', 'question_score_display']
    readonly_fields = ['question_type_display', 'question_content_display', 'question_score_display']
    ordering = ['order']
    
    # 使用自动完成搜索，提升用户体验
    autocomplete_fields = ['question']
    
    # 明确的类，用于前端识别
    classes = ['collapse']  # 可以折叠，减少页面长度
    
    def has_delete_permission(self, request, obj=None):
        """确保有删除权限"""
        return True
    
    def get_extra(self, request, obj=None, **kwargs):
        """动态设置 extra，编辑时不显示空行"""
        if obj:  # 编辑现有对象
            return 0
        return 1  # 新建时显示1个空行
    
    class Media:
        """添加自定义样式，确保删除按钮可见"""
        css = {
            'all': []
        }
        js = []
    
    def question_type_display(self, obj):
        """题目类型"""
        if obj.question:
            type_map = {
                'single': '<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px;">单选</span>',
                'multiple': '<span style="background: #007bff; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px;">多选</span>',
                'judge': '<span style="background: #ffc107; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px;">判断</span>'
            }
            return format_html(type_map.get(obj.question.question_type, '-'))
        return '-'
    question_type_display.short_description = '类型'
    
    def question_content_display(self, obj):
        """题目内容"""
        if obj.question:
            content = obj.question.content
            return content[:60] + '...' if len(content) > 60 else content
        return '-'
    question_content_display.short_description = '题目内容'
    
    def question_score_display(self, obj):
        """分数"""
        if obj.question:
            return format_html('<span style="color: #007bff; font-weight: bold;">{} 分</span>', obj.question.score)
        return '-'
    question_score_display.short_description = '分数'


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    """竞赛管理"""
    list_display = [
        'id',
        'title',
        'total_score',
        'pass_score',
        'duration',
        'anti_cheat_display',
        'random_order_display',
        'registration_display',
        'participants_count',
        'online_count',
        'avg_score_display',
        'is_active',
        'created_at'
    ]
    prepopulated_fields = {'slug': ('title',)}
    list_filter = [
        'is_active', 
        'enable_pass_score', 
        'show_leaderboard', 
        'enable_anti_cheat',
        'random_order',
        'require_registration',
        'require_approval',
        'created_at', 
        'start_time'
    ]
    search_fields = ['title', 'description', 'slug']
    list_editable = ['is_active']
    readonly_fields = [
        'total_score', 
        'created_at', 
        'updated_at', 
        'statistics_display',
        'score_distribution_display',
        'top_10_display'
    ]
    date_hierarchy = 'created_at'
    
    fieldsets = (
        (None, {
            'fields': ('title', 'slug', 'description', 'cover_image','is_active'),
            'description': '🏆 竞赛基本信息设置'
        }),
        ('⏰ 时间与分数', {
            'fields': (
                ('duration', 'total_score'),
                ('start_time', 'end_time'),
                ('enable_pass_score', 'pass_score')
            ),
            'description': '设置考试时长、总分和时间范围'
        }),
        ('🎮 答题控制', {
            'fields': (
                ('max_attempts', 'show_answer'),
                'show_leaderboard'
            ),
            'description': '控制答题次数、答案显示和排行榜功能'
        }),
        ('🛡️ 安全设置', {
            'fields': (
                ('enable_anti_cheat', 'max_violations'),
                'random_order'
            ),
            'description': '防作弊监控和题目顺序随机化'
        }),
        ('📝 报名设置', {
            'fields': (
                'require_registration',
                'require_approval'
            ),
            'description': '报名功能和审核机制控制'
        }),
        ('📊 统计数据', {
            'fields': ('statistics_display', 'score_distribution_display', 'top_10_display'),
            'classes': ('collapse',),
            'description': '竞赛实时统计数据'
        }),
        ('ℹ️ 系统信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    inlines = [QuizQuestionInline]
    
    actions = ['calculate_total_scores', 'clear_cache', 'export_statistics', 'export_user_best_scores']
    
    def participants_count(self, obj):
        """参与人数"""
        count = QuizRecord.objects.filter(
            quiz=obj,
            status__in=['completed', 'timeout']
        ).values('user').distinct().count()
        return format_html('<span style="color: #007bff; font-weight: bold;">{}</span>', count)
    participants_count.short_description = '参与人数'
    
    def online_count(self, obj):
        """在线答题人数"""
        count = QuizRecord.objects.filter(
            quiz=obj,
            status='in_progress'
        ).count()
        if count > 0:
            return format_html('<span style="color: #28a745; font-weight: bold;">{} 人在线</span>', count)
        return format_html('<span style="color: #6c757d;">0</span>')
    online_count.short_description = '在线答题'
    
    def avg_score_display(self, obj):
        """平均分显示"""
        from django.db.models import Avg
        avg = QuizRecord.objects.filter(
            quiz=obj,
            status__in=['completed', 'timeout']
        ).aggregate(avg_score=Avg('score'))['avg_score']
        
        if avg:
            avg_str = f'{float(avg):.2f}'
            return format_html('<span style="color: #17a2b8;">{}</span>', avg_str)
        return '-'
    avg_score_display.short_description = '平均分'
    
    def anti_cheat_display(self, obj):
        """防作弊设置显示"""
        if not hasattr(obj, 'enable_anti_cheat') or not obj.enable_anti_cheat:
            return format_html('<span style="color: #6c757d;">❌ 未启用</span>')
        
        max_violations = getattr(obj, 'max_violations', 5)
        return format_html(
            '<span style="color: #28a745;">✅ 启用</span><br/>'
            '<small style="color: #6c757d;">违规限制: {} 次</small>',
            max_violations
        )
    anti_cheat_display.short_description = '防作弊'
    
    def random_order_display(self, obj):
        """题目顺序显示"""
        random_order = getattr(obj, 'random_order', False)
        
        # 显示题目数量
        total = obj.quiz_questions.count()
        
        if random_order:
            return format_html(
                '<span style="color: #ffc107;">🔀 随机顺序</span><br/>'
                '<small style="color: #6c757d;">共 {} 题</small>',
                total
            )
        else:
            return format_html(
                '<span style="color: #6c757d;">📋 固定顺序</span><br/>'
                '<small style="color: #6c757d;">共 {} 题</small>',
                total
            )
    random_order_display.short_description = '题目顺序'
    
    def registration_display(self, obj):
        """报名设置显示"""
        require_registration = getattr(obj, 'require_registration', False)
        require_approval = getattr(obj, 'require_approval', False)
        
        if not require_registration:
            return format_html('<span style="color: #6c757d;">❌ 无需报名</span>')
        
        if require_approval:
            # 统计待审核数量
            pending_count = QuizRegistration.objects.filter(
                quiz=obj,
                status='pending'
            ).count()
            
            return format_html(
                '<span style="color: #ffc107;">📝 需审核</span><br/>'
                '<small style="color: #6c757d;">待审核: {} 人</small>',
                pending_count
            )
        else:
            # 统计报名总数
            registered_count = QuizRegistration.objects.filter(
                quiz=obj,
                status='approved'
            ).count()
            
            return format_html(
                '<span style="color: #28a745;">✅ 自动通过</span><br/>'
                '<small style="color: #6c757d;">已报名: {} 人</small>',
                registered_count
            )
    registration_display.short_description = '报名设置'
    
    def statistics_display(self, obj):
        """统计信息显示"""
        stats = obj.get_statistics()
        
        html = '<table style="width: 100%; border-collapse: collapse;">'
        html += '<tr style="background: #f8f9fa;"><th style="padding: 8px; border: 1px solid #dee2e6;">项目</th><th style="padding: 8px; border: 1px solid #dee2e6;">数值</th></tr>'
        
        html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">参与人数</td><td style="padding: 8px; border: 1px solid #dee2e6; font-weight: bold; color: #007bff;">{stats["total_participants"] or 0} 人</td></tr>'
        html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">总答题次数</td><td style="padding: 8px; border: 1px solid #dee2e6;">{stats["total_attempts"] or 0} 次</td></tr>'
        html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">在线答题人数</td><td style="padding: 8px; border: 1px solid #dee2e6; font-weight: bold; color: #28a745;">{stats["online_count"] or 0} 人</td></tr>'
        
        if stats['avg_score'] is not None:
            html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">平均分</td><td style="padding: 8px; border: 1px solid #dee2e6; color: #17a2b8;">{stats["avg_score"]:.2f} 分</td></tr>'
            html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">最高分</td><td style="padding: 8px; border: 1px solid #dee2e6; color: #28a745;">{stats["max_score"]:.2f} 分</td></tr>'
            html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">最低分</td><td style="padding: 8px; border: 1px solid #dee2e6; color: #dc3545;">{stats["min_score"]:.2f} 分</td></tr>'
        
        if stats['pass_rate'] is not None:
            color = '#28a745' if stats['pass_rate'] >= 60 else '#dc3545'
            html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">及格人次</td><td style="padding: 8px; border: 1px solid #dee2e6;">{stats["pass_count"]} 次</td></tr>'
            html += f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">及格率</td><td style="padding: 8px; border: 1px solid #dee2e6; font-weight: bold; color: {color};">{stats["pass_rate"]:.2f}%</td></tr>'
        
        html += '</table>'
        return format_html(html)
    statistics_display.short_description = '竞赛统计'
    
    def score_distribution_display(self, obj):
        """分数分布显示"""
        distribution = obj.get_score_distribution()
        
        if not distribution:
            return '暂无数据'
        
        html = '<div style="width: 100%;">'
        html += '<h4>分数分布图</h4>'
        
        max_count = max(distribution.values()) if distribution.values() else 1
        
        segments = [
            ('0-10%', 'segment_0_10'),
            ('10-20%', 'segment_10_20'),
            ('20-30%', 'segment_20_30'),
            ('30-40%', 'segment_30_40'),
            ('40-50%', 'segment_40_50'),
            ('50-60%', 'segment_50_60'),
            ('60-70%', 'segment_60_70'),
            ('70-80%', 'segment_70_80'),
            ('80-90%', 'segment_80_90'),
            ('90-100%', 'segment_90_100'),
        ]
        
        for label, key in segments:
            count = distribution.get(key, 0)
            percentage = (count / max_count * 100) if max_count > 0 else 0
            
            html += f'<div style="margin-bottom: 10px;">'
            html += f'<span style="display: inline-block; width: 80px;">{label}</span>'
            html += f'<div style="display: inline-block; width: 300px; background: #e9ecef; height: 20px; vertical-align: middle;">'
            html += f'<div style="width: {percentage}%; background: #007bff; height: 100%;"></div>'
            html += f'</div>'
            html += f'<span style="margin-left: 10px;">{count} 人</span>'
            html += f'</div>'
        
        html += '</div>'
        return format_html(html)
    score_distribution_display.short_description = '分数分布'
    
    def top_10_display(self, obj):
        """TOP 10 排行榜显示"""
        leaderboard = obj.get_leaderboard(limit=10)
        
        if not leaderboard:
            return '暂无数据'
        
        html = '<table style="width: 100%; border-collapse: collapse;">'
        html += '<tr style="background: #f8f9fa;"><th style="padding: 8px; border: 1px solid #dee2e6;">排名</th><th style="padding: 8px; border: 1px solid #dee2e6;">用户</th><th style="padding: 8px; border: 1px solid #dee2e6;">分数</th><th style="padding: 8px; border: 1px solid #dee2e6;">用时</th></tr>'
        
        medals = ['🥇', '🥈', '🥉']
        for idx, item in enumerate(leaderboard, 1):
            medal = medals[idx-1] if idx <= 3 else idx
            html += f'<tr>'
            html += f'<td style="padding: 8px; border: 1px solid #dee2e6; text-align: center; font-size: 18px;">{medal}</td>'
            html += f'<td style="padding: 8px; border: 1px solid #dee2e6;">{item["user__username"]}</td>'
            html += f'<td style="padding: 8px; border: 1px solid #dee2e6; font-weight: bold; color: #28a745;">{item["best_score"]} 分</td>'
            html += f'<td style="padding: 8px; border: 1px solid #dee2e6; color: #007bff;">{item["duration_formatted"]}</td>'
            html += f'</tr>'
        
        html += '</table>'
        return format_html(html)
    top_10_display.short_description = 'TOP 10 排行榜'
    
    def calculate_total_scores(self, request, queryset):
        """批量计算总分"""
        count = 0
        for quiz in queryset:
            quiz.calculate_total_score()
            count += 1
        self.message_user(request, f'成功计算了 {count} 个竞赛的总分')
    calculate_total_scores.short_description = '重新计算总分'
    
    def clear_cache(self, request, queryset):
        """清除缓存"""
        count = 0
        for quiz in queryset:
            quiz.clear_leaderboard_cache()
            count += 1
        self.message_user(request, f'成功清除了 {count} 个竞赛的缓存')
    clear_cache.short_description = '清除排行榜缓存'
    
    def export_statistics(self, request, queryset):
        """导出统计数据"""
        import csv
        from django.http import HttpResponse
        import datetime
        
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="quiz_statistics_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['竞赛ID', '竞赛名称', '参与人数', '总答题次数', '平均分', '最高分', '最低分', '及格率'])
        
        for quiz in queryset:
            stats = quiz.get_statistics()
            writer.writerow([
                quiz.id,
                quiz.title,
                stats['total_participants'] or 0,
                stats['total_attempts'] or 0,
                f"{stats['avg_score']:.2f}" if stats['avg_score'] else '-',
                f"{stats['max_score']:.2f}" if stats['max_score'] else '-',
                f"{stats['min_score']:.2f}" if stats['min_score'] else '-',
                f"{stats['pass_rate']:.2f}%" if stats['pass_rate'] is not None else '-'
            ])
        
        self.message_user(request, f'成功导出了 {queryset.count()} 个竞赛的统计数据')
        return response
    export_statistics.short_description = '导出统计数据（CSV）'
    
    def export_user_best_scores(self, request, queryset):
        """导出所选竞赛中每个用户的最高成绩"""
        import csv
        from django.http import HttpResponse
        import datetime
        from django.db.models import Max
        
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="quiz_user_best_scores_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['竞赛名称', '用户名', '真实姓名', '学院/部门', '学号/工号', '联系方式', '最高分', '总分', '得分率(%)', '是否及格', '答题用时', '提交时间', '排名'])
        
        total_users = 0
        for quiz in queryset:
            # 获取每个用户的最高分记录
            user_best_scores = QuizRecord.objects.filter(
                quiz=quiz,
                status__in=['completed', 'timeout']
            ).values('user').annotate(
                best_score=Max('score')
            ).order_by('-best_score')
            
            # 构建用户最高分字典
            user_scores = {item['user']: item['best_score'] for item in user_best_scores}
            
            # 获取每个用户最高分对应的详细记录（取最早的）
            for user_id, best_score in user_scores.items():
                record = QuizRecord.objects.filter(
                    quiz=quiz,
                    user_id=user_id,
                    score=best_score,
                    status__in=['completed', 'timeout']
                ).select_related('user').order_by('submit_time').first()
                
                if record:
                    total_users += 1
                    
                    # 计算排名
                    rank = quiz.get_user_rank(record.user)
                    
                    # 计算用时
                    duration = '-'
                    if record.submit_time:
                        duration_seconds = int((record.submit_time - record.start_time).total_seconds())
                        minutes = duration_seconds // 60
                        seconds = duration_seconds % 60
                        duration = f'{minutes}:{seconds:02d}'
                    
                    # 计算得分率
                    score_rate = (float(record.score) / float(quiz.total_score) * 100) if quiz.total_score > 0 else 0
                    
                    # 判断是否及格
                    is_passed = '-'
                    if quiz.enable_pass_score:
                        is_passed = '及格' if record.score >= quiz.pass_score else '不及格'
                    
                    # 获取用户敏感信息（已自动解密）
                    real_name = record.user.real_name if record.user.real_name else '-'
                    department = record.user.department if record.user.department else '-'
                    student_id = record.user.student_id if record.user.student_id else '-'
                    phones = record.user.phones if record.user.phones else '-'
                    
                    writer.writerow([
                        quiz.title,
                        record.user.username,
                        real_name,
                        department,
                        student_id,
                        phones,
                        float(record.score),
                        float(quiz.total_score),
                        f'{score_rate:.2f}',
                        is_passed,
                        duration,
                        record.submit_time.strftime('%Y-%m-%d %H:%M:%S') if record.submit_time else '-',
                        rank if rank else '-'
                    ])
        
        self.message_user(request, f'成功导出了 {queryset.count()} 个竞赛，共 {total_users} 个用户的最高分数据')
        return response
    export_user_best_scores.short_description = '导出用户最高成绩（CSV）'


class AnswerInline(admin.TabularInline):
    """答案内联编辑"""
    model = Answer
    extra = 0
    can_delete = False
    readonly_fields = ['question_inline', 'selected_options_inline', 'is_correct_inline', 'created_at']
    fields = ['question_inline', 'selected_options_inline', 'is_correct_inline', 'created_at']
    
    def question_inline(self, obj):
        """题目显示"""
        if obj.question:
            content = obj.question.content[:40] + '...' if len(obj.question.content) > 40 else obj.question.content
            type_colors = {
                'single': '#28a745',
                'multiple': '#007bff',
                'judge': '#ffc107'
            }
            color = type_colors.get(obj.question.question_type, '#6c757d')
            return format_html(
                '<span style="color: {}; font-weight: bold;">[{}]</span> {}',
                color,
                obj.question.get_question_type_display()[:2],
                content
            )
        return '-'
    question_inline.short_description = '题目'
    
    def selected_options_inline(self, obj):
        """用户选择的选项"""
        options = obj.selected_options.all().order_by('order')
        if options:
            parts = []
            for opt in options:
                color = '#28a745' if opt.is_correct else '#dc3545'
                icon = '✓' if opt.is_correct else '✗'
                parts.append(f'<span style="color: {color};">[{icon}] {opt.order}</span>')
            return format_html(' '.join(parts))
        return '-'
    selected_options_inline.short_description = '用户选择'
    
    def is_correct_inline(self, obj):
        """正确性"""
        if obj.is_correct:
            return format_html('<span style="color: #28a745; font-weight: bold;">✓</span>')
        return format_html('<span style="color: #dc3545; font-weight: bold;">✗</span>')
    is_correct_inline.short_description = '✓'
    
    def has_add_permission(self, request, obj=None):
        """禁止添加"""
        return False


@admin.register(QuizRecord)
class QuizRecordAdmin(admin.ModelAdmin):
    """答题记录管理"""
    list_display = [
        'id',
        'user',
        'quiz_title',
        'status_display',
        'score_display',
        'rank_display',
        'duration_display',
        'start_time'
    ]
    list_filter = ['status', 'start_time', 'quiz']
    search_fields = ['user__username', 'quiz__title', 'uuid']
    readonly_fields = [
        'uuid', 'user_display', 'quiz_display', 'status',
        'start_time', 'submit_time', 'score_detail_display', 'user_rank_display', 'duration_display'
    ]
    date_hierarchy = 'start_time'
    
    fieldsets = (
        ('基本信息', {
            'fields': ('uuid', 'user_display', 'quiz_display', 'status')
        }),
        ('成绩信息', {
            'fields': ('score_detail_display', 'user_rank_display')
        }),
        ('时间信息', {
            'fields': ('start_time', 'submit_time', 'duration_display')
        }),
    )
    
    inlines = [AnswerInline]
    
    actions = ['recalculate_scores', 'export_best_scores_by_quiz']
    
    def quiz_title(self, obj):
        """竞赛名称"""
        return obj.quiz.title
    quiz_title.short_description = '竞赛'
    
    def user_display(self, obj):
        """详情页显示用户信息（只读）"""
        try:
            user = obj.user
            
            # 安全获取用户属性
            real_name = getattr(user, 'real_name', None) or '-'
            student_id = getattr(user, 'student_id', None) or '-'
            department = getattr(user, 'department', None) or '-'
            phones = getattr(user, 'phones', None) or '-'
            
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #17a2b8;">'
                '<div style="margin-bottom: 8px;">'
                '<strong style="font-size: 16px;">👤 {}</strong>'
                '</div>'
                '<div style="color: #6c757d; font-size: 13px; line-height: 1.8;">'
                '<strong>用户 ID:</strong> #{}<br>'
                '<strong>真实姓名:</strong> {}<br>'
                '<strong>学号/工号:</strong> {}<br>'
                '<strong>学院/部门:</strong> {}<br>'
                '<strong>联系方式:</strong> {}'
                '</div>'
                '</div>',
                user.username,
                user.id,
                real_name,
                student_id,
                department,
                phones
            )
        except Exception as e:
            return format_html('<span style="color: #dc3545;">显示错误: {}</span>', str(e))
    user_display.short_description = '用户信息'
    
    def quiz_display(self, obj):
        """详情页显示竞赛信息（只读）"""
        try:
            quiz = obj.quiz
            
            # 安全获取时间
            start_time_str = '不限'
            if quiz.start_time:
                start_time_str = quiz.start_time.strftime('%Y-%m-%d %H:%M:%S')
            
            end_time_str = '不限'
            if quiz.end_time:
                end_time_str = quiz.end_time.strftime('%Y-%m-%d %H:%M:%S')
            
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #007bff;">'
                '<div style="margin-bottom: 10px;">'
                '<strong style="font-size: 16px; color: #007bff;">📋 {}</strong>'
                '</div>'
                '<div style="color: #6c757d; font-size: 13px; line-height: 1.8;">'
                '<strong>竞赛 ID:</strong> #{}<br>'
                '<strong>总分:</strong> {} 分<br>'
                '<strong>时长:</strong> {} 分钟<br>'
                '<strong>最大答题次数:</strong> {}<br>'
                '<strong>开始时间:</strong> {}<br>'
                '<strong>结束时间:</strong> {}'
                '</div>'
                '</div>',
                quiz.title,
                quiz.id,
                quiz.total_score,
                quiz.duration,
                '不限' if quiz.max_attempts == 0 else quiz.max_attempts,
                start_time_str,
                end_time_str
            )
        except Exception as e:
            return format_html('<span style="color: #dc3545;">显示错误: {}</span>', str(e))
    quiz_display.short_description = '竞赛信息'
    
    def status_display(self, obj):
        """状态显示"""
        colors = {
            'in_progress': '#007bff',
            'completed': '#28a745',
            'timeout': '#dc3545'
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_display.short_description = '状态'
    
    def score_detail_display(self, obj):
        """详细分数展示"""
        if obj.status in ['completed', 'timeout']:
            # 统计答题情况
            answers = obj.answers.all()
            total_questions = answers.count()
            correct_count = answers.filter(is_correct=True).count()
            wrong_count = total_questions - correct_count
            
            # 计算准确率和得分率
            accuracy = (correct_count / total_questions * 100) if total_questions > 0 else 0
            score_rate = (float(obj.score) / float(obj.quiz.total_score) * 100) if obj.quiz.total_score > 0 else 0
            
            # 判断是否及格
            pass_status = ''
            if obj.quiz.enable_pass_score:
                if obj.score >= obj.quiz.pass_score:
                    pass_status = '<div style="margin-top: 10px; padding: 8px; background: #d4edda; border-left: 3px solid #28a745; color: #155724;"><strong>✓ 已及格</strong> （及格线：{} 分）</div>'.format(obj.quiz.pass_score)
                else:
                    pass_status = '<div style="margin-top: 10px; padding: 8px; background: #f8d7da; border-left: 3px solid #dc3545; color: #721c24;"><strong>✗ 不及格</strong> （及格线：{} 分）</div>'.format(obj.quiz.pass_score)
            
            return format_html(
                '<div style="padding: 12px; background: #f8f9fa; border-radius: 5px;">'
                '<div style="display: flex; gap: 20px; margin-bottom: 10px;">'
                '<div style="flex: 1; text-align: center; padding: 15px; background: white; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
                '<div style="font-size: 28px; font-weight: bold; color: #007bff; margin-bottom: 5px;">{}</div>'
                '<div style="color: #6c757d; font-size: 12px;">得分</div>'
                '</div>'
                '<div style="flex: 1; text-align: center; padding: 15px; background: white; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
                '<div style="font-size: 28px; font-weight: bold; color: #6c757d; margin-bottom: 5px;">{}</div>'
                '<div style="color: #6c757d; font-size: 12px;">总分</div>'
                '</div>'
                '<div style="flex: 1; text-align: center; padding: 15px; background: white; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
                '<div style="font-size: 28px; font-weight: bold; color: #17a2b8; margin-bottom: 5px;">{:.1f}%</div>'
                '<div style="color: #6c757d; font-size: 12px;">得分率</div>'
                '</div>'
                '</div>'
                '<div style="display: flex; gap: 10px; margin-bottom: 10px;">'
                '<div style="flex: 1; padding: 10px; background: white; border-radius: 5px; border-left: 3px solid #28a745;">'
                '<span style="color: #28a745; font-weight: bold; font-size: 20px;">{}</span> <span style="color: #6c757d;">题正确</span>'
                '</div>'
                '<div style="flex: 1; padding: 10px; background: white; border-radius: 5px; border-left: 3px solid #dc3545;">'
                '<span style="color: #dc3545; font-weight: bold; font-size: 20px;">{}</span> <span style="color: #6c757d;">题错误</span>'
                '</div>'
                '<div style="flex: 1; padding: 10px; background: white; border-radius: 5px; border-left: 3px solid #17a2b8;">'
                '<span style="color: #17a2b8; font-weight: bold; font-size: 20px;">{:.1f}%</span> <span style="color: #6c757d;">准确率</span>'
                '</div>'
                '</div>'
                '{}'
                '</div>',
                float(obj.score),
                float(obj.quiz.total_score),
                score_rate,
                correct_count,
                wrong_count,
                accuracy,
                pass_status
            )
        return format_html('<span style="color: #6c757d;">未完成</span>')
    score_detail_display.short_description = '成绩详情'
    
    def score_display(self, obj):
        """分数显示"""
        if obj.status in ['completed', 'timeout']:
            score_str = f'{float(obj.score):.2f}'
            if obj.quiz.enable_pass_score:
                if obj.score >= obj.quiz.pass_score:
                    return format_html('<span style="color: #28a745; font-weight: bold;">{}</span>', score_str)
                else:
                    return format_html('<span style="color: #dc3545; font-weight: bold;">{}</span>', score_str)
            return format_html('<span style="color: #007bff; font-weight: bold;">{}</span>', score_str)
        return '-'
    score_display.short_description = '分数'
    
    def rank_display(self, obj):
        """排名显示"""
        if obj.status == 'completed':
            rank = obj.quiz.get_user_rank(obj.user)
            if rank:
                if rank == 1:
                    return format_html('<span style="font-size: 18px;">🥇</span>')
                elif rank == 2:
                    return format_html('<span style="font-size: 18px;">🥈</span>')
                elif rank == 3:
                    return format_html('<span style="font-size: 18px;">🥉</span>')
                else:
                    return format_html('<span style="color: #6c757d;">#{}</span>', rank)
        return '-'
    rank_display.short_description = '排名'
    
    def user_rank_display(self, obj):
        """用户排名详情"""
        if obj.status == 'completed':
            rank = obj.quiz.get_user_rank(obj.user)
            if rank:
                medal = ''
                if rank == 1:
                    medal = '🥇 '
                elif rank == 2:
                    medal = '🥈 '
                elif rank == 3:
                    medal = '🥉 '
                
                return format_html(
                    '<span style="font-size: 24px; font-weight: bold; color: #007bff;">{}{}</span>',
                    medal,
                    rank
                )
            return '未上榜'
        return '-'
    user_rank_display.short_description = '当前排名'
    
    def duration_display(self, obj):
        """答题用时显示"""
        if obj.status in ['completed', 'timeout'] and obj.submit_time:
            duration = (obj.submit_time - obj.start_time).total_seconds()
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            time_str = f'{minutes}:{seconds:02d}'
            return format_html(
                '<span style="color: #17a2b8;">{}</span>',
                time_str
            )
        return '-'
    duration_display.short_description = '用时'
    
    def recalculate_scores(self, request, queryset):
        """批量重新计算分数"""
        count = 0
        for record in queryset:
            # 只重新计算已完成的记录
            if record.status in ['completed', 'timeout']:
                record.calculate_score()
                count += 1
        self.message_user(request, f'成功重新计算了 {count} 条记录的分数')
    recalculate_scores.short_description = '重新计算分数'
    
    def export_best_scores_by_quiz(self, request, queryset):
        """导出每个用户在所选竞赛中的最高成绩"""
        import csv
        from django.http import HttpResponse
        import datetime
        from django.db.models import Max
        
        # 获取涉及的竞赛
        quiz_ids = queryset.values_list('quiz_id', flat=True).distinct()
        
        if not quiz_ids:
            self.message_user(request, '没有选择任何记录', level='error')
            return
        
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="quiz_best_scores_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['竞赛名称', '用户名', '真实姓名', '学院/部门', '学号/工号', '联系方式', '最高分', '总分', '得分率(%)', '是否及格', '答题用时', '提交时间', '排名'])
        
        for quiz_id in quiz_ids:
            quiz = Quiz.objects.get(id=quiz_id)
            
            # 获取每个用户的最高分记录
            user_best_scores = QuizRecord.objects.filter(
                quiz_id=quiz_id,
                status__in=['completed', 'timeout']
            ).values('user').annotate(
                best_score=Max('score')
            ).order_by('-best_score')
            
            # 构建用户最高分字典
            user_scores = {item['user']: item['best_score'] for item in user_best_scores}
            
            # 获取每个用户最高分对应的详细记录（取最早的）
            for user_id, best_score in user_scores.items():
                record = QuizRecord.objects.filter(
                    quiz_id=quiz_id,
                    user_id=user_id,
                    score=best_score,
                    status__in=['completed', 'timeout']
                ).select_related('user', 'quiz').order_by('submit_time').first()
                
                if record:
                    # 计算排名
                    rank = quiz.get_user_rank(record.user)
                    
                    # 计算用时
                    duration = '-'
                    if record.submit_time:
                        duration_seconds = int((record.submit_time - record.start_time).total_seconds())
                        minutes = duration_seconds // 60
                        seconds = duration_seconds % 60
                        duration = f'{minutes}:{seconds:02d}'
                    
                    # 计算得分率
                    score_rate = (float(record.score) / float(quiz.total_score) * 100) if quiz.total_score > 0 else 0
                    
                    # 判断是否及格
                    is_passed = '-'
                    if quiz.enable_pass_score:
                        is_passed = '及格' if record.score >= quiz.pass_score else '不及格'
                    
                    # 获取用户敏感信息（已自动解密）
                    real_name = record.user.real_name if record.user.real_name else '-'
                    department = record.user.department if record.user.department else '-'
                    student_id = record.user.student_id if record.user.student_id else '-'
                    phones = record.user.phones if record.user.phones else '-'
                    
                    writer.writerow([
                        quiz.title,
                        record.user.username,
                        real_name,
                        department,
                        student_id,
                        phones,
                        float(record.score),
                        float(quiz.total_score),
                        f'{score_rate:.2f}',
                        is_passed,
                        duration,
                        record.submit_time.strftime('%Y-%m-%d %H:%M:%S') if record.submit_time else '-',
                        rank if rank else '-'
                    ])
        
        self.message_user(request, f'成功导出了 {len(quiz_ids)} 个竞赛的最高分数据')
        return response
    export_best_scores_by_quiz.short_description = '导出所选竞赛的用户最高成绩'


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    """用户答案管理"""
    list_display = [
        'id',
        'record_user',
        'question_short',
        'answer_display',
        'score_display',
        'is_correct_display',
        'created_at'
    ]
    list_filter = [
        'is_correct',
        'question__question_type',
        ('manual_score', admin.EmptyFieldListFilter),
        'created_at'
    ]
    search_fields = ['record__user__username', 'question__content', 'text_answer']
    readonly_fields = [
        'record_display',
        'question_display',
        'is_correct',
        'created_at',
        'answer_content_display',
        'correct_answer_display',
        'review_info_display'
    ]
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('基本信息', {
            'fields': ('record_display', 'question_display')
        }),
        ('答题结果', {
            'fields': ('answer_content_display', 'correct_answer_display', 'is_correct')
        }),
        ('批改信息', {
            'fields': ('review_info_display',),
            'classes': ('collapse',)
        }),
        ('时间信息', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def record_user(self, obj):
        """答题用户"""
        return obj.record.user.username
    record_user.short_description = '答题用户'
    
    def record_display(self, obj):
        """详情页显示答题记录信息（只读）"""
        record = obj.record
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #007bff;">'
            '<strong>答题记录 ID:</strong> #{}<br>'
            '<strong>用户:</strong> {}<br>'
            '<strong>试卷:</strong> {}<br>'
            '<strong>状态:</strong> {}<br>'
            '<strong>开始时间:</strong> {}'
            '</div>',
            record.id,
            record.user.username,
            record.quiz.title,
            record.get_status_display(),
            record.start_time.strftime('%Y-%m-%d %H:%M:%S')
        )
    record_display.short_description = '答题记录'
    
    def question_display(self, obj):
        """详情页显示题目信息（只读）"""
        question = obj.question
        type_colors = {
            'single': '#28a745',
            'multiple': '#007bff',
            'judge': '#ffc107',
            'fill_blank': '#17a2b8',
            'essay': '#fd7e14'
        }
        type_labels = {
            'single': '单选题',
            'multiple': '多选题',
            'judge': '判断题',
            'fill_blank': '填空题',
            'essay': '简答题'
        }
        color = type_colors.get(question.question_type, '#6c757d')
        label = type_labels.get(question.question_type, '未知')
        
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid {};">'
            '<div style="margin-bottom: 8px;">'
            '<span style="background: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 12px;">{}</span> '
            '<strong>题目 ID:</strong> #{}'
            '</div>'
            '<div style="font-size: 14px; line-height: 1.6;">{}</div>'
            '<div style="margin-top: 8px; color: #6c757d; font-size: 12px;">'
            '<strong>分数:</strong> {} 分 | <strong>难度:</strong> {} | <strong>分类:</strong> {}'
            '</div>'
            '</div>',
            color,
            color, label, question.id,
            question.content,
            question.score,
            question.get_difficulty_display(),
            question.category or '-'
        )
    question_display.short_description = '题目'
    
    def question_short(self, obj):
        """题目简短显示"""
        content = obj.question.content[:50] + '...' if len(obj.question.content) > 50 else obj.question.content
        # 显示题目类型标签
        type_colors = {
            'single': '#28a745',
            'multiple': '#007bff', 
            'judge': '#ffc107',
            'fill_blank': '#17a2b8',
            'essay': '#fd7e14'
        }
        type_labels = {
            'single': '单选',
            'multiple': '多选',
            'judge': '判断',
            'fill_blank': '填空',
            'essay': '简答'
        }
        color = type_colors.get(obj.question.question_type, '#6c757d')
        label = type_labels.get(obj.question.question_type, '未知')
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; margin-right: 5px;">{}</span> {}',
            color, label, content
        )
    question_short.short_description = '题目'
    
    def answer_display(self, obj):
        """列表页显示答案"""
        if obj.question.question_type in ['fill_blank', 'essay']:
            # 文本答案
            if obj.text_answer:
                answer_preview = obj.text_answer[:30] + '...' if len(obj.text_answer) > 30 else obj.text_answer
                return format_html('<span style="color: #495057;">{}</span>', answer_preview)
            return format_html('<span style="color: #999;">未作答</span>')
        else:
            # 选项答案
            options = obj.selected_options.all().order_by('order')
            if options:
                option_parts = []
                for opt in options:
                    color = '#28a745' if opt.is_correct else '#dc3545'
                    option_parts.append(f'<span style="color: {color}; font-weight: bold;">{opt.order}</span>')
                return format_html(', '.join(option_parts))
            return '-'
    answer_display.short_description = '答案'
    
    def score_display(self, obj):
        """分数显示"""
        if obj.question.question_type in ['fill_blank', 'essay']:
            if obj.manual_score is not None:
                # 已批改
                percentage = (obj.manual_score / obj.question.score * 100) if obj.question.score > 0 else 0
                if percentage == 100:
                    color = '#28a745'
                    icon = '✓'
                elif percentage > 0:
                    color = '#ffc107'
                    icon = '◐'
                else:
                    color = '#dc3545'
                    icon = '✗'
                return format_html(
                    '<span style="color: {}; font-weight: bold;">{} {}/{}</span>',
                    color, icon, obj.manual_score, obj.question.score
                )
            else:
                # 待批改
                return format_html('<span style="color: #6c757d;">⏳ 待批改</span>')
        else:
            # 客观题
            score = obj.question.score if obj.is_correct else 0
            return format_html(
                '<span style="color: {};">{}/{}</span>',
                '#28a745' if obj.is_correct else '#dc3545',
                score, obj.question.score
            )
    score_display.short_description = '得分'
    
    def is_correct_display(self, obj):
        """正确性显示"""
        if obj.question.question_type in ['fill_blank', 'essay']:
            if obj.manual_score is not None:
                if obj.manual_score == obj.question.score:
                    return format_html('<span style="color: green; font-weight: bold;">✓ 满分</span>')
                elif obj.manual_score > 0:
                    return format_html('<span style="color: orange; font-weight: bold;">◐ 部分</span>')
                else:
                    return format_html('<span style="color: red; font-weight: bold;">✗ 未得分</span>')
            else:
                return format_html('<span style="color: gray;">⏳ 待批改</span>')
        else:
            if obj.is_correct:
                return format_html('<span style="color: green; font-weight: bold;">✓ 正确</span>')
            return format_html('<span style="color: red; font-weight: bold;">✗ 错误</span>')
    is_correct_display.short_description = '状态'
    
    def answer_content_display(self, obj):
        """详情页显示答案内容"""
        if obj.question.question_type in ['fill_blank', 'essay']:
            # 文本答案
            if obj.text_answer:
                return format_html(
                    '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #007bff; white-space: pre-wrap;">{}</div>',
                    obj.text_answer
                )
            return format_html('<span style="color: #999;">未作答</span>')
        else:
            # 选项答案
            options = obj.selected_options.all().order_by('order')
            if not options:
                return format_html('<span style="color: #999;">未选择</span>')
            
            html_parts = []
            for opt in options:
                color = '#28a745' if opt.is_correct else '#dc3545'
                icon = '✓' if opt.is_correct else '✗'
                html_parts.append(
                    f'<div style="padding: 8px; margin: 4px 0; background: #f8f9fa; border-left: 3px solid {color};">'
                    f'<span style="color: {color}; font-weight: bold;">[{icon}] {opt.order}.</span> {opt.content}'
                    f'</div>'
                )
            return format_html(''.join(html_parts))
    answer_content_display.short_description = '用户答案'
    
    def correct_answer_display(self, obj):
        """显示正确答案"""
        if obj.question.question_type in ['fill_blank', 'essay']:
            # 参考答案
            if obj.question.standard_answer:
                return format_html(
                    '<div style="padding: 10px; background: #d4edda; border-left: 3px solid #28a745; white-space: pre-wrap;">{}</div>',
                    obj.question.standard_answer
                )
            return format_html('<span style="color: #999;">无参考答案</span>')
        else:
            # 选项答案
            correct_options = obj.question.options.filter(is_correct=True).order_by('order')
            if not correct_options:
                return format_html('<span style="color: #999;">无正确答案</span>')
            
            html_parts = []
            for opt in correct_options:
                html_parts.append(
                    f'<div style="padding: 8px; margin: 4px 0; background: #d4edda; border-left: 3px solid #28a745;">'
                    f'<span style="color: #28a745; font-weight: bold;">[✓] {opt.order}.</span> {opt.content}'
                    f'</div>'
                )
            return format_html(''.join(html_parts))
    correct_answer_display.short_description = '正确/参考答案'
    
    def review_info_display(self, obj):
        """显示批改信息"""
        if obj.question.question_type not in ['fill_blank', 'essay']:
            return format_html('<span style="color: #999;">客观题无需批改</span>')
        
        if obj.manual_score is None:
            return format_html(
                '<div style="padding: 10px; background: #fff3cd; border-left: 3px solid #ffc107;">'
                '<strong style="color: #856404;">⏳ 待批改</strong>'
                '</div>'
            )
        
        return format_html(
            '<div style="padding: 10px; background: #d1ecf1; border-left: 3px solid #17a2b8;">'
            '<div style="margin-bottom: 8px;"><strong>得分:</strong> <span style="color: #0c5460; font-size: 16px; font-weight: bold;">{}</span> / {} 分</div>'
            '<div style="margin-bottom: 8px;"><strong>批改人:</strong> {}</div>'
            '<div style="margin-bottom: 8px;"><strong>批改时间:</strong> {}</div>'
            '{}'
            '</div>',
            obj.manual_score,
            obj.question.score,
            obj.reviewer.username if obj.reviewer else '-',
            obj.reviewed_at.strftime('%Y-%m-%d %H:%M:%S') if obj.reviewed_at else '-',
            f'<div style="margin-top: 8px;"><strong>评语:</strong><div style="margin-top: 4px; padding: 8px; background: white; border-radius: 4px; white-space: pre-wrap;">{obj.review_comment}</div></div>' if obj.review_comment else ''
        )
    review_info_display.short_description = '批改信息'


@admin.register(QuizRegistration)
class QuizRegistrationAdmin(admin.ModelAdmin):
    """竞赛报名管理"""
    list_display = [
        'id',
        'quiz_title',
        'user_display',
        'student_id',
        'name',
        'role',
        'phone',
        'status_display',
        'created_at'
    ]
    list_filter = ['status', 'created_at', 'quiz']
    search_fields = ['user__username', 'user__real_name', 'user__student_id', 'quiz__title']
    readonly_fields = ['created_at', 'updated_at', 'student_id', 'name', 'role', 'phone']
    raw_id_fields = ['user', 'quiz']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('基本信息', {
            'fields': ('quiz', 'user', 'status')
        }),
        ('用户信息', {
            'fields': ('student_id', 'name', 'role', 'phone'),
            'description': '以下信息从用户资料中自动获取'
        }),
        ('时间信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['approve_registrations', 'reject_registrations', 'export_registrations']
    
    def quiz_title(self, obj):
        """竞赛名称"""
        return obj.quiz.title
    quiz_title.short_description = '竞赛'
    
    def user_display(self, obj):
        """用户显示"""
        return format_html('<strong>{}</strong>', obj.user.username)
    user_display.short_description = '用户名'
    
    def student_id(self, obj):
        """学号/工号"""
        return obj.student_id or '-'
    student_id.short_description = '学号/工号'
    
    def name(self, obj):
        """真实姓名"""
        return obj.name or '-'
    name.short_description = '真实姓名'
    
    def role(self, obj):
        """学院/部门"""
        return obj.role or '-'
    role.short_description = '学院/部门'
    
    def phone(self, obj):
        """联系方式"""
        return obj.phone or '-'
    phone.short_description = '联系方式'
    
    def status_display(self, obj):
        """状态显示"""
        colors = {
            'pending': '#ffc107',
            'approved': '#28a745',
            'rejected': '#dc3545'
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_display.short_description = '审核状态'
    
    def approve_registrations(self, request, queryset):
        """批量通过报名"""
        count = queryset.update(status='approved')
        self.message_user(request, f'成功通过了 {count} 条报名申请')
    approve_registrations.short_description = '批量通过报名'
    
    def reject_registrations(self, request, queryset):
        """批量拒绝报名"""
        count = queryset.update(status='rejected')
        self.message_user(request, f'成功拒绝了 {count} 条报名申请')
    reject_registrations.short_description = '批量拒绝报名'
    
    def export_registrations(self, request, queryset):
        """导出报名信息"""
        import csv
        from django.http import HttpResponse
        import datetime
        
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="quiz_registrations_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['竞赛名称', '用户名', '真实姓名', '学号/工号', '学院/部门', '联系方式', '审核状态', '报名时间'])
        
        for reg in queryset.select_related('quiz', 'user'):
            writer.writerow([
                reg.quiz.title,
                reg.user.username,
                reg.name,
                reg.student_id,
                reg.role,
                reg.phone,
                reg.get_status_display(),
                reg.created_at.strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        self.message_user(request, f'成功导出了 {queryset.count()} 条报名记录')
        return response
    export_registrations.short_description = '导出报名信息（CSV）'
