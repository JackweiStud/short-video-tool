#!/usr/bin/env python3
"""
Clipper Module Verification Script

This script demonstrates the clipper module functionality by:
1. Loading analysis results
2. Clipping the video into key segments
3. Verifying all clips and ASR subsets

Usage:
    python verify_clipper.py <analysis_result_path>
    
Example:
    python verify_clipper.py analysis_results/analysis_result.json
"""

import sys
import os
import json
from clipper import Clipper

def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_clipper.py <analysis_result_path>")
        print("\nExample:")
        print('  python verify_clipper.py analysis_results/analysis_result.json')
        sys.exit(1)
    
    analysis_path = sys.argv[1]
    
    if not os.path.exists(analysis_path):
        print(f"Error: Analysis result file not found: {analysis_path}")
        sys.exit(1)
    
    print("="*70)
    print("Clipper Module Verification")
    print("="*70)
    
    # Load analysis result
    with open(analysis_path, 'r', encoding='utf-8') as f:
        analysis_result = json.load(f)
    
    video_path = analysis_result['video_path']
    
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        sys.exit(1)
    
    print(f"\nVideo: {video_path}")
    print(f"Size: {os.path.getsize(video_path) / (1024*1024):.2f} MB")
    print(f"Audio climax points: {len(analysis_result['audio_climax_points'])}")
    print(f"Scene changes: {len(analysis_result['scene_changes'])}")
    
    # Initialize clipper
    clipper = Clipper(min_duration=15, max_duration=60)
    
    # Run clipping
    print("\n" + "="*70)
    print("Running Clipping...")
    print("="*70)
    result = clipper.clip_video(video_path, analysis_result, output_dir="clips")
    
    if not result:
        print("\n❌ Clipping failed!")
        sys.exit(1)
    
    # Display results
    print("\n" + "="*70)
    print("Clipping Results Summary")
    print("="*70)
    
    print(f"\n✅ Total clips created: {len(result['clips'])}")
    
    for i, clip in enumerate(result['clips'], 1):
        print(f"\n--- Clip {i} ---")
        print(f"  Path: {clip['clip_path']}")
        print(f"  Time: {clip['start_time']:.2f}s - {clip['end_time']:.2f}s")
        print(f"  Duration: {clip['duration']:.2f}s")
        print(f"  Score: {clip['score']:.2f}")
        print(f"  File size: {os.path.getsize(clip['clip_path']) / (1024*1024):.2f} MB")
        print(f"  ASR segments: {len(clip['asr_subset'])}")
        
        if clip['asr_subset']:
            print(f"  Sample ASR (first 3):")
            for seg in clip['asr_subset'][:3]:
                print(f"    [{seg['start']:.2f}s - {seg['end']:.2f}s]: {seg['text']}")
    
    # Verification
    print("\n" + "="*70)
    print("Verification Status")
    print("="*70)
    
    checks = {
        "Clips created": len(result['clips']) > 0,
        "All clips have valid paths": all(os.path.exists(c['clip_path']) for c in result['clips']),
        "All clips have non-zero size": all(os.path.getsize(c['clip_path']) > 0 for c in result['clips']),
        "All clips have duration in range": all(15 <= c['duration'] <= 60 for c in result['clips']),
        "All clips have ASR subsets": all('asr_subset' in c for c in result['clips']),
        "Metadata file exists": os.path.exists("clips/clips_metadata.json")
    }
    
    all_passed = all(checks.values())
    
    for check, passed in checks.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {check}")
    
    print("\n" + "="*70)
    if all_passed:
        print("✅ All verification checks passed!")
        print("="*70)
        print(f"\nMetadata saved to: clips/clips_metadata.json")
        return 0
    else:
        print("❌ Some verification checks failed!")
        print("="*70)
        return 1

if __name__ == "__main__":
    sys.exit(main())
