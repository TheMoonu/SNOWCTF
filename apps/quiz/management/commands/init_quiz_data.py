from django.core.management.base import BaseCommand
from django.db import transaction
from quiz.models import Question, Option, Quiz, QuizQuestion


class Command(BaseCommand):
    help = '初始化知识竞赛测试数据'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('开始初始化知识竞赛数据...'))
        
        # 清空现有数据
        if options.get('clear', False):
            self.stdout.write('清空现有数据...')
            Question.objects.all().delete()
            Quiz.objects.all().delete()
        
        # 创建单选题
        self.stdout.write('创建单选题...')
        single_questions = self.create_single_choice_questions()
        
        # 创建多选题
        self.stdout.write('创建多选题...')
        multiple_questions = self.create_multiple_choice_questions()
        
        # 创建判断题
        self.stdout.write('创建判断题...')
        judge_questions = self.create_judge_questions()
        
        # 创建竞赛
        self.stdout.write('创建知识竞赛...')
        quiz = self.create_quiz(single_questions, multiple_questions, judge_questions)
        
        self.stdout.write(self.style.SUCCESS(
            f'初始化完成！\n'
            f'- 单选题: {len(single_questions)} 道\n'
            f'- 多选题: {len(multiple_questions)} 道\n'
            f'- 判断题: {len(judge_questions)} 道\n'
            f'- 竞赛: {quiz.title}\n'
            f'- 总分: {quiz.total_score}'
        ))

    def create_single_choice_questions(self):
        """创建单选题"""
        questions_data = [
            {
                'content': 'Python是什么类型的编程语言？',
                'options': [
                    ('A', '编译型语言', False),
                    ('B', '解释型语言', True),
                    ('C', '汇编语言', False),
                    ('D', '机器语言', False),
                ],
                'explanation': 'Python是一种解释型、面向对象的高级编程语言。',
                'category': 'Python基础',
                'difficulty': 'easy',
                'score': 2.00
            },
            {
                'content': 'Django是什么？',
                'options': [
                    ('A', '一个JavaScript框架', False),
                    ('B', '一个Python Web框架', True),
                    ('C', '一个数据库', False),
                    ('D', '一个操作系统', False),
                ],
                'explanation': 'Django是一个用Python编写的开源Web应用框架。',
                'category': 'Web开发',
                'difficulty': 'easy',
                'score': 2.00
            },
            {
                'content': '在Python中，哪个关键字用于定义函数？',
                'options': [
                    ('A', 'function', False),
                    ('B', 'def', True),
                    ('C', 'func', False),
                    ('D', 'define', False),
                ],
                'explanation': '在Python中使用def关键字来定义函数。',
                'category': 'Python基础',
                'difficulty': 'easy',
                'score': 2.00
            },
            {
                'content': 'HTTP协议中，GET和POST的主要区别是什么？',
                'options': [
                    ('A', 'GET请求参数在URL中，POST在请求体中', True),
                    ('B', 'GET比POST更安全', False),
                    ('C', 'POST只能用于提交表单', False),
                    ('D', 'GET请求速度比POST快', False),
                ],
                'explanation': 'GET请求的参数通过URL传递，而POST请求的参数在请求体中传递。',
                'category': '网络协议',
                'difficulty': 'medium',
                'score': 3.00
            },
            {
                'content': 'SQL中，用于从数据库中提取数据的命令是？',
                'options': [
                    ('A', 'GET', False),
                    ('B', 'EXTRACT', False),
                    ('C', 'SELECT', True),
                    ('D', 'OPEN', False),
                ],
                'explanation': 'SELECT语句用于从数据库中提取数据。',
                'category': '数据库',
                'difficulty': 'easy',
                'score': 2.00
            },
        ]
        
        questions = []
        for data in questions_data:
            question = Question.objects.create(
                question_type='single',
                content=data['content'],
                explanation=data['explanation'],
                category=data['category'],
                difficulty=data['difficulty'],
                score=data['score']
            )
            
            for order, content, is_correct in data['options']:
                Option.objects.create(
                    question=question,
                    order=order,
                    content=content,
                    is_correct=is_correct
                )
            
            questions.append(question)
        
        return questions

    def create_multiple_choice_questions(self):
        """创建多选题"""
        questions_data = [
            {
                'content': '以下哪些是Python的内置数据类型？',
                'options': [
                    ('A', 'list', True),
                    ('B', 'dict', True),
                    ('C', 'array', False),
                    ('D', 'tuple', True),
                ],
                'explanation': 'Python的内置数据类型包括list、dict、tuple等，array不是内置类型。',
                'category': 'Python基础',
                'difficulty': 'medium',
                'score': 4.00
            },
            {
                'content': 'RESTful API的常用HTTP方法包括？',
                'options': [
                    ('A', 'GET', True),
                    ('B', 'POST', True),
                    ('C', 'PUT', True),
                    ('D', 'SEND', False),
                ],
                'explanation': 'RESTful API常用的HTTP方法有GET、POST、PUT、DELETE等，SEND不是标准HTTP方法。',
                'category': 'Web开发',
                'difficulty': 'medium',
                'score': 4.00
            },
            {
                'content': '以下哪些是面向对象编程的特性？',
                'options': [
                    ('A', '封装', True),
                    ('B', '继承', True),
                    ('C', '多态', True),
                    ('D', '递归', False),
                ],
                'explanation': '面向对象编程的三大特性是封装、继承和多态，递归是一种编程技术。',
                'category': '编程思想',
                'difficulty': 'medium',
                'score': 4.00
            },
        ]
        
        questions = []
        for data in questions_data:
            question = Question.objects.create(
                question_type='multiple',
                content=data['content'],
                explanation=data['explanation'],
                category=data['category'],
                difficulty=data['difficulty'],
                score=data['score']
            )
            
            for order, content, is_correct in data['options']:
                Option.objects.create(
                    question=question,
                    order=order,
                    content=content,
                    is_correct=is_correct
                )
            
            questions.append(question)
        
        return questions

    def create_judge_questions(self):
        """创建判断题"""
        questions_data = [
            {
                'content': 'Python是一种强类型语言。',
                'is_true': True,
                'explanation': 'Python是强类型语言，不允许不同类型之间的隐式转换。',
                'category': 'Python基础',
                'difficulty': 'medium',
                'score': 2.00
            },
            {
                'content': 'HTTP是一种有状态协议。',
                'is_true': False,
                'explanation': 'HTTP是无状态协议，每次请求都是独立的。',
                'category': '网络协议',
                'difficulty': 'easy',
                'score': 2.00
            },
            {
                'content': 'SQL注入是一种常见的Web安全漏洞。',
                'is_true': True,
                'explanation': 'SQL注入是最常见的Web安全漏洞之一，需要通过参数化查询等方式防范。',
                'category': 'Web安全',
                'difficulty': 'easy',
                'score': 2.00
            },
            {
                'content': 'Git是一种集中式版本控制系统。',
                'is_true': False,
                'explanation': 'Git是分布式版本控制系统，SVN才是集中式的。',
                'category': '开发工具',
                'difficulty': 'easy',
                'score': 2.00
            },
        ]
        
        questions = []
        for data in questions_data:
            question = Question.objects.create(
                question_type='judge',
                content=data['content'],
                explanation=data['explanation'],
                category=data['category'],
                difficulty=data['difficulty'],
                score=data['score']
            )
            
            # 判断题只有两个选项：正确和错误
            Option.objects.create(
                question=question,
                order='A',
                content='正确',
                is_correct=data['is_true']
            )
            Option.objects.create(
                question=question,
                order='B',
                content='错误',
                is_correct=not data['is_true']
            )
            
            questions.append(question)
        
        return questions

    def create_quiz(self, single_questions, multiple_questions, judge_questions):
        """创建竞赛"""
        quiz = Quiz.objects.create(
            title='Python Web开发知识竞赛',
            description='测试Python、Web开发、数据库等相关知识',
            pass_score=60.00,
            duration=30,  # 30分钟
            is_active=True
        )
        
        # 添加题目到竞赛
        order = 1
        
        # 先添加单选题
        for question in single_questions:
            QuizQuestion.objects.create(
                quiz=quiz,
                question=question,
                order=order
            )
            order += 1
        
        # 再添加多选题
        for question in multiple_questions:
            QuizQuestion.objects.create(
                quiz=quiz,
                question=question,
                order=order
            )
            order += 1
        
        # 最后添加判断题
        for question in judge_questions:
            QuizQuestion.objects.create(
                quiz=quiz,
                question=question,
                order=order
            )
            order += 1
        
        # 计算总分
        quiz.calculate_total_score()
        
        return quiz

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='清空现有数据后再初始化',
        )

