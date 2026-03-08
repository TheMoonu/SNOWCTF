class CTFPlatform:
    def __init__(self):
        # 不同难度的奖励倍数
        self.difficulty_multiplier = {
            "Easy": 1.0,
            "Medium": 1.2,
            "Hard": 1.4
        }
        self.reward_decay_factor = 1.1  # 奖励递增的因子 (例如：每次解答奖励递增10%)
        self.max_reward_per_solution = 10  # 每次解答的最大奖励金币数
        self.reward_threshold = 5  # 每解答多少次后，才给创造者奖励

    def calculate_reward_for_creator(self, solved_count, difficulty):
        """
        根据解答次数和题目难度计算当前解答后需要给题目创建者的金币奖励
        :param solved_count: 用户已解答的次数
        :param difficulty: 题目难度 (easy, medium, hard)
        :return: 当前解答后的奖励金币数
        """
        
        # 如果解答次数小于奖励周期阈值，则不奖励
        if solved_count % self.reward_threshold != 0:
            return 0

        # 基础奖励：每次解答的初始奖励
        reward_for_creator = 2 * (self.reward_decay_factor ** (solved_count // self.reward_threshold - 1))  # 奖励递增

        # 根据难度调整奖励
        reward_for_creator *= self.difficulty_multiplier[difficulty]

        # 四舍五入为整数
        reward_for_creator = round(reward_for_creator)

        # 确保奖励不超过最大奖励金币数
        if reward_for_creator > self.max_reward_per_solution:
            reward_for_creator = self.max_reward_per_solution

        return reward_for_creator
