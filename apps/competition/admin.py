from django.contrib import admin
from competition.models import Competition, Team, ScoreTeam, ScoreUser, CheatingLog,Registration,Submission,Challenge,Tag,Writeup,WriteupTemplate,CombinedLeaderboard,LeaderboardCalculationTask
from django import forms
from django.contrib.sites.models import Site
from django.utils.html import format_html
from django.urls import reverse
from public.utils import site_full_url
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils import timezone
from comment.models import SystemNotification
import logging

logger = logging.getLogger('apps.competition')
User = get_user_model()

class CompetitionAdminForm(forms.ModelForm):
    """竞赛管理表单"""
    
    class Meta:
        model = Competition
        fields = '__all__'
        help_texts = {
            'related_quiz': '关联知识竞赛后，将自动生成综合排行榜',
            'combined_score_ctf_weight': '⚠️ 仅在关联知识竞赛时生效。CTF所占权重（0-1之间）',
            'combined_score_top_percent': '⚠️ 仅在关联知识竞赛时生效。取前百分之几的平均分作为归一化基准'
        }

class RegistrationForm(forms.ModelForm):
    class Meta:
        model = Registration
        fields = '__all__'  # 或者列出您想要的字段

    def clean(self):
        cleaned_data = super().clean()
        registration_type = cleaned_data.get('registration_type')
        team_name = cleaned_data.get('team_name')
        competition = cleaned_data.get('competition')

        # 验证个人报名时不填写队伍名称
        if registration_type == Registration.INDIVIDUAL and team_name is not None:
            self.add_error('team_name', '个人报名不需要填写队伍名称')

        # 验证团队报名时必须填写队伍名称
        if registration_type == Registration.TEAM and team_name is None:
            self.add_error('team_name', '团队报名需要填写队伍名称')

        # 确保比赛类型与报名类型一致
        if competition:
            if competition.competition_type == 'team' and registration_type == Registration.INDIVIDUAL:
                raise forms.ValidationError('团队赛不允许个人报名')
            elif competition.competition_type == 'individual' and registration_type == Registration.TEAM:
                raise forms.ValidationError('个人赛不允许团队报名')

        return cleaned_data

