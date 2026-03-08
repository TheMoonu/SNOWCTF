# -*- coding: utf-8 -*-

from haystack import indexes
from blog.models import Article
from competition.models import Challenge, Competition
from practice.models import PC_Challenge
from recruit.models import Job


class ArticleIndex(indexes.SearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)
    views = indexes.IntegerField(model_attr='views')
    # 添加这个字段，可以在查询的时候作为过滤条件，如果不添加则不能用来过滤，新增字段要重新生成索引
    is_publish = indexes.BooleanField(model_attr='is_publish')
    is_memberShow = indexes.BooleanField(model_attr='is_memberShow')
    def get_model(self):
        return Article

    def index_queryset(self, using=None):
        return self.get_model().objects.all()


class ChallengeIndex(indexes.SearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)
    title = indexes.CharField(model_attr='title')
    category = indexes.CharField(model_attr='category')
    difficulty = indexes.CharField(model_attr='difficulty')
    is_active = indexes.BooleanField(model_attr='is_active')
    
    def get_model(self):
        return Challenge
    
    def index_queryset(self, using=None):
        """用于确定哪些对象要被索引"""
        return self.get_model().objects.filter(is_active=True)

class CompetitionIndex(indexes.SearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)
    title = indexes.CharField(model_attr='title')
    description = indexes.CharField(model_attr='description')
    slug = indexes.CharField(model_attr='slug', null=True)
    competition_type = indexes.CharField(model_attr='competition_type')
    visibility_type = indexes.CharField(model_attr='visibility_type')
    start_time = indexes.DateTimeField(model_attr='start_time')
    end_time = indexes.DateTimeField(model_attr='end_time')
    is_active = indexes.BooleanField(model_attr='is_active')
    
    def get_model(self):
        return Competition
    
    def index_queryset(self, using=None):
        """用于确定哪些对象要被索引"""
        return self.get_model().objects.all()

class PC_ChallengeIndex(indexes.SearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)
    title = indexes.CharField(model_attr='title')
    category = indexes.CharField(model_attr='category')
    difficulty = indexes.CharField(model_attr='difficulty')
    is_active = indexes.BooleanField(model_attr='is_active')
    
    def get_model(self):
        return PC_Challenge
    
    def index_queryset(self, using=None):
        """用于确定哪些对象要被索引"""
        return self.get_model().objects.filter(is_active=True)


class JobIndex(indexes.SearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)
    title = indexes.CharField(model_attr='title')
    track = indexes.CharField(model_attr='track')
    RecruitmentType = indexes.CharField(model_attr='RecruitmentType')
    is_published = indexes.BooleanField(model_attr='is_published')
    
    def get_model(self):
        return Job
    
    def index_queryset(self, using=None):
        """只索引已发布且未过期的职位"""
        from django.utils import timezone
        return self.get_model().objects.filter(
            is_published=True,
            expire_at__gte=timezone.now()
        )
