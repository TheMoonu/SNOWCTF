from django.db.models.signals import post_save
from django.dispatch import receiver
from comment.models import ArticleComment, ChallengeComment, Notification

@receiver(post_save, sender=ArticleComment)
@receiver(post_save, sender=ChallengeComment)
def notify_handler(sender, instance, created, **kwargs):
    if sender == ArticleComment:
        the_object = instance.belong
        object_author = the_object.author
    elif sender == ChallengeComment:
        the_object = instance.belong  # 假设字段名是 'belong'
        object_author = the_object.author 
    create_p = instance.author
    if created:
        if instance.rep_to:
            if object_author == instance.rep_to.author:
                get_p = instance.rep_to.author
                if create_p != get_p:
                    new_notify = Notification(create_p=create_p, get_p=get_p, comment=instance)
                    new_notify.save()
            else:
                get_p1 = object_author
                if create_p != get_p1:
                    new1 = Notification(create_p=create_p, get_p=get_p1, comment=instance)
                    new1.save()
                get_p2 = instance.rep_to.author
                if create_p != get_p2:
                    new2 = Notification(create_p=create_p, get_p=get_p2, comment=instance)
                    new2.save()
        else:
            get_p = object_author
            if create_p != get_p:
                new_notify = Notification(create_p=create_p, get_p=get_p, comment=instance)
                new_notify.save()
    
