#!/usr/bin/env python3
"""
测试 Siliconflow API 的并发能力
"""
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from translator import Translator
from config import get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_concurrent_requests(concurrency_levels=[1, 2, 4, 8]):
    """测试不同并发级别下的 API 性能"""

    # 准备测试数据：12个短句
    test_texts = [
        "Hello, how are you?",
        "This is a test.",
        "Machine learning is amazing.",
        "Python is a great language.",
        "Artificial intelligence is the future.",
        "Deep learning models are powerful.",
        "Natural language processing is complex.",
        "Computer vision has many applications.",
        "Data science is important.",
        "Cloud computing is scalable.",
        "Software engineering requires skill.",
        "Open source is collaborative."
    ]

    config = get_config()
    translator = Translator(
        api_key=config.llm_api_key,
        backend="siliconflow"
    )

    results = {}

    for concurrency in concurrency_levels:
        logging.info(f"\n{'='*60}")
        logging.info(f"Testing concurrency level: {concurrency}")
        logging.info(f"{'='*60}")

        start_time = time.time()

        try:
            if concurrency == 1:
                # 串行测试
                translated = []
                for text in test_texts:
                    result = translator._translate_siliconflow_chunk([text], "zh")
                    translated.extend(result)
            else:
                # 并发测试
                translated = [None] * len(test_texts)

                def translate_one(idx, text):
                    try:
                        result = translator._translate_siliconflow_chunk([text], "zh")
                        return idx, result[0] if result else None
                    except Exception as e:
                        logging.error(f"Request {idx} failed: {e}")
                        return idx, None

                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = {
                        executor.submit(translate_one, i, text): i
                        for i, text in enumerate(test_texts)
                    }

                    for future in as_completed(futures):
                        idx, result = future.result()
                        translated[idx] = result

            elapsed = time.time() - start_time
            success_count = sum(1 for t in translated if t is not None)

            results[concurrency] = {
                'elapsed': elapsed,
                'success': success_count,
                'total': len(test_texts),
                'avg_per_request': elapsed / len(test_texts),
                'requests_per_second': len(test_texts) / elapsed
            }

            logging.info(f"✅ Completed in {elapsed:.2f}s")
            logging.info(f"   Success: {success_count}/{len(test_texts)}")
            logging.info(f"   Avg per request: {elapsed/len(test_texts):.2f}s")
            logging.info(f"   Requests/sec: {len(test_texts)/elapsed:.2f}")

        except Exception as e:
            logging.error(f"❌ Concurrency {concurrency} failed: {e}")
            results[concurrency] = {'error': str(e)}

        # 等待一下避免触发限流
        time.sleep(2)

    # 输出总结
    logging.info(f"\n{'='*60}")
    logging.info("SUMMARY")
    logging.info(f"{'='*60}")

    for concurrency, data in results.items():
        if 'error' in data:
            logging.info(f"Concurrency {concurrency:2d}: ❌ {data['error']}")
        else:
            speedup = results[1]['elapsed'] / data['elapsed'] if concurrency > 1 else 1.0
            logging.info(
                f"Concurrency {concurrency:2d}: {data['elapsed']:6.2f}s "
                f"({data['requests_per_second']:5.2f} req/s, speedup: {speedup:.2f}x)"
            )

    # 推荐并发数
    best_concurrency = 1
    best_rps = 0
    for concurrency, data in results.items():
        if 'requests_per_second' in data and data['requests_per_second'] > best_rps:
            best_rps = data['requests_per_second']
            best_concurrency = concurrency

    logging.info(f"\n✅ Recommended concurrency: {best_concurrency} (best throughput)")

if __name__ == "__main__":
    test_concurrent_requests()
