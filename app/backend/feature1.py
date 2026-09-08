"""機能1: スプライトシート → パラパラ動画。

やることは three つだけ。等間隔の格子で切る、同じ絵を複製して尺を作る、
背景を単色にして mp4 にする。新しい絵は 1 枚も作らない。

工程の順序に意味がある。切り出しの直後に元アルファを PNG で保存し、その
あとで初めて背景を合成する。合成後の画からアルファを推定し直す設計には
しない ― 半透明の縁は背景と混ざったあと元には戻らないので、透過は最初に
取っておくしかない。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from . import compose, config, io_paths, manifest, sequence, shadow, sheet, video
from .io_paths import Session
from .sequence import Timeline
from .sheet import FrameSpec, Grid

Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    return None


@dataclass
class Analysis:
    """実行前の下見。格子だけを推定して見せ、走らせる前に直させる。"""

    grid: Grid
    specs: list[FrameSpec]
    frames: list[Image.Image]
    image_size: tuple[int, int]

    @property
    def warnings(self) -> list[str]:
        return sheet.warnings(self.grid, self.image_size)

    @property
    def report(self) -> str:
        lines = [
            f"シート {self.image_size[0]}x{self.image_size[1]}",
            sheet.describe(self.grid, self.specs),
        ]
        lines += [f"[注意] {note}" for note in self.warnings]
        return "\n".join(lines)


def analyze(
    sheet_path: str | Path,
    *,
    grid: Grid | None = None,
    drop_empty: bool = True,
) -> Analysis:
    """格子を推定（または指定を適用）して切ってみる。ファイルは書かない。"""
    image = Image.open(sheet_path).convert("RGBA")
    resolved = grid or sheet.detect_grid(image)
    frames, specs = sheet.slice_frames(image, resolved, drop_empty=drop_empty)
    if not frames:
        raise ValueError(
            "中身のあるコマが 1 つもありません。透過が無いシートでは自動検出が"
            "効かないので、セル寸法と列数を手で指定してください。"
        )
    return Analysis(resolved, specs, frames, image.size)


@dataclass
class Result:
    session: Session
    grid: Grid
    specs: list[FrameSpec]
    timeline: Timeline
    frames: list[Image.Image]
    background: tuple[int, int, int]
    image_size: tuple[int, int]
    shadow_masks: list
    video_path: Path | None
    video_note: str

    @property
    def report(self) -> str:
        lines = [
            f"セッション: {self.session.name}",
            sheet.describe(self.grid, self.specs),
            shadow.describe(self.shadow_masks),
            *(
                f"[注意] {note}"
                for note in sheet.warnings(self.grid, self.image_size)
            ),
            self.timeline.summary(),
            f"背景: {compose.to_hex(self.background)}",
            f"連番PNG: {self.session.sequence}",
        ]
        if self.video_path:
            lines.append(f"動画: {video.describe(self.video_path)}")
        else:
            lines.append(f"動画: 未作成 — {self.video_note}")
        return "\n".join(lines)


def run(
    sheet_path: str | Path,
    *,
    grid: Grid | None = None,
    drop_empty: bool = True,
    background: str = config.DEFAULT_BACKGROUND,
    fps: int = config.DEFAULT_FPS,
    duration_s: float = config.DEFAULT_DURATION_S,
    loop: bool = config.DEFAULT_LOOP,
    crf: int = config.DEFAULT_CRF,
    pix_fmt: str = config.DEFAULT_PIX_FMT,
    work_root: Path | None = None,
    progress: Progress = _noop,
) -> Result:
    source = Path(sheet_path)
    colour = compose.parse_color(background)

    progress(0.02, "セッションを作成")
    session = io_paths.new_session(source.name, work_root)
    kept_source = session.source / source.name
    shutil.copy2(source, kept_source)

    progress(0.08, "格子を推定")
    analysis = analyze(kept_source, grid=grid, drop_empty=drop_empty)

    progress(0.20, f"{len(analysis.frames)} コマを書き出し（透過保持）")
    session.clear(
        session.frames_rgba, session.shadow_masks, session.sequence, session.video
    )
    shadow_masks = []
    for index, frame in enumerate(analysis.frames):
        frame.save(io_paths.numbered(session.frames_rgba, index))
        # 影マスクはここで作る。合成後の画からは作れない — 背景と混ざった
        # あとでは、半透明の黒が影だったのか背景だったのか区別できない。
        mask = shadow.detect(frame)
        shadow.to_image(mask).save(io_paths.numbered(session.shadow_masks, index))
        shadow_masks.append(mask)

    timeline = sequence.build_timeline(
        len(analysis.frames), fps=fps, duration_s=duration_s, loop=loop
    )

    progress(0.30, f"{timeline.frame_count} フレームに展開")
    flattened = _write_sequence(
        analysis.frames, timeline, colour, session, progress
    )

    progress(0.88, "mp4 をエンコード")
    video_path: Path | None = session.video / "flipbook.mp4"
    note = ""
    try:
        video.encode_sequence(
            session.sequence,
            video_path,
            fps=timeline.fps,
            crf=crf,
            pix_fmt=pix_fmt,
        )
    except (video.FfmpegMissing, video.FfmpegFailed) as exc:
        # 連番 PNG は書けている。次の工程はそちらを読むので、動画が無くても
        # 作業は止まらない ― 止めずに理由だけ伝える。
        video_path, note = None, str(exc)

    progress(0.96, "manifest を保存")
    manifest.update(
        session.manifest_path,
        "feature1",
        {
            "source": {
                "path": str(source),
                "stored": str(kept_source),
                "size": list(analysis.image_size),
                "sha256": manifest.file_digest(kept_source),
            },
            "grid": analysis.grid.to_dict(),
            "drop_empty": drop_empty,
            "frames": [spec.to_dict() for spec in analysis.specs],
            "frames_rgba_dir": str(session.frames_rgba),
            "shadow_masks_dir": str(session.shadow_masks),
            "shadow_pixels": [int(mask.sum()) for mask in shadow_masks],
            "background": {
                "hex": compose.to_hex(colour),
                "rgb": list(colour),
            },
            "timeline": timeline.to_dict(),
            "sequence_dir": str(session.sequence),
            "sequence_size": list(flattened),
            "video": {
                "path": str(video_path) if video_path else None,
                "crf": crf,
                "pix_fmt": pix_fmt,
                "note": note,
            },
        },
    )

    progress(1.0, "完了")
    return Result(
        session=session,
        grid=analysis.grid,
        specs=analysis.specs,
        timeline=timeline,
        frames=analysis.frames,
        background=colour,
        image_size=analysis.image_size,
        shadow_masks=shadow_masks,
        video_path=video_path,
        video_note=note,
    )


def _write_sequence(
    frames: Sequence[Image.Image],
    timeline: Timeline,
    colour: tuple[int, int, int],
    session: Session,
    progress: Progress,
) -> tuple[int, int]:
    """タイムラインどおりに連番 PNG を書く。

    合成は元コマ 1 枚につき 1 回だけ行い、その結果を必要な回数だけ書き出す。
    120 フレームでも実際に合成するのは 6 枚で済む。
    """
    composited: dict[int, Image.Image] = {}
    size = (0, 0)

    for position, source_index in enumerate(timeline.indices):
        if source_index not in composited:
            flat = compose.flatten(frames[source_index], colour)
            composited[source_index] = compose.pad_to_even(flat, colour)
            size = composited[source_index].size
        composited[source_index].save(io_paths.numbered(session.sequence, position))
        if position % 20 == 0:
            progress(
                0.30 + 0.55 * (position / max(1, len(timeline.indices))),
                f"連番 {position + 1}/{len(timeline.indices)}",
            )
    return size
