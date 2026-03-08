from django.contrib import admin

# Register your models here.
# jobs/admin.py

from recruit.models import Job, Tag, Company, City

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ('title', 'track',
                    'salary_desc', 'is_published', 'Internal_push', 'expire_at')
    exclude = ('views',)
    list_filter = ('track', 'cityname', 'is_published', 'created_at')
    search_fields = ('title',)
    date_hierarchy = 'created_at'


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'id', 'slug',)
    search_fields = ('name',)


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ('name', 'id', 'slug',)
    search_fields = ('name',)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'company_size', 'company_homepage', 'website',)
    search_fields = ('company_name',)