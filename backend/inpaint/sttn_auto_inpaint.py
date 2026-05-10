import os
import queue
import sys
import threading
import time
from typing import List

import cv2
import numpy as np
import torch
from torchvision import transforms
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.config import config
from backend.inpaint.sttn.auto_sttn import InpaintGenerator
from backend.inpaint.utils.sttn_utils import Stack, ToTorchFormatTensor
from backend.tools.hardware_accelerator import HardwareAccelerator
from backend.tools.inpaint_tools import get_inpaint_area_by_mask, is_frame_number_in_ab_sections
from backend.tools.video_io import AsyncVideoWriter, FramePrefetcher


_to_tensors = transforms.Compose([
    Stack(),
    ToTorchFormatTensor(),
])


def _prepare_model_tensor(frames: List[np.ndarray], pin_memory=False):
    if not frames:
        return None

    tensor = _to_tensors(frames).unsqueeze(0) * 2 - 1
    if pin_memory and torch.cuda.is_available():
        try:
            tensor = tensor.pin_memory()
        except RuntimeError:
            pass
    return tensor


class ChunkPrefetcher:
    """CPU stage: read, crop, resize, and tensorize chunks ahead of GPU inference."""

    def __init__(
        self,
        frame_prefetcher,
        frame_info,
        inpaint_area,
        ab_sections,
        model_input_width,
        model_input_height,
        chunk_size,
        total_chunks,
        pin_memory=False,
        queue_size=2,
    ):
        self.frame_prefetcher = frame_prefetcher
        self.frame_info = frame_info
        self.inpaint_area = inpaint_area
        self.ab_sections = ab_sections
        self.model_input_width = model_input_width
        self.model_input_height = model_input_height
        self.chunk_size = chunk_size
        self.total_chunks = total_chunks
        self.pin_memory = pin_memory
        self._queue = queue.Queue(maxsize=queue_size)
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            for i in range(self.total_chunks):
                if self._stopped:
                    break
                start_f = i * self.chunk_size
                end_f = min((i + 1) * self.chunk_size, self.frame_info["len"])
                self._put(("chunk", self._read_and_prepare_chunk(start_f, end_f)))
            self._put(("done", None))
        except Exception as exc:
            self._put(("error", exc))

    def _read_and_prepare_chunk(self, start_f, end_f):
        frames_hr = []
        frames = {k: [] for k in range(len(self.inpaint_area))}
        processed_frames_map = {}
        processed_idx = 0
        valid_frames_count = 0

        for j in range(start_f, end_f):
            success, image = self.frame_prefetcher.read()
            if not success:
                print(f"Warning: Failed to read frame {j}.")
                break

            frames_hr.append(image)
            valid_frames_count += 1

            if is_frame_number_in_ab_sections(j, self.ab_sections):
                processed_frames_map[j - start_f] = processed_idx
                processed_idx += 1
                for k, area in enumerate(self.inpaint_area):
                    image_crop = image[area[0]:area[1], :, :]
                    frames[k].append(
                        cv2.resize(
                            image_crop,
                            (self.model_input_width, self.model_input_height),
                        )
                    )

        return {
            "start_f": start_f,
            "end_f": end_f,
            "frames_hr": frames_hr,
            "frame_tensors": {
                k: _prepare_model_tensor(frames[k], self.pin_memory)
                for k in range(len(self.inpaint_area))
            },
            "processed_frames_map": processed_frames_map,
            "valid_frames_count": valid_frames_count,
        }

    def _put(self, item):
        while not self._stopped:
            try:
                self._queue.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def read(self):
        item_type, payload = self._queue.get()
        if item_type == "error":
            raise payload
        if item_type == "done":
            return None
        return payload

    def stop(self):
        self._stopped = True
        try:
            self._thread.join(timeout=5)
        except Exception:
            pass


