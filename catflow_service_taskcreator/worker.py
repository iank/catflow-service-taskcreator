from typing import Any, List, Tuple
import signal
import asyncio
import aiohttp
from .label_studio import LabelStudioAPI
from catflow_worker import Worker
from catflow_worker.types import (
    AnnotatedFrameSchema,
    Prediction,
)
import os
import io
from PIL import Image
import random

import logging

LS_BUCKET = None
LS_URL = None
LS_PROJECT_ID = None
LS_AUTH_TOKEN = None


def yolo_to_ls(x_px, y_px, width_px, height_px, original_width, original_height):
    """Convert YOLO bounding box to Label Studio format

    (x/y centered, units pixels) to (x/y top left, units proportion of original image)
    """
    # Scale
    x = x_px * 100.0 / original_width
    y = y_px * 100.0 / original_height
    width = width_px * 100.0 / original_width
    height = height_px * 100.0 / original_height

    # Convert (x, y) from center of bounding box to top left corner
    x = x - width / 2
    y = y - height / 2

    return x, y, width, height


def predictions_to_ls(
    predictions: List[Prediction], original_width: int, original_height: int
):
    result = []
    for idx, p in enumerate(predictions):
        x, y, width, height = yolo_to_ls(
            p.x, p.y, p.width, p.height, original_width, original_height
        )

        result.append(
            {
                "id": f"result{idx+1}",
                "type": "rectanglelabels",
                "from_name": "label",
                "to_name": "image",
                "original_width": original_width,
                "original_height": original_height,
                "image_rotation": 0,
                "value": {
                    "rotation": 0,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "rectanglelabels": [p.label],
                },
            }
        )

    return result


async def taskcreator_handler(
    msg: Any, key: str, s3: Any, bucket: str
) -> Tuple[bool, List[Tuple[str, Any]]]:
    """Add pre-annotated frames to Label Studio"""

    global LS_BUCKET
    global LS_AUTH_TOKEN
    global LS_PROJECT_ID
    global LS_URL

    api = LabelStudioAPI(LS_URL, LS_AUTH_TOKEN)

    logging.info(f"[*] Message received ({key})")

    # Load message
    annotated_frames = AnnotatedFrameSchema(many=True).load(msg)

    for frame in annotated_frames:
        # Skip 95% of incoming frames
        if random.random() > 0.05:
            continue

        # Download from bucket
        frame_file = io.BytesIO()
        await s3.download_fileobj(bucket, frame.key, frame_file)
        frame_file.seek(0)

        # Get original image width and height
        pil_image = Image.open(frame_file)
        original_width, original_height = pil_image.size

        # Upload to LS bucket
        frame_file.seek(0)
        await s3.upload_fileobj(frame_file, LS_BUCKET, frame.key)

        # Create task
        s3_uri = os.environ["CATFLOW_S3_ENDPOINT_URL"] + f"/{LS_BUCKET}/{frame.key}"
        ls_predictions = predictions_to_ls(
            frame.predictions, original_width, original_height
        )

        ls_task = {"data": {"image": s3_uri, "meta": {"source": frame.source.key}}}
        if len(ls_predictions) > 0:
            ls_task["predictions"] = [
                {
                    "model_version": frame.model_name,
                    "score": 0.5,
                    "result": ls_predictions,
                }
            ]

        # post to LS
        try:
            logging.debug(f"Importing task: {ls_task}")
            await api.import_task(LS_PROJECT_ID, ls_task)
            logging.info(f"Task {frame.key} imported: {s3_uri}")
        except aiohttp.ClientConnectorError as e:
            logging.error(f"Failed to connect to Label Studio API: {e}")
        except aiohttp.ClientResponseError as e:
            logging.error(f"Failed to import task: {e}")

    return True, []


async def shutdown(worker, task):
    await worker.shutdown()
    task.cancel()
    try:
        await task
    except asyncio.exceptions.CancelledError:
        pass


async def startup(queue: str, topic_key: str):
    # Start worker
    worker = await Worker.create(taskcreator_handler, queue, topic_key)
    task = asyncio.create_task(worker.work())

    def handle_sigint(sig, frame):
        print("^ SIGINT received, shutting down...")
        asyncio.create_task(shutdown(worker, task))

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        if not await task:
            print("[!] Exited with error")
            return False
    except asyncio.exceptions.CancelledError:
        return True


def main() -> bool:
    global LS_BUCKET
    global LS_AUTH_TOKEN
    global LS_PROJECT_ID
    global LS_URL

    LS_BUCKET = os.environ["CATFLOW_LS_BUCKET_NAME"]
    LS_URL = os.environ["CATFLOW_LS_BASE_URL"]
    LS_PROJECT_ID = os.environ["CATFLOW_LS_PROJECT_ID"]
    LS_AUTH_TOKEN = os.environ["CATFLOW_LS_AUTH_TOKEN"]

    topic_key = "ingest.annotatedframes"
    queue_name = "catflow-service-taskcreator"
    logging.basicConfig(level=logging.INFO)

    return asyncio.run(startup(queue_name, topic_key))
