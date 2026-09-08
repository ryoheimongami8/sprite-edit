"""透過を単色に潰す。動画は透過を持てないので、必ずどこかで通る工程。

背景色を選べるようにしてあるのは好みの問題ではない。ここで置いた色が、
のちに生成結果から背景を抜くときの手がかりになる。素材に含まれない色ほど
抜きやすく、素材に近い色ほど自然な絵として扱われやすい。どちらが効くかは
モデル次第なので、両方試せる形にしておく。

なお、最終的な透過をこの背景の抜き取りに任せる設計にはしない。元アルファ
は切り出しの時点で別に保存してあり、それを戻すのが本筋で、ここでの背景は
あくまで途中の運び方でしかない。
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def parse_color(value: str) -> tuple[int, int, int]:
    """``#RRGGBB`` / ``#RGB`` / ``r,g,b`` / ``rgba(r,g,b,a)`` を受ける。

    UI のカラーピッカーと、プリセットの文字列と、手打ちの三つ組みが同じ
    経路を通るので、入口で吸収しておく。``rgba()`` は Gradio のカラー
    ピッカーが返してくる形。アルファは捨てる — ここは不透明な背景を作る
    ための色で、透過はこのあと合成で潰す側にある。
    """
    text = str(value).strip()
    if not text:
        raise ValueError("背景色が空です。")

    lowered = text.lower()
    if lowered.startswith(("rgba(", "rgb(")):
        inner = text[text.index("(") + 1 : text.rindex(")")]
        parts = [p for p in inner.split(",") if p.strip()][:3]
        text = ",".join(parts)

    if text.startswith("#"):
        digits = text[1:]
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        if len(digits) != 6:
            raise ValueError(f"色として読めません: {value!r}")
        return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]

    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"色として読めません: {value!r}")
    channels = tuple(int(float(p)) for p in parts)
    if any(c < 0 or c > 255 for c in channels):
        raise ValueError(f"0-255 の範囲外です: {value!r}")
    return channels  # type: ignore[return-value]


def to_hex(color: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)


def flatten(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """RGBA を背景色の上に合成して RGB にする。

    半透明の縁はここで背景と混ざる。元素材の縁は 0-255 の連続値なので、
    この混色は避けられない。だからこそ元アルファを別に持っておく。
    """
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (*background, 255))
    canvas.alpha_composite(rgba)
    return canvas.convert("RGB")


def flatten_all(
    images: list[Image.Image], background: tuple[int, int, int]
) -> list[Image.Image]:
    return [flatten(image, background) for image in images]


def _resize_plane(plane: np.ndarray, size: tuple[int, int], resample) -> np.ndarray:
    """float の 1 チャンネルを、精度を落とさずに拡縮する。

    uint8 に丸めてから縮小すると、プリマルチプライドの掛け戻しで誤差が
    増幅される。Pillow の 'F' モードなら float32 のまま補間できる。
    """
    return np.asarray(
        Image.fromarray(plane.astype(np.float32), mode="F").resize(size, resample)
    )


def downscale_premultiplied(
    rgb: np.ndarray,
    alpha: np.ndarray,
    size: tuple[int, int],
    resample=Image.LANCZOS,
) -> tuple[np.ndarray, np.ndarray]:
    """アルファを考慮して縮小する。``rgb`` は 0-255 float、``alpha`` は 0-1。

    素直に RGB と アルファを別々に縮小すると、透明な画素の色まで平均に
    参加してしまう。背景を抜いた直後の「透明だが色は背景色のまま」という
    画素が混ざり込み、輪郭に背景色が戻ってくる。

    RGB にアルファを掛けてから縮小し、最後に割り戻すと、透明な画素は
    重み 0 として扱われ、この混入が起きない（プリマルチプライドアルファ）。
    LANCZOS は行き過ぎ（リンギング）を出すので、最後に範囲へ収める。
    """
    premultiplied = rgb * alpha[..., None]
    resized_premul = np.dstack(
        [_resize_plane(premultiplied[..., c], size, resample) for c in range(3)]
    )
    resized_alpha = np.clip(_resize_plane(alpha, size, resample), 0.0, 1.0)

    safe = np.maximum(resized_alpha, 1e-6)[..., None]
    straight = np.clip(resized_premul / safe, 0.0, 255.0)
    return straight, resized_alpha


def over_backgrounds(
    image: Image.Image, colours: dict[str, tuple[int, int, int]]
) -> Image.Image:
    """同じ絵を複数の背景色の上に並べた 1 枚を作る。

    透過の出来は、置く背景によって見え方がまるで変わる。黒の上では
    見えない縁の色被りが、白の上でははっきり出る。判断のために全部
    同時に見せるための画。
    """
    rgba = image.convert("RGBA")
    panels = [flatten(rgba, colour) for colour in colours.values()]
    canvas = Image.new("RGB", (rgba.width, rgba.height * len(panels)))
    for row, panel in enumerate(panels):
        canvas.paste(panel, (0, row * rgba.height))
    return canvas


def pad_to_even(
    image: Image.Image, fill: tuple[int, ...] | None = None
) -> Image.Image:
    """幅・高さを偶数にする。足すのは右と下だけ。

    H.264 の yuv420p は色差を 2x2 でまとめるので、奇数寸法は encoder に
    拒否されるか黙って 1px 削られる。削られると全フレームが 1px ずれて、
    元コマとの突き合わせが最初から狂う。

    透過版にも同じ処理を通す必要がある。片方だけ偶数に揃えると、不透明版
    と透過版で寸法が 1px 違う開始画像が出てきて、重ねた瞬間にずれる。
    RGBA には透明を、RGB には背景色を詰める。
    """
    width = image.width + (image.width % 2)
    height = image.height + (image.height % 2)
    if (width, height) == image.size:
        return image
    if fill is None:
        fill = (0, 0, 0, 0) if image.mode == "RGBA" else (0, 0, 0)
    canvas = Image.new(image.mode, (width, height), fill)
    canvas.paste(image, (0, 0))
    return canvas