class GpuChunkProcessor:
    """GPU stage: run STTN while CPU prepares and composes other chunks."""

    def __init__(self, sttn_inpaint, chunk_source, inpaint_area, queue_size=2):
        self.sttn_inpaint = sttn_inpaint
        self.chunk_source = chunk_source
        self.inpaint_area = inpaint_area
        self._queue = queue.Queue(maxsize=queue_size)
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while not self._stopped:
                chunk = self.chunk_source.read()
                if chunk is None:
                    self._put(("done", None))
                    return

                frame_tensors = chunk.pop("frame_tensors", {})
                comps = {}
                for k in range(len(self.inpaint_area)):
                    tensor = frame_tensors.get(k)
                    comps[k] = self.sttn_inpaint.inpaint_tensor(tensor) if tensor is not None else []
                chunk["comps"] = comps
                self._put(("chunk", chunk))
        except Exception as exc:
            self._put(("error", exc))

    def _put(self, item):
        while not self._stopped:
            try:
                self._queue.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def read(self):
        item_type, payload = self._queue.get()
        if item_type == "error":
            raise payload
        if item_type == "done":
            return None
        return payload

    def stop(self):
        self._stopped = True
        try:
            self._thread.join(timeout=5)
        except Exception:
            pass


class STTNInpaint:
    def __init__(self, device, model_path):
        self.device = device
        self.use_amp = getattr(self.device, "type", str(self.device)) == "cuda"
        if self.use_amp:
            torch.backends.cudnn.benchmark = True
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            try:
                torch.set_float32_matmul_precision("high")
            except Exception:
                pass

        self.model = InpaintGenerator().to(self.device)
        self.model.load_state_dict(torch.load(model_path, map_location="cpu")["netG"])
        self.model.eval()
        self.model_input_width, self.model_input_height = 640, 120
        self.neighbor_stride = config.sttnNeighborStride.value
        self.ref_length = config.sttnReferenceLength.value

    def __call__(self, input_frames: List[np.ndarray], input_mask: np.ndarray):
        _, mask = cv2.threshold(input_mask, 127, 1, cv2.THRESH_BINARY)
        mask = mask[:, :, None]
        h_ori, w_ori = mask.shape[:2]
        split_h = int(w_ori * 3 / 16)
        inpaint_area = get_inpaint_area_by_mask(w_ori, h_ori, split_h, mask)

        frames_hr = [frame.copy() for frame in input_frames]
        frames_scaled = {k: [] for k in range(len(inpaint_area))}
        for image in frames_hr:
            for k, area in enumerate(inpaint_area):
                image_crop = image[area[0]:area[1], :, :]
                frames_scaled[k].append(
                    cv2.resize(image_crop, (self.model_input_width, self.model_input_height))
                )

        comps = {k: self.inpaint(frames_scaled[k]) for k in range(len(inpaint_area))}
        if not inpaint_area:
            return frames_hr

        inpainted_frames = []
        for j, frame in enumerate(frames_hr):
            for k, area in enumerate(inpaint_area):
                comp = cv2.resize(comps[k][j], (w_ori, split_h))
                comp = cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_BGR2RGB)
                mask_area = mask[area[0]:area[1], :]
                frame[area[0]:area[1], :, :] = (
                    mask_area * comp + (1 - mask_area) * frame[area[0]:area[1], :, :]
                )
            inpainted_frames.append(frame)
        return inpainted_frames

    @staticmethod
    def read_mask(path):
        img = cv2.imread(path, 0)
        _, img = cv2.threshold(img, 127, 1, cv2.THRESH_BINARY)
        return img[:, :, None]

    def get_ref_index(self, neighbor_ids, length):
        return [
            i for i in range(0, length, self.ref_length)
            if i not in neighbor_ids
        ]

    def inpaint(self, frames: List[np.ndarray]):
        return self.inpaint_tensor(_prepare_model_tensor(frames))

    def inpaint_tensor(self, feats):
        if feats is None:
            return []

        frame_length = int(feats.shape[1])
        feats = feats.to(self.device, non_blocking=self.use_amp)
        comp_frames = [None] * frame_length

        with torch.inference_mode(), torch.amp.autocast(
            "cuda", dtype=torch.float16, enabled=self.use_amp
        ):
            feats = self.model.encoder(
                feats.view(frame_length, 3, self.model_input_height, self.model_input_width)
            )
            _, c, feat_h, feat_w = feats.size()
            feats = feats.view(1, frame_length, c, feat_h, feat_w)

            for f in range(0, frame_length, self.neighbor_stride):
                neighbor_ids = [
                    i for i in range(
                        max(0, f - self.neighbor_stride),
                        min(frame_length, f + self.neighbor_stride + 1),
                    )
                ]
                ref_ids = self.get_ref_index(neighbor_ids, frame_length)
                pred_feat = self.model.infer(feats[0, neighbor_ids + ref_ids, :, :, :])
                pred_img = torch.tanh(self.model.decoder(pred_feat[:len(neighbor_ids), :, :, :]))
                pred_img = (pred_img + 1) / 2
                pred_img = pred_img.float().cpu().permute(0, 2, 3, 1).numpy() * 255

                for i, idx in enumerate(neighbor_ids):
                    img = pred_img[i].astype(np.uint8)
                    if comp_frames[idx] is None:
                        comp_frames[idx] = img
                    else:
                        comp_frames[idx] = (
                            comp_frames[idx].astype(np.float32) * 0.5
                            + img.astype(np.float32) * 0.5
                        )
        return comp_frames


