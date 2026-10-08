"""機能1 のタブ: シートを分解してパラパラ動画にする。

画面の並びは工程の順序に合わせてある。読み込む → 格子を確かめる →
尺を決める → 走らせる。格子の確認を実行の前に独立させてあるのは、ここを
間違えたまま 120 フレーム書き出しても得るものが無いからで、`自動検出` は
数値欄を埋めるだけで何も生成しない。
"""

from __future__ import annotations

import gradio as gr
from PIL import Image

from backend import config, feature1, sheet
from backend.sheet import Grid

from . import common


def _resolve_grid(path, cell_w, cell_h, cols, rows, offset_x, offset_y) -> Grid | None:
    """欄に入っている値から格子を決める。空欄は自動に任せる。

    三通りある。全部埋まっていればそのまま使う。列数・行数だけ入っていれば
    シート寸法から割り算でセルを出す（自動検出が効かない素材で、数えた枚数
    だけ入れれば進めるように）。何も入っていなければ検出に回す。
    """
    width = common.as_int(cell_w)
    height = common.as_int(cell_h)
    columns = common.as_int(cols)
    lines = common.as_int(rows)

    if width > 0 and height > 0:
        return Grid(
            cell_w=width,
            cell_h=height,
            cols=max(1, columns),
            rows=max(1, lines),
            offset_x=common.as_int(offset_x, 0),
            offset_y=common.as_int(offset_y, 0),
        )
    if columns > 0 or lines > 0:
        with Image.open(path) as image:
            size = image.size
        return sheet.grid_from_counts(size, max(1, columns), max(1, lines))
    return None


def _upload_path(uploaded) -> str:
    """gr.File が返す形をパスに均す。

    画像コンポーネントではなくファイルコンポーネントで受けているのは、
    アルファを一切触らせないため。透過は最終復元の材料なので、UI の
    読み書きで再エンコードされる経路には乗せない。
    """
    if uploaded is None:
        raise ValueError("スプライトシートを選んでください。")
    if isinstance(uploaded, (list, tuple)):
        uploaded = uploaded[0]
    return str(getattr(uploaded, "name", uploaded))


def _preview(frames, specs) -> list:
    return [
        (frame, f"#{spec.index}  {spec.opaque_pixels}px")
        for frame, spec in zip(frames, specs)
    ]


