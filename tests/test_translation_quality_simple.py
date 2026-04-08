#!/usr/bin/env python3
"""
简化版翻译质量对比测试 - 逐句测试避免批量超时
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
    "Hello, how are you today?",
    "Machine learning requires a lot of data.",
    "Artificial intelligence is rapidly transforming industries by automating complex tasks.",
    "I'm gonna grab some coffee, wanna join?",
    "The API endpoint returns a JSON response with user authentication tokens.",
    "The sun slowly descended behind the mountains, painting the sky in shades of orange and purple.",
]

def translate_single(translator, text):
    """翻译单个句子"""
    try:
        result = translator._batch_translate(texts=[text], target_lang='zh')
        return result[0] if result else None
    except Exception as e:
        print(f"   ❌ 翻译失败: {e}")
        return None

def main():
    print("\n" + "="*70)
    print("翻译质量对比测试 (逐句测试)")
    print("="*70 + "\n")

    models = [
        ("Qwen/Qwen3.5-4B", "Qwen3.5-4B"),
        ("deepseek-ai/DeepSeek-V3", "DeepSeek-V3")
    ]

    results = {model[1]: [] for model in models}

    # 对每个句子分别测试两个模型
    for i, sentence in enumerate(TEST_SENTENCES, 1):
        print(f"\n{i}. 原文: {sentence}")

        for model_id, model_name in models:
            os.environ['LLM_MODEL'] = model_id
            config = get_config()
            translator = Translator(config=config)

            translation = translate_single(translator, sentence)
            results[model_name].append(translation)
            print(f"   {model_name:15s}: {translation}")

    # 总结
    print("\n" + "="*70)
    print("对比总结")
    print("="*70 + "\n")

    same_count = 0
    diff_count = 0

    for i, sentence in enumerate(TEST_SENTENCES):
        qwen_trans = results["Qwen3.5-4B"][i]
        deepseek_trans = results["DeepSeek-V3"][i]

        if qwen_trans and deepseek_trans:
            if qwen_trans == deepseek_trans:
                same_count += 1
                status = "✓ 相同"
            else:
                diff_count += 1
                status = "⚠ 不同"
            print(f"{i+1}. {status}")

    print(f"\n相同: {same_count}/{len(TEST_SENTENCES)}")
    print(f"不同: {diff_count}/{len(TEST_SENTENCES)}")

if __name__ == "__main__":
    main()
