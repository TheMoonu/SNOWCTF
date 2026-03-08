from practice.models import Tag
from django.utils.text import slugify
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **options):
        for tag in Tag.objects.filter(slug__isnull=True):
            base_slug = slugify(tag.name)
            slug = base_slug
            counter = 1
            while Tag.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            tag.slug = slug
            tag.save()