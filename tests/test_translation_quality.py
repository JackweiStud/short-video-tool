#!/usr/bin/env python3
"""
对比测试 Qwen3.5-4B 和 DeepSeek-V3 的翻译质量
"""
import os
import sys
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from translator import Translator
from config import get_config

# 测试句子：包含不同难度和类型的英文句子
TEST_SENTENCES = [
    # 简单日常对话
    "Hello, how are you today?",
    "The weather is beautiful outside.",

    # 技术术语
    "Machine learning requires a lot of data.",
    "Neural networks are inspired by the human brain.",
    "Cloud computing has revolutionized IT infrastructure.",

    # 长句和复杂句式
    "Artificial intelligence is rapidly transforming industries by automating complex tasks that previously required human intelligence.",
    "The integration of deep learning algorithms with big data analytics has enabled unprecedented insights into consumer behavior patterns.",

    # 口语化表达
    "I'm gonna grab some coffee, wanna join?",
    "That's awesome! Let's catch up later.",

    # 专业术语混合
    "The API endpoint returns a JSON response with user authentication tokens.",
    "We need to optimize the database queries to reduce latency and improve throughput.",

    # 文学性表达
    "The sun slowly descended behind the mountains, painting the sky in shades of orange and purple.",
    "Time flies like an arrow; fruit flies like a banana.",

    # 商务英语
    "We are pleased to announce our quarterly earnings exceeded expectations.",
    "Please find attached the proposal for your review and consideration.",
]

def test_model_quality(model_name: str, test_data: list):
    """测试指定模型的翻译质量"""
    print(f"\n{'='*70}")
    print(f"模型: {model_name}")
    print(f"{'='*70}\n")

    # 设置环境变量
    os.environ['LLM_MODEL'] = model_name

    # 重新加载配置
    config = get_config()

    # 创建翻译器
    translator = Translator(config=config)

    try:
        # 执行翻译
        results = translator._batch_translate(
            texts=test_data,
            target_lang='zh'
        )

        # 输出结果
        for i, (original, translated) in enumerate(zip(test_data, results), 1):
            print(f"{i}. 原文: {original}")
            print(f"   译文: {translated}")
            print()

        return results

    except Exception as e:
        print(f"❌ 翻译失败: {e}")
        return None

def main():
    print("\n" + "="*70)
    print("翻译质量对比测试")
    print("="*70)

    models = [
        "Qwen/Qwen3.5-4B",
        "deepseek-ai/DeepSeek-V3"
    ]

    all_results = {}

    for model in models:
        results = test_model_quality(model, TEST_SENTENCES)
        if results:
            all_results[model] = results

        # 两次测试之间暂停
        if model != models[-1]:
            print("\n" + "="*70)
            print("等待 3 秒后测试下一个模型...")
            print("="*70)
            import time
            time.sleep(3)

    # 并排对比
    if len(all_results) == 2:
        print("\n" + "="*70)
        print("并排对比")
        print("="*70 + "\n")

        qwen_results = all_results["Qwen/Qwen3.5-4B"]
        deepseek_results = all_results["deepseek-ai/DeepSeek-V3"]

        for i, (original, qwen, deepseek) in enumerate(zip(TEST_SENTENCES, qwen_results, deepseek_results), 1):
            print(f"{i}. 原文: {original}")
            print(f"   Qwen3.5-4B:  {qwen}")
            print(f"   DeepSeek-V3: {deepseek}")

            # 简单对比
            if qwen == deepseek:
                print(f"   ✓ 译文相同")
            else:
                print(f"   ⚠ 译文不同")
            print()

if __name__ == "__main__":
    main()
