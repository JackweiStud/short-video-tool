#!/usr/bin/env python3
"""
对比测试 DeepSeek-V3 和 DeepSeek-R1-0528-Qwen3-8B 的翻译速度
"""
import os
import sys
import time
import logging
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from translator import Translator
from config import get_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 测试数据：100个英文句子
TEST_SENTENCES = [
    "Hello, how are you today?",
    "The weather is beautiful outside.",
    "I love learning new programming languages.",
    "Artificial intelligence is changing the world.",
    "This is a test sentence for translation.",
    "Machine learning requires a lot of data.",
    "Python is a versatile programming language.",
    "Cloud computing has revolutionized IT infrastructure.",
    "Data science combines statistics and programming.",
    "Neural networks are inspired by the human brain.",
] * 10  # 重复10次，共100句

def test_translation_speed(model_name: str, test_data: list, workers: int = 6):
    """测试指定模型的翻译速度"""
    print(f"\n{'='*70}")
    print(f"测试模型: {model_name}")
    print(f"并发数: {workers} workers")
    print(f"测试数据: {len(test_data)} 个句子")
    print(f"{'='*70}\n")

    # 设置环境变量
    os.environ['LLM_MODEL'] = model_name

    # 重新加载配置
    config = get_config()

    # 创建翻译器
    translator = Translator(config=config)

    # 开始计时
    start_time = time.time()

    try:
        # 执行翻译
        results = translator._batch_translate(
            texts=test_data,
            target_lang='zh'
        )

        # 结束计时
        end_time = time.time()
        elapsed = end_time - start_time

        # 统计结果
        success_count = len([r for r in results if r])

        print(f"\n{'='*70}")
        print(f"测试完成: {model_name}")
        print(f"{'='*70}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"成功翻译: {success_count}/{len(test_data)} 句")
        print(f"平均速度: {len(test_data)/elapsed:.2f} 句/秒")
        print(f"单句耗时: {elapsed/len(test_data)*1000:.2f} 毫秒")
        print(f"{'='*70}\n")

        return {
            'model': model_name,
            'total_time': elapsed,
            'success_count': success_count,
            'total_count': len(test_data),
            'sentences_per_second': len(test_data)/elapsed,
            'ms_per_sentence': elapsed/len(test_data)*1000
        }

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return None

def main():
    print("\n" + "="*70)
    print("DeepSeek 模型翻译速度对比测试")
    print("="*70)

    models = [
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
        "Qwen/Qwen3.5-27B",
        "Qwen/Qwen3.5-4B"
    ]

    results = []

    for model in models:
        result = test_translation_speed(model, TEST_SENTENCES, workers=6)
        if result:
            results.append(result)

        # 两次测试之间暂停5秒
        if model != models[-1]:
            print("等待 5 秒后进行下一个测试...\n")
            time.sleep(5)

    # 输出对比结果
    if len(results) >= 2:
        print("\n" + "="*70)
        print("对比结果")
        print("="*70)

        print(f"\n模型对比:")
        for result in results:
            model_short = result['model'].split('/')[-1]
            print(f"  {model_short:25s} {result['total_time']:6.2f}s  ({result['sentences_per_second']:.2f} 句/秒)")

        # 找出最快的模型
        fastest = min(results, key=lambda x: x['total_time'])
        print(f"\n✅ 最快模型: {fastest['model'].split('/')[-1]}")

        print(f"\n单句耗时对比:")
        for result in results:
            model_short = result['model'].split('/')[-1]
            print(f"  {model_short:25s} {result['ms_per_sentence']:6.2f} ms")

        print("="*70 + "\n")

if __name__ == "__main__":
    main()