def build(session_state: gr.State) -> dict:
    """タブを組み立てる。

    ``session_state`` は呼び出し側（ui_main）が gr.Tabs() の外で作って渡す。
    ここで作ると State が Tabs の直下に置かれ、タブのペイン割り当てが
    ずれる。
    """

    with gr.Tab("機能1: 分解してパラパラ動画") as tab:
        gr.Markdown(
            "スプライトシートを等間隔の格子で切り、同じ絵を複製して尺を作ります。"
            "**新しい中間ポーズは作りません。** "
            "コマの位置関係を保つため、各コマの中央寄せもしません。"
        )

        with gr.Row():
            with gr.Column(scale=1):
                upload = gr.File(
                    label="スプライトシート (PNG / 透過つき)",
                    file_types=[".png", ".webp", ".gif", ".tif", ".tiff"],
                    file_count="single",
                    type="filepath",
                )
                detect = gr.Button("① 格子を自動検出", variant="secondary")

                with gr.Group():
                    gr.Markdown(
                        "**② 格子** — 自動検出の値を上書きできます。"
                        "セル寸法を空(0)のまま **列数・行数だけ** 入れると、"
                        "シート寸法から割り算で出します。"
                    )
                    with gr.Row():
                        cell_w = gr.Number(label="セル幅", value=0, precision=0)
                        cell_h = gr.Number(label="セル高", value=0, precision=0)
                    with gr.Row():
                        cols = gr.Number(label="列数", value=0, precision=0)
                        rows = gr.Number(label="行数", value=0, precision=0)
                    with gr.Row():
                        offset_x = gr.Number(label="原点X", value=0, precision=0)
                        offset_y = gr.Number(label="原点Y", value=0, precision=0)
                    drop_empty = gr.Checkbox(
                        label="中身が空のセルを除外する", value=True
                    )

                with gr.Group():
                    gr.Markdown("**③ 背景と尺**")
                    preset = gr.Dropdown(
                        label="背景プリセット",
                        choices=list(config.BACKGROUND_PRESETS),
                        value=next(
                            label
                            for label, color in config.BACKGROUND_PRESETS.items()
                            if color == config.DEFAULT_BACKGROUND
                        ),
                    )
                    background = gr.ColorPicker(
                        label="背景色（透過を潰す色）",
                        value=config.DEFAULT_BACKGROUND,
                    )
                    with gr.Row():
                        fps = gr.Number(
                            label="fps", value=config.DEFAULT_FPS, precision=0
                        )
                        duration = gr.Number(
                            label="尺（秒）", value=config.DEFAULT_DURATION_S
                        )
                    hold_note = gr.Markdown("")
                    loop = gr.Checkbox(
                        label="尺いっぱいまで巡回する（既定は1周のみ）",
                        value=config.DEFAULT_LOOP,
                    )

                with gr.Accordion("mp4 の詳細設定", open=False):
                    gr.Markdown(
                        "連番PNGが正本で、mp4は確認用です。"
                        "`yuv420p` は色差を間引くのでドットの輪郭が少しにじみます。"
                        "判断の邪魔になるときだけ `yuv444p` に上げてください"
                        "（再生できないプレイヤーがあります）。"
                    )
                    with gr.Row():
                        crf = gr.Slider(
                            label="CRF（小さいほど高画質）",
                            minimum=0, maximum=32, step=1,
                            value=config.DEFAULT_CRF,
                        )
                        pix_fmt = gr.Dropdown(
                            label="pix_fmt",
                            choices=["yuv420p", "yuv444p"],
                            value=config.DEFAULT_PIX_FMT,
                        )

                run = gr.Button("④ 実行", variant="primary")

            with gr.Column(scale=1):
                report = gr.Textbox(
                    label="結果", lines=16, max_lines=30
                )
                gallery = gr.Gallery(
                    label="切り出したコマ（透過のまま）",
                    columns=6, height=200, object_fit="contain",
                )
                preview_video = gr.Video(label="パラパラ動画（等倍）", height=280)
                session_path = gr.Textbox(
                    label="セッション", interactive=False
                )

    # ─── 配線 ───

    def on_preset(name: str) -> str:
        return config.BACKGROUND_PRESETS.get(name, config.DEFAULT_BACKGROUND)

    preset.change(on_preset, inputs=preset, outputs=background)

    def on_timing(fps_value, duration_value, loop_value, cols_value, rows_value):
        """尺の割り振りを、走らせる前に文章で見せる。

        「24fps にする」ことと「1ポーズが何秒映る」ことは別なので、
        入力した瞬間に後者を出しておく。
        """
        frames = max(1, common.as_int(cols_value, 1)) * max(
            1, common.as_int(rows_value, 1)
        )
        fps_int = max(1, common.as_int(fps_value, config.DEFAULT_FPS))
        total = max(frames, round(fps_int * common.as_float(duration_value, 1.0)))
        if loop_value:
            return (
                f"→ 全 {total} フレーム / 1コマ1フレームで巡回 "
                f"({total / fps_int:.2f}秒)"
            )
        base, remainder = divmod(total, frames)
        span = f"{base}" if remainder == 0 else f"{base}〜{base + 1}"
        return (
            f"→ 全 {total} フレーム / {frames} コマ / 1ポーズ {span} フレーム "
            f"(約 {base / fps_int:.3f}秒) / 1周のみ"
        )

    timing_inputs = [fps, duration, loop, cols, rows]
    for component in timing_inputs:
        component.change(on_timing, inputs=timing_inputs, outputs=hold_note)

    def on_detect(uploaded, drop):
        try:
            path = _upload_path(uploaded)
            analysis = feature1.analyze(path, drop_empty=bool(drop))
        except Exception as exc:  # noqa: BLE001 — UI へ返して止めない
            return (
                common.failure(exc),
                gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update(),
                [],
            )
        grid = analysis.grid
        return (
            analysis.report,
            grid.cell_w, grid.cell_h, grid.cols, grid.rows,
            grid.offset_x, grid.offset_y,
            _preview(analysis.frames, analysis.specs),
        )

    detect.click(
        on_detect,
        inputs=[upload, drop_empty],
        outputs=[report, cell_w, cell_h, cols, rows, offset_x, offset_y, gallery],
    )

    def on_run(
        uploaded, cw, ch, cc, cr, ox, oy, drop,
        colour, fps_value, duration_value, loop_value, crf_value, pix,
        progress=gr.Progress(),
    ):
        try:
            path = _upload_path(uploaded)
            # 欄が空のままなら自動検出へ落とす。空欄を 1x1 の格子として
            # 真に受けると、意味の無い 120 フレームを書き出して時間を捨てる。
            grid = _resolve_grid(path, cw, ch, cc, cr, ox, oy)
            result = feature1.run(
                path,
                grid=grid,
                drop_empty=bool(drop),
                background=colour,
                fps=max(1, common.as_int(fps_value, config.DEFAULT_FPS)),
                duration_s=common.as_float(
                    duration_value, config.DEFAULT_DURATION_S
                ),
                loop=bool(loop_value),
                crf=common.as_int(crf_value, config.DEFAULT_CRF),
                pix_fmt=str(pix),
                progress=common.bridge(progress),
            )
        except Exception as exc:  # noqa: BLE001
            return common.failure(exc), [], None, "", None

        return (
            result.report,
            _preview(result.frames, result.specs),
            common.existing(result.video_path),
            str(result.session.root),
            str(result.session.root),
        )

    run.click(
        on_run,
        inputs=[
            upload, cell_w, cell_h, cols, rows, offset_x, offset_y, drop_empty,
            background, fps, duration, loop, crf, pix_fmt,
        ],
        outputs=[report, gallery, preview_video, session_path, session_state],
    )

    return {"tab": tab, "session_state": session_state, "session_path": session_path}
