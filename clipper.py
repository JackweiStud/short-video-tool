import os
import json
import logging
import subprocess
from typing import List, Dict, Tuple, Optional

from config import Config, get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Clipper:
    def __init__(
        self,
        min_duration: int = 15,
        max_duration: int = 60,
        max_clips: Optional[int] = None,
        config: Optional[Config] = None,
    ):
        """
        Initialize Clipper.
        
        Args:
            min_duration: Minimum clip duration in seconds (default: 15)
            max_duration: Maximum clip duration in seconds (default: 60)
        """
        self.config = config or get_config()
        self.min_duration = min_duration
        self.max_duration = max_duration
        self.max_clips = max_clips if max_clips is not None else self.config.max_clips
        self.ffmpeg_timeout = self.config.ffmpeg_timeout
    
    def clip_video(self, video_path: str, analysis_result: dict, output_dir: Optional[str] = None) -> dict:
        """
        Clip video based on analysis results.
        
        Args:
            video_path: Path to original video file
            analysis_result: Analysis result from Analyzer module
            output_dir: Directory to save clipped videos
            
        Returns:
            dict: {
                "clips": [
                    {
                        "clip_path": "...",
                        "start_time": 0.0,
                        "end_time": 30.0,
                        "duration": 30.0,
                        "score": 0.85,
                        "asr_subset": [...]
                    }
                ]
            }
        """
        output_dir = output_dir or self.config.clips_dir

        logging.info(f"Starting video clipping for: {video_path}")
        
        # Validation: Check video file
        if not video_path:
            logging.error("Video path is empty")
            return None
        
        if not os.path.exists(video_path):
            logging.error(f"Video file not found: {video_path}")
            return None
        
        if not os.access(video_path, os.R_OK):
            logging.error(f"Video file is not readable: {video_path}")
            return None
        
        file_size = os.path.getsize(video_path)
        if file_size == 0:
            logging.error(f"Video file is empty: {video_path}")
            return None
        
        # Validation: Check analysis result
        if not analysis_result:
            logging.error("Analysis result is empty")
            return None
        
        if not isinstance(analysis_result, dict):
            logging.error("Analysis result must be a dictionary")
            return None
        
        # Validate required fields
        required_fields = ['audio_climax_points', 'scene_changes', 'asr_result']
        for field in required_fields:
            if field not in analysis_result:
                logging.error(f"Analysis result missing required field: {field}")
                return None
        
        # Get data with defaults
        climax_points = analysis_result.get('audio_climax_points', [])
        scene_changes = analysis_result.get('scene_changes', [])
        asr_result = analysis_result.get('asr_result', [])
        topic_segments = analysis_result.get('topic_segments', [])
        
        # Validate data types
        if not isinstance(climax_points, list):
            logging.error("audio_climax_points must be a list")
            return None
        
        if not isinstance(scene_changes, list):
            logging.error("scene_changes must be a list")
            return None
        
        if not isinstance(asr_result, list):
            logging.error("asr_result must be a list")
            return None
        
        # Check if we have enough data to clip
        if not topic_segments and not climax_points and not scene_changes:
            logging.error("No topic segments, climax points or scene changes found - cannot identify segments")
            return {"clips": [], "error": "No data for segment identification"}
        
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create output directory: {e}")
            return None
        
        # F3.1: Identify key segments (prioritize topic_segments)
        logging.info("Identifying key segments...")
        segmentation_meta = analysis_result.get("segmentation_meta", {})
        clip_strategy = segmentation_meta.get("clip_strategy_used", "unknown")

        if topic_segments:
            logging.info(f"Using topic-based segmentation: {len(topic_segments)} segments")
            candidate_segments = self._segments_from_topics(
                topic_segments,
                analysis_result.get('topic_summaries', []),
                asr_result
            )
            candidate_segments = self._normalize_topic_candidate_durations(
                candidate_segments,
                clip_strategy,
                asr_result=asr_result,
            )
            candidate_segments = self._select_topic_candidates(candidate_segments)
        elif clip_strategy in ("opinion", "topic"):
            logging.warning(
                "No topic segments found for clip strategy '%s'; skipping fallback segmentation",
                clip_strategy,
            )
            candidate_segments = []
        else:
            logging.info("Falling back to climax/scene-based segmentation")
            candidate_segments = self._identify_key_segments(
                climax_points,
                scene_changes,
                asr_result
            )
        
        if not candidate_segments:
            logging.warning("No suitable segments found")
            return {"clips": []}
        
        logging.info(f"Found {len(candidate_segments)} candidate segments")
        
        # F3.2: Clip videos
        clips = []
        video_basename = os.path.splitext(os.path.basename(video_path))[0]
        
        for i, segment in enumerate(candidate_segments, 1):
            start_time = segment['start']
            end_time = segment['end']
            duration = end_time - start_time
            
            # Generate output filename
            clip_filename = f"{video_basename}_clip_{i}.mp4"
            clip_path = os.path.join(output_dir, clip_filename)
            
            # F3.2, F3.3, F3.4: Clip video with FFmpeg
            logging.info(f"Clipping segment {i}: {start_time:.2f}s - {end_time:.2f}s ({duration:.2f}s)")
            success = self._clip_with_ffmpeg(video_path, clip_path, start_time, end_time)
            
            if success:
                # Extract ASR subset for this clip
                asr_subset = self._extract_asr_subset(
                    analysis_result['asr_result'],
                    start_time,
                    end_time
                )
                
                clips.append({
                    "clip_path": clip_path,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration": duration,
                    "score": segment['score'],
                    "asr_subset": asr_subset,
                    # opinion fields from analyzer
                    "topic": segment.get("topic", ""),
                    "summary": segment.get("summary", ""),
                    "reason": segment.get("reason", ""),
                    "stance": segment.get("stance", ""),
                    "self_contained": segment.get("self_contained"),
                    "publishability": segment.get("publishability"),
                    "key_sentences": segment.get("key_sentences", []),
                    "conclusion_clarity": segment.get("conclusion_clarity"),
                    "info_density": segment.get("info_density"),
                    "viral_fit": segment.get("viral_fit"),
                })
                
                logging.info(f"✅ Clip {i} created: {clip_path}")
            else:
                logging.error(f"❌ Failed to create clip {i}")
        
        # Save metadata — segmentation_meta already extracted at top
        if not segmentation_meta:
            segmentation_meta = {
                "clip_strategy_used": "unknown",
                "segmentation_effective": False,
                "fallback_reason": "meta_not_in_analysis_result",
            }
        result = {
            "original_video": video_path,
            "clips": clips,
            "segmentation_meta": segmentation_meta,
        }
        
        metadata_path = os.path.join(output_dir, "clips_metadata.json")
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        logging.info(f"Clipping complete: {len(clips)} clips created")
        logging.info(f"Metadata saved to: {metadata_path}")
        
        return result
    
    def _identify_key_segments(self, climax_points: List[Dict], scene_changes: List[float], asr_result: List[Dict]) -> List[Dict]:
        """
        F3.1: Identify key segments based on climax points and scene changes.
        
        Fallback strategies:
        - If no climax points: use scene changes to create segments
        - If no scene changes: use fixed intervals around climax points
        
        Returns:
            List of segments: [{"start": 0.0, "end": 30.0, "score": 0.85}, ...]
        """
        # Case 1: No climax points, but have scene changes
        if not climax_points and scene_changes:
            logging.warning("No climax points provided, using scene changes as fallback")
            return self._segments_from_scene_changes(scene_changes)
        
        # Case 2: No climax points and no scene changes
        if not climax_points and not scene_changes:
            logging.error("No climax points or scene changes - cannot identify segments")
            return []
        
        # Case 3: Have climax points (normal case)
        segments = []
        
        for climax in climax_points:
            climax_time = climax['time']
            climax_score = climax['score']
            
            # Find nearest scene changes before and after climax
            before_scenes = [s for s in scene_changes if s < climax_time]
            after_scenes = [s for s in scene_changes if s > climax_time]
            
            # Determine segment boundaries
            # Try to center the climax in the segment
            target_duration = (self.min_duration + self.max_duration) / 2
            half_duration = target_duration / 2
            
            ideal_start = climax_time - half_duration
            ideal_end = climax_time + half_duration
            
            # Adjust to nearest scene changes (if available)
            if scene_changes:
                if before_scenes:
                    # Find scene change closest to ideal_start
                    start_time = min(before_scenes, key=lambda x: abs(x - ideal_start))
                else:
                    start_time = max(0, ideal_start)
                
                if after_scenes:
                    # Find scene change closest to ideal_end
                    end_time = min(after_scenes, key=lambda x: abs(x - ideal_end))
                else:
                    end_time = ideal_end
            else:
                # No scene changes: use fixed intervals
                start_time = max(0, ideal_start)
                end_time = ideal_end
            
            # Ensure duration constraints
            duration = end_time - start_time
            
            if duration < self.min_duration:
                # Extend segment
                needed = self.min_duration - duration
                end_time += needed
            elif duration > self.max_duration:
                # Trim segment
                excess = duration - self.max_duration
                end_time -= excess
            
            # Ensure start_time >= 0
            if start_time < 0:
                start_time = 0
            
            segments.append({
                "start": start_time,
                "end": end_time,
                "score": climax_score
            })
        
        # Remove overlapping segments (keep higher score)
        segments = self._remove_overlaps(segments)
        
        # Sort by score (descending) for selection, then reorder by start_time for output
        segments.sort(key=lambda x: x['score'], reverse=True)
        segments = segments[:self.max_clips] if len(segments) > self.max_clips else segments
        segments.sort(key=lambda x: x['start'])
        
        return segments
    
    def _segments_from_scene_changes(self, scene_changes: List[float]) -> List[Dict]:
        """
        Fallback: Create segments from scene changes when no climax points available.
        
        Strategy: Group consecutive scenes into segments of appropriate duration.
        """
        if not scene_changes:
            return []
        
        segments = []
        target_duration = (self.min_duration + self.max_duration) / 2
        
        i = 0
        while i < len(scene_changes) - 1:
            start_time = scene_changes[i]
            
            # Find end scene that gives us target duration
            end_time = start_time + target_duration
            
            # Find the scene change closest to target end time
            remaining_scenes = [s for s in scene_changes[i+1:] if s > start_time]
            
            if remaining_scenes:
                # Find closest scene to target end
                end_time = min(remaining_scenes, key=lambda x: abs(x - end_time))
                
                # Move index to this scene
                i = scene_changes.index(end_time)
            else:
                # No more scenes, use target end
                i = len(scene_changes)
            
            duration = end_time - start_time
            
            # Only add if duration is reasonable
            if self.min_duration <= duration <= self.max_duration * 1.5:
                segments.append({
                    "start": start_time,
                    "end": end_time,
                    "score": 1.0  # Default score for scene-based segments
                })
            
            # Limit to configured max clips
            if len(segments) >= self.max_clips:
                break
        
        return segments
    
    def _segments_from_topics(
        self,
        topic_segments: List[Dict],
        topic_summaries: List[Dict],
        asr_result: List[Dict],
    ) -> List[Dict]:
        """
        Create ranked segments from topic-based segmentation.

        Topic segments are the primary signal. We refine them by:
        - using the LLM-provided quality score as the base rank
        - favoring clips whose duration is close to the ideal window
        - rewarding segments with rich ASR coverage
        - preserving semantic summaries/reasons for downstream reporting
        """
        if not topic_segments:
            return []

        topic_segments = self._merge_overlapping_topic_segments(topic_segments)

        summary_lookup = {
            str(entry.get("topic", "")).strip(): entry
            for entry in topic_summaries
            if str(entry.get("topic", "")).strip()
        }

        candidates: List[Dict] = []

        for topic_seg in topic_segments:
            start = float(topic_seg.get("start", 0.0))
            end = float(topic_seg.get("end", 0.0))
            if end <= start:
                continue

            topic = str(topic_seg.get("topic", "")).strip() or "Untitled Topic"
            summary_entry = summary_lookup.get(topic, {})
            summary = str(
                topic_seg.get("summary")
                or summary_entry.get("summary")
                or topic
            ).strip()
            reason = str(
                topic_seg.get("reason")
                or summary_entry.get("reason")
                or summary
            ).strip()
            base_score = self._coerce_topic_score(
                topic_seg.get("score", summary_entry.get("score", 70.0))
            )

            if end - start > self.max_duration:
                logging.info(f"Splitting long topic segment ({end - start:.1f}s): {topic}")

            for split_start, split_end in self._split_topic_segment(start, end, asr_result):
                duration = split_end - split_start
                if duration <= 0:
                    continue

                clip_fit_score = self._score_topic_clip_fit(
                    start=split_start,
                    end=split_end,
                    asr_result=asr_result,
                )

                candidates.append({
                    "start": split_start,
                    "end": split_end,
                    "score": base_score,
                    "topic_score": base_score,
                    "clip_fit_score": clip_fit_score,
                    "topic": topic,
                    "summary": summary,
                    "reason": reason,
                    # opinion fields — pass-through from analyzer, None if not present
                    "conclusion_clarity": topic_seg.get("conclusion_clarity"),
                    "self_contained": topic_seg.get("self_contained"),
                    "info_density": topic_seg.get("info_density"),
                    "viral_fit": topic_seg.get("viral_fit"),
                    "stance": topic_seg.get("stance", ""),
                    "key_sentences": topic_seg.get("key_sentences", []),
                    "publishability": topic_seg.get("publishability"),
                })

        candidates.sort(key=lambda x: x["start"])

        return candidates

    def _normalize_topic_candidate_durations(
        self,
        candidates: List[Dict],
        clip_strategy: str,
        asr_result: List[Dict] | None = None,
    ) -> List[Dict]:
        """
        Normalize topic candidates so topic-based clipping obeys the same
        duration contract as the fallback path.
        """
        if not candidates:
            return candidates

        merged = self._merge_short_topic_candidates(candidates, clip_strategy)
        return self._pad_edge_topic_candidates(merged, asr_result=asr_result or [])

    def _merge_short_topic_candidates(
        self,
        candidates: List[Dict],
        clip_strategy: str,
    ) -> List[Dict]:
        """Merge adjacent topic candidates before ranking/selection."""
        target = self._target_topic_duration()
        ceiling = float(self.max_duration)
        gap_limit = 30.0 if clip_strategy == "opinion" else 5.0

        ordered = sorted(candidates, key=lambda item: item["start"])
        merged: List[Dict] = [ordered[0]]

        for current in ordered[1:]:
            last = merged[-1]

            last_dur = last["end"] - last["start"]
            curr_dur = current["end"] - current["start"]
            gap = current["start"] - last["end"]
            combined_dur = current["end"] - last["start"]
            continuity = self._topic_candidate_continuity(last, current)

            # Decide whether to merge
            should_merge = False
            if gap < gap_limit and combined_dur <= ceiling:
                if clip_strategy == "topic":
                    # Topic mode should preserve chapter integrity, but it still
                    # needs to satisfy the hard min/max contract. Merge short
                    # adjacent windows when required, but avoid merging already
                    # valid medium windows just to chase the target duration.
                    if last_dur < self.min_duration and curr_dur < self.min_duration:
                        should_merge = True
                    elif last_dur < self.min_duration or curr_dur < self.min_duration:
                        should_merge = True
                    elif continuity >= 0.95 and last_dur < target and curr_dur < target:
                        should_merge = True
                elif clip_strategy == "hybrid":
                    if last_dur < self.min_duration and curr_dur < self.min_duration:
                        should_merge = True
                    elif last_dur < self.min_duration or curr_dur < self.min_duration:
                        should_merge = True
                    elif continuity >= 0.8 and last_dur < target and curr_dur < target:
                        should_merge = True
                else:
                    # Opinion mode can be more aggressive as long as adjacent
                    # windows improve the publishable duration fit.
                    if last_dur < self.min_duration and curr_dur < self.min_duration:
                        should_merge = True
                    elif last_dur < self.min_duration or curr_dur < self.min_duration:
                        last_delta = abs(last_dur - target)
                        combined_delta = abs(combined_dur - target)
                        should_merge = combined_delta < last_delta
                    elif continuity >= 0.25 and last_dur < target and curr_dur < target:
                        combined_delta = abs(combined_dur - target)
                        best_single = min(abs(last_dur - target), abs(curr_dur - target))
                        should_merge = combined_delta < best_single

            if should_merge:
                merged[-1] = self._merge_topic_segment(last, current)
                logging.info(
                    f"  ↳ Merged {clip_strategy} segments: "
                    f"{last['start']:.1f}-{last['end']:.1f}s + "
                    f"{current['start']:.1f}-{current['end']:.1f}s → "
                    f"{merged[-1]['start']:.1f}-{merged[-1]['end']:.1f}s "
                    f"({merged[-1]['end'] - merged[-1]['start']:.1f}s)"
                )
            else:
                merged.append(current)

        before = len(ordered)
        after = len(merged)
        if before != after:
            avg_dur = (
                sum(s["end"] - s["start"] for s in merged) / after
                if after else 0
            )
            logging.info(
                f"Topic duration merge ({clip_strategy}): {before} → {after} segments "
                f"(target={target:.0f}s, avg={avg_dur:.0f}s)"
            )

        return merged

    def _topic_candidate_continuity(self, left: Dict, right: Dict) -> float:
        topic_similarity = self._topic_similarity(
            left.get("topic", ""),
            right.get("topic", ""),
        )
        summary_similarity = self._topic_similarity(
            left.get("summary", ""),
            right.get("summary", ""),
        )
        stance_similarity = self._topic_similarity(
            left.get("stance", ""),
            right.get("stance", ""),
        )
        return max(topic_similarity, summary_similarity, stance_similarity)

    def _pad_edge_topic_candidates(
        self,
        candidates: List[Dict],
        asr_result: List[Dict] | None = None,
    ) -> List[Dict]:
        """Extend short edge candidates, preferring nearby ASR boundaries over raw second offsets."""
        if not candidates:
            return candidates

        ordered = sorted((dict(item) for item in candidates), key=lambda item: item["start"])
        timeline_end = ordered[-1]["end"]

        for index, candidate in enumerate(ordered):
            duration = candidate["end"] - candidate["start"]
            if duration >= self.min_duration:
                continue

            previous_end = ordered[index - 1]["end"] if index > 0 else 0.0
            next_start = ordered[index + 1]["start"] if index + 1 < len(ordered) else timeline_end

            needed = self.min_duration - duration
            available_right = max(0.0, next_start - candidate["end"])
            extend_right = min(needed, available_right)
            if extend_right > 0:
                original_end = candidate["end"]
                padded_end = self._snap_topic_boundary(
                    current=candidate["end"],
                    target=candidate["end"] + extend_right,
                    lower_bound=candidate["end"],
                    upper_bound=candidate["end"] + available_right,
                    asr_result=asr_result or [],
                    direction="right",
                )
                candidate["end"] = max(candidate["end"], padded_end)
                needed -= max(0.0, candidate["end"] - original_end)

            if needed > 0:
                available_left = max(0.0, candidate["start"] - previous_end)
                extend_left = min(needed, available_left)
                if extend_left > 0:
                    original_start = candidate["start"]
                    padded_start = self._snap_topic_boundary(
                        current=candidate["start"],
                        target=candidate["start"] - extend_left,
                        lower_bound=candidate["start"] - available_left,
                        upper_bound=candidate["start"],
                        asr_result=asr_result or [],
                        direction="left",
                    )
                    candidate["start"] = min(candidate["start"], padded_start)
                    needed -= max(0.0, original_start - candidate["start"])

            if needed > 0 and index + 1 == len(ordered):
                original_end = candidate["end"]
                candidate["end"] = self._snap_topic_boundary(
                    current=candidate["end"],
                    target=candidate["end"] + needed,
                    lower_bound=candidate["end"],
                    upper_bound=candidate["end"] + needed,
                    asr_result=asr_result or [],
                    direction="right",
                )
                needed -= max(0.0, candidate["end"] - original_end)
                timeline_end = candidate["end"]

        return ordered

    def _snap_topic_boundary(
        self,
        current: float,
        target: float,
        lower_bound: float,
        upper_bound: float,
        asr_result: List[Dict],
        direction: str,
    ) -> float:
        """Prefer snapping repaired boundaries to nearby ASR segment ends/starts."""
        if upper_bound <= lower_bound:
            return current

        candidates = []
        for seg in asr_result or []:
            try:
                seg_start = float(seg.get("start", 0.0))
                seg_end = float(seg.get("end", 0.0))
            except Exception:
                continue
            if lower_bound <= seg_start <= upper_bound:
                candidates.append(seg_start)
            if lower_bound <= seg_end <= upper_bound:
                candidates.append(seg_end)

        candidates = sorted(set(candidates))
        if candidates:
            if direction == "right":
                valid = [point for point in candidates if point >= current]
            else:
                valid = [point for point in candidates if point <= current]
            if valid:
                return min(valid, key=lambda point: abs(point - target))

        return max(lower_bound, min(target, upper_bound))

    def _select_topic_candidates(self, candidates: List[Dict]) -> List[Dict]:
        """Select the highest-scoring non-overlapping topic candidates."""
        if not candidates:
            return []

        ranked = sorted(
            candidates,
            key=lambda item: (
                -float(item.get("score", 0.0)),
                -float(item.get("clip_fit_score", 0.0)),
                item["start"],
            ),
        )
        if len(ranked) > self.max_clips:
            logging.info(
                "Limiting ranked topic segments from %s to %s",
                len(ranked),
                self.max_clips,
            )

        selected: List[Dict] = []
        for candidate in ranked:
            if any(
                candidate["start"] < chosen["end"] and candidate["end"] > chosen["start"]
                for chosen in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= self.max_clips:
                break

        selected.sort(key=lambda item: item["start"])
        return selected

    def _split_topic_segment(
        self,
        start: float,
        end: float,
        asr_result: List[Dict],
    ) -> List[tuple]:
        """
        Split long topic segments at nearby ASR boundaries when possible.

        The goal is to keep chapter boundaries semantically clean instead of
        slicing at arbitrary fixed offsets.
        """
        duration = end - start
        if duration <= self.max_duration:
            return [(start, end)]

        if not asr_result:
            return self._split_topic_segment_fixed(start, end)

        boundaries = sorted({
            float(seg.get("end", 0.0))
            for seg in asr_result
            if start < float(seg.get("end", 0.0)) < end
        })

        if not boundaries:
            return self._split_topic_segment_fixed(start, end)

        chunks = []
        cursor = start
        target = self._target_topic_duration()

        while cursor < end:
            remaining = end - cursor
            if remaining <= self.max_duration:
                chunk_end = end
            else:
                lower_bound = cursor + self.min_duration
                # Reserve at least one valid min-duration tail when we split.
                upper_bound = min(cursor + self.max_duration, end - self.min_duration)
                if upper_bound < lower_bound:
                    upper_bound = min(cursor + self.max_duration, end)
                ideal_end = min(cursor + target, upper_bound)
                candidates = [b for b in boundaries if lower_bound <= b <= upper_bound]

                if candidates:
                    chunk_end = min(candidates, key=lambda b: abs(b - ideal_end))
                else:
                    chunk_end = ideal_end

                if chunk_end <= cursor:
                    chunk_end = min(cursor + self.max_duration, end)

            if chunk_end <= cursor:
                break

            chunks.append((cursor, chunk_end))
            cursor = chunk_end

        if not chunks:
            return self._split_topic_segment_fixed(start, end)

        tail = end - chunks[-1][1]
        if len(chunks) >= 2 and 0 < tail < self.min_duration:
            previous_start, _ = chunks[-2]
            chunks[-2] = (previous_start, end)
            chunks.pop()
        elif 0 < tail < self.min_duration:
            previous_start, _ = chunks[-1]
            chunks[-1] = (previous_start, end)

        return chunks

    def _split_topic_segment_fixed(self, start: float, end: float) -> List[tuple]:
        """Fallback split strategy for long topic segments."""
        chunks = []
        cursor = start
        target = self._target_topic_duration()

        while cursor < end:
            remaining = end - cursor
            if remaining <= self.max_duration:
                chunk_end = end
            else:
                upper_bound = min(cursor + self.max_duration, end - self.min_duration)
                if upper_bound <= cursor:
                    upper_bound = min(cursor + self.max_duration, end)
                chunk_end = min(cursor + target, upper_bound, end)

            if chunk_end <= cursor:
                break

            chunks.append((cursor, chunk_end))
            cursor = chunk_end

        tail = end - chunks[-1][1]
        if len(chunks) >= 2 and 0 < tail < self.min_duration:
            previous_start, _ = chunks[-2]
            chunks[-2] = (previous_start, end)
            chunks.pop()
        elif chunks and 0 < tail < self.min_duration:
            previous_start, _ = chunks[-1]
            chunks[-1] = (previous_start, end)

        return chunks

    @staticmethod
    def _topic_tokens(value: str) -> set:
        import re
        return {
            token
            for token in re.findall(r"[A-Za-z0-9]+|[一-鿿]", str(value).lower())
            if token
        }

    def _topic_similarity(self, left: str, right: str) -> float:
        left_tokens = self._topic_tokens(left)
        right_tokens = self._topic_tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _should_merge_topic_segments(self, previous: Dict, current: Dict) -> bool:
        overlap = min(float(previous.get("end", 0.0)), float(current.get("end", 0.0))) - max(float(previous.get("start", 0.0)), float(current.get("start", 0.0)))
        gap = float(current.get("start", 0.0)) - float(previous.get("end", 0.0))
        topic_similarity = self._topic_similarity(previous.get("topic", ""), current.get("topic", ""))
        summary_similarity = self._topic_similarity(previous.get("summary", ""), current.get("summary", ""))

        if overlap > 0 and (topic_similarity >= 0.2 or summary_similarity >= 0.2):
            return True
        if 0 <= gap <= 3.0 and (topic_similarity >= 0.75 or summary_similarity >= 0.75):
            return True
        return False

    @staticmethod
    def _merge_segment_text(values: List[str], limit: int = 3) -> str:
        import re
        parts = []
        seen = set()
        for value in values:
            if not value:
                continue
            for fragment in re.split(r"(?<=[。！？.!?])\s+", str(value).strip()):
                fragment = fragment.strip(" \t\r\n。；;，,")
                if not fragment or fragment in seen:
                    continue
                seen.add(fragment)
                parts.append(fragment)
                if len(parts) >= limit:
                    return " ".join(parts).strip()
        return " ".join(parts).strip()

    @staticmethod
    def _merge_unique_list(existing: List[str], incoming: List[str], limit: int = 3) -> List[str]:
        merged = []
        seen = set()
        for value in list(existing or []) + list(incoming or []):
            value = str(value).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
            if len(merged) >= limit:
                break
        return merged

    def _merge_topic_segment(self, target: Dict, source: Dict) -> Dict:
        merged = dict(target)
        merged["start"] = min(float(target.get("start", 0.0)), float(source.get("start", 0.0)))
        merged["end"] = max(float(target.get("end", 0.0)), float(source.get("end", 0.0)))
        merged["score"] = max(float(target.get("score", 0.0)), float(source.get("score", 0.0)))
        merged["topic"] = target.get("topic") or source.get("topic") or "Untitled Topic"
        merged["summary"] = self._merge_segment_text([target.get("summary", ""), source.get("summary", "")], limit=3) or merged["topic"]
        merged["reason"] = self._merge_segment_text([target.get("reason", ""), source.get("reason", "")], limit=3) or merged["summary"]

        numeric_fields = ["conclusion_clarity", "self_contained", "info_density", "viral_fit", "publishability"]
        for field in numeric_fields:
            values = [value for value in (target.get(field), source.get(field)) if isinstance(value, (int, float))]
            if values:
                merged[field] = max(values)

        target_stance = str(target.get("stance", "")).strip()
        source_stance = str(source.get("stance", "")).strip()
        if source_stance and (not target_stance or len(source_stance) > len(target_stance)):
            merged["stance"] = source_stance
        else:
            merged["stance"] = target_stance

        merged["key_sentences"] = self._merge_unique_list(
            target.get("key_sentences", []),
            source.get("key_sentences", []),
            limit=3,
        )
        return merged

    def _merge_overlapping_topic_segments(self, topic_segments: List[Dict]) -> List[Dict]:
        if not topic_segments:
            return []

        normalized = []
        for seg in topic_segments:
            try:
                normalized.append({
                    **seg,
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "score": self._coerce_topic_score(seg.get("score", 0.0)),
                })
            except Exception:
                continue

        normalized.sort(key=lambda item: (item["start"], item["end"], -item["score"]))
        merged = [normalized[0]]
        for seg in normalized[1:]:
            last = merged[-1]
            if self._should_merge_topic_segments(last, seg):
                merged[-1] = self._merge_topic_segment(last, seg)
            else:
                merged.append(seg)
        return merged

    def _target_topic_duration(self) -> float:
        """Return the ideal duration for a topic-based clip."""
        return max(
            float(self.min_duration),
            min(float(self.max_duration), (float(self.min_duration) + float(self.max_duration)) / 2.0),
        )

    def _coerce_topic_score(self, raw_score) -> float:
        """Normalize LLM topic scores into a 0-100 range."""
        try:
            score = float(raw_score)
        except Exception:
            return 70.0

        if score <= 0:
            return 0.0
        if score <= 1.0:
            score *= 100.0
        elif score <= 10.0:
            score *= 10.0

        return max(0.0, min(score, 100.0))

    def _score_topic_clip_fit(
        self,
        start: float,
        end: float,
        asr_result: List[Dict],
    ) -> float:
        """
        Score only the technical fit of a candidate window.

        Semantic ranking should come from the upstream strategy output
        (`score` from analyzer/LLM). This helper only supplies a tie-break
        based on duration fit and speech coverage.
        """
        duration = max(end - start, 0.0)
        if duration <= 0:
            return 0.0

        target = self._target_topic_duration()
        duration_fit = 1.0 - min(1.0, abs(duration - target) / max(target, 1.0))
        duration_score = duration_fit * 60.0

        speech_bonus = 0.0
        if asr_result:
            overlap_segments = self._extract_asr_subset(asr_result, start, end)
            if overlap_segments:
                covered_duration = sum(
                    max(0.0, float(seg["end"]) - float(seg["start"]))
                    for seg in overlap_segments
                )
                speech_ratio = min(1.0, covered_duration / duration)
                speech_bonus = speech_ratio * 40.0

        return round(max(0.0, min(duration_score + speech_bonus, 100.0)), 2)

    
    def _remove_overlaps(self, segments: List[Dict]) -> List[Dict]:
        """Remove overlapping segments, keeping the one with higher score."""
        if len(segments) <= 1:
            return segments
        
        # Sort by start time
        sorted_segments = sorted(segments, key=lambda x: x['start'])
        
        result = [sorted_segments[0]]
        
        for current in sorted_segments[1:]:
            last = result[-1]
            
            # Check for overlap
            if current['start'] < last['end']:
                # Overlap detected, keep the one with higher score
                if current['score'] > last['score']:
                    result[-1] = current
            else:
                # No overlap
                result.append(current)
        
        return result
    
    def _clip_with_ffmpeg(self, input_path: str, output_path: str, start_time: float, end_time: float) -> bool:
        """
        F3.2, F3.3, F3.4: Clip video using FFmpeg.
        
        - Maintains original resolution and aspect ratio (F3.3)
        - Ensures audio-video sync (F3.4)
        """
        try:
            duration = end_time - start_time

            # FFmpeg command — accurate seek mode:
            #   -ss AFTER -i = frame-accurate seek (slightly slower but avoids
            #   the up-to-GOP-size drift that occurs when -ss is placed before -i
            #   and combined with -c copy, which is the root cause of subtitles
            #   appearing 0.5-2s ahead of the corresponding audio).
            #
            #   We re-encode video (libx264 fast preset) so every clip starts on
            #   a real keyframe and timestamps are exact.  Audio is copied as-is.
            cmd = [
                "ffmpeg",
                "-i", input_path,
                "-ss", str(start_time),   # accurate seek (after -i)
                "-t", str(duration),
                "-c:v", "libx264",        # re-encode video for clean keyframes
                "-preset", "fast",
                "-crf", "18",             # visually lossless quality
                "-c:a", "copy",           # keep original audio track
                "-avoid_negative_ts", "make_zero",
                "-y",
                output_path
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.ffmpeg_timeout
            )
            
            if result.returncode != 0:
                logging.error(f"FFmpeg error: {result.stderr}")
                return False
            
            # Verify output file
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                logging.error("Output file is empty or missing")
                return False
            
            return True
            
        except subprocess.TimeoutExpired:
            logging.error("FFmpeg clipping timed out")
            return False
        except Exception as e:
            logging.error(f"Failed to clip video: {e}")
            return False
    
    def _extract_asr_subset(self, asr_result: List[Dict], start_time: float, end_time: float) -> List[Dict]:
        """
        Extract ASR text subset for the clipped segment.
        
        Adjusts timestamps to be relative to the clip start (0-based).
        """
        subset = []
        
        for segment in asr_result:
            seg_start = segment['start']
            seg_end = segment['end']
            
            # Check if segment overlaps with clip
            if seg_end < start_time or seg_start > end_time:
                continue
            
            # Calculate overlap
            overlap_start = max(seg_start, start_time)
            overlap_end = min(seg_end, end_time)
            
            # Adjust timestamps to be relative to clip start
            relative_start = overlap_start - start_time
            relative_end = overlap_end - start_time

            entry = {
                "start": relative_start,
                "end": relative_end,
                "text": segment['text']
            }

            # Also shift word-level timestamps if they exist
            if 'words' in segment:
                shifted_words = []
                for w in segment['words']:
                    w_start, w_end = w['start'], w['end']
                    # Keep only words that belong to this clip
                    if w_end >= start_time and w_start <= end_time:
                        shifted_words.append({
                            "word": w['word'],
                            "start": max(0.0, w_start - start_time),
                            "end": min(end_time - start_time, w_end - start_time)
                        })
                entry['words'] = shifted_words

            subset.append(entry)
        
        return subset


if __name__ == "__main__":
    # Test with analysis result
    clipper = Clipper(min_duration=15, max_duration=60)
    
    # Load analysis result
    analysis_path = "analysis_results/analysis_result.json"
    if not os.path.exists(analysis_path):
        logging.error(f"Analysis result not found: {analysis_path}")
        exit(1)
    
    with open(analysis_path, 'r', encoding='utf-8') as f:
        analysis_result = json.load(f)
    
    video_path = analysis_result['video_path']
    
    if not os.path.exists(video_path):
        logging.error(f"Video file not found: {video_path}")
        exit(1)
    
    logging.info(f"\n{'='*70}")
    logging.info("Testing Clipper with analysis result")
    logging.info(f"{'='*70}\n")
    
    result = clipper.clip_video(video_path, analysis_result)
    
    if result:
        logging.info(f"\n{'='*70}")
        logging.info("Clipping Complete!")
        logging.info(f"{'='*70}")
        logging.info(f"\nTotal clips created: {len(result['clips'])}")
        
        for i, clip in enumerate(result['clips'], 1):
            logging.info(f"\nClip {i}:")
            logging.info(f"  Path: {clip['clip_path']}")
            logging.info(f"  Time: {clip['start_time']:.2f}s - {clip['end_time']:.2f}s")
            logging.info(f"  Duration: {clip['duration']:.2f}s")
            logging.info(f"  Score: {clip['score']:.2f}")
            logging.info(f"  ASR segments: {len(clip['asr_subset'])}")
            
            if clip['asr_subset']:
                logging.info(f"  Sample ASR:")
                for seg in clip['asr_subset'][:3]:
                    logging.info(f"    [{seg['start']:.2f}s - {seg['end']:.2f}s]: {seg['text']}")
    else:
        logging.error("Clipping failed!")
