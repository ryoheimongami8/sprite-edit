"""地面の影だけを指すマスクを作る。

なぜ専用のマスクが要るのか。生成後の絵を元コマへ戻すとき、影の領域には
「マゼンタ背景と混ざった生成側の RGB」が入る。そこへ元のアルファだけを
戻しても、RGB に混ざった背景色は取り除けない — 半透明の黒い影が紫色の
影になる。影は元スプライトの RGB ごと戻すのが正しい。

問題は影の見分け方にある。「半透明の画素はすべて影」は使えない。剣にも
兜にも馬の輪郭にもアンチエイリアスの半透明画素があり、この素材では
それが 1,089px あった（影は 1,680px）。色とアルファだけの条件では
両者を分けられない。

分けているのは **形** である。影は面で、輪郭は線。3x3 の収縮で 1px 幅の
線は消え、面は芯が残る。その芯を膨張で戻し、元の候補集合で頭打ちにする
（モルフォロジーの開処理）。実測では判定領域が y=9-80 から y=67-80 の
接地帯だけに絞られ、剣と輪郭は落ちた。

この規則が当たらない素材はある（影を持たないスプライト、影が線画の
素材など）。そのときはマスクが空になるだけで、影の復元が起きないという
安全側に倒れる。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from . import config


def _neighbourhood(mask: np.ndarray) -> np.ndarray:
    """3x3 の 8 近傍＋自身を積む。境界の外は False として扱う。

    scipy を使わないのは依存を 1 つ増やすほどの処理ではないから。3x3 の
    収縮・膨張はパディングして 9 枚重ねるだけで済む。
    """
    padded = np.pad(mask, 1, constant_values=False)
    height, width = mask.shape
    return np.stack(
        [
            padded[dy : dy + height, dx : dx + width]
            for dy in range(3)
            for dx in range(3)
        ]
    )


def erode(mask: np.ndarray) -> np.ndarray:
    return _neighbourhood(mask).all(axis=0)


def dilate(mask: np.ndarray) -> np.ndarray:
    return _neighbourhood(mask).any(axis=0)


def candidates(image: Image.Image) -> np.ndarray:
    """色とアルファだけで「影かもしれない」画素を拾う。

    この時点では図形の輪郭線も混ざっている。分離は ``detect`` の開処理で行う。
    """
    array = np.asarray(image.convert("RGBA"), dtype=np.int32)
    rgb, alpha = array[..., :3], array[..., 3]
    brightest = rgb.max(axis=2)
    saturation = brightest - rgb.min(axis=2)
    return (
        (alpha > 0)
        & (alpha <= config.SHADOW_ALPHA_MAX)
        & (brightest <= config.SHADOW_DARK_MAX)
        & (saturation <= config.SHADOW_SATURATION_MAX)
    )


def detect(image: Image.Image) -> np.ndarray:
    """1 コマぶんの影マスク（bool 配列）を返す。

    開処理（収縮 → 膨張）で線を落とし、面だけを残す。膨張の結果を候補集合
    で頭打ちにしているので、元々候補でなかった画素が影に混ざることはない。
    """
    found = candidates(image)
    if not found.any():
        return found
    return dilate(erode(found)) & found


def to_image(mask: np.ndarray) -> Image.Image:
    """保存用のグレースケール（255 = 影）。目で見て確かめられる形にする。"""
    return Image.fromarray((mask.astype(np.uint8) * 255), mode="L")


def from_image(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L")) > 127


def describe(masks: list[np.ndarray]) -> str:
    total = sum(int(mask.sum()) for mask in masks)
    if total == 0:
        return "影マスク: 該当なし（影を持たない素材とみなして復元しません）"
    rows = [int(np.where(mask.any(axis=1))[0].min()) for mask in masks if mask.any()]
    return f"影マスク: 計 {total}px / 最上端 y={min(rows) if rows else '-'}"
