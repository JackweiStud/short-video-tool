
import yt_dlp
import os
import logging
import re
from typing import Optional

from config import Config, get_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Downloader:
    def __init__(self, output_dir: Optional[str] = None, config: Optional[Config] = None):
        self.config = config or get_config()
        self.output_dir = output_dir or self.config.downloads_dir
        self.download_retries = self.config.download_retries
        os.makedirs(self.output_dir, exist_ok=True)

    def download_video(self, url: str, quality: str = "best") -> dict:
        """
        Downloads a video from the given URL using yt-dlp.

        Args:
            url (str): The URL of the video to download.
            quality (str): Desired video quality (e.g., "best", "worst", "1080p", "720p").
                           If the exact quality is not available, yt-dlp will choose the closest
                           quality that is less than or equal to the specified quality.

        Returns:
            dict: A dictionary containing download information, or None if download fails.
                  Example: {
                      "filepath": "/path/to/downloaded/video.mp4",
                      "title": "Video Title",
                      "uploader": "Uploader Name",
                      "duration": 300,
                      "original_url": "http://example.com/video"
                  }
        """
        logging.info(f"Attempting to download video from: {url} with quality: {quality}")
        
        # yt-dlp format selection logic
        # Improved for YouTube and other platforms to get actual best resolution
        if quality == "best":
            format_string = 'bestvideo+bestaudio/best'
        elif quality.endswith('p'):
            # Example: '1080p' -> select best video with height <= 1080 and best audio
            h = quality[:-1]
            format_string = f'bestvideo[height<={h}]+bestaudio/best[height<={h}]'
        else:
            format_string = quality # e.g., 'worst' or specific IDs

        ydl_opts = {
            'format': format_string,
            'outtmpl': os.path.join(self.output_dir, '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',
            'progress_hooks': [self._download_progress_hook],
            'retries': self.download_retries,
            'fragment_retries': self.download_retries,
            'abort_on_error': False,
            'noplaylist': True, # Ensure only single video is downloaded
            'quiet': True,      # Suppress most console output
            'no_warnings': True, # Suppress warnings
        }

        cookies_browser = getattr(self.config, "ytdlp_cookies_browser", "").strip()
        if cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        youtube_player_client = getattr(
            self.config, "ytdlp_youtube_player_client", ""
        ).strip()
        if youtube_player_client:
            ydl_opts["extractor_args"] = {
                "youtube": {"player_client": [youtube_player_client]}
            }

        # Special handling for Twitter/X URLs to extract direct video URLs if possible
        if "x.com" in url or "twitter.com" in url:
            try:
                # Attempt to get direct video URL from Twitter API if available
                # This often bypasses issues with embedded videos
                # This part is complex and may require API keys/cookies for consistent success
                # For now, rely on yt-dlp's generic extractor, but acknowledge its limitations
                pass
            except Exception as e:
                logging.warning(f"Could not get direct Twitter video URL via custom logic: {e}")
                # Fallback to yt-dlp's default handling

        info_dict = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                if not info_dict:
                    logging.error(f"Failed to extract info or download for URL: {url}")
                    return None

                filepath = ydl.prepare_filename(info_dict)
                
                # Extract and log metadata
                metadata = {
                    "filepath": filepath,
                    "title": info_dict.get('title'),
                    "uploader": info_dict.get('uploader'),
                    "duration": info_dict.get('duration'),
                    "description": info_dict.get('description'),
                    "upload_date": info_dict.get('upload_date'),
                    "view_count": info_dict.get('view_count'),
                    "like_count": info_dict.get('like_count'),
                    "original_url": url,
                    "webpage_url": info_dict.get('webpage_url')
                }
                
                logging.info(f"Successfully downloaded: {metadata['title']} to {filepath}")
                logging.info(f"Video Metadata:")
                logging.info(f"  - Title: {metadata['title']}")
                logging.info(f"  - Uploader: {metadata['uploader']}")
                logging.info(f"  - Duration: {metadata['duration']} seconds")
                logging.info(f"  - Upload Date: {metadata['upload_date']}")
                logging.info(f"  - View Count: {metadata['view_count']}")
                logging.info(f"  - Like Count: {metadata['like_count']}")
                logging.info(f"  - Description: {metadata['description'][:100] if metadata['description'] else 'N/A'}...")
                
                return metadata
        except yt_dlp.DownloadError as e:
            # Extract relevant part of the error message for cleaner logs
            error_message = str(e)
            match = re.search(r"ERROR: (.+)", error_message)
            if match:
                clean_error = match.group(1).strip()
            else:
                clean_error = error_message
            logging.error(f"Download failed for URL: {url}. Error: {clean_error}")
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during download for URL: {url}. Error: {e}")
            return None

    def _download_progress_hook(self, d):
        # This hook is currently silenced by 'quiet': True in ydl_opts
        # If 'quiet' is set to False, this will print progress.
        if d['status'] == 'downloading':
            logging.info(f"Downloading: {d['_percent_str']} of {d['_total_bytes_str']} at {d['_speed_str']} ETA {d['_eta_str']}")
        elif d['status'] == 'finished':
            logging.info(f"Finished downloading: {d['filename']}")

if __name__ == "__main__":
    downloader = Downloader()

    # Test Case 1: YouTube video download (Success)
    youtube_url = "https://www.youtube.com/watch?v=LXb3EKWsInQ" # "Big Buck Bunny" - Blender Foundation (CC-BY)
    logging.info("\n--- Testing YouTube download ---")
    result_youtube = downloader.download_video(youtube_url, quality="720p")
    if result_youtube:
        logging.info(f"YouTube Download Successful: {result_youtube['filepath']}")
    else:
        logging.error("YouTube Download Failed. Please check logs for details.")

    # Test Case 2: TikTok video download (Success - requires a valid public TikTok URL)
    # IMPORTANT: TikTok URLs are highly dynamic and often require region-specific access or authentication.
    # The provided URL might not work for all regions. Replace with a real, PUBLIC, and accessible
    # TikTok video URL for proper testing in your environment.
    # Trying another public TikTok, sometimes they work, sometimes they don't without headers/cookies.
    tiktok_url = "https://www.tiktok.com/@scout2015/video/7339031201944620330" # Public domain, often more stable
    logging.info("\n--- Testing TikTok download ---")
    result_tiktok = downloader.download_video(tiktok_url, quality="720p")
    if result_tiktok:
        logging.info(f"TikTok Download Successful: {result_tiktok['filepath']}")
    else:
        logging.error("TikTok Download Failed. (Ensure URL is valid, public, and accessible from your region. TikTok often requires specific headers or cookies for reliable downloading.) Please check logs for details.")

    # Test Case 3: Twitter (X) video download (Success - requires a valid public Twitter video URL)
    # IMPORTANT: Twitter video URLs can be tricky. Ensure it's a direct video tweet that's publicly accessible.
    # Trying another public Twitter video that is known to be directly embeddable/downloadable by yt-dlp.
    twitter_url = "https://twitter.com/NASA/status/1769490100762640529" # Example from @NASA with direct video
    logging.info("\n--- Testing Twitter (X) download ---")
    result_twitter = downloader.download_video(twitter_url, quality="best")
    if result_twitter:
        logging.info(f"Twitter (X) Download Successful: {result_twitter['filepath']}")
    else:
        logging.error("Twitter (X) Download Failed. (Ensure URL is valid, public, and directly links to a video tweet.) Please check logs for details.")

    # Test Case 4: Invalid URL (Failure)
    invalid_url = "https://this-is-not-a-valid-video-url.com/nonexistent-video"
    logging.info("\n--- Testing Invalid URL ---")
    result_invalid = downloader.download_video(invalid_url)
    if result_invalid:
        logging.error("Invalid URL Download Unexpectedly Successful.")
    else:
        logging.info("Invalid URL Download Failed as expected.")

    # Test Case 5: Unsupported URL (Failure)
    unsupported_url = "https://www.google.com"
    logging.info("\n--- Testing Unsupported URL ---")
    result_unsupported = downloader.download_video(unsupported_url)
    if result_unsupported:
        logging.error("Unsupported URL Download Unexpectedly Successful.")
    else:
        logging.info("Unsupported URL Download Failed as expected.")

    logging.info("\n--- All tests completed ---")
