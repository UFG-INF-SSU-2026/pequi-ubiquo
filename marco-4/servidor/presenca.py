"""Produtor independente de presença. A captura e o detector ficam locais."""

import argparse
import os
import time
from pathlib import Path
from uuid import uuid4

from transport import request_json

PERSON_MODEL = Path(__file__).resolve().parent / "modelos" / "object_detection_yolox_2022nov.onnx"
INPUT_SIZE = 640
PERSON_CLASS = 0  # Índice da classe pessoa no COCO.
MIN_CONFIDENCE = 0.30
NMS_THRESHOLD = 0.50


class PersonDetector:
    """Conta pessoas com o YOLOX-S do OpenCV Zoo."""

    def __init__(self, model_path):
        import cv2
        import numpy as np

        self.net = cv2.dnn.readNetFromONNX(str(model_path))
        grids, strides = [], []
        # O modelo prevê caixas em três escalas. O grid converte as previsões em pixels.
        for stride in (8, 16, 32):
            side = INPUT_SIZE // stride
            rows, columns = np.indices((side, side), dtype=np.float32)
            grids.append(np.column_stack((columns.ravel(), rows.ravel())))
            strides.append(np.full(side * side, stride, dtype=np.float32))
        self.grid = np.concatenate(grids)
        self.strides = np.concatenate(strides)[:, None]

    def count(self, frame):
        import cv2
        import numpy as np

        if cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean() < 28:
            return None

        height, width = frame.shape[:2]
        scale = INPUT_SIZE / max(height, width)
        resized_width = max(1, int(width * scale))
        resized_height = max(1, int(height * scale))
        padded = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, dtype=np.uint8)
        padded[:resized_height, :resized_width] = cv2.resize(frame, (resized_width, resized_height))
        self.net.setInput(cv2.dnn.blobFromImage(padded, swapRB=True))
        predictions = self.net.forward()[0]

        class_scores = predictions[:, 5:]
        scores = predictions[:, 4] * class_scores[:, PERSON_CLASS]
        candidates = (class_scores.argmax(axis=1) == PERSON_CLASS) & (scores > MIN_CONFIDENCE)
        if not candidates.any():
            return 0

        geometry = predictions[candidates, :4]
        strides = self.strides[candidates]
        centers = (geometry[:, :2] + self.grid[candidates]) * strides
        sizes = np.exp(geometry[:, 2:]) * strides
        boxes = np.column_stack((centers - sizes / 2, sizes))
        kept = cv2.dnn.NMSBoxes(boxes.tolist(), scores[candidates].tolist(), MIN_CONFIDENCE, NMS_THRESHOLD)
        return min(50, len(kept))


def camera_samples(index, count_people, *, backend=None, stop=None, preview=None):
    import cv2

    pause = stop.wait if stop is not None else time.sleep
    while stop is None or not stop.is_set():
        camera = cv2.VideoCapture(index) if backend is None else cv2.VideoCapture(index, backend)
        previous, consecutive, published = None, 0, None
        try:
            while camera.isOpened() and (stop is None or not stop.is_set()):
                ok, frame = camera.read()
                when = time.time()
                if not ok:
                    break
                count = count_people(frame)
                consecutive = consecutive + 1 if count == previous else 1
                previous = count
                if consecutive >= 3:
                    published = count
                if preview is not None:
                    preview(frame)
                yield when, published
                pause(0.6)
        finally:
            camera.release()
        if stop is not None and stop.is_set():
            return
        if preview is not None:
            preview(None)
        yield time.time(), None
        pause(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="https://127.0.0.1:8443")
    parser.add_argument("--ca", default="local/tls/ca.pem")
    parser.add_argument("--camera", type=int, default=0)
    options = parser.parse_args()
    token = os.environ.get("TOWERAI_PRESENCE_TOKEN")
    if not token:
        parser.error("Defina TOWERAI_PRESENCE_TOKEN com a credencial do produtor de presença.")
    if not PERSON_MODEL.is_file():
        parser.error(f"Modelo YOLOX-S ausente em {PERSON_MODEL}. Veja a seção da webcam em docs/implantacao.md.")
    detector = PersonDetector(PERSON_MODEL)
    last_sent = 0
    last_people = object()
    for observed_at, people in camera_samples(options.camera, detector.count):
        if observed_at - last_sent < 2 and people == last_people:
            continue
        try:
            result = request_json(options.server, options.ca, token, "/v1/presence", {
                "eventId": uuid4().hex, "observedAt": observed_at, "people": people,
            })
            print(f"Presença {people if people is not None else 'desconhecida'} · {result['ack']}", flush=True)
        except OSError as error:
            print(f"Envio indisponível: {error}", flush=True)
        last_sent, last_people = observed_at, people


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
