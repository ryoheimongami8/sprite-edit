"""スプライトシートの格子を推定し、コマに切り分ける。

推定の方針は「等間隔の格子が 1 つある」。コマは詰め方の都合で幅がまちまち
に見えるが、実際には同じ大きさのセルに置かれていることがほとんどで、
見えている幅の差はポーズごとの輪郭の差でしかない。だから可変幅で切るので
はなく、間隔から 1 つのセル寸法を割り出して全コマに同じ枠を当てる。

これは見た目の都合ではなく後工程の前提になる。各コマを個別に中央寄せして
切ると、元の上下動やコマ間の位置関係が失われ、生成後に「元のポーズと
同じ位置か」を比べる基準そのものが消える。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict
from typing import Sequence

import numpy as np
from PIL import Image

from . import config


@dataclass(frozen=True)
class Grid:
    """シート上の等間隔セル。左上 ``(offset_x, offset_y)`` から並ぶ。

    ``offset`` が負や、最終セルがシートの外へはみ出すことは正常にあり得る。
    書き出し時にシートの端を 1〜数 px 落としてある素材が多く、そのぶんは
    透明で埋めて全セルを同じ寸法に揃える。
    """

    cell_w: int
    cell_h: int
    cols: int
    rows: int
    offset_x: int = 0
    offset_y: int = 0

    @property
    def count(self) -> int:
        return self.cols * self.rows

    def box(self, index: int) -> tuple[int, int, int, int]:
        """``index`` 番目のセルの ``(left, top, right, bottom)``（行優先）。"""
        col, row = index % self.cols, index // self.cols
        left = self.offset_x + col * self.cell_w
        top = self.offset_y + row * self.cell_h
        return left, top, left + self.cell_w, top + self.cell_h

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FrameSpec:
    """1 コマ。セル枠と、その中で実際に絵が occupying している範囲。

    ``content_box`` はセル内の相対座標。生成結果を元のコマと突き合わせる
    ときの基準になるので、切り出しと同時に控えておく。中身が空のセルでは
    ``None``。
    """

    index: int
    cell_box: tuple[int, int, int, int]
    content_box: tuple[int, int, int, int] | None
    opaque_pixels: int

    @property
    def is_empty(self) -> bool:
        return self.content_box is None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "cell_box": list(self.cell_box),
            "content_box": list(self.content_box) if self.content_box else None,
            "opaque_pixels": self.opaque_pixels,
        }


# ─── 検出 ───


def _runs(occupied: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """``occupied`` が立っている区間を ``(start, end)`` の並びで返す。

    ``min_gap`` 本に満たない空白は区間の切れ目とみなさない。1 px の抜けで
    1 コマが 2 つに割れると、そこから先の間隔推定が全部ずれる。
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for i, value in enumerate(occupied):
        if value:
            if start is None:
                start = i
            elif gap:
                gap = 0
            continue
        if start is None:
            continue
        gap += 1
        if gap >= min_gap:
            runs.append((start, i - gap))
            start, gap = None, 0
    if start is not None:
        runs.append((start, len(occupied) - 1 - gap))
    return runs


#: 間隔が基本ピッチの整数倍だとみなす許容差（ピッチに対する割合）。
_PITCH_TOLERANCE = 0.18


def _pitch(spacings: Sequence[float], widest: int) -> int:
    """間隔の並びから、格子の基本ピッチを 1 つ取り出す。

    中央値をそのまま使うと、空のコマを挟んだシートで壊れる。4 列のうち
    3 列目が空なら間隔は 40, 80 と並び、中央値は 60 という存在しないピッチ
    になる。飛んだぶんは基本ピッチの整数倍で現れるので、最小間隔を仮の
    ピッチとして各間隔をその倍数に割り戻し、辻褄が合えば採用する。

    最小間隔が最も広いコマより狭いときは、1 コマが 2 つに割れて読まれた
    可能性のほうが高い（脚の間などに完全な透明列が入ると起きる）。その
    仮ピッチは捨てて中央値へ戻す — 誤検出で全体を壊すより、ありふれた
    素材で確実に当たるほうを取る。
    """
    if not spacings:
        return widest
    fallback = max(1, round(statistics.median(spacings)))

    base = min(spacings)
    if base < widest:
        return fallback

    estimates: list[float] = []
    for spacing in spacings:
        multiple = round(spacing / base)
        if multiple < 1 or abs(spacing - multiple * base) > base * _PITCH_TOLERANCE:
            return fallback
        estimates.append(spacing / multiple)
    return max(1, round(statistics.median(estimates)))


