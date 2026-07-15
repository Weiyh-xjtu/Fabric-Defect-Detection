"""Detection specialist tool exports."""
from app.agent.detection_agent import detect_batch_images, detect_single_image, detect_video_file, detect_zip_images_file

DETECTION_TOOLS = [detect_single_image, detect_batch_images, detect_zip_images_file, detect_video_file]
