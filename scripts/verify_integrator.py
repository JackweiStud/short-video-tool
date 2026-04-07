#!/usr/bin/env python3
"""
Integrator Module Verification Script

This script demonstrates the integrator module functionality by:
1. Integrating all module outputs
2. Creating unified output directory structure
3. Generating summary report
4. Verifying all outputs

Usage:
    python verify_integrator.py
"""

import sys
import os
from integrator import Integrator

def main():
    print("="*70)
    print("Integrator Module Verification")
    print("="*70)
    
    # Input paths
    video_path = "downloads/OpenClaw小龙虾保姆级安装教程！小白10分钟搞定【Windows系统】.mp4"
    analysis_result_path = "analysis_results/analysis_result.json"
    clips_metadata_path = "clips/clips_metadata.json"
    translations_metadata_path = "subtitles/translations_metadata.json"
    
    # Check if all files exist
    print("\nChecking input files...")
    files = {
        "Original video": video_path,
        "Analysis result": analysis_result_path,
        "Clips metadata": clips_metadata_path,
        "Translations metadata": translations_metadata_path
    }
    
    all_exist = True
    for name, path in files.items():
        exists = os.path.exists(path)
        status = "✅" if exists else "❌"
        print(f"{status} {name}: {path}")
        if not exists:
            all_exist = False
    
    if not all_exist:
        print("\n❌ Some input files are missing!")
        return 1
    
    # Initialize integrator
    integrator = Integrator(output_dir="output")
    
    # Run integration
    print("\n" + "="*70)
    print("Running Integration...")
    print("="*70)
    
    result = integrator.integrate(
        video_path=video_path,
        analysis_result_path=analysis_result_path,
        clips_metadata_path=clips_metadata_path,
        translations_metadata_path=translations_metadata_path
    )
    
    if not result:
        print("\n❌ Integration failed!")
        return 1
    
    # Display results
    print("\n" + "="*70)
    print("Integration Results Summary")
    print("="*70)
    
    print(f"\n✅ Video title: {result['video_title']}")
    print(f"✅ Total clips: {result['statistics']['total_clips']}")
    print(f"✅ Total subtitles: {result['statistics']['total_subtitles']}")
    print(f"✅ Processing time: {result['statistics']['processing_time']:.2f} seconds")
    
    # Verification
    print("\n" + "="*70)
    print("Verification Status")
    print("="*70)
    
    checks = {
        "Output directory exists": os.path.exists("output"),
        "Original directory exists": os.path.exists("output/original"),
        "Clips directory exists": os.path.exists("output/clips"),
        "Subtitles directory exists": os.path.exists("output/subtitles"),
        "Analysis directory exists": os.path.exists("output/analysis"),
        "Original video copied": os.path.exists(result['original_video']),
        "Analysis result copied": os.path.exists(result['analysis_result']),
        "All clips copied": all(os.path.exists(c['clip_path']) for c in result['clips']),
        "All subtitles copied": all(
            os.path.exists(sub_path) 
            for c in result['clips'] 
            for sub_path in c['subtitle_files'].values()
        ),
        "Summary report exists": os.path.exists(result['summary_report']),
        "Integration metadata exists": os.path.exists("output/integration_metadata.json")
    }
    
    all_passed = all(checks.values())
    
    for check, passed in checks.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {check}")
    
    # File count verification
    print("\n" + "="*70)
    print("File Count Verification")
    print("="*70)
    
    original_count = len([f for f in os.listdir("output/original") if f.endswith('.mp4')])
    clips_count = len([f for f in os.listdir("output/clips") if f.endswith('.mp4')])
    subtitles_count = len([f for f in os.listdir("output/subtitles") if f.endswith('.srt')])
    analysis_count = len([f for f in os.listdir("output/analysis") if f.endswith('.json')])
    
    print(f"Original videos: {original_count} (expected: 1)")
    print(f"Clip videos: {clips_count} (expected: {result['statistics']['total_clips']})")
    print(f"Subtitle files: {subtitles_count} (expected: {result['statistics']['total_subtitles']})")
    print(f"Analysis files: {analysis_count} (expected: 1)")
    
    count_checks = {
        "Original video count": original_count == 1,
        "Clips count": clips_count == result['statistics']['total_clips'],
        "Subtitles count": subtitles_count == result['statistics']['total_subtitles'],
        "Analysis count": analysis_count == 1
    }
    
    all_counts_correct = all(count_checks.values())
    
    for check, passed in count_checks.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {check}")
    
    print("\n" + "="*70)
    if all_passed and all_counts_correct:
        print("✅ All verification checks passed!")
        print("="*70)
        print(f"\nOutput directory: output/")
        print(f"Summary report: {result['summary_report']}")
        print("\nYou can now review the output directory and summary report.")
        return 0
    else:
        print("❌ Some verification checks failed!")
        print("="*70)
        return 1

if __name__ == "__main__":
    sys.exit(main())
