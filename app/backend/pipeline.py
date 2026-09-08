"""機能1 → 機能2 → 機能4 を 1 回の実行でつなぐ。

新UI（かんたん実行）のための層で、新しい処理はここに書かない。既存の
feature1/2/4 をこの順に呼び、セッションを持ち回るだけにしてある。工程の
中身を変えたくなったら、それぞれの feature*.py を直すのが正しい。

    Input1  スプライトシート ──▶ 機能1 ──▶ 連番PNG・等倍mp4
                                  └▶ 機能2 ──▶ Output1 First Frame
                                                Output2 スプライト動画
    Input3  変更案の画像 ──┐
    Output1 First Frame ──┴▶ 機能4 ──▶ Output3 変更後 First Frame

**機能4 が失敗しても、機能1・2 の結果は捨てない。** esora 側の理由
（未サインイン、モデルid違い、生成失敗）で落ちるのは日常的に起きるが、
そのたびに 120 フレームの拡大をやり直す理由は無い。機能4 の失敗は
``feature4_error`` に文字列で持たせて、他は成功として返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from . import config, esora_cli, feature1, feature2, feature4, sheet
from .io_paths import Session
from .sheet import Grid

Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    return None


def _scaled(progress: Progress, low: float, high: float) -> Progress:
    """子の工程が返す 0-1 を、全体の中の担当区間へ写す。"""

    def report(fraction: float, message: str) -> None:
        clamped = max(0.0, min(1.0, fraction))
        progress(low + (high - low) * clamped, message)

    return report


def _grid_from_counts(sheet_path: Path, cols: int, rows: int) -> Grid | None:
    """列数・行数だけの指定を格子にする。両方 0 なら自動検出に任せる。"""
    if cols <= 0 and rows <= 0:
        return None
    with Image.open(sheet_path) as image:
        size = image.size
    return sheet.grid_from_counts(size, max(1, cols), max(1, rows))


@dataclass
class Result:
    session: Session
    first_frame: Path
    video_path: Path | None
    generated_image: Path | None
    feature4_error: str
    lines: list[str]

    @property
    def report(self) -> str:
        body = list(self.lines)
        if self.feature4_error:
            body.append("")
            body.append(f"[機能4は失敗] {self.feature4_error}")
            body.append(
                "機能1・機能2 の結果（First Frame とスプライト動画）は"
                "できているので、原因を直したあと機能4 だけやり直せます。"
            )
        return "\n".join(body)


def run(
    sheet_path: str | Path,
    design_path: str | Path,
    *,
    background: str = config.DEFAULT_BACKGROUND,
    cols: int = 0,
    rows: int = 0,
    fps: int = config.DEFAULT_FPS,
    duration_s: float = config.DEFAULT_DURATION_S,
    target: int = config.DEFAULT_TARGET_SIZE,
    prompt: str = config.DEFAULT_IMAGE_PROMPT,
    model: str = "",
    aspect_ratio: str = config.DEFAULT_IMAGE_ASPECT_RATIO,
    image_size: str = config.DEFAULT_IMAGE_SIZE,
    seed: int | None = None,
    progress: Progress = _noop,
) -> Result:
    source = Path(sheet_path)
    if not source.is_file():
        raise ValueError(f"スプライトシートが見つかりません: {sheet_path!r}")
    design = Path(design_path)
    if not design.is_file():
        raise ValueError(f"変更案の画像が見つかりません: {design_path!r}")

    progress(0.01, "機能1: シートを分解")
    result1 = feature1.run(
        source,
        grid=_grid_from_counts(source, cols, rows),
        background=background,
        fps=fps,
        duration_s=duration_s,
        progress=_scaled(progress, 0.02, 0.40),
    )
    session = result1.session

    progress(0.40, "機能2: 拡大して開始画像を書き出し")
    result2 = feature2.run(
        session,
        target=target,
        fps=fps,
        progress=_scaled(progress, 0.40, 0.62),
    )

    lines = [
        f"セッション: {session.name}",
        "",
        "── 機能1 ──",
        result1.report,
        "",
        "── 機能2 ──",
        result2.report,
    ]

    # 機能4 は esora のサーバー任せなので、ここから先は落ちうる。落ちても
    # 上の 2 工程は返す。
    progress(0.64, "機能4: 衣装差し替え画像を生成")
    generated: Path | None = None
    failure = ""
    try:
        resolved = model.strip() or esora_cli.resolve_model_id(
            "image", config.ESORA_IMAGE_MODEL_HINTS
        )
        result4 = feature4.run(
            session,
            result2.first_frame,
            design,
            prompt=prompt,
            model=resolved,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            seed=seed,
            progress=_scaled(progress, 0.66, 0.99),
        )
        generated = result4.image_path
        lines += ["", "── 機能4 ──", result4.report]
    except (esora_cli.EsoraCliError, ValueError) as exc:
        failure = str(exc)

    progress(1.0, "完了")
    return Result(
        session=session,
        first_frame=result2.first_frame,
        video_path=result2.video_path,
        generated_image=generated,
        feature4_error=failure,
        lines=lines,
    )
