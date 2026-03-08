from quiz.models import Quiz
for quiz in Quiz.objects.all():
    quiz.save()  # 会自动生成唯一的slug
exit()