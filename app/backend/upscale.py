"""CPU で整数倍に拡大する。ここに AI は関与しない。

倍率は整数に限る。1024 ちょうどに合わせようとすると 114px のセルでは
8.98 倍になり、元の 1 画素が場所によって 8px になったり 9px になったりして
輪郭が不均一に崩れる。拡大しても情報量は増えないのだから、せめて元の画素
の形は保つ ― という方針で、目標寸法のほうを譲る。

倍率は素材ごとに決める。セルの長辺に対して、目標にいちばん近い整数倍を
選ぶ。114px なら 9 倍 = 1026px。
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from . import config

#: 最近傍以外は「拡大処理による描き足し」になる。比較用に残してあるだけで、
#: 既定から動かす理由は普通は無い。
RESAMPLE = {
    "nearest": Image.NEAREST,
    "box": Image.BOX,
    "bilinear": Image.BILINEAR,
    "lanczos": Image.LANCZOS,
}


@dataclass(frozen=True)
class ScalePlan:
    """決まった倍率と、その結果どうなるか。実行前に UI へ出して確認させる。"""

    scale: int
    source_size: tuple[int, int]
    output_size: tuple[int, int]
    target: int
    resample: str

    @property
    def delta(self) -> int:
        """目標との差。ここが大きいときは倍率を手で入れ直す判断材料になる。"""
        return max(self.output_size) - self.target

    def to_dict(self) -> dict:
        return {
            "scale": self.scale,
            "source_size": list(self.source_size),
            "output_size": list(self.output_size),
            "target": self.target,
            "resample": self.resample,
            "delta": self.delta,
        }

    def summary(self) -> str:
        sign = "+" if self.delta > 0 else ""
        return (
            f"{self.source_size[0]}x{self.source_size[1]} → "
            f"{self.output_size[0]}x{self.output_size[1]} "
            f"({self.scale}倍 / {self.resample} / 目標{self.target}px に対し {sign}{self.delta}px)"
        )


def choose_scale(size: tuple[int, int], target: int = config.DEFAULT_TARGET_SIZE) -> int:
    """長辺が ``target`` にいちばん近くなる整数倍を返す。

    同点なら小さいほうを取る。1024 を挟んで等距離なら、超えないほうが
    あとの取り回しで困らない。
    """
    longest = max(size)
    if longest <= 0:
        raise ValueError("寸法が 0 です。")

    lower = max(1, target // longest)
    candidates = range(lower, lower + 3)
    return min(candidates, key=lambda n: (abs(longest * n - target), n))


def plan(
    size: tuple[int, int],
    *,
    target: int = config.DEFAULT_TARGET_SIZE,
    scale: int | None = None,
    resample: str = config.UPSCALE_RESAMPLE,
) -> ScalePlan:
    """倍率を決める。``scale`` が渡されればそれを使い、自動選択はしない。"""
    if resample not in RESAMPLE:
        raise ValueError(f"未知の補間です: {resample!r}（{', '.join(RESAMPLE)}）")
    factor = scale if scale and scale > 0 else choose_scale(size, target)
    return ScalePlan(
        scale=factor,
        source_size=size,
        output_size=(size[0] * factor, size[1] * factor),
        target=target,
        resample=resample,
    )


def upscale_by(
    image: Image.Image, factor: int, resample: str = config.UPSCALE_RESAMPLE
) -> Image.Image:
    """倍率で拡大する。出力寸法ではなく倍率を渡すのが基本形。

    寸法で指定すると、渡した画がその寸法の前提と 1px でも違ったとき（偶数
    合わせのパディングが入った列と、入っていない透過原本を混ぜたときに
    起きる）、気づかないまま縦横比の違う絵ができる。
    """
    return image.resize(
        (image.width * factor, image.height * factor), RESAMPLE[resample]
    )


def upscale(image: Image.Image, plan_: ScalePlan) -> Image.Image:
    """1 枚拡大する。モードは変えない（RGBA は RGBA のまま）。"""
    return upscale_by(image, plan_.scale, plan_.resample)