@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    form = CompetitionAdminForm
    
    list_display = (
        'title', 
        'competition_type_display', 
        'visibility_type_display',
        'start_time', 
        'end_time', 
        'is_active',
        'is_register',
        'is_audit',
        'get_challenge_types',
        'author',
        'competition_link',  # 添加比赛链接
        'registration_link',   # 添加报名链接
    )
    list_filter = (
        'competition_type',
        'visibility_type',
        'is_active',
        'is_register',
        'is_audit',
        'theme',
        'dashboard_template',
        'start_time',
        'author',
    )
    list_editable = ('is_active', 'is_register', 'is_audit')
    filter_horizontal = ('challenges', )
    search_fields = ('title', 'author__username', 'invitation_code')
    date_hierarchy = 'start_time'
    # 移除 prepopulated_fields，改为在模型 save() 中自动生成（支持中文转拼音）
    # prepopulated_fields = {'slug': ('title',)}
    
    fieldsets = (
        ('基本信息', {
            'fields': ('title', 'description', 'img_link', 'author')
        }),
        ('时间设置', {
            'fields': ('start_time', 'end_time')
        }),
        ('比赛配置', {
            'fields': ('competition_type', 'team_max_members', 'visibility_type', 'is_audit', 'invitation_code')
        }),
        ('题目管理', {
            'fields': ('challenges',),
            'description': '注意：题目选择器中只显示未被其他比赛使用的题目。如需使用已被占用的题目，请先从原比赛中移除该题目。'
        }),
        ('前端配置', {
            'fields': ('theme', 'dashboard_template', 'slug', 're_slug')
        }),
        ('状态管理', {
            'fields': ('is_active', 'is_register')
        }),
        ('综合排行榜配置', {
            'fields': ('related_quiz', 'combined_score_ctf_weight', 'combined_score_top_percent'),
            'classes': ('collapse',),
            'description': '关联知识竞赛后，将自动生成综合排行榜（CTF+知识竞赛）'
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        """设置表单初始值"""
        form = super().get_form(request, obj, **kwargs)
        # 创建新题目时，默认设置作者为当前用户
        if not obj and 'author' in form.base_fields:
            form.base_fields['author'].initial = request.user
        return form

    def competition_type_display(self, obj):
        return dict(Competition.COMPETITION_TYPE_CHOICES).get(obj.competition_type, '未知类型')
    competition_type_display.short_description = '比赛类型'  # 自定义列标题

    def visibility_type_display(self, obj):
        return dict(Competition.COMPETITION_VISIBILITY_CHOICES).get(obj.visibility_type, '未知类型')
    visibility_type_display.short_description = '公开类型'

    def get_challenge_types(self, obj):
        return obj.get_challenge_types()
    get_challenge_types.short_description = '题目类型'  # 自定义列标题

    def competition_link(self, obj):
        # 获取当前站点的域名
        url = f"{site_full_url()}{reverse('competition:competition_detail', args=[obj.slug])}"
        return format_html('<a href="{}" target="_blank">查看比赛</a>', url)
    competition_link.short_description = '比赛链接'  # 自定义列标题

    def registration_link(self, obj):
        # 获取当前站点的域名
        url = f"{site_full_url()}{reverse('competition:registration_detail', args=[obj.slug, obj.re_slug])}"
        return format_html('<a href="{}" target="_blank">报名链接</a>', url)
    registration_link.short_description = '报名链接'  # 自定义列标题

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "challenges":
            # 获取当前正在编辑的比赛ID（如果是新建则为None）
            competition_id = request.resolver_match.kwargs.get('object_id')
            
            # 获取所有激活的题目
            active_challenges = Challenge.objects.filter(is_active=True)
            
            if competition_id:
                # 编辑模式：排除已被其他比赛使用的题目（但包含当前比赛已有的题目）
                current_competition = Competition.objects.get(pk=competition_id)
                current_challenge_ids = current_competition.challenges.values_list('id', flat=True)
                
                # 获取已被其他比赛使用的题目ID
                used_challenge_ids = Competition.objects.exclude(
                    pk=competition_id
                ).values_list('challenges__id', flat=True).distinct()
                
                # 排除已被其他比赛使用的题目，但保留当前比赛已有的题目
                available_challenges = active_challenges.exclude(
                    id__in=used_challenge_ids
                ) | active_challenges.filter(id__in=current_challenge_ids)
                
                kwargs["queryset"] = available_challenges.distinct()
            else:
                # 新建模式：排除所有已被使用的题目
                used_challenge_ids = Competition.objects.values_list(
                    'challenges__id', flat=True
                ).distinct()
                
                kwargs["queryset"] = active_challenges.exclude(id__in=used_challenge_ids)
                
        return super().formfield_for_manytomany(db_field, request, **kwargs)

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'team_code','competition', 'leader', 'get_members', 'member_count', 'get_current_members', 'created_at')
    list_filter = ('competition',)  # 添加按比赛筛选
    search_fields = ('name', 'leader__username', 'competition__title','team_code',)  # 扩展搜索字段
    autocomplete_fields = ['leader', 'members']

    def get_members(self, obj):
        # 返回队员名称的字符串
        return ", ".join([member.username for member in obj.members.all()])
    get_members.short_description = '队员名称'

    def get_current_members(self, obj):
        # 返回当前队员数量
        return obj.members.count()
    get_current_members.short_description = '当前队员数量'

    def save_model(self, request, obj, form, change):
        # 先调用父类的 save_model 保存对象
        super().save_model(request, obj, form, change)
        
        # 如果是新建队伍，自动将队长添加为队员
        if not change:
            obj.members.add(obj.leader)
        
        # 检查队伍成员数量
        if obj.members.count() > obj.member_count:
            from django.contrib import messages
            messages.warning(request, f'队伍成员数量已超过设定的最大数量 {obj.member_count} 人')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # 在选择队长时，过滤掉已经是其他队伍队长的用户
        if db_field.name == "leader":
            # 获取当前比赛 ID
            competition_id = request.GET.get('competition')
            if not competition_id:
                # 如果是编辑模式，从 URL 中获取队伍 ID
                match = request.resolver_match
                if match and match.kwargs.get('object_id'):
                    try:
                        team = Team.objects.get(pk=match.kwargs['object_id'])
                        competition_id = team.competition_id
                    except Team.DoesNotExist:
                        pass
            
            # 如果有比赛 ID，过滤掉该比赛中已经是其他队伍队长的用户
            if competition_id:
                kwargs["queryset"] = User.objects.exclude(
                    led_teams__competition_id=competition_id
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is None:  # 新建队伍时
            form.base_fields['member_count'].initial = 4  # 设置默认成员数量
        return form
    

class ScoreTeamAdminForm(forms.ModelForm):
    """队伍积分管理表单"""
    change_reason = forms.CharField(
        label='修改原因',
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '请输入修改分数的原因（将通知队伍成员）...'}),
        required=False,
        help_text='修改分数时必须填写原因，系统会自动通知队伍所有成员'
    )
    
    class Meta:
        model = ScoreTeam
        fields = '__all__'
    
    def clean(self):
        cleaned_data = super().clean()
        # 如果是修改现有记录且分数发生变化
        if self.instance.pk:
            old_score = ScoreTeam.objects.get(pk=self.instance.pk).score
            new_score = cleaned_data.get('score')
            if old_score != new_score and not cleaned_data.get('change_reason'):
                raise forms.ValidationError('修改分数时必须填写修改原因！')
        return cleaned_data


@admin.register(ScoreTeam)
class ScoreTeamAdmin(admin.ModelAdmin):
    """队伍积分记录管理"""
    form = ScoreTeamAdminForm
    list_display = ('team_display', 'competition', 'rank_display', 'score_display', 'time')
    list_filter = ('competition', 'rank')
    search_fields = ('team__name', 'competition__title')
    readonly_fields = ['team_info', 'competition_info', 'time']
    ordering = ['competition', 'rank']
    
    fieldsets = (
        ('基本信息', {
            'fields': ('team_info', 'competition_info')
        }),
        ('排名与得分', {
            'fields': ('rank', 'score', 'time')
        }),
        ('修改原因', {
            'fields': ('change_reason',),
            'classes': ('collapse',),
            'description': '⚠️ 修改分数时必须填写原因，系统会自动通知队伍成员'
        }),
    )
    
    def save_model(self, request, obj, form, change):
        """保存模型时处理分数修改逻辑"""
        if change:  # 如果是修改现有记录
            old_obj = ScoreTeam.objects.get(pk=obj.pk)
            old_score = old_obj.score
            new_score = obj.score
            
            if old_score != new_score:
                change_reason = form.cleaned_data.get('change_reason', '管理员调整')
                score_diff = new_score - old_score
                
                # 保存新分数
                super().save_model(request, obj, form, change)
                
                # 如果是团队赛，需要更新团队成员的个人分数（平均分配差值）
                if obj.competition.competition_type == 'team':
                    team_members = list(obj.team.members.all())
                    member_count = len(team_members)
                    
                    if member_count > 0:
                        # 使用整数除法，确保分数精确
                        base_diff = score_diff // member_count  # 基础分数差值
                        remainder = score_diff % member_count     # 余数
                        
                        # 更新每个成员的分数
                        for index, member in enumerate(team_members):
                            # 前remainder个成员多分配1分，确保总和准确
                            member_diff = base_diff + (1 if index < remainder else 0)
                            
                            try:
                                user_score = ScoreUser.objects.get(
                                    user=member,
                                    competition=obj.competition
                                )
                                user_score.points += member_diff
                                user_score.save()
                            except ScoreUser.DoesNotExist:
                                # 如果成员没有分数记录，创建一个
                                ScoreUser.objects.create(
                                    user=member,
                                    team=obj.team,
                                    competition=obj.competition,
                                    points=member_diff,
                                    rank=0
                                )
                
                # 发送通知给队伍所有成员
                from django.utils.html import escape
                
                notification = SystemNotification.objects.create(
                    title='队伍分数调整通知',
                    content=f'''
                        <div style="padding: 12px; background: #f8f9fa; border-radius: 4px; font-size: 0.9rem;">
                            <p style="margin: 0 0 8px 0;"><strong>队伍：</strong>{escape(obj.team.name)} | <strong>竞赛：</strong>{escape(obj.competition.title)}</p>
                            <p style="margin: 0 0 8px 0;"><strong>分数变化：</strong>{old_score} → {new_score} <span style="color: {'#28a745' if score_diff > 0 else '#dc3545'}; font-weight: bold;">({'+' if score_diff > 0 else ''}{score_diff})</span></p>
                            <p style="margin: 0;"><strong>原因：</strong>{escape(change_reason)}</p>
                        </div>
                    '''

                    
                )
                
                # 通知队伍所有成员
                for member in obj.team.members.all():
                    notification.get_p.add(member)
                
                # 清除团队分数缓存
                try:
                    from competition.views import clear_competition_ranking_cache
                    clear_competition_ranking_cache(
                        obj.competition.id,
                        is_team=True,
                        team_id=obj.team.id
                    )
                except Exception as e:
                    logger.error(f'清除缓存失败: {e}', exc_info=True)
                
                # 如果比赛关联了知识竞赛，触发综合排行榜重新计算
                if obj.competition.related_quiz and timezone.now() > obj.competition.end_time:
                    try:
                        from competition.utils_optimized import CombinedLeaderboardCalculator
                        
                        # 使用 force_recreate=True 强制重建（自动清理旧数据和任务）
                        calculator = CombinedLeaderboardCalculator(obj.competition, obj.competition.related_quiz)
                        result = calculator.calculate_leaderboard_with_lock(force=True, force_recreate=True)
                        
                        if result.get('success'):
                            combined_msg = '，综合排行榜已自动更新'
                        else:
                            combined_msg = f'，但综合排行榜更新失败：{result.get("message", "未知错误")}'
                    except Exception as e:
                        combined_msg = f'，但综合排行榜更新失败：{str(e)}'
                        logger.error(f'更新综合排行榜失败: {e}', exc_info=True)
                else:
                    combined_msg = ''
                
                self.message_user(
                    request, 
                    f' 队伍 "{obj.team.name}" 分数已调整（{old_score} → {new_score}），已通知队伍所有成员{combined_msg}',
                    level='success'
                )
                return
        
        super().save_model(request, obj, form, change)
    
    def has_add_permission(self, request):
        """禁止添加（由系统自动计算）"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """禁止删除（保留积分历史）"""
        return request.user.is_superuser
    
    def team_info(self, obj):
        """队伍信息展示"""
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #52c41a;">'
            '<strong style="font-size: 15px;">👥 {}</strong><br>'
            '<span style="color: #6c757d; font-size: 12px;">队伍 ID: #{}</span>'
            '</div>',
            obj.team.name,
            obj.team.id
        )
    team_info.short_description = '队伍'
    
    def competition_info(self, obj):
        """竞赛信息展示"""
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #722ed1;">'
            '<strong style="font-size: 15px;">🏆 {}</strong><br>'
            '<span style="color: #6c757d; font-size: 12px;">竞赛 ID: #{}</span>'
            '</div>',
            obj.competition.title,
            obj.competition.id
        )
    competition_info.short_description = '竞赛'
    
    def team_display(self, obj):
        """列表页队伍显示"""
        return format_html('<strong>{}</strong>', obj.team.name)
    team_display.short_description = '队伍'
    team_display.admin_order_field = 'team__name'
    
    def rank_display(self, obj):
        """排名显示（带奖牌）"""
        if obj.rank == 1:
            return format_html('<span style="font-size: 18px;">🥇</span> <strong>1</strong>')
        elif obj.rank == 2:
            return format_html('<span style="font-size: 18px;">🥈</span> <strong>2</strong>')
        elif obj.rank == 3:
            return format_html('<span style="font-size: 18px;">🥉</span> <strong>3</strong>')
        else:
            return format_html('<strong>#{}</strong>', obj.rank)
    rank_display.short_description = '排名'
    rank_display.admin_order_field = 'rank'
    
    def score_display(self, obj):
        """分数显示"""
        return format_html(
            '<span style="color: #1890ff; font-weight: bold; font-size: 14px;">{}</span>',
            obj.score
        )
    score_display.short_description = '得分'
    score_display.admin_order_field = 'score'
    
    def get_queryset(self, request):
        """优化查询性能"""
        return super().get_queryset(request).select_related('team', 'competition')



@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    form = RegistrationForm
    list_display = ('user', 'get_name', 'get_student_id', 'get_role', 'competition', 'registration_type', 'get_phone', 'team_name', 'created_at', 'is_audit', 'audit', 'audit_comment')
    list_filter = ('competition', 'registration_type')
    search_fields = ('user__username', 'team_name__name', 'competition__title')

    def get_queryset(self, request):
        # 只显示当前用户的报名记录
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)
    
    def get_name(self, obj):
        """显示真实姓名（从用户模型获取）"""
        return obj.name or '-'
    get_name.short_description = '真实姓名'
    
    def get_student_id(self, obj):
        """显示学号/工号（从用户模型获取）"""
        return obj.student_id or '-'
    get_student_id.short_description = '学号/工号'
    
    def get_role(self, obj):
        """显示学院/部门（从用户模型获取）"""
        return obj.role or '-'
    get_role.short_description = '学院/部门'
    
    def get_phone(self, obj):
        """显示联系方式（从用户模型获取）"""
        return obj.phone or '-'
    get_phone.short_description = '联系方式'

    

    

class ScoreUserAdminForm(forms.ModelForm):
    """用户积分管理表单"""
    change_reason = forms.CharField(
        label='修改原因',
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '请输入修改分数的原因（将通知用户）...'}),
        required=False,
        help_text='修改分数时必须填写原因，系统会自动通知用户'
    )
    
    class Meta:
        model = ScoreUser
        fields = '__all__'
    
    def clean(self):
        cleaned_data = super().clean()
        # 如果是修改现有记录且分数发生变化
        if self.instance.pk:
            old_points = ScoreUser.objects.get(pk=self.instance.pk).points
            new_points = cleaned_data.get('points')
            if old_points != new_points and not cleaned_data.get('change_reason'):
                raise forms.ValidationError('修改分数时必须填写修改原因！')
        return cleaned_data


@admin.register(ScoreUser)
class ScoreUserAdmin(admin.ModelAdmin):
    """用户积分记录管理"""
    form = ScoreUserAdminForm
    list_display = ('user_display', 'team_display', 'points_display', 'rank_display', 'competition', 'created_at')
    list_filter = ('competition', 'rank', 'created_at')
    search_fields = ('user__username', 'team__name', 'competition__title')
    readonly_fields = ['user_info', 'team_info', 'competition_info', 'created_at']
    ordering = ['competition', 'rank']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('基本信息', {
            'fields': ('user_info', 'team_info', 'competition_info')
        }),
        ('排名与得分', {
            'fields': ('rank', 'points', 'created_at')
        }),
        ('修改原因', {
            'fields': ('change_reason',),
            'classes': ('collapse',),
            'description': '⚠️ 修改分数时必须填写原因，系统会自动通知用户'
        }),
    )
    
    def save_model(self, request, obj, form, change):
        """保存模型时处理分数修改逻辑"""
        if change:  # 如果是修改现有记录
            old_obj = ScoreUser.objects.get(pk=obj.pk)
            old_points = old_obj.points
            new_points = obj.points
            
            if old_points != new_points:
                change_reason = form.cleaned_data.get('change_reason', '管理员调整')
                points_diff = new_points - old_points
                
                # 保存新分数
                super().save_model(request, obj, form, change)
                
                # 如果是团队赛且用户有队伍，需要更新团队总分
                if obj.competition.competition_type == 'team' and obj.team:
                    try:
                        team_score = ScoreTeam.objects.get(
                            team=obj.team,
                            competition=obj.competition
                        )
                        team_score.score += points_diff
                        team_score.save()
                        
                        team_info_msg = f'，同时已更新队伍 "{obj.team.name}" 的分数'
                    except ScoreTeam.DoesNotExist:
                        team_info_msg = ''
                else:
                    team_info_msg = ''
                
                # 发送通知给用户
         
                from django.utils.html import escape
                
                notification = SystemNotification.objects.create(
                    title='个人分数调整通知',
                    content=f'''
                        <div style="padding: 12px; background: #f8f9fa; border-radius: 4px; font-size: 0.9rem;">
                            <p style="margin: 0 0 8px 0;"><strong>用户：</strong>{escape(obj.user.username)} | <strong>竞赛：</strong>{escape(obj.competition.title)}</p>
                            <p style="margin: 0 0 8px 0;"><strong>分数变化：</strong>{old_points} → {new_points} <span style="color: {'#28a745' if points_diff > 0 else '#dc3545'}; font-weight: bold;">({'+' if points_diff > 0 else ''}{points_diff})</span></p>
                            <p style="margin: 0;"><strong>原因：</strong>{escape(change_reason)}</p>
                        </div>
                    '''
                )
                notification.get_p.add(obj.user)
                
                # 清除缓存
                try:
                    from competition.views import clear_competition_ranking_cache
                    if obj.competition.competition_type == 'team' and obj.team:
                        # 团队赛：清除团队缓存
                        clear_competition_ranking_cache(
                            obj.competition.id,
                            is_team=True,
                            team_id=obj.team.id
                        )
                    else:
                        # 个人赛：清除个人缓存
                        clear_competition_ranking_cache(
                            obj.competition.id,
                            is_team=False,
                            user_id=obj.user.id
                        )
                except Exception as e:
                    logger.error(f'清除缓存失败: {e}', exc_info=True)
                
                # 如果比赛关联了知识竞赛，触发综合排行榜重新计算
                if obj.competition.related_quiz and timezone.now() > obj.competition.end_time:
                    try:
                        from competition.utils_optimized import CombinedLeaderboardCalculator
                        
                        # 使用 force_recreate=True 强制重建（自动清理旧数据和任务）
                        calculator = CombinedLeaderboardCalculator(obj.competition, obj.competition.related_quiz)
                        result = calculator.calculate_leaderboard_with_lock(force=True, force_recreate=True)
                        
                        if result.get('success'):
                            combined_msg = '，综合排行榜已自动更新'
                        else:
                            combined_msg = f'，但综合排行榜更新失败：{result.get("message", "未知错误")}'
                    except Exception as e:
                        combined_msg = f'，但综合排行榜更新失败：{str(e)}'
                        logger.error(f'更新综合排行榜失败: {e}', exc_info=True)
                else:
                    combined_msg = ''
                
                self.message_user(
                    request, 
                    f' 用户 "{obj.user.username}" 分数已调整（{old_points} → {new_points}）{team_info_msg}，已通知用户{combined_msg}',
                    level='success'
                )
                return
        
        super().save_model(request, obj, form, change)
    
    def has_add_permission(self, request):
        """禁止添加（由系统自动计算）"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """禁止删除（保留积分历史）"""
        return request.user.is_superuser
    
    def user_info(self, obj):
        """用户信息展示"""
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #17a2b8;">'
            '<strong style="font-size: 15px;">👤 {}</strong><br>'
            '<span style="color: #6c757d; font-size: 12px;">用户 ID: #{}</span>'
            '</div>',
            obj.user.username,
            obj.user.id
        )
    user_info.short_description = '用户'
    
    def team_info(self, obj):
        """队伍信息展示"""
        if obj.team:
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #52c41a;">'
                '<strong style="font-size: 15px;">👥 {}</strong><br>'
                '<span style="color: #6c757d; font-size: 12px;">队伍 ID: #{}</span>'
                '</div>',
                obj.team.name,
                obj.team.id
            )
        return format_html('<span style="color: #999;">个人参赛</span>')
    team_info.short_description = '队伍'
    
    def competition_info(self, obj):
        """竞赛信息展示"""
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #722ed1;">'
            '<strong style="font-size: 15px;">🏆 {}</strong><br>'
            '<span style="color: #6c757d; font-size: 12px;">竞赛 ID: #{}</span>'
            '</div>',
            obj.competition.title,
            obj.competition.id
        )
    competition_info.short_description = '竞赛'
    
    def user_display(self, obj):
        """列表页用户显示"""
        return format_html('<strong>{}</strong>', obj.user.username)
    user_display.short_description = '用户'
    user_display.admin_order_field = 'user__username'
    
    def team_display(self, obj):
        """列表页队伍显示"""
        if obj.team:
            return obj.team.name
        return format_html('<span style="color: #999;">个人</span>')
    team_display.short_description = '队伍'
    team_display.admin_order_field = 'team__name'
    
    def rank_display(self, obj):
        """排名显示（带奖牌）"""
        if obj.rank == 1:
            return format_html('<span style="font-size: 18px;">🥇</span> <strong>1</strong>')
        elif obj.rank == 2:
            return format_html('<span style="font-size: 18px;">🥈</span> <strong>2</strong>')
        elif obj.rank == 3:
            return format_html('<span style="font-size: 18px;">🥉</span> <strong>3</strong>')
        else:
            return format_html('<strong>#{}</strong>', obj.rank)
    rank_display.short_description = '排名'
    rank_display.admin_order_field = 'rank'
    
    def points_display(self, obj):
        """分数显示"""
        return format_html(
            '<span style="color: #1890ff; font-weight: bold; font-size: 14px;">{}</span>',
            obj.points
        )
    points_display.short_description = '得分'
    points_display.admin_order_field = 'points'
    
    def get_queryset(self, request):
        """优化查询性能"""
        return super().get_queryset(request).select_related('user', 'team', 'competition')

@admin.register(CheatingLog)
class CheatingLogAdmin(admin.ModelAdmin):
    """作弊日志管理"""
    list_display = ('user_display', 'team_display', 'competition', 'cheating_type_display', 'description_short', 'timestamp')
    list_filter = ('competition', 'cheating_type', 'timestamp')
    search_fields = ('user__username', 'team__name', 'competition__title', 'description')
    readonly_fields = ['user_info', 'team_info', 'competition_info', 'cheating_type', 'description', 'timestamp', 'detected_by']
    ordering = ['-timestamp']
    date_hierarchy = 'timestamp'
    
    fieldsets = (
        ('基本信息', {
            'fields': ('user_info', 'team_info', 'competition_info')
        }),
        ('作弊详情', {
            'fields': ('cheating_type', 'description', 'detected_by', 'timestamp'),
            'classes': ('wide',)
        }),
    )
    
    def has_add_permission(self, request):
        """禁止添加（由系统自动记录）"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """禁止删除（保留作弊日志）"""
        return request.user.is_superuser
    
    def user_info(self, obj):
        """用户信息展示"""
        return format_html(
            '<div style="padding: 10px; background: #fff1f0; border-left: 3px solid #ff4d4f;">'
            '<strong style="font-size: 15px; color: #cf1322;">⚠️ {}</strong><br>'
            '<span style="color: #6c757d; font-size: 12px;">用户 ID: #{}</span>'
            '</div>',
            obj.user.username,
            obj.user.id
        )
    user_info.short_description = '用户'
    
    def team_info(self, obj):
        """队伍信息展示"""
        if obj.team:
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #52c41a;">'
                '<strong style="font-size: 15px;">👥 {}</strong><br>'
                '<span style="color: #6c757d; font-size: 12px;">队伍 ID: #{}</span>'
                '</div>',
                obj.team.name,
                obj.team.id
            )
        return format_html('<span style="color: #999;">个人参赛</span>')
    team_info.short_description = '队伍'
    
    def competition_info(self, obj):
        """竞赛信息展示"""
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #722ed1;">'
            '<strong style="font-size: 15px;">🏆 {}</strong><br>'
            '<span style="color: #6c757d; font-size: 12px;">竞赛 ID: #{}</span>'
            '</div>',
            obj.competition.title,
            obj.competition.id
        )
    competition_info.short_description = '竞赛'
    
    def user_display(self, obj):
        """列表页用户显示"""
        return format_html(
            '<span style="color: #cf1322; font-weight: bold;">⚠️ {}</span>',
            obj.user.username
        )
    user_display.short_description = '用户'
    user_display.admin_order_field = 'user__username'
    
    def team_display(self, obj):
        """列表页队伍显示"""
        if obj.team:
            return obj.team.name
        return format_html('<span style="color: #999;">个人</span>')
    team_display.short_description = '队伍'
    team_display.admin_order_field = 'team__name'
    
    def cheating_type_display(self, obj):
        """作弊类型显示"""
        type_colors = {
            'switch_tab': '#faad14',
            'leave_page': '#ff7a45',
            'copy_paste': '#ff4d4f',
            'multiple_login': '#f5222d',
            'other': '#8c8c8c'
        }
        type_icons = {
            'switch_tab': '🔄',
            'leave_page': '🚪',
            'copy_paste': '📋',
            'multiple_login': '👥',
            'other': '❓'
        }
        color = type_colors.get(obj.cheating_type, '#8c8c8c')
        icon = type_icons.get(obj.cheating_type, '❓')
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            color,
            icon,
            obj.get_cheating_type_display()
        )
    cheating_type_display.short_description = '作弊类型'
    cheating_type_display.admin_order_field = 'cheating_type'
    
    def description_short(self, obj):
        """描述简短显示"""
        if len(obj.description) > 50:
            return obj.description[:50] + '...'
        return obj.description
    description_short.short_description = '描述'
    
    def get_queryset(self, request):
        """优化查询性能"""
        return super().get_queryset(request).select_related('user', 'team', 'competition')



@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    """提交记录管理"""
    list_display = [
        'id', 
        'challenge_title', 
        'user_info', 
        'team_name', 
        'status_badge', 
        'display_score_breakdown',
        'solve_rank',
        'ip_address', 
        'created_at',
        'competition'
    ]
    
    list_filter = [
        'status',
        ('challenge', admin.RelatedOnlyFieldListFilter),
        'created_at',
        ('team', admin.RelatedOnlyFieldListFilter),
        ('competition', admin.RelatedOnlyFieldListFilter),
    ]
    
    search_fields = [
        'user__username',
        'team__name',
        'challenge__title',
        'flag',
        'ip'
    ]
    
    # 所有字段都设置为只读（提交记录不应被修改）
    readonly_fields = [
        'challenge_display', 'user_display', 'team_display', 'competition_display', 
        'flag', 'status', 'ip', 'created_at', 
        'points_earned', 'base_score', 'blood_bonus', 
        'time_bonus', 'solve_rank', 'is_first_blood_display'
    ]
    
    ordering = ['-created_at']
    list_per_page = 20
    
    fieldsets = (
        ('基本信息', {
            'fields': (
                'challenge_display',
                'user_display',
                'team_display',
                'competition_display'
            )
        }),
        ('提交详情', {
            'fields': (
                'flag',
                'ip',
                'status',
                'created_at',
                'is_first_blood_display'
            )
        }),
        ('得分明细', {
            'fields': ('points_earned', 'base_score', 'blood_bonus', 'time_bonus', 'solve_rank'),
            'description': '总分 = 基础分 + 血榜奖励(前3名) + 时间奖励(所有人)'
        }),
    )
    
    def has_add_permission(self, request):
        """禁止添加（提交记录由系统生成）"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """禁止删除（保留提交历史）"""
        return request.user.is_superuser  # 只有超级管理员可以删除
    
    def challenge_display(self, obj):
        """题目信息展示"""
        challenge = obj.challenge
        category_colors = {
            'web': '#1890ff',
            'pwn': '#f5222d',
            'reverse': '#722ed1',
            'crypto': '#fa8c16',
            'misc': '#52c41a',
            'forensics': '#13c2c2'
        }
        color = category_colors.get(challenge.category, '#666')
        
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid {};">'
            '<div style="margin-bottom: 5px;">'
            '<strong style="font-size: 15px;">{}</strong> '
            '<span style="background: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;">{}</span>'
            '</div>'
            '<div style="color: #6c757d; font-size: 12px;">'
            '<strong>题目 ID:</strong> #{} | <strong>分值:</strong> {} 分'
            '</div>'
            '</div>',
            color,
            challenge.title,
            color, challenge.get_category_display(),
            challenge.id, challenge.points
        )
    challenge_display.short_description = '题目'
    
    def user_display(self, obj):
        """用户信息展示"""
        return format_html(
            '<div style="padding: 8px; background: #f8f9fa; border-left: 3px solid #17a2b8;">'
            '<strong>👤 {}</strong><br>'
            '<span style="color: #6c757d; font-size: 12px;">{}</span>'
            '</div>',
            obj.user.username,
            obj.user.email
        )
    user_display.short_description = '用户'
    
    def team_display(self, obj):
        """队伍信息展示"""
        if obj.team:
            return format_html(
                '<div style="padding: 8px; background: #f8f9fa; border-left: 3px solid #52c41a;">'
                '<strong>👥 {}</strong>'
                '</div>',
                obj.team.name
            )
        return format_html('<span style="color: #999;">个人参赛</span>')
    team_display.short_description = '队伍'
    
    def competition_display(self, obj):
        """竞赛信息展示"""
        if obj.competition:
            return format_html(
                '<div style="padding: 8px; background: #f8f9fa; border-left: 3px solid #722ed1;">'
                '<strong>🏆 {}</strong>'
                '</div>',
                obj.competition.title
            )
        return '-'
    competition_display.short_description = '竞赛'
    
    def display_score_breakdown(self, obj):
        """显示得分明细"""
        if obj.status == 'correct' and obj.points_earned > 0:
            parts = [f'<strong>总:{obj.points_earned}</strong>']
            if obj.base_score > 0:
                parts.append(f'基:{obj.base_score}')
            if obj.blood_bonus > 0:
                parts.append(f'<span style="color: #ff4d4f;">血:{obj.blood_bonus}</span>')
            if obj.time_bonus > 0:
                parts.append(f'时:{obj.time_bonus}')
            return format_html(' | '.join(parts))
        return '-'
    display_score_breakdown.short_description = '得分明细'
    
    def challenge_title(self, obj):
        """列表页题目显示"""
        return format_html(
            '{} <span style="color: #666;">[{}]</span>',
            obj.challenge.title,
            obj.challenge.get_category_display()
        )
    challenge_title.short_description = '题目'
    
    def user_info(self, obj):
        """列表页用户显示"""
        return format_html(
            '{}<br/><span style="color: #666; font-size: 11px;">{}</span>',
            obj.user.username,
            obj.user.email
        )
    user_info.short_description = '用户'
    
    def team_name(self, obj):
        """列表页队伍显示"""
        if obj.team:
            return obj.team.name
        return format_html('<span style="color: #999;">个人参赛</span>')
    team_name.short_description = '队伍'
    
    def status_badge(self, obj):
        """状态徽章"""
        colors = {
            'correct': '#52c41a',
            'wrong': '#ff4d4f',
            'pending': '#faad14'
        }
        icons = {
            'correct': '✓',
            'wrong': '✗',
            'pending': '⏳'
        }
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            colors.get(obj.status, '#666'),
            icons.get(obj.status, ''),
            obj.get_status_display()
        )
    status_badge.short_description = '状态'
    
    def ip_address(self, obj):
        """IP地址"""
        return obj.ip or format_html('<span style="color: #999;">未记录</span>')
    ip_address.short_description = 'IP地址'
    
    def is_first_blood_display(self, obj):
        """是否一血"""
        if hasattr(obj, 'is_first_blood') and callable(obj.is_first_blood):
            if obj.is_first_blood():
                return format_html(
                    '<div style="padding: 5px 10px; background: #fff1f0; border-left: 3px solid #ff4d4f; color: #cf1322;">'
                    '<strong>🩸 一血</strong>'
                    '</div>'
                )
        return format_html('<span style="color: #999;">否</span>')
    is_first_blood_display.short_description = '是否一血'
    
    def get_queryset(self, request):
        """优化查询性能"""
        return super().get_queryset(request).select_related(
            'challenge',
            'user',
            'team',
            'competition'
        )
def reset_fields(modeladmin, request, queryset):
    updated_count = 0
    for obj in queryset:
        obj.points = obj.initial_points  # 将 points 字段重置为 initial_points 的值
        obj.solves = 0  # 重置 solves 为 0
        obj.save()  # 保存更改
        updated_count += 1
    modeladmin.message_user(request, f'{updated_count} 重置成功')
reset_fields.short_description = "重置题目"



@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    actions = [reset_fields]
    list_display = ('title', 'category', 'difficulty', 'display_score_info', 'solves', 
                    'is_active', 'is_top', 'created_at', 'author')
    list_filter = ('category', 'difficulty', 'is_active', 'is_top')
    search_fields = ('title', 'description')
    readonly_fields = ('uuid', 'solves', 'points', 'created_at', 'updated_at', 'display_score_preview')
    filter_horizontal = ('tags', )
    autocomplete_fields = ['static_files','docker_image', 'network_topology_config']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('基本信息', {
            'fields': ('title', 'description', 'category', 'difficulty', 'tags', 'is_top')
        }),
        ('分数配置', {
            'fields': ('initial_points', 'minimum_points', 'points', 'solves', 'display_score_preview'),
            'description': '初始分数：200-1000分 | 最低分数：不低于50分且建议不低于初始分数的20%'
        }),
        ('Flag配置', {
            'fields': ('flag_type', 'flag_template')
        }),
        ('部署配置', {
            'fields': ('static_files', 'static_file_url', 'docker_image', 'network_topology_config'),
        }),
        ('其他信息', {
            'fields': ('hint', 'is_active','author')
        }),
        ('系统信息', {
            'fields': ('uuid', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def display_score_info(self, obj):
        """显示分数信息"""
        return f"{obj.points}/{obj.initial_points} ({obj.solves}解)"
    display_score_info.short_description = '分数信息(当前/初始/解题数)'
    
    def display_score_preview(self, obj):
        """显示分数衰减预览"""
        from competition.scoring_system import CTFScoringSystem
        from django.utils.html import format_html
        
        preview = CTFScoringSystem.get_score_preview(
            obj.initial_points,
            obj.minimum_points,
            obj.difficulty,
            max_solves=20
        )
        
        html = '<table style="border-collapse: collapse; width: 100%;">'
        html += '<tr style="background: #f0f0f0;"><th style="padding: 5px; border: 1px solid #ddd;">解题数</th>'
        html += '<th style="padding: 5px; border: 1px solid #ddd;">基础分数</th></tr>'
        
        for solves, score in preview[:10]:  # 只显示前10个
            html += f'<tr><td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{solves}</td>'
            html += f'<td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{score}</td></tr>'
        
        html += '</table>'
        html += '<p style="color: #666; font-size: 12px; margin-top: 10px;">注：此预览仅显示基础分，实际得分还包括血榜、时间和快速解题奖励</p>'
        
        return format_html(html)
    display_score_preview.short_description = '分数衰减预览'

    def get_form(self, request, obj=None, **kwargs):
        """设置表单初始值"""
        form = super().get_form(request, obj, **kwargs)
        # 创建新题目时，默认设置作者为当前用户
        if not obj and 'author' in form.base_fields:
            form.base_fields['author'].initial = request.user
        return form

    def save_model(self, request, obj, form, change):
        """保存模型时自动设置作者"""
        # 如果是新建题目且未设置作者，自动设置为当前用户
        if not change and not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)



@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'get_challenge_count')
    search_fields = ('name', 'description')

    def get_challenge_count(self, obj):
        return obj.challenge_set.count()
    get_challenge_count.short_description = '关联题目数'


# ============================================
# Writeup 管理（极简版）
# ============================================

@admin.register(WriteupTemplate)
class WriteupTemplateAdmin(admin.ModelAdmin):
    """Writeup 模板管理"""
    list_display = (
        'id',
        'uuid_display',
        'title',
        'competition',
        'is_active',
        'created_at',
        'download_link'
    )
    list_filter = ('is_active', 'competition', 'created_at')
    search_fields = ('title', 'competition__title')
    
    fieldsets = (
        ('基本信息', {
            'fields': ('competition', 'title', 'is_active')
        }),
        ('文件信息', {
            'fields': ('template_file', 'file_preview')
        }),
    )
    
    readonly_fields = ['file_preview', 'created_at', 'uuid']
    
    def uuid_display(self, obj):
        return str(obj.uuid)[:8] + '...'
    uuid_display.short_description = 'UUID'
    
    def download_link(self, obj):
        if obj.template_file:
            return format_html(
                '<a href="{}" target="_blank">查看</a>',
                obj.template_file.url
            )
        return '-'
    download_link.short_description = '操作'
    
    def file_preview(self, obj):
        if obj.template_file:
            file_ext = obj.template_file.name.split('.')[-1]
            return format_html(
                '<div style="margin: 10px 0;">'
                '<p><strong>文件名：</strong>{}</p>'
                '<p><strong>文件大小：</strong>{:.2f} MB</p>'
                '<p><strong>文件类型：</strong>{}</p>'
                '<a href="{}" target="_blank" style="margin-top: 10px;">查看</a>'
                '</div>',
                obj.template_file.name.split('/')[-1],
                obj.template_file.size / (1024 * 1024),
                file_ext.upper(),
                obj.template_file.url
            )
        return '-'
    file_preview.short_description = '文件信息'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            # 非超级管理员只能看到自己比赛的模板
            qs = qs.filter(competition__author=request.user)
        return qs


@admin.register(Writeup)
class WriteupAdmin(admin.ModelAdmin):
    """Writeup 提交管理"""
    list_display = (
        'id',
        'title', 
        'user_display',
        'team_display', 
        'competition',
        'created_at',
        'file_size_display',
        'download_link'
    )
    list_filter = ('competition', 'created_at')
    search_fields = ('title', 'user__username', 'team__name', 'competition__title')
    readonly_fields = ['user', 'team', 'competition', 'created_at', 'pdf_preview']
    
    fieldsets = (
        ('基本信息', {
            'fields': ('competition', 'user', 'team', 'title', 'description', 'created_at')
        }),
        ('文件信息', {
            'fields': ('pdf_file', 'pdf_preview')
        }),
    )
    
    def user_display(self, obj):
        return obj.user.username
    user_display.short_description = '提交用户'
    
    def team_display(self, obj):
        return obj.team.name if obj.team else '-'
    team_display.short_description = '所属队伍'
    
    def file_size_display(self, obj):
        if obj.pdf_file:
            size_mb = obj.pdf_file.size / (1024 * 1024)
            return f'{size_mb:.2f} MB'
        return '-'
    file_size_display.short_description = '文件大小'
    
    def download_link(self, obj):
        if obj.pdf_file:
            return format_html(
                '<a href="{}" target="_blank" >查看</a>',
                obj.pdf_file.url
            )
        return '-'
    download_link.short_description = '操作'
    
    def pdf_preview(self, obj):
        if obj.pdf_file:
            return format_html(
                '<div style="margin: 10px 0;">'
                '<p><strong>文件名：</strong>{}</p>'
                '<p><strong>文件大小：</strong>{:.2f} MB</p>'
                '<a href="{}" target="_blank" style="margin-top: 10px;">查看</a>'
                '</div>',
                obj.pdf_file.name.split('/')[-1],
                obj.pdf_file.size / (1024 * 1024),
                obj.pdf_file.url
            )
        return '-'
    pdf_preview.short_description = 'PDF 文件'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            # 非超级管理员只能看到自己比赛的 Writeup
            qs = qs.filter(competition__author=request.user)
        return qs


# ============ 综合排行榜管理 ============

@admin.register(CombinedLeaderboard)
class CombinedLeaderboardAdmin(admin.ModelAdmin):
    """综合排行榜管理"""
    
    list_display = ['rank', 'participant_display', 'competition_link', 'combined_score', 'ctf_score', 'quiz_score', 'is_final', 'updated_at']
    list_filter = ['competition', 'is_final', 'created_at']
    search_fields = ['user__username', 'user__real_name', 'team__name', 'competition__title']
    readonly_fields = ['created_at', 'updated_at', 'participant_detail', 'score_breakdown']
    ordering = ['competition', 'rank']
    
    fieldsets = (
        ('基本信息', {
            'fields': ('competition', 'rank', 'participant_detail')
        }),
        ('分数详情', {
            'fields': ('score_breakdown', 'ctf_score', 'ctf_rank', 'quiz_score', 'combined_score', 'is_final')
        }),
        ('时间信息', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def participant_display(self, obj):
        """参与者显示"""
        if obj.user:
            return format_html(
                '<span style="color: #2196F3;">👤 {}</span>',
                obj.user.username
            )
        elif obj.team:
            return format_html(
                '<span style="color: #4CAF50;">👥 {}</span>',
                obj.team.name
            )
        return '-'
    participant_display.short_description = '参与者'
    
    def competition_link(self, obj):
        """竞赛链接"""
        return format_html(
            '<a href="{}" target="_blank">{}</a>',
            reverse('admin:competition_competition_change', args=[obj.competition.id]),
            obj.competition.title
        )
    competition_link.short_description = '所属竞赛'
    
    def participant_detail(self, obj):
        """参与者详情（只读显示）"""
        if obj.user:
            return format_html(
                '<div style="padding: 10px; background: #e3f2fd; border-radius: 4px;">'
                '<p><strong>类型：</strong>个人参赛</p>'
                '<p><strong>用户名：</strong>{}</p>'
                '<p><strong>真实姓名：</strong>{}</p>'
                '<p><strong>用户ID：</strong>{}</p>'
                '</div>',
                obj.user.username,
                getattr(obj.user, 'real_name', '-'),
                obj.user.id
            )
        elif obj.team:
            member_count = obj.team.members.count()
            return format_html(
                '<div style="padding: 10px; background: #e8f5e9; border-radius: 4px;">'
                '<p><strong>类型：</strong>团队参赛</p>'
                '<p><strong>队伍名称：</strong>{}</p>'
                '<p><strong>队伍代码：</strong>{}</p>'
                '<p><strong>队长：</strong>{}</p>'
                '<p><strong>成员数：</strong>{} 人</p>'
                '</div>',
                obj.team.name,
                obj.team.team_code,
                obj.team.leader.username,
                member_count
            )
        return '-'
    participant_detail.short_description = '参与者详情'
    
    def score_breakdown(self, obj):
        """分数分解（只读显示）"""
        # 计算权重
        competition = obj.competition
        ctf_weight = float(competition.combined_score_ctf_weight)
        quiz_weight = 1 - ctf_weight
        
        # 计算归一化分数（反推）
        if obj.combined_score > 0:
            # 由于 combined_score = ctf_normalized * ctf_weight + quiz_normalized * quiz_weight
            # 我们需要显示大致的归一化值
            ctf_normalized_estimate = (float(obj.ctf_score) / max(float(obj.ctf_score), 1)) * 100
            quiz_normalized_estimate = (float(obj.quiz_score) / max(float(obj.quiz_score), 1)) * 100
        else:
            ctf_normalized_estimate = 0
            quiz_normalized_estimate = 0
        
        return format_html(
            '<div style="padding: 10px; background: #fff3e0; border-radius: 4px;">'
            '<h4 style="margin-top: 0; color: #e65100;">📊 分数组成</h4>'
            '<table style="width: 100%; border-collapse: collapse;">'
            '<tr style="background: #ffe0b2;">'
            '<th style="padding: 8px; text-align: left; border: 1px solid #ffcc80;">项目</th>'
            '<th style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">原始分数</th>'
            '<th style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">排名</th>'
            '<th style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">权重</th>'
            '</tr>'
            '<tr>'
            '<td style="padding: 8px; border: 1px solid #ffcc80;"><strong>CTF竞赛</strong></td>'
            '<td style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">{:.2f}</td>'
            '<td style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">#{}</td>'
            '<td style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">{}%</td>'
            '</tr>'
            '<tr>'
            '<td style="padding: 8px; border: 1px solid #ffcc80;"><strong>知识竞赛</strong></td>'
            '<td style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">{:.2f}</td>'
            '<td style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">-</td>'
            '<td style="padding: 8px; text-align: center; border: 1px solid #ffcc80;">{}%</td>'
            '</tr>'
            '<tr style="background: #ffcc80; font-weight: bold;">'
            '<td style="padding: 8px; border: 1px solid #ffcc80;">综合分数</td>'
            '<td colspan="3" style="padding: 8px; text-align: center; border: 1px solid #ffcc80; font-size: 1.2em; color: #e65100;">{:.2f}</td>'
            '</tr>'
            '</table>'
            '<p style="margin-top: 10px; color: #666; font-size: 0.9em;">'
            '💡 综合分数 = CTF归一化 × {:.0f}% + 知识竞赛归一化 × {:.0f}%'
            '</p>'
            '</div>',
            float(obj.ctf_score),
            obj.ctf_rank,
            int(ctf_weight * 100),
            float(obj.quiz_score),
            int(quiz_weight * 100),
            float(obj.combined_score),
            ctf_weight * 100,
            quiz_weight * 100
        )
    score_breakdown.short_description = '分数详细分解'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            # 非超级管理员只能看到自己比赛的排行榜
            qs = qs.filter(competition__author=request.user)
        return qs.select_related('user', 'team', 'team__leader', 'competition')
    
    def has_add_permission(self, request):
        """禁止手动添加（应该由系统自动计算）"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """只有超级管理员可以删除"""
        return request.user.is_superuser
    
    actions = ['recalculate_selected']
    
    def recalculate_selected(self, request, queryset):
        """批量重新计算选中的比赛排行榜"""
        from competition.utils_optimized import CombinedLeaderboardCalculator
        
        competitions = set()
        for obj in queryset:
            competitions.add(obj.competition)
        
        success_count = 0
        failed_count = 0
        
        for competition in competitions:
            try:
                if competition.related_quiz:
                    CombinedLeaderboardCalculator.clear_cache(competition.id)
                    calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
                    result = calculator.calculate_leaderboard_with_lock(force=True)
                    
                    if result.get('success'):
                        success_count += 1
                    else:
                        failed_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
        
        self.message_user(request, f'重新计算完成：成功 {success_count} 场，失败 {failed_count} 场')
    
    recalculate_selected.short_description = '重新计算选中比赛的综合排行榜'


@admin.register(LeaderboardCalculationTask)
class LeaderboardCalculationTaskAdmin(admin.ModelAdmin):
    """排行榜计算任务管理"""
    
    list_display = ['id', 'competition_link', 'competition_type', 'status_display', 'progress_display', 'result_count', 'duration_display', 'created_at']
    list_filter = ['status', 'competition_type', 'created_at']
    search_fields = ['competition__title', 'error_message']
    readonly_fields = ['competition', 'competition_type', 'status', 'total_participants', 'processed_count', 
                      'result_count', 'data_version', 'idempotency_key', 'error_message', 
                      'created_at', 'started_at', 'completed_at', 'updated_at', 'task_detail']
    ordering = ['-created_at']
    
    fieldsets = (
        ('基本信息', {
            'fields': ('competition', 'competition_type', 'status')
        }),
        ('任务详情', {
            'fields': ('task_detail',)
        }),
        ('进度信息', {
            'fields': ('total_participants', 'processed_count', 'result_count')
        }),
        ('技术信息', {
            'fields': ('data_version', 'idempotency_key'),
            'classes': ('collapse',)
        }),
        ('时间信息', {
            'fields': ('created_at', 'started_at', 'completed_at', 'updated_at'),
            'classes': ('collapse',)
        }),
        ('错误信息', {
            'fields': ('error_message',),
            'classes': ('collapse',)
        }),
    )
    
    def competition_link(self, obj):
        """竞赛链接"""
        return format_html(
            '<a href="{}" target="_blank">{}</a>',
            reverse('admin:competition_competition_change', args=[obj.competition.id]),
            obj.competition.title
        )
    competition_link.short_description = '所属竞赛'
    
    def status_display(self, obj):
        """状态显示"""
        status_colors = {
            'pending': '#ff9800',
            'running': '#2196F3',
            'completed': '#4CAF50',
            'failed': '#f44336',
            'cancelled': '#9E9E9E',
        }
        status_icons = {
            'pending': '⏳',
            'running': '⚙️',
            'completed': '',
            'failed': '❌',
            'cancelled': '🚫',
        }
        
        color = status_colors.get(obj.status, '#000')
        icon = status_icons.get(obj.status, '❓')
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            color,
            icon,
            obj.get_status_display()
        )
    status_display.short_description = '状态'
    
    def progress_display(self, obj):
        """进度显示"""
        progress = obj.progress_percentage
        
        if progress >= 100:
            color = '#4CAF50'
        elif progress >= 50:
            color = '#2196F3'
        else:
            color = '#ff9800'
        
        return format_html(
            '<div style="position: relative; width: 100px; height: 20px; background: #e0e0e0; border-radius: 10px; overflow: hidden;">'
            '<div style="position: absolute; width: {}%; height: 100%; background: {}; transition: width 0.3s;"></div>'
            '<span style="position: absolute; width: 100%; text-align: center; line-height: 20px; font-size: 11px; font-weight: bold;">{}%</span>'
            '</div>',
            progress,
            color,
            progress
        )
    progress_display.short_description = '进度'
    
    def duration_display(self, obj):
        """耗时显示"""
        duration = obj.duration_seconds
        if duration:
            if duration < 60:
                return f'{duration:.1f}秒'
            else:
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                return f'{minutes}分{seconds}秒'
        return '-'
    duration_display.short_description = '耗时'
    
    def task_detail(self, obj):
        """任务详情（只读显示）"""
        status_color = {
            'pending': '#ff9800',
            'running': '#2196F3',
            'completed': '#4CAF50',
            'failed': '#f44336',
            'cancelled': '#9E9E9E',
        }.get(obj.status, '#000')
        
        html = f'''
        <div style="padding: 15px; background: #f5f5f5; border-radius: 8px;">
            <h3 style="margin-top: 0; color: {status_color};">{obj.get_status_display()}</h3>
            
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <tr style="background: #e0e0e0;">
                    <th style="padding: 8px; text-align: left; border: 1px solid #ccc;">项目</th>
                    <th style="padding: 8px; text-align: left; border: 1px solid #ccc;">值</th>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ccc;"><strong>竞赛类型</strong></td>
                    <td style="padding: 8px; border: 1px solid #ccc;">{'个人赛' if obj.competition_type == 'individual' else '团队赛'}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ccc;"><strong>总参与者</strong></td>
                    <td style="padding: 8px; border: 1px solid #ccc;">{obj.total_participants}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ccc;"><strong>已处理</strong></td>
                    <td style="padding: 8px; border: 1px solid #ccc;">{obj.processed_count}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ccc;"><strong>结果数量</strong></td>
                    <td style="padding: 8px; border: 1px solid #ccc;">{obj.result_count}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ccc;"><strong>进度</strong></td>
                    <td style="padding: 8px; border: 1px solid #ccc;">{obj.progress_percentage}%</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ccc;"><strong>耗时</strong></td>
                    <td style="padding: 8px; border: 1px solid #ccc;">{self.duration_display(obj)}</td>
                </tr>
            </table>
        '''
        
        if obj.error_message:
            html += f'''
            <div style="margin-top: 15px; padding: 10px; background: #ffebee; border-left: 4px solid #f44336; border-radius: 4px;">
                <strong style="color: #c62828;">错误信息：</strong>
                <pre style="margin-top: 5px; white-space: pre-wrap; word-wrap: break-word; font-size: 12px;">{obj.error_message}</pre>
            </div>
            '''
        
        html += '</div>'
        
        return format_html(html)
    task_detail.short_description = '任务详情'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            # 非超级管理员只能看到自己比赛的任务
            qs = qs.filter(competition__author=request.user)
        return qs.select_related('competition')
    
    def has_add_permission(self, request):
        """禁止手动添加（应该由系统自动创建）"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """禁止修改（只读查看）"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """只有超级管理员可以删除"""
        return request.user.is_superuser



