"""背景色を手がかりに「どこを透明にするか」と「残す画素の色をどう直すか」
を、別々の処理として提供する。

この 2 つを一体にすると詰む。tolerance を上げれば背景の残りは減るが、
同時に剣・蹄・袖の細い部分まで消える。逆に tolerance を下げれば形は
残るが輪郭に背景色が焼き付く。**消す範囲と、残した画素の色を直す量は
別の問題**なので、別の関数・別の設定にしてある。

  background_alpha()   … どこを透明にするか（形の問題）
  unmix_background()   … 残した画素から背景色を抜くか（色の問題）

色を抜くほうには根拠がある。機能1 は透過画像を背景色 B の上に合成して
不透明にした。つまり観測色は

    C_obs = C_true * a + B * (1 - a)

という既知の式で作られている。これを a について解き直せば元の色が戻る。

    C_true = (C_obs - B * (1 - a)) / a

a が 1 に近い内側では何も起きず、a が小さい輪郭だけが直る。「輪郭に
混ざった背景色を抜く」という操作の、推測ではない形がこれである。

前提が崩れる場面もある。生成 AI は背景を完全には保存しないので、実際の
観測色が上の式どおりとは限らない。だから strength で効き具合を落とせる
ようにしてある。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

#: 色距離 (0-255 スケールのユークリッド距離、最大は約441) の下限。
#: これ未満は背景そのものとみなして完全に透明にする。
DEFAULT_TOLERANCE = 40

#: tolerance からこの幅ぶん外側までを、なだらかに不透明へ戻す帯にする。
#: 0 にすると二値になり、輪郭にジャギーが残る。
DEFAULT_SOFT_EDGE = 24

#: 色被り補正の効き具合。1.0 で上式どおりの完全な逆合成。
DEFAULT_UNMIX_STRENGTH = 1.0

#: これ未満のアルファでは逆合成の割り算が暴れるので手を出さない。
_MIN_ALPHA_FOR_UNMIX = 0.08


def background_alpha(
    image: Image.Image,
    background: tuple[int, int, int],
    *,
    tolerance: int = DEFAULT_TOLERANCE,
    soft: int = DEFAULT_SOFT_EDGE,
) -> np.ndarray:
    """背景色からの距離で 0-1 のアルファを作る。**色は触らない。**

    高解像度のまま呼ぶこと。縮小してから判定すると、縮小の補間で背景色が
    輪郭に練り込まれたあとの画を見ることになり、そこはもう分離できない。
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    target = np.asarray(background, dtype=np.float32)
    distance = np.sqrt(((rgb - target) ** 2).sum(axis=-1))

    low = float(max(0, tolerance))
    high = low + float(max(1, soft))
    return np.clip((distance - low) / (high - low), 0.0, 1.0)


def unmix_background(
    image: Image.Image,
    alpha: np.ndarray,
    background: tuple[int, int, int],
    *,
    strength: float = DEFAULT_UNMIX_STRENGTH,
) -> np.ndarray:
    """輪郭に混ざった背景色を抜いた RGB を float32 で返す。**形は触らない。**

    半透明の画素ほど強く効き、不透明な内側は素通しになる。
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    if strength <= 0:
        return rgb

    target = np.asarray(background, dtype=np.float32)
    safe = np.maximum(alpha, _MIN_ALPHA_FOR_UNMIX)[..., None]
    recovered = (rgb - target * (1.0 - alpha[..., None])) / safe

    # 逆合成が使えるほどアルファが無い画素は、元の色のまま残す。
    usable = (alpha >= _MIN_ALPHA_FOR_UNMIX)[..., None]
    recovered = np.where(usable, recovered, rgb)

    limit = _headroom(rgb, recovered)
    blended = rgb + (recovered - rgb) * np.minimum(strength, limit)[..., None]
    return np.clip(blended, 0.0, 255.0)


def _headroom(rgb: np.ndarray, recovered: np.ndarray) -> np.ndarray:
    """各画素に「どこまで補正してよいか」を 0-1 で返す。

    逆合成の結果が 0-255 をはみ出したなら、その画素では
    ``C_obs = C_true*a + B*(1-a)`` という前提が成り立っていない — 使った
    アルファがその画素の実際の被覆率と合っていない、ということ。生成側が
    描いた形は元の形と厳密には一致しないので、輪郭では普通に起きる。

    そこで、はみ出さずに済む最大の補正量まで自動で緩める。閾値で線を引く
    のではなく、画素ごとにデータが支持する量だけ効かせる。マゼンタ背景では
    行き過ぎがそのまま緑への転びになるので、この頭打ちが効く。

    式の上では、各チャンネルについて
    ``C_obs + s*(C_rec - C_obs)`` が [0,255] に収まる最大の ``s`` を解き、
    3 チャンネルで最も厳しいものを採る。
    """
    delta = recovered - rgb
    with np.errstate(divide="ignore", invalid="ignore"):
        upper = np.where(delta > 0, (255.0 - rgb) / delta, np.inf)
        lower = np.where(delta < 0, rgb / -delta, np.inf)
    allowed = np.minimum(np.nan_to_num(upper, nan=np.inf), np.nan_to_num(lower, nan=np.inf))
    return np.clip(allowed.min(axis=-1), 0.0, 1.0)
