from django.core.management.base import BaseCommand
from oauth.models import Ouser
import uuid

class Command(BaseCommand):
    help = '为没有 UUID 的用户生成 UUID'

    def handle(self, *args, **options):
        # 获取所有没有 UUID 的用户
        users = Ouser.objects.filter(uuid__isnull=True)
        total = users.count()
        
        if total == 0:
            self.stdout.write(self.style.SUCCESS('没有找到需要生成 UUID 的用户'))
            return
            
        self.stdout.write(f'开始为 {total} 个用户生成 UUID...')
        
        # 获取现有的 UUID 集合
        existing_uuids = set(
            Ouser.objects.exclude(uuid__isnull=True)
            .values_list('uuid', flat=True)
        )
        
        # 为每个用户生成 UUID
        success_count = 0
        for user in users:
            try:
                while True:
                    new_uuid = uuid.uuid4()
                    if new_uuid not in existing_uuids:
                        user.uuid = new_uuid
                        user.save()
                        existing_uuids.add(new_uuid)
                        success_count += 1
                        self.stdout.write(f'用户 {user.username}(ID:{user.id}) 的 UUID 已生成')
                        break
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'用户 {user.username}(ID:{user.id}) 生成 UUID 失败: {str(e)}')
                )
        
        self.stdout.write(self.style.SUCCESS(f'完成！成功生成 {success_count}/{total} 个 UUID'))