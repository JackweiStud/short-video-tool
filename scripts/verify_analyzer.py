#!/usr/bin/env python3
"""
Analyzer Module Verification Script

This script demonstrates the analyzer module functionality by:
1. Analyzing a video file
2. Displaying the analysis results
3. Verifying all three core functions work correctly

Usage:
    python verify_analyzer.py <video_path>
    
Example:
    python verify_analyzer.py "downloads/COSTA RICA IN 4K 60fps HDR (ULTRA HD).mp4"
"""

import sys
import os
import json
from analyzer import Analyzer

def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_analyzer.py <video_path>")
        print("\nExample:")
        print('  python verify_analyzer.py "downloads/COSTA RICA IN 4K 60fps HDR (ULTRA HD).mp4"')
        sys.exit(1)
    
    video_path = sys.argv[1]
    
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        sys.exit(1)
    
    print("="*70)
    print("Analyzer Module Verification")
    print("="*70)
    print(f"\nVideo: {video_path}")
    print(f"Size: {os.path.getsize(video_path) / (1024*1024):.2f} MB")
    
    # Initialize analyzer
    analyzer = Analyzer()
    
    # Run analysis
    print("\n" + "="*70)
    print("Running Analysis...")
    print("="*70)
    result = analyzer.analyze_video(video_path, output_dir="analysis_results")
    
    if not result:
        print("\n❌ Analysis failed!")
        sys.exit(1)
    
    # Display results
    print("\n" + "="*70)
    print("Analysis Results Summary")
    print("="*70)
    
    # Extract results with defensive access
    asr_result = result.get('asr_result')
    audio_climax_points = result.get('audio_climax_points', [])
    scene_changes = result.get('scene_changes', [])

    # F2.1: ASR Results
    print(f"\n✅ F2.1 - Speech to Text (ASR):")
    if isinstance(asr_result, list):
        print(f"   Total segments: {len(asr_result)}")
        if asr_result:
            print(f"   Sample segments:")
            for i, seg in enumerate(asr_result[:5], 1):
                print(f"     {i}. [{seg['start']:.2f}s - {seg['end']:.2f}s]: {seg['text']}")
        else:
            print("   ⚠️  No speech detected in video")
    else:
        print("   ❌ ASR returned invalid result")
    
    # F2.2: Audio Climax Points
    print(f"\n✅ F2.2 - Audio Analysis (Climax Detection):")
    print(f"   Total climax points: {len(audio_climax_points)}")
    if audio_climax_points:
        print(f"   Top climax points:")
        for i, point in enumerate(audio_climax_points, 1):
            print(f"     {i}. Time: {point['time']:.2f}s, Score: {point['score']:.2f}")
    
    # F2.3: Scene Changes
    print(f"\n✅ F2.3 - Video Analysis (Scene Detection):")
    print(f"   Total scene changes: {len(scene_changes)}")
    if scene_changes:
        print(f"   Scene change timestamps (first 15):")
        for i, time in enumerate(scene_changes[:15], 1):
            print(f"     {i}. {time:.2f}s")
    
    # Verification
    print("\n" + "="*70)
    print("Verification Status")
    print("="*70)
    
    checks = {
        "ASR function works": isinstance(asr_result, list),
        "Audio analysis works": len(audio_climax_points) > 0,
        "Scene detection works": len(scene_changes) > 0,
        "JSON output generated": os.path.exists("analysis_results/analysis_result.json")
    }
    
    all_passed = all(checks.values())
    
    for check, passed in checks.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {check}")
    
    print("\n" + "="*70)
    if all_passed:
        print("✅ All verification checks passed!")
        print("="*70)
        print(f"\nFull results saved to: analysis_results/analysis_result.json")
        return 0
    else:
        print("❌ Some verification checks failed!")
        print("="*70)
        return 1

if __name__ == "__main__":
    sys.exit(main())
