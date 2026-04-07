import os
import json
import shutil
import logging
from datetime import datetime
from typing import Dict, List, Optional

from config import Config, get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Integrator:
    def __init__(self, output_dir: Optional[str] = None, config: Optional[Config] = None):
        """
        Initialize Integrator.
        
        Args:
            output_dir: Root output directory
        """
        self.config = config or get_config()
        self.output_dir = output_dir or self.config.output_dir
        self.original_dir = os.path.join(self.output_dir, "original")
        self.clips_dir = os.path.join(self.output_dir, "clips")
        self.subtitles_dir = os.path.join(self.output_dir, "subtitles")
        self.analysis_dir = os.path.join(self.output_dir, "analysis")
        
    def integrate(self, 
                  video_path: str,
                  analysis_result_path: str,
                  clips_metadata_path: str,
                  translations_metadata_path: str) -> dict:
        """
        Integrate all module outputs into unified output structure.
        
        Args:
            video_path: Path to original video file
            analysis_result_path: Path to analysis result JSON
            clips_metadata_path: Path to clips metadata JSON
            translations_metadata_path: Path to translations metadata JSON
            
        Returns:
            dict: Integration result with statistics
        """
        logging.info("="*70)
        logging.info("Starting Integration Process")
        logging.info("="*70)
        
        start_time = datetime.now()
        
        # Validation
        if not self._validate_inputs(video_path, analysis_result_path, 
                                     clips_metadata_path, translations_metadata_path):
            return None
        
        # Create output directory structure
        self._create_output_structure()
        
        # Load metadata
        analysis_result = self._load_json(analysis_result_path)
        clips_metadata = self._load_json(clips_metadata_path)
        translations_metadata = self._load_json(translations_metadata_path)
        
        if not all([analysis_result, clips_metadata, translations_metadata]):
            logging.error("Failed to load metadata files")
            return None
        
        # F5.1 & F5.2: Copy files to output structure
        video_title = os.path.splitext(os.path.basename(video_path))[0]
        
        # Copy original video
        logging.info("\nCopying original video...")
        original_dest = os.path.join(self.original_dir, os.path.basename(video_path))
        if os.path.abspath(video_path) != os.path.abspath(original_dest):
            shutil.copy2(video_path, original_dest)
        else:
            logging.info(f"Original video already in destination, skipping copy: {original_dest}")
        logging.info(f"✅ Original video: {original_dest}")
        
        # Copy analysis result
        logging.info("\nCopying analysis result...")
        analysis_dest = os.path.join(self.analysis_dir, f"{video_title}_analysis.json")
        if os.path.abspath(analysis_result_path) != os.path.abspath(analysis_dest):
            shutil.copy2(analysis_result_path, analysis_dest)
        else:
            logging.info(f"Analysis result already in destination, skipping copy: {analysis_dest}")
        logging.info(f"✅ Analysis result: {analysis_dest}")
        
        # Copy clips and subtitles
        logging.info("\nCopying clips and subtitles...")
        clips_info = []
        
        for i, clip in enumerate(clips_metadata['clips'], 1):
            clip_path = clip['clip_path']
            
            if not os.path.exists(clip_path):
                logging.warning(f"Clip not found: {clip_path}")
                continue
            
            # Copy clip video
            clip_dest = os.path.join(self.clips_dir, os.path.basename(clip_path))
            if os.path.abspath(clip_path) != os.path.abspath(clip_dest):
                shutil.copy2(clip_path, clip_dest)
            else:
                logging.info(f"Clip already in destination, skipping copy: {clip_dest}")
            
            # Find corresponding translation
            translation = None
            for trans in translations_metadata['clips']:
                if os.path.basename(trans['clip_path']) == os.path.basename(clip_path):
                    translation = trans
                    break
            
            # Copy subtitle files
            clip_name = os.path.splitext(os.path.basename(clip_path))[0]
            subtitle_files = {}
            if translation and 'subtitle_files' in translation:
                subtitle_pairs = []
                for lang, sub_path in translation['subtitle_files'].items():
                    if not os.path.exists(sub_path):
                        continue
                    sub_dest = os.path.join(self.subtitles_dir, os.path.basename(sub_path))
                    subtitle_pairs.append((lang, sub_path, sub_dest))

                # 仅清理真正的旧目标文件；源路径与目标路径相同的情况绝不删除
                stale_targets = {
                    os.path.abspath(sub_dest)
                    for _, sub_path, sub_dest in subtitle_pairs
                    if os.path.abspath(sub_path) != os.path.abspath(sub_dest) and os.path.exists(sub_dest)
                }
                for stale in stale_targets:
                    try:
                        os.remove(stale)
                        logging.info(f"[Integrator] 清理旧字幕文件: {stale}")
                    except OSError as e:
                        logging.warning(f"[Integrator] 清理旧字幕失败: {stale}: {e}")

                for lang, sub_path, sub_dest in subtitle_pairs:
                    if os.path.abspath(sub_path) != os.path.abspath(sub_dest):
                        shutil.copy2(sub_path, sub_dest)
                    else:
                        logging.info(f"Subtitle already in destination, skipping copy: {sub_dest}")
                    subtitle_files[lang] = sub_dest
            
            clips_info.append({
                "clip_number": i,
                "clip_path": clip_dest,
                "start_time": clip['start_time'],
                "end_time": clip['end_time'],
                "duration": clip['duration'],
                "score": clip['score'],
                "subtitle_files": subtitle_files,
                "subtitle_burn": clip.get("subtitle_burn"),
            })
            
            logging.info(f"✅ Clip {i}: {os.path.basename(clip_dest)}")
        
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        # Prepare result
        result = {
            "video_title": video_title,
            "original_video": original_dest,
            "analysis_result": analysis_dest,
            "clips": clips_info,
            "statistics": {
                "total_clips": len(clips_info),
                "total_subtitles": sum(len(c['subtitle_files']) for c in clips_info),
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat()
            }
        }
        
        # F5.3: Generate summary report
        logging.info("\nGenerating summary report...")
        summary_path = self._generate_summary(result, analysis_result)
        result['summary_report'] = summary_path
        
        # Save integration metadata
        metadata_path = os.path.join(self.output_dir, "integration_metadata.json")
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        logging.info(f"\n{'='*70}")
        logging.info("Integration Complete!")
        logging.info(f"{'='*70}")
        logging.info(f"Output directory: {self.output_dir}")
        logging.info(f"Summary report: {summary_path}")
        logging.info(f"Processing time: {processing_time:.2f} seconds")
        
        return result
    
    def _validate_inputs(self, video_path: str, analysis_result_path: str,
                        clips_metadata_path: str, translations_metadata_path: str) -> bool:
        """Validate all input files exist."""
        files = {
            "Original video": video_path,
            "Analysis result": analysis_result_path,
            "Clips metadata": clips_metadata_path,
            "Translations metadata": translations_metadata_path
        }
        
        all_valid = True
        for name, path in files.items():
            if not os.path.exists(path):
                logging.error(f"{name} not found: {path}")
                all_valid = False
        
        return all_valid
    
    def _create_output_structure(self):
        """F5.4: Create output directory structure."""
        logging.info("\nCreating output directory structure...")
        
        dirs = [
            self.output_dir,
            self.original_dir,
            self.clips_dir,
            self.subtitles_dir,
            self.analysis_dir
        ]
        
        for dir_path in dirs:
            os.makedirs(dir_path, exist_ok=True)
            logging.info(f"✅ Created: {dir_path}")
    
    def _load_json(self, path: str) -> Optional[dict]:
        """Load JSON file."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load {path}: {e}")
            return None
    
    def _generate_summary(self, result: dict, analysis_result: dict) -> str:
        """F5.3: Generate Markdown summary report."""
        summary_path = os.path.join(self.output_dir, "summary.md")
        
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write("# 短视频智能剪辑与翻译 - 项目摘要报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("---\n\n")
            
            # Original video info
            f.write("## 原始视频信息\n\n")
            f.write(f"- **视频标题**: {result['video_title']}\n")
            f.write(f"- **视频路径**: `{result['original_video']}`\n")
            
            if 'video_path' in analysis_result:
                video_size = os.path.getsize(analysis_result['video_path']) / (1024*1024)
                f.write(f"- **文件大小**: {video_size:.2f} MB\n")
            
            f.write("\n")
            
            # Analysis info
            f.write("## 分析结果\n\n")
            f.write(f"- **分析结果文件**: `{result['analysis_result']}`\n")
            
            if 'asr_result' in analysis_result:
                f.write(f"- **ASR 文本段数**: {len(analysis_result['asr_result'])}\n")
            
            if 'audio_climax_points' in analysis_result:
                f.write(f"- **音频高潮点**: {len(analysis_result['audio_climax_points'])} 个\n")
            
            if 'scene_changes' in analysis_result:
                f.write(f"- **场景切换点**: {len(analysis_result['scene_changes'])} 个\n")
            
            f.write("\n")
            
            # Clips info
            f.write("## 剪辑片段\n\n")
            f.write(f"**总计**: {result['statistics']['total_clips']} 个片段\n\n")
            
            for clip in result['clips']:
                f.write(f"### Clip {clip['clip_number']}\n\n")
                f.write(f"- **文件**: `{os.path.basename(clip['clip_path'])}`\n")
                f.write(f"- **时间范围**: {clip['start_time']:.2f}s - {clip['end_time']:.2f}s\n")
                f.write(f"- **时长**: {clip['duration']:.2f}s\n")
                f.write(f"- **评分**: {clip['score']:.2f}\n")
                
                if clip['subtitle_files']:
                    f.write(f"- **字幕文件**:\n")
                    for lang, path in clip['subtitle_files'].items():
                        lang_name = {"original": "原始", "zh": "中文", "en": "英文"}.get(lang, lang)
                        f.write(f"  - {lang_name}: `{os.path.basename(path)}`\n")

                subtitle_burn = clip.get("subtitle_burn") or {}
                policy_summary = subtitle_burn.get("subtitle_burn_policy_summary")
                if policy_summary:
                    f.write(f"- **最终策略**: {policy_summary}\n")
                if subtitle_burn.get("auto_final_action"):
                    f.write(f"- **最终动作**: `{subtitle_burn.get('auto_final_action')}`\n")
                if subtitle_burn.get("burn_renderer"):
                    f.write(f"- **渲染器**: `{subtitle_burn.get('burn_renderer')}`\n")
                
                f.write("\n")
            
            # Statistics
            f.write("## 统计信息\n\n")
            f.write(f"- **剪辑片段总数**: {result['statistics']['total_clips']}\n")
            f.write(f"- **字幕文件总数**: {result['statistics']['total_subtitles']}\n")
            f.write(f"- **处理耗时**: {result['statistics']['processing_time']:.2f} 秒\n")
            
            f.write("\n---\n\n")
            f.write("## 输出目录结构\n\n")
            f.write("```\n")
            f.write(f"{self.output_dir}/\n")
            f.write("├── original/          # 原始视频\n")
            f.write("├── clips/             # 剪辑视频\n")
            f.write("├── subtitles/         # 字幕文件\n")
            f.write("├── analysis/          # 分析结果\n")
            f.write("├── summary.md         # 项目摘要报告（本文件）\n")
            f.write("└── integration_metadata.json  # 整合元数据\n")
            f.write("```\n")
        
        logging.info(f"✅ Summary report: {summary_path}")
        return summary_path


if __name__ == "__main__":
    # Test integration
    integrator = Integrator(output_dir="output")
    
    # Input paths
    video_path = "downloads/OpenClaw小龙虾保姆级安装教程！小白10分钟搞定【Windows系统】.mp4"
    analysis_result_path = "analysis_results/analysis_result.json"
    clips_metadata_path = "clips/clips_metadata.json"
    translations_metadata_path = "subtitles/translations_metadata.json"
    
    # Check if all files exist
    if not all(os.path.exists(p) for p in [video_path, analysis_result_path, 
                                            clips_metadata_path, translations_metadata_path]):
        logging.error("Some input files are missing!")
        exit(1)
    
    # Run integration
    result = integrator.integrate(
        video_path=video_path,
        analysis_result_path=analysis_result_path,
        clips_metadata_path=clips_metadata_path,
        translations_metadata_path=translations_metadata_path
    )
    
    if result:
        logging.info("\n✅ Integration successful!")
    else:
        logging.error("\n❌ Integration failed!")