def _estimate_axis(
    runs: Sequence[tuple[int, int]], length: int
) -> tuple[int, int, int]:
    """1 軸ぶんの ``(cell, count, offset)`` を返す。

    セル寸法は中心間隔の中央値から取る。平均ではなく中央値なのは、端の
    コマだけ極端に横長／横短でも間隔そのものは狂わないからで、平均だと
    その 1 つに全体が引きずられる。

    オフセットは「各コマがセル内でなるべく中央に来る」値を選ぶ。ただし
    どのコマもセルからはみ出さない範囲に必ず収める — はみ出しを許すと
    切り出しでコマの端が隣のセルに欠け落ちる。
    """
    if not runs:
        return length, 1, 0
    if len(runs) == 1:
        return length, 1, 0

    centers = [(start + end) / 2 for start, end in runs]
    spacings = [b - a for a, b in zip(centers, centers[1:])]
    cell = max(1, _pitch(spacings, widest=max(e - s + 1 for s, e in runs)))

    # 空セルを挟んでいても崩れないよう、位置そのものからセル番号を引き直す。
    indices = [round((center - centers[0]) / cell) for center in centers]

    # 収まる offset の範囲: 各コマ i について
    #   offset + k_i*cell <= start_i  かつ  end_i < offset + (k_i+1)*cell
    lower = max(end - (k + 1) * cell + 1 for (_, end), k in zip(runs, indices))
    upper = min(start - k * cell for (start, _), k in zip(runs, indices))

    if lower > upper:
        # 中心間隔から出したセルが、実際のコマ幅より狭い。最も広いコマが
        # 入る寸法まで広げ直す。均等割りが崩れている素材なので、UI 側で
        # 手動指定してもらう前提の保険。
        cell = max(end - start + 1 for start, end in runs)
        indices = [round((center - centers[0]) / cell) for center in centers]
        lower = max(end - (k + 1) * cell + 1 for (_, end), k in zip(runs, indices))
        upper = min(start - k * cell for (start, _), k in zip(runs, indices))

    centred = [
        start - k * cell - (cell - (end - start + 1)) / 2
        for (start, end), k in zip(runs, indices)
    ]
    offset = int(round(statistics.median(centred)))
    offset = max(lower, min(upper, offset))

    count = max(max(indices) + 1, -(-(length - offset) // cell))
    return cell, count, offset


def detect_grid(image: Image.Image) -> Grid:
    """アルファの空白から格子を推定する。

    完全な透明を境界とみなすので、背景が透過ではなく単色で塗られたシート
    には効かない。その場合は UI から手で寸法を入れてもらう。
    """
    alpha = np.array(image.convert("RGBA"))[..., 3]
    occupied = alpha > config.GRID_ALPHA_THRESHOLD
    height, width = occupied.shape

    col_runs = _runs(occupied.any(axis=0), config.MIN_GAP_WIDTH)
    row_runs = _runs(occupied.any(axis=1), config.MIN_GAP_WIDTH)

    cell_w, cols, offset_x = _estimate_axis(col_runs, width)
    cell_h, rows, offset_y = _estimate_axis(row_runs, height)
    return Grid(cell_w, cell_h, cols, rows, offset_x, offset_y)


# ─── 切り出し ───


def crop_cell(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """セル 1 つを、常に ``cell_w x cell_h`` の RGBA として取り出す。

    ``Image.crop`` は範囲外を黒の不透明で埋めるので使わない。はみ出した
    ぶんは透明でなければ、最後に元アルファへ戻すときに枠が残る。
    """
    left, top, right, bottom = box
    cell = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))

    sx0, sy0 = max(0, left), max(0, top)
    sx1, sy1 = min(image.width, right), min(image.height, bottom)
    if sx0 < sx1 and sy0 < sy1:
        cell.paste(image.crop((sx0, sy0, sx1, sy1)), (sx0 - left, sy0 - top))
    return cell


def _content_box(cell: Image.Image) -> tuple[tuple[int, int, int, int] | None, int]:
    alpha = np.array(cell)[..., 3]
    mask = alpha > config.GRID_ALPHA_THRESHOLD
    if not mask.any():
        return None, 0
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    box = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
    return box, int((alpha > 200).sum())


def slice_frames(
    image: Image.Image, grid: Grid, *, drop_empty: bool = True
) -> tuple[list[Image.Image], list[FrameSpec]]:
    """格子どおりに切り出す。順序は行優先（左→右、上→下）。

    ``drop_empty`` は、格子がシートの端に余分なセルを作ったときに効く。
    中身の無いセルは動画に入れても静止した空白が挟まるだけで、コマ番号の
    対応も狂わせる。
    """
    source = image.convert("RGBA")
    cells: list[Image.Image] = []
    specs: list[FrameSpec] = []

    for index in range(grid.count):
        cell = crop_cell(source, grid.box(index))
        content, opaque = _content_box(cell)
        if drop_empty and content is None:
            continue
        specs.append(
            FrameSpec(
                index=len(specs),
                cell_box=grid.box(index),
                content_box=content,
                opaque_pixels=opaque,
            )
        )
        cells.append(cell)
    return cells, specs


def grid_from_counts(image_size: tuple[int, int], cols: int, rows: int) -> Grid:
    """列数・行数だけからセル寸法を割り出す。

    シートは列数 x セル幅でぴったり作られているのが普通なので、枚数さえ
    分かれば寸法は割り算で出る。自動検出が効かない素材（背景が透過でない、
    コマの中に完全な透明列がある）で、利用者が数えた枚数を入れるだけで
    先へ進めるようにするための入口。
    """
    width, height = image_size
    cols, rows = max(1, cols), max(1, rows)
    return Grid(
        cell_w=max(1, round(width / cols)),
        cell_h=max(1, round(height / rows)),
        cols=cols,
        rows=rows,
    )


def warnings(grid: Grid, image_size: tuple[int, int]) -> list[str]:
    """推定が怪しいときに、黙って進ませないための注意書き。

    見るのは「格子がシートを覆いきっているか」の一点。コマの中に完全な
    透明列があると 1 コマが 2 つに読まれ、ピッチが実際の半分ほどになる。
    その壊れ方は必ず「格子の合計寸法がシートと合わない」形で表に出る。
    """
    notes: list[str] = []
    width, height = image_size

    span_x = grid.offset_x + grid.cols * grid.cell_w
    span_y = grid.offset_y + grid.rows * grid.cell_h
    if abs(span_x - width) > grid.cell_w * 0.5:
        notes.append(
            f"横の格子がシート幅と合いません（格子 {span_x}px / シート {width}px）。"
            f"コマの中に完全な透明列があるか、端に空のコマがある可能性があります。"
            f"列数を手で入れ直すとセル寸法は自動で割り出します。"
        )
    if abs(span_y - height) > grid.cell_h * 0.5:
        notes.append(
            f"縦の格子がシート高と合いません（格子 {span_y}px / シート {height}px）。"
            f"行数を手で入れ直してください。"
        )
    return notes


def describe(grid: Grid, specs: Sequence[FrameSpec]) -> str:
    """UI にそのまま出す 1 段落。数字を読んで手で直せるだけの情報を出す。"""
    lines = [
        f"セル {grid.cell_w}x{grid.cell_h} / {grid.cols}列 x {grid.rows}行 "
        f"/ 原点 ({grid.offset_x}, {grid.offset_y}) / 有効コマ {len(specs)}",
    ]
    for spec in specs:
        if spec.content_box is None:
            lines.append(f"  #{spec.index}  （空）")
            continue
        x0, y0, x1, y1 = spec.content_box
        lines.append(
            f"  #{spec.index}  セル内 x{x0}-{x1 - 1} y{y0}-{y1 - 1} "
            f"({x1 - x0}x{y1 - y0}px) / 不透明 {spec.opaque_pixels}px"
        )
    return "\n".join(lines)
