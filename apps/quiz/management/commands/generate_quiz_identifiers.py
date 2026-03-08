from django.core.management.base import BaseCommand
from django.db import transaction
from quiz.models import Quiz, QuizRecord
import uuid as uuid_lib


class Command(BaseCommand):
    help = '为现有的Quiz和QuizRecord生成唯一标识符'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('开始生成唯一标识符...'))
        
        # 为Quiz生成slug
        quiz_count = 0
        for quiz in Quiz.objects.all():
            if not quiz.slug:
                quiz.save()  # 会触发自动生成slug
                quiz_count += 1
                self.stdout.write(f'为竞赛 "{quiz.title}" 生成slug: {quiz.slug}')
        
        # 为QuizRecord生成UUID
        record_count = 0
        for record in QuizRecord.objects.all():
            if not record.uuid:
                record.uuid = uuid_lib.uuid4()
                record.save()
                record_count += 1
        
        self.stdout.write(self.style.SUCCESS(
            f'\n完成！\n'
            f'- 更新了 {quiz_count} 个竞赛的slug\n'
            f'- 更新了 {record_count} 个答题记录的uuid'
        ))