class STTNAutoInpaint:
    def __init__(self, device, model_path, video_path, mask_path=None, clip_gap=None):
        self.sttn_inpaint = STTNInpaint(device, model_path)
        self.video_path = video_path
        self.mask_path = mask_path
        self.video_out_path = os.path.join(
            os.path.dirname(os.path.abspath(self.video_path)),
            f"{os.path.basename(self.video_path).rsplit('.', 1)[0]}_no_sub.mp4",
        )
        self.clip_gap = config.getSttnMaxLoadNum() if clip_gap is None else clip_gap

    def read_frame_info_from_video(self):
        reader = cv2.VideoCapture(self.video_path)
        frame_info = {
            "W_ori": int(reader.get(cv2.CAP_PROP_FRAME_WIDTH) + 0.5),
            "H_ori": int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT) + 0.5),
            "fps": reader.get(cv2.CAP_PROP_FPS),
            "len": int(reader.get(cv2.CAP_PROP_FRAME_COUNT) + 0.5),
        }
        return reader, frame_info

    def _compose_and_write_chunk(
        self,
        chunk,
        frame_info,
        mask,
        split_h,
        inpaint_area,
        input_sub_remover,
        tbar,
        writer,
    ):
        frames_hr = chunk["frames_hr"]
        processed_frames_map = chunk["processed_frames_map"]
        comps = chunk["comps"]

        for j in range(chunk["valid_frames_count"]):
            original_frame = (
                frames_hr[j].copy()
                if input_sub_remover is not None and input_sub_remover.gui_mode
                else None
            )
            frame = frames_hr[j]

            if j in processed_frames_map:
                comp_idx = processed_frames_map[j]
                for k, area in enumerate(inpaint_area):
                    if comp_idx < len(comps[k]):
                        comp = cv2.resize(comps[k][comp_idx], (frame_info["W_ori"], split_h))
                        comp = cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_BGR2RGB)
                        mask_area = mask[area[0]:area[1], :]
                        frame[area[0]:area[1], :, :] = (
                            mask_area * comp + (1 - mask_area) * frame[area[0]:area[1], :, :]
                        )

            writer.write(frame)

            if input_sub_remover is not None:
                if tbar is not None:
                    input_sub_remover.update_progress(tbar, increment=1)
                if original_frame is not None and input_sub_remover.gui_mode:
                    input_sub_remover.update_preview_with_comp(original_frame, frame)

    def __call__(self, input_mask=None, input_sub_remover=None, tbar=None):
        reader = None
        writer = None
        frame_prefetcher = None
        chunk_prefetcher = None
        gpu_processor = None

        try:
            reader, frame_info = self.read_frame_info_from_video()
            if input_sub_remover is not None:
                ab_sections = input_sub_remover.ab_sections
                writer = input_sub_remover.video_writer
            else:
                ab_sections = None
                writer = cv2.VideoWriter(
                    self.video_out_path,
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    frame_info["fps"],
                    (frame_info["W_ori"], frame_info["H_ori"]),
                )
            writer = AsyncVideoWriter(writer, maxsize=48)

            split_h = int(frame_info["W_ori"] * 3 / 16)
            if input_mask is None:
                mask = self.sttn_inpaint.read_mask(self.mask_path)
            else:
                _, mask = cv2.threshold(input_mask, 127, 1, cv2.THRESH_BINARY)
                mask = mask[:, :, None]

            inpaint_area = get_inpaint_area_by_mask(
                frame_info["W_ori"], frame_info["H_ori"], split_h, mask
            )

            effective_clip_gap = self.clip_gap
            vram_mb = HardwareAccelerator.instance().get_available_vram_mb()
            if vram_mb > 0:
                bytes_per_frame = frame_info["W_ori"] * frame_info["H_ori"] * 12
                max_frames_by_vram = int(vram_mb * 1024 * 1024 / bytes_per_frame)
                max_frames_by_vram = max(max_frames_by_vram, 10)
                effective_clip_gap = min(self.clip_gap, max_frames_by_vram)
                if effective_clip_gap < self.clip_gap:
                    tqdm.write(
                        f"GPU VRAM: {vram_mb:.0f}MB, adjusting clip_gap: "
                        f"{self.clip_gap} -> {effective_clip_gap}"
                    )

            rec_time = (
                frame_info["len"] // effective_clip_gap
                if frame_info["len"] % effective_clip_gap == 0
                else frame_info["len"] // effective_clip_gap + 1
            )

            frame_prefetcher = FramePrefetcher(
                reader,
                buffer_size=min(max(effective_clip_gap, 10), 64),
            )
            chunk_prefetcher = ChunkPrefetcher(
                frame_prefetcher,
                frame_info,
                inpaint_area,
                ab_sections,
                self.sttn_inpaint.model_input_width,
                self.sttn_inpaint.model_input_height,
                effective_clip_gap,
                rec_time,
                pin_memory=self.sttn_inpaint.use_amp,
                queue_size=3,
            )
            gpu_processor = GpuChunkProcessor(
                self.sttn_inpaint,
                chunk_prefetcher,
                inpaint_area,
                queue_size=2,
            )

            for _ in range(rec_time):
                chunk = gpu_processor.read()
                if chunk is None:
                    break
                start_f = chunk["start_f"]
                end_f = chunk["end_f"]
                tqdm.write(f"Processing: {start_f + 1} - {end_f} / Total: {frame_info['len']}")

                if chunk["valid_frames_count"] == 0:
                    print(
                        f"Warning: No valid frames found in range "
                        f"{start_f + 1}-{end_f}. Skipping this segment."
                    )
                    continue

                self._compose_and_write_chunk(
                    chunk,
                    frame_info,
                    mask,
                    split_h,
                    inpaint_area,
                    input_sub_remover,
                    tbar,
                    writer,
                )

                del chunk
        except Exception as exc:
            print(f"Error during video processing: {exc}")
        finally:
            if gpu_processor:
                gpu_processor.stop()
            if chunk_prefetcher:
                chunk_prefetcher.stop()
            if frame_prefetcher:
                frame_prefetcher.release()
            elif reader:
                reader.release()
            if writer:
                writer.release()


if __name__ == "__main__":
    from backend.tools.model_config import ModelConfig

    mask_path = "../../test/test.png"
    video_path = "../../test/test.mp4"
    model_config = ModelConfig()
    start = time.time()
    sttn_video_inpaint = STTNAutoInpaint(
        HardwareAccelerator.instance().device,
        model_config.STTN_AUTO_MODEL_PATH,
        video_path,
        mask_path,
        clip_gap=config.getSttnMaxLoadNum(),
    )
    sttn_video_inpaint()
    print(f"video generated at {sttn_video_inpaint.video_out_path}")
    print(f"time cost: {time.time() - start}")
