"""機能2: パラパラ動画を CPU で拡大し、開始画像と mp4 を出す。

入力は機能1 が出した連番 PNG で、mp4 ではない。H.264 を一度通した画を
拡大すると、圧縮でにじんだ色をそのまま 9 倍に引き伸ばすことになる。同じ
工程を二度通す理由が無いので、正本のほうを読む。

拡大は整数倍の最近傍のみ。ここで輪郭を描き直さないことが、あとで元の
アルファを重ねたときに縁が合う条件になる。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from . import compose, config, io_paths, manifest, upscale, video
from .io_paths import Session
from .upscale import ScalePlan

Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    return None


def plan_for(
    session: Session,
    *,
    target: int = config.DEFAULT_TARGET_SIZE,
    scale: int | None = None,
    resample: str = config.UPSCALE_RESAMPLE,
) -> ScalePlan:
    """走らせる前に倍率だけ決めて見せる。素材ごとに変わる値なので必ず出す。"""
    files = io_paths.read_sequence(session.sequence)
    if not files:
        raise ValueError(
            f"連番 PNG がありません（{session.sequence}）。先に機能1 を実行してください。"
        )
    with Image.open(files[0]) as first:
        size = first.size
    return upscale.plan(size, target=target, scale=scale, resample=resample)


@dataclass
class Result:
    session: Session
    plan: ScalePlan
    frame_count: int
    first_frame: Path
    first_frame_rgba: Path | None
    video_path: Path | None
    video_note: str
    fps: int

    @property
    def report(self) -> str:
        lines = [
            f"セッション: {self.session.name}",
            self.plan.summary(),
            f"{self.frame_count} フレームを拡大: {self.session.upscaled}",
            f"開始画像: {self.first_frame.name}",
        ]
        if self.first_frame_rgba:
            lines.append(f"開始画像（透過版）: {self.first_frame_rgba.name}")
        if self.video_path:
            lines.append(f"動画: {video.describe(self.video_path)}")
        else:
            lines.append(f"動画: 未作成 — {self.video_note}")
        return "\n".join(lines)


def run(
    session: Session,
    *,
    target: int = config.DEFAULT_TARGET_SIZE,
    scale: int | None = None,
    resample: str = config.UPSCALE_RESAMPLE,
    fps: int | None = None,
    crf: int = config.DEFAULT_CRF,
    pix_fmt: str = config.DEFAULT_PIX_FMT,
    progress: Progress = _noop,
) -> Result:
    files = io_paths.read_sequence(session.sequence)
    if not files:
        raise ValueError(
            f"連番 PNG がありません（{session.sequence}）。先に機能1 を実行してください。"
        )

    record = manifest.section(session.manifest_path, "feature1") or {}
    timeline = record.get("timeline") or {}
    indices: list[int] = timeline.get("indices") or []
    resolved_fps = fps or int(timeline.get("fps") or config.DEFAULT_FPS)
    background = tuple(
        (record.get("background") or {}).get("rgb") or compose.parse_color(
            config.DEFAULT_BACKGROUND
        )
    )

    progress(0.03, "倍率を決定")
    scale_plan = plan_for(session, target=target, scale=scale, resample=resample)

    progress(0.08, f"{len(files)} フレームを {scale_plan.scale} 倍に拡大")
    session.clear(session.upscaled, session.upscaled_video, session.first_frame)

    # 同じ絵が何十枚も並ぶ列なので、拡大は元コマの種類ぶんだけ行う。
    # どのファイルが同じ絵かは manifest のタイムラインが知っている。
    cache: dict[int, Image.Image] = {}
    for position, path in enumerate(files):
        key = indices[position] if position < len(indices) else position
        if key not in cache:
            with Image.open(path) as image:
                cache[key] = upscale.upscale(image.convert("RGB"), scale_plan)
        cache[key].save(io_paths.numbered(session.upscaled, position))
        if position % 20 == 0:
            progress(
                0.08 + 0.62 * (position / max(1, len(files))),
                f"拡大 {position + 1}/{len(files)}",
            )

    progress(0.74, "開始画像を書き出し")
    first_frame = session.first_frame / "first_frame.png"
    first_key = indices[0] if indices else 0
    cache[first_key].save(first_frame)

    first_rgba = _first_frame_rgba(session, scale_plan, first_key)

    progress(0.82, "mp4 をエンコード")
    video_path: Path | None = session.upscaled_video / "flipbook_upscaled.mp4"
    note = ""
    try:
        video.encode_sequence(
            session.upscaled,
            video_path,
            fps=resolved_fps,
            crf=crf,
            pix_fmt=pix_fmt,
        )
    except (video.FfmpegMissing, video.FfmpegFailed) as exc:
        video_path, note = None, str(exc)

    progress(0.96, "manifest を保存")
    manifest.update(
        session.manifest_path,
        "feature2",
        {
            "plan": scale_plan.to_dict(),
            "frame_count": len(files),
            "fps": resolved_fps,
            "background": {
                "hex": compose.to_hex(background),  # type: ignore[arg-type]
                "rgb": list(background),
            },
            "upscaled_dir": str(session.upscaled),
            "first_frame": str(first_frame),
            "first_frame_rgba": str(first_rgba) if first_rgba else None,
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
        plan=scale_plan,
        frame_count=len(files),
        first_frame=first_frame,
        first_frame_rgba=first_rgba,
        video_path=video_path,
        video_note=note,
        fps=resolved_fps,
    )


def _first_frame_rgba(
    session: Session, scale_plan: ScalePlan, source_index: int
) -> Path | None:
    """開始コマを透過のまま拡大したものも並べて出す。

    生成 API に渡すのは不透明版だが、位置合わせや差分検査で「背景が無い
    元の形」が要る場面が必ず来る。合成後の画から作り直すと縁の混色が
    そのまま残るので、透過側から独立に拡大しておく。
    """
    sources = io_paths.read_sequence(session.frames_rgba)
    if not sources or source_index >= len(sources):
        return None
    destination = session.first_frame / "first_frame_rgba.png"
    with Image.open(sources[source_index]) as image:
        # 不透明版は連番を作る時点で偶数寸法に揃えてある。こちらにも同じ
        # パディングを通さないと、同じコマから出た 2 枚の寸法が食い違う。
        even = compose.pad_to_even(image.convert("RGBA"))
        upscale.upscale(even, scale_plan).save(destination)
    return destination
