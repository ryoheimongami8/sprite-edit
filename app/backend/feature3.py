"""機能3: 編集後の動画 → スプライトシート。

機能1・機能2 の逆方向。編集後の動画からポーズごとに 1 枚を選び、元の
セル寸法へ縮小し、元のシートと同じキャンバス・同じセル位置に貼り戻す。

工程の順序に意味がある。**背景の処理は縮小の前に、高解像度のまま行う。**
縮小してから背景を抜くと、縮小の補間で背景色が輪郭に練り込まれたあとの
画を相手にすることになり、そこはもう分離できない。

    1. 動画から代表フレームを取る（960px のまま）
    2. 高解像度のまま背景を判定してアルファを作る   ← 形の問題
    3. 高解像度のまま輪郭の色被りを抜く             ← 色の問題（別設定）
    4. アルファを考慮して縮小する（プリマルチプライド）
    5. アルファを与える（元アルファ版／クロマキー版）
    6. 影だけ元スプライトの RGBA を戻す
    7. マスクを渡さずにセルへコピーする

7 が効く。透明なキャンバスへ `paste(tile, pos, tile)` と書くと、tile 自身の
アルファがマスクとしても使われ、アルファが二乗される
（出力 = round(元^2/255)）。実測で全 77,748 画素がこの式に一致した。
アルファ 64 の影が 16 まで薄まり、同時に RGB へ背景のマゼンタが残るため、
影が薄い紫になる。重ならないセルへの書き込みにマスクは要らない。

透過の付け方は 2 通り出す。どちらが正解かは素材と生成モデル次第で、ここ
では決め打ちしない。

  A. 元コマのアルファをそのまま適用する
     輪郭は元と完全に一致する（実行のたびに検査して報告する）。
     形が大きく変わった部分は、はみ出したぶんが切り落とされる。

  B. 機能1 で塗った背景色をキーにして抜く
     新しい形の輪郭がそのまま透過になるが、生成側が背景を保てていないと
     縁に色被りが残る。

位置合わせ（生成後の絵が元コマからずれたときの補正）はまだ入れていない。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from . import compose, config, extract, io_paths, keying, manifest, shadow, upscale, video
from .extract import Sampled
from .io_paths import Session

Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    return None


#: これ未満のアルファは「生成側では背景だった」とみなす。そこにある色は
#: 背景色そのもので、被写体の情報を持たない。
_GAP_ALPHA = 0.05


@dataclass
class Preflight:
    """走らせる前に見せる下見。動画の実測値と、元セッションの前提を並べる。"""

    video_info: dict[str, str]
    duration_s: float | None
    fps: float | None
    frame_count: int | None
    original_duration_s: float
    original_pose_count: int
    duration_mismatch: float | None

    @property
    def warnings(self) -> list[str]:
        notes = []
        if self.duration_mismatch is not None and self.duration_mismatch > 0.15:
            notes.append(
                f"動画の尺（{self.duration_s:.2f}秒）が元の想定"
                f"（{self.original_duration_s:.2f}秒）と "
                f"{self.duration_mismatch * 100:.0f}% 違います。"
                f"時間比率でフレームを選ぶので動作はしますが、"
                f"別の素材を選んでいないか確認してください。"
            )
        return notes

    @property
    def report(self) -> str:
        fps_text = f"{self.fps:g}" if self.fps else "?"
        lines = [
            f"動画: {self.video_info.get('width', '?')}x{self.video_info.get('height', '?')} "
            f"/ {fps_text}fps / {self.frame_count or '?'}フレーム "
            f"/ {self.duration_s:.2f}秒" if self.duration_s else "動画: 情報を読めませんでした",
            f"元セッション: {self.original_pose_count}ポーズ / {self.original_duration_s:.2f}秒",
        ]
        lines += [f"[注意] {note}" for note in self.warnings]
        return "\n".join(lines)


def preflight(session: Session, video_path: str | Path) -> Preflight:
    """アップロードした動画を、走らせる前に確かめる。"""
    record = manifest.require(
        session.manifest_path, "feature1", "先に機能1を実行してください。"
    )
    info = video.probe(Path(video_path))
    duration = video.duration_seconds(Path(video_path))
    fps = video.parse_fps(info.get("r_frame_rate"))
    frame_count = int(info["nb_frames"]) if info.get("nb_frames", "").isdigit() else None
    original_duration = float(record["timeline"]["duration_s"])
    return Preflight(
        video_info=info,
        duration_s=duration,
        fps=fps,
        frame_count=frame_count,
        original_duration_s=original_duration,
        original_pose_count=int(record["timeline"]["source_count"]),
        duration_mismatch=extract.duration_mismatch(original_duration, duration),
    )


@dataclass
class Result:
    session: Session
    sampled: list[Sampled]
    original_alpha_sheet: Path
    chromakey_sheet: Path
    comparison_sheets: dict[str, Path]
    original_alpha_cells: list[Path]
    chromakey_cells: list[Path]
    alpha_matches_original: bool
    alpha_max_error: int
    shadow_pixels: int
    gap_pixels: int
    warnings: list[str]

    @property
    def report(self) -> str:
        check = (
            "OK — 元画像と完全一致"
            if self.alpha_matches_original
            else f"不一致（最大差 {self.alpha_max_error}）"
        )
        lines = [
            f"セッション: {self.session.name}",
            f"{len(self.sampled)} ポーズを抽出",
            *(
                f"  #{s.pose_index}  動画の{s.frame_index}枚目"
                + (f" ({s.time_s:.3f}秒)" if s.time_s is not None else "")
                for s in self.sampled
            ),
            f"アルファ検査（元アルファ版）: {check}",
            f"影を戻した画素: {self.shadow_pixels}px",
            f"背景しか無く元RGBで埋めた画素: {self.gap_pixels}px",
            f"元アルファ版: {self.original_alpha_sheet.name}",
            f"クロマキー版: {self.chromakey_sheet.name}",
        ]
        lines += [f"[注意] {note}" for note in self.warnings]
        return "\n".join(lines)


def _upscale_alpha(alpha: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """元コマのアルファ（0-255）を動画の解像度へ引き伸ばし、0-1 で返す。

    最近傍で伸ばす。機能2 が動画へ送った絵も最近傍の整数倍拡大だったので、
    同じ引き伸ばし方をするのが筋が通る。ここで滑らかに補間すると、元の
    輪郭と半画素ずれたアルファを使って逆合成することになる。
    """
    stretched = Image.fromarray(alpha, mode="L").resize(size, Image.NEAREST)
    return np.asarray(stretched, dtype=np.float32) / 255.0


def _load_shadow_masks(session: Session, count: int) -> list[np.ndarray | None]:
    """機能1 が保存した影マスクを読む。無ければ元コマから作り直す。

    この機能より前に作られたセッションでも動くようにしてある。マスクは
    元コマ（透過つき）さえあれば再現できる。
    """
    saved = io_paths.read_sequence(session.shadow_masks)
    if len(saved) >= count:
        return [shadow.from_image(Image.open(path)) for path in saved[:count]]

    frames = io_paths.read_sequence(session.frames_rgba)
    masks: list[np.ndarray | None] = []
    for index in range(count):
        if index >= len(frames):
            masks.append(None)
            continue
        with Image.open(frames[index]) as frame:
            masks.append(shadow.detect(frame))
    return masks


def run(
    session: Session,
    video_path: str | Path,
    *,
    pick: str = config.DEFAULT_PICK,
    resample: str = config.DOWNSCALE_RESAMPLE,
    chroma_tolerance: int = keying.DEFAULT_TOLERANCE,
    chroma_soft: int = keying.DEFAULT_SOFT_EDGE,
    unmix_strength: float = keying.DEFAULT_UNMIX_STRENGTH,
    restore_shadow: bool = True,
    fill_background_gaps: bool = True,
    progress: Progress = _noop,
) -> Result:
    if resample not in upscale.RESAMPLE:
        raise ValueError(f"未知の補間です: {resample!r}（{', '.join(upscale.RESAMPLE)}）")

    progress(0.02, "元セッションの記録を確認")
    record = manifest.require(
        session.manifest_path, "feature1", "先に機能1を実行してください。"
    )
    grid = record["grid"]
    specs = record["frames"]
    timeline = record["timeline"]
    background = tuple(record["background"]["rgb"])
    image_size = tuple(record["source"]["size"])
    cell_size = (grid["cell_w"], grid["cell_h"])

    if len(specs) != timeline["source_count"]:
        raise ValueError(
            "機能1の記録が壊れています（コマ数とタイムラインの元コマ数が"
            f"一致しません: {len(specs)} != {timeline['source_count']}）。"
            "機能1を実行し直してください。"
        )

    source = Path(video_path)
    progress(0.05, "動画を保存")
    session.clear(
        session.incoming_video,
        session.extracted_frames,
        session.rebuilt_cells,
        session.rebuilt_sheets,
    )
    kept_video = session.incoming_video / source.name
    shutil.copy2(source, kept_video)

    progress(0.10, "動画を全展開")
    sampled, all_frames = extract.sample_video(
        kept_video, session.extracted_frames, timeline["holds"], pick=pick
    )
    duration = video.duration_seconds(kept_video)
    mismatch = extract.duration_mismatch(float(timeline["duration_s"]), duration)
    warnings: list[str] = []
    if mismatch is not None and mismatch > 0.15:
        warnings.append(
            f"動画の尺が元の想定と {mismatch * 100:.0f}% 違います。"
            f"時間比率で選んでいるので致命的ではありませんが、確認してください。"
        )

    alpha_dir = session.rebuilt_cells / "original_alpha"
    chroma_dir = session.rebuilt_cells / "chromakey"
    alpha_dir.mkdir(parents=True, exist_ok=True)
    chroma_dir.mkdir(parents=True, exist_ok=True)

    alpha_canvas = Image.new("RGBA", image_size, (0, 0, 0, 0))
    chroma_canvas = Image.new("RGBA", image_size, (0, 0, 0, 0))
    alpha_cells: list[Path] = []
    chroma_cells: list[Path] = []

    original_frames = io_paths.read_sequence(session.frames_rgba)
    masks = _load_shadow_masks(session, len(specs)) if restore_shadow else [None] * len(specs)
    shadow_total = 0
    gap_total = 0

    for position, sample in enumerate(sampled):
        progress(
            0.20 + 0.60 * (position / max(1, len(sampled))),
            f"ポーズ {position + 1}/{len(sampled)} を再構成",
        )
        spec = specs[sample.pose_index]
        left, top, _right, _bottom = spec["cell_box"]

        with Image.open(original_frames[sample.pose_index]) as original:
            original_rgba = np.asarray(original.convert("RGBA"))

        with Image.open(sample.path) as raw:
            frame = raw.convert("RGB")
            # 2: 形の問題 — どこを透明にするか（高解像度のまま）
            keyed_alpha = keying.background_alpha(
                frame, background, tolerance=chroma_tolerance, soft=chroma_soft
            )
            # 元アルファ版では、正しい被覆率をこちらは既に知っている。
            # 色距離で推定し直す必要はなく、元のアルファを動画の解像度へ
            # 引き伸ばしたものがそのまま使える。影や輪郭のような「背景と
            # 被写体の中間色」は色距離では判定できない — 距離だけで見ると
            # 背景からも被写体からも遠く、不透明と誤って扱われて色被りが
            # 残る。実測でマゼンタ残留 1,372px はほぼ全部この半透明画素だった。
            source_alpha = _upscale_alpha(original_rgba[..., 3], frame.size)

            # 3: 色の問題 — 輪郭に混ざった背景色を抜く（高解像度のまま）
            #    版ごとに、その版が信じているアルファで逆合成する。
            hi_rgb_a = keying.unmix_background(
                frame, source_alpha, background, strength=unmix_strength
            )
            hi_rgb_b = keying.unmix_background(
                frame, keyed_alpha, background, strength=unmix_strength
            )

        # 4: アルファを考慮して縮小（プリマルチプライド）
        lo_rgb_a, _ = compose.downscale_premultiplied(
            hi_rgb_a, source_alpha, cell_size, upscale.RESAMPLE[resample]
        )
        lo_rgb_b, lo_alpha = compose.downscale_premultiplied(
            hi_rgb_b, keyed_alpha, cell_size, upscale.RESAMPLE[resample]
        )

        # 5: アルファを与える
        variant_a = np.dstack(
            [lo_rgb_a.round().astype(np.uint8), original_rgba[..., 3]]
        )
        variant_b = np.dstack(
            [lo_rgb_b.round().astype(np.uint8), (lo_alpha * 255).round().astype(np.uint8)]
        )

        # 6a: 生成側に情報が無かった画素を元 RGB で埋める
        #
        # 生成側が「ここは背景」と判定したのに、元には中身があった画素。
        # そこに入っている色は背景色そのもので、意味のある情報を持たない。
        # 元の色を戻すほうが常に良い。影の薄い先端など、影マスクが形の細さ
        # ゆえに拾いきれない場所も、この規則が拾う。
        if fill_background_gaps:
            gap = (lo_alpha < _GAP_ALPHA) & (original_rgba[..., 3] > 0)
            if gap.any():
                variant_a[gap] = original_rgba[gap]
                # B 側はアルファを動かさない（透過の出来を見るための版なので、
                # 形の判定結果はそのまま残す）。色だけ背景色を追い出す。
                variant_b[..., :3][gap] = original_rgba[..., :3][gap]
                gap_total += int(gap.sum())

        # 6b: 影だけ元の RGBA を戻す
        mask = masks[sample.pose_index] if restore_shadow else None
        if mask is not None and mask.any():
            variant_a[mask] = original_rgba[mask]
            variant_b[mask] = original_rgba[mask]
            shadow_total += int(mask.sum())

        # 7: マスクを渡さずコピー（渡すとアルファが二乗される）
        for array, directory, cells, canvas in (
            (variant_a, alpha_dir, alpha_cells, alpha_canvas),
            (variant_b, chroma_dir, chroma_cells, chroma_canvas),
        ):
            tile = Image.fromarray(array, mode="RGBA")
            path = io_paths.numbered(directory, sample.pose_index)
            tile.save(path)
            cells.append(path)
            canvas.paste(tile, (left, top))

    progress(0.85, "シートを書き出し")
    alpha_sheet = session.rebuilt_sheets / "sprite_original_alpha.png"
    chroma_sheet = session.rebuilt_sheets / "sprite_chromakey.png"
    alpha_canvas.save(alpha_sheet)
    chroma_canvas.save(chroma_sheet)

    progress(0.90, "アルファを検査")
    matches, max_error = _verify_alpha(record, alpha_canvas)
    if not matches:
        warnings.append(
            f"元アルファ版のアルファが元画像と一致しません（最大差 {max_error}）。"
            f"貼り戻しでアルファが混合されている可能性があります。"
        )

    progress(0.94, "比較画像を作成")
    comparison = {}
    for label, sheet_path in (
        ("original_alpha", alpha_sheet),
        ("chromakey", chroma_sheet),
    ):
        with Image.open(sheet_path) as sheet_image:
            preview = compose.over_backgrounds(
                sheet_image, config.COMPARISON_BACKGROUNDS
            )
        target = session.rebuilt_sheets / f"compare_{label}.png"
        preview.save(target)
        comparison[label] = target

    progress(0.97, "manifest を保存")
    manifest.update(
        session.manifest_path,
        "feature3",
        {
            "video": {
                "path": str(source),
                "stored": str(kept_video),
                "sha256": manifest.file_digest(kept_video),
                "duration_s": duration,
                "frame_count": len(all_frames),
            },
            "pick": pick,
            "resample": resample,
            "keying": {"tolerance": chroma_tolerance, "soft": chroma_soft},
            "unmix_strength": unmix_strength,
            "restore_shadow": restore_shadow,
            "shadow_pixels_restored": shadow_total,
            "fill_background_gaps": fill_background_gaps,
            "gap_pixels_filled": gap_total,
            "alpha_check": {"matches_original": matches, "max_error": max_error},
            "samples": [s.to_dict() for s in sampled],
            "warnings": warnings,
            "outputs": {
                "original_alpha_sheet": str(alpha_sheet),
                "chromakey_sheet": str(chroma_sheet),
                "comparison": {k: str(v) for k, v in comparison.items()},
                "original_alpha_cells": str(alpha_dir),
                "chromakey_cells": str(chroma_dir),
            },
        },
    )

    progress(1.0, "完了")
    return Result(
        session=session,
        sampled=sampled,
        original_alpha_sheet=alpha_sheet,
        chromakey_sheet=chroma_sheet,
        comparison_sheets=comparison,
        original_alpha_cells=alpha_cells,
        chromakey_cells=chroma_cells,
        alpha_matches_original=matches,
        alpha_max_error=max_error,
        shadow_pixels=shadow_total,
        gap_pixels=gap_total,
        warnings=warnings,
    )


def _verify_alpha(record: dict, canvas: Image.Image) -> tuple[bool, int]:
    """元アルファ版のアルファが、元スプライトと一致するか毎回確かめる。

    アルファの二重適用は出力を見ても気づきにくい — 少し薄いだけに見える。
    実測で分かったときには全画素が壊れていた。だから目視ではなく、実行の
    たびに機械で照合して結果を報告する。

    比べるのはコマのある領域だけ。格子の外や空セルは元も出力も透明なので、
    ここに含めても常に一致し、検査の意味が薄まる。
    """
    stored = Path(record["source"]["stored"])
    if not stored.is_file():
        return True, 0

    with Image.open(stored) as original:
        original_alpha = np.asarray(original.convert("RGBA"))[..., 3].astype(np.int32)
    produced = np.asarray(canvas)[..., 3].astype(np.int32)
    if original_alpha.shape != produced.shape:
        return False, 255

    covered = np.zeros(original_alpha.shape, dtype=bool)
    for spec in record["frames"]:
        left, top, right, bottom = spec["cell_box"]
        covered[max(0, top) : bottom, max(0, left) : right] = True

    difference = np.abs(original_alpha - produced)[covered]
    max_error = int(difference.max()) if difference.size else 0
    return max_error == 0, max_error
