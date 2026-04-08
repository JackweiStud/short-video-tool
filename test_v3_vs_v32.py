#!/usr/bin/env python3
"""
对比测试 DeepSeek-V3 和 DeepSeek-V3.2 的翻译性能
"""
import os
import sys
import time
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from translator import Translator
from config import get_config

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

def test_model_performance(model_name: str, test_data: list, workers: int = 6):
    """测试指定模型的翻译性能"""
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
            'ms_per_sentence': elapsed/len(test_data)*1000,
            'sample_translations': results[:3]  # 保存前3个翻译样本
        }

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    print("\n" + "="*70)
    print("DeepSeek-V3 vs V3.2 性能对比测试")
    print("="*70)

    models = [
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-V3.2"
    ]

    results = []

    for model in models:
        result = test_model_performance(model, TEST_SENTENCES, workers=6)
        if result:
            results.append(result)

        # 两次测试之间暂停5秒
        if model != models[-1]:
            print("等待 5 秒后进行下一个测试...\n")
            time.sleep(5)

    # 输出对比结果
    if len(results) == 2:
        print("\n" + "="*70)
        print("对比结果")
        print("="*70)

        v3 = results[0]
        v32 = results[1]

        print(f"\n速度对比:")
        print(f"  DeepSeek-V3:   {v3['total_time']:6.2f}s  ({v3['sentences_per_second']:.2f} 句/秒)")
        print(f"  DeepSeek-V3.2: {v32['total_time']:6.2f}s  ({v32['sentences_per_second']:.2f} 句/秒)")

        speedup = v3['total_time'] / v32['total_time']
        if speedup > 1:
            print(f"\n✅ V3.2 比 V3 快 {speedup:.2f}x")
        else:
            print(f"\n✅ V3 比 V3.2 快 {1/speedup:.2f}x")

        print(f"\n单句耗时对比:")
        print(f"  DeepSeek-V3:   {v3['ms_per_sentence']:.2f} ms")
        print(f"  DeepSeek-V3.2: {v32['ms_per_sentence']:.2f} ms")
        print(f"  差异: {abs(v3['ms_per_sentence'] - v32['ms_per_sentence']):.2f} ms")

        # 翻译质量对比（前3个样本）
        print(f"\n翻译样本对比 (前3句):")
        for i in range(min(3, len(TEST_SENTENCES))):
            print(f"\n{i+1}. 原文: {TEST_SENTENCES[i]}")
            print(f"   V3:   {v3['sample_translations'][i]}")
            print(f"   V3.2: {v32['sample_translations'][i]}")
            if v3['sample_translations'][i] == v32['sample_translations'][i]:
                print(f"   ✓ 译文相同")
            else:
                print(f"   ⚠ 译文不同")

        print("\n" + "="*70 + "\n")

if __name__ == "__main__":
    main()
