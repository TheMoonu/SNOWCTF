from django.urls import path
from django.conf import settings
from .views import JobListView, JobdetailView, CityDetailView, CompanyDetailView, toggle_job_collect

app_name = 'recruit'

urlpatterns = [
    path('', JobListView.as_view(), name='job_list'),
    path('city/<slug:slug>/', CityDetailView.as_view(), name='city_detail'),
    path('company/<slug:slug>/', CompanyDetailView.as_view(), name='company_detail'),
    path('collect/<slug:slug>/', toggle_job_collect, name='toggle_job_collect'),
    path('<slug:slug>/', JobdetailView.as_view(), name='jobdetail'),
]