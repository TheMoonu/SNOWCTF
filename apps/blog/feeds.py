# -*- coding: utf-8 -*-
from xml.sax.saxutils import escape
from django.contrib.syndication.views import Feed
from blog.models import Article
from public.utils import SiteSettingsCache


class AllArticleRssFeed(Feed):
    
    def title(self):
        """显示在聚合阅读器上的标题"""
        try:
            site_settings = SiteSettingsCache.get_settings()
            return site_settings['site_name']
        except:
            return 'SECSNOW'
    
    # 跳转网址，为主页
    link = "/"
    
    def description(self):
        """描述内容"""
        try:
            site_settings = SiteSettingsCache.get_settings()
            return site_settings['site_description']
        except:
            return ''

    # 需要显示的内容条目，这个可以自己挑选一些热门或者最新的博客
    def items(self):
        return Article.objects.filter(is_publish=True)[:10]

    # 显示的内容的标题,这个才是最主要的东西
    def item_title(self, item):
        return item.title

    # 显示的内容的描述
    def item_description(self, item):
        return item.body_to_markdown()
