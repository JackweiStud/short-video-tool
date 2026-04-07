#!/usr/bin/env python3
"""
Translator Module Verification Script

This script demonstrates the translator module functionality by:
1. Loading clips metadata
2. Translating ASR text to Chinese and English
3. Generating SRT subtitle files
4. Verifying all outputs

Usage:
    python verify_translator.py <clips_metadata_path>
    
Example:
    python verify_translator.py clips/clips_metadata.json
"""

import sys
import os
import json
from translator import Translator

def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_translator.py <clips_metadata_path>")
        print("\nExample:")
        print('  python verify_translator.py clips/clips_metadata.json')
        sys.exit(1)
    
    clips_metadata_path = sys.argv[1]
    
    if not os.path.exists(clips_metadata_path):
        print(f"Error: Clips metadata file not found: {clips_metadata_path}")
        sys.exit(1)
    
    print("="*70)
    print("Translator Module Verification")
    print("="*70)
    
    # Load clips metadata
    with open(clips_metadata_path, 'r', encoding='utf-8') as f:
        clips_metadata = json.load(f)
    
    print(f"\nClips metadata: {clips_metadata_path}")
    print(f"Total clips: {len(clips_metadata.get('clips', []))}")
    
    # Initialize translator
    translator = Translator()
    
    # Run translation
    print("\n" + "="*70)
    print("Running Translation...")
    print("="*70)
    result = translator.translate_clips(clips_metadata_path, output_dir="subtitles")
    
    if not result:
        print("\n❌ Translation failed!")
        sys.exit(1)
    
    # Display results
    print("\n" + "="*70)
    print("Translation Results Summary")
    print("="*70)
    
    print(f"\n✅ Total clips processed: {len(result['clips'])}")
    
    for i, clip in enumerate(result['clips'], 1):
        print(f"\n--- Clip {i} ---")
        print(f"  Path: {clip['clip_path']}")
        print(f"  Subtitle files:")
        for lang, path in clip['subtitle_files'].items():
            file_size = os.path.getsize(path) if os.path.exists(path) else 0
            print(f"    {lang}: {path} ({file_size} bytes)")
        
        # Show sample translations
        if 'translations' in clip:
            zh_count = len(clip['translations'].get('zh', []))
            en_count = len(clip['translations'].get('en', []))
            print(f"  Translation segments:")
            print(f"    Chinese: {zh_count} segments")
            print(f"    English: {en_count} segments")
            
            # Show first translation sample
            if clip['translations'].get('zh'):
                sample = clip['translations']['zh'][0]
                print(f"  Sample Chinese translation:")
                print(f"    [{sample['start']:.2f}s - {sample['end']:.2f}s]: {sample['text']}")
    
    # Verification
    print("\n" + "="*70)
    print("Verification Status")
    print("="*70)
    
    checks = {
        "Clips processed": len(result['clips']) > 0,
        "All clips have subtitle files": all('subtitle_files' in c for c in result['clips']),
        "All original subtitles exist": all(os.path.exists(c['subtitle_files']['original']) for c in result['clips']),
        "All Chinese subtitles exist": all(os.path.exists(c['subtitle_files']['zh']) for c in result['clips']),
        "All English subtitles exist": all(os.path.exists(c['subtitle_files']['en']) for c in result['clips']),
        "All subtitle files non-empty": all(
            os.path.getsize(c['subtitle_files'][lang]) > 0 
            for c in result['clips'] 
            for lang in ['original', 'zh', 'en']
        ),
        "Metadata file exists": os.path.exists("subtitles/translations_metadata.json")
    }
    
    all_passed = all(checks.values())
    
    for check, passed in checks.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {check}")
    
    print("\n" + "="*70)
    if all_passed:
        print("✅ All verification checks passed!")
        print("="*70)
        print(f"\nMetadata saved to: subtitles/translations_metadata.json")
        print("\nYou can now use these subtitle files with video players or editors.")
        return 0
    else:
        print("❌ Some verification checks failed!")
        print("="*70)
        return 1

if __name__ == "__main__":
    sys.exit(main())
