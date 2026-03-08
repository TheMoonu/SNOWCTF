"""izone URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.10/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
"""
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from django.contrib import admin
from django.views.generic import RedirectView

from django.contrib.sitemaps.views import sitemap
from blog.sitemaps import ArticleSitemap, CategorySitemap, TagSitemap
from blog.feeds import AllArticleRssFeed
from blog.views import robots
from oauth.admin_views import reset_user_password

# 网站地图
sitemaps = {
    'articles': ArticleSitemap,
    'tags': TagSitemap,
    'categories': CategorySitemap
}

urlpatterns = [
                  path('favicon.ico', RedirectView.as_view(url='/static/blog/img/favicon.ico')),
                  path('admin/oauth/reset-password/', reset_user_password, name='admin_reset_user_password'),
                  path('adminx/', admin.site.urls),
                  path('captcha/', include('captcha.urls')),
                  path('accounts/', include('allauth.urls')),  # allauth
                  path('accounts/', include(('oauth.urls', 'oauth'), namespace='oauth')),
                  # oauth,只展现一个用户登录界面
                  path('wiki/', include(('blog.urls', 'blog'), namespace='blog')),  # blog
                  path('comment/', include(('comment.urls', 'comment'), namespace='comment')),
                  # comment
                  path('robots.txt', robots, name='robots'),  # robots
                  path('sitemap.xml', sitemap, {'sitemaps': sitemaps},
                       name='django.contrib.sitemaps.views.sitemap'),  # 网站地图
                  path('feed/', AllArticleRssFeed(), name='rss'),  # rss订阅

                  path('rss/', include(('rsshub.urls', 'rsshub'), namespace='rsshub')),
                  path('snowlab/', include(('practice.urls', 'practice'), namespace='practice')),
                  path('', include(('public.urls', 'public'), namespace='public')),
                  #path('apis/', include(('dockerService.urls', 'dockerService'), namespace='dockerService')),
                  path('ctf/', include(('competition.urls', 'competition'), namespace='competition')),
                  path('container/', include(('container.urls', 'container'), namespace='container')),
                  path('recruit/', include(('recruit.urls', 'recruit'), namespace='recruitr')),
                  path('quiz/', include(('quiz.urls', 'quiz'), namespace='quiz'))
              ] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)  # 加入这个才能显示media文件

# 在开发环境中提供静态文件服务
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

if settings.API_FLAG:
    from api.urls import router

    urlpatterns.append(path('api/v1/', include((router.urls, router.root_view_name),
                                               namespace='api')))  # restframework

if settings.TOOL_FLAG:
    urlpatterns.append(path('tool/', include(('tool.urls', 'tool'), namespace='tool')))  # tool

