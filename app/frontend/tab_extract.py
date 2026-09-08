"""機能3 のタブ: 編集後の動画をスプライトシートに戻す。

透過の付け方は 2 通り出す。どちらが良いかは素材と生成モデル次第で決め
打ちできないので、結果を並べて見比べてもらう画面にしてある。
"""

from __future__ import annotations

import gradio as gr
from PIL import Image

from backend import feature3, io_paths, keying, upscale

from . import common

#: ギャラリーで見比べるときだけ拡大する。114px のセルは並べても小さすぎて
#: 差が見えないための表示側の都合で、保存されるファイルには影響しない。
_PREVIEW_SCALE = 4


def _upload_path(uploaded) -> str:
    if uploaded is None:
        raise ValueError("編集後の動画（mp4）を選んでください。")
    if isinstance(uploaded, (list, tuple)):
        uploaded = uploaded[0]
    return str(getattr(uploaded, "name", uploaded))


def _preview_cells(paths, sampled) -> list:
    """保存済みの小さいセルを、見比べやすいよう拡大して並べる。"""
    items = []
    for path, sample in zip(paths, sampled):
        with Image.open(path) as image:
            big = image.resize(
                (image.width * _PREVIEW_SCALE, image.height * _PREVIEW_SCALE),
                Image.NEAREST,
            )
        caption = f"#{sample.pose_index}"
        if sample.time_s is not None:
            caption += f"  {sample.time_s:.2f}秒"
        items.append((big, caption))
    return items


def build(session_state: gr.State) -> dict:
    with gr.Tab("機能3: 動画→スプライト") as tab:
        gr.Markdown(
            "編集後の動画から各ポーズの代表フレームを 1 枚ずつ選び、"
            "元のセル寸法に縮小して、元シートと同じキャンバス・同じ位置に貼り戻します。"
            "新しい中間フレームは作りません。透過の付け方は "
            "**元コマのアルファをそのまま使う版** と "
            "**背景色をキー抜きする版** の 2 通りを同時に出すので、見比べて選んでください。"
        )

        with gr.Row():
            with gr.Column(scale=1):
                with gr.Row():
                    session = gr.Dropdown(
                        label="対象セッション（元のスプライト）",
                        choices=common.session_choices(),
                        value=None,
                        allow_custom_value=True,
                        scale=5,
                    )
                    refresh = gr.Button("↻", scale=1, min_width=48)

                upload = gr.File(
                    label="編集後の動画 (mp4 / mov / webm)",
                    file_types=[".mp4", ".mov", ".webm", ".mkv"],
                    file_count="single",
                    type="filepath",
                )
                check = gr.Button("① 動画を確認", variant="secondary")
                preflight_note = gr.Markdown("")

                with gr.Group():
                    gr.Markdown("**② フレーム選択**")
                    pick = gr.Dropdown(
                        label="各ポーズの保持区間のどこを取るか",
                        choices=[
                            ("保持区間の中央（推奨）", "middle"),
                            ("保持区間の先頭", "first"),
                            ("保持区間の末尾", "last"),
                        ],
                        value="middle",
                    )
                    resample = gr.Dropdown(
                        label="元セルサイズへの縮小方式",
                        choices=list(upscale.RESAMPLE),
                        value="lanczos",
                    )

                with gr.Group():
                    gr.Markdown(
                        "**③ 元の情報を戻す** — 生成側に情報が無い場所を埋めます"
                    )
                    restore_shadow = gr.Checkbox(
                        label="地面の影を元スプライトから戻す",
                        value=True,
                        info="影の領域には背景色と混ざった生成側のRGBが入ります。"
                        "アルファを戻しても紫のままなので、影はRGBごと戻します。"
                        "影マスクは機能1が作り、01b_shadow_masks に保存されます。",
                    )
                    fill_gaps = gr.Checkbox(
                        label="生成側が背景と判定した画素を元RGBで埋める",
                        value=True,
                        info="元には中身があったのに生成側が背景と見た画素。"
                        "そこにある色は背景色そのもので情報を持ちません。",
                    )

                with gr.Accordion("背景処理の詳細設定", open=False):
                    gr.Markdown(
                        "**透明にする範囲**と**残す画素の色を直す量**は別の設定です。"
                        "tolerance だけを強めると、背景と一緒に剣・蹄・袖の輪郭も消えます。"
                    )
                    chroma_tolerance = gr.Slider(
                        label="tolerance（背景とみなす色距離）※クロマキー版のみ",
                        minimum=0, maximum=150, step=1,
                        value=keying.DEFAULT_TOLERANCE,
                    )
                    chroma_soft = gr.Slider(
                        label="縁のやわらかさ ※クロマキー版のみ",
                        minimum=0, maximum=100, step=1,
                        value=keying.DEFAULT_SOFT_EDGE,
                    )
                    unmix = gr.Slider(
                        label="輪郭の色被り補正（逆合成）の強さ ※両版に効く",
                        minimum=0.0, maximum=1.0, step=0.05,
                        value=keying.DEFAULT_UNMIX_STRENGTH,
                        info="機能1が背景色の上に合成した式を解き直して元の色を戻します。"
                        "はみ出す画素では自動で効きを緩めるので、通常は1.0のままで構いません。",
                    )

                run = gr.Button("④ 実行", variant="primary")

            with gr.Column(scale=1):
                report = gr.Textbox(label="結果", lines=14, max_lines=28)
                with gr.Row():
                    alpha_sheet = gr.Image(
                        label="A: 元アルファ版", height=180, type="filepath"
                    )
                    chroma_sheet = gr.Image(
                        label="B: クロマキー版", height=180, type="filepath"
                    )
                gr.Markdown(
                    "透過の出来は置く背景で見え方が変わります。"
                    "黒では見えない縁の色被りが、白ではっきり出ます。"
                )
                with gr.Row():
                    alpha_compare = gr.Image(
                        label="A: 黒/白/グレーの上", height=260, type="filepath"
                    )
                    chroma_compare = gr.Image(
                        label="B: 黒/白/グレーの上", height=260, type="filepath"
                    )
                with gr.Row():
                    alpha_gallery = gr.Gallery(
                        label="A: ポーズごと（拡大表示）",
                        columns=6, height=200, object_fit="contain",
                    )
                    chroma_gallery = gr.Gallery(
                        label="B: ポーズごと（拡大表示）",
                        columns=6, height=200, object_fit="contain",
                    )
                downloads = gr.File(label="書き出したファイル", file_count="multiple")

    # ─── 配線 ───

    refresh.click(
        lambda: gr.update(choices=common.session_choices()), outputs=session
    )

    def adopt(path):
        if not path:
            return gr.update()
        return gr.update(choices=common.session_choices(), value=str(path))

    session_state.change(adopt, inputs=session_state, outputs=session)

    def on_check(path, uploaded):
        if not path:
            return "対象セッションを選んでください。"
        if uploaded is None:
            return "動画を選んでください。"
        try:
            pf = feature3.preflight(io_paths.open_session(path), _upload_path(uploaded))
        except Exception as exc:  # noqa: BLE001
            return common.failure(exc)
        return pf.report

    check.click(on_check, inputs=[session, upload], outputs=preflight_note)

    def on_run(
        path, uploaded, pick_value, resample_value,
        shadow_value, gaps_value, tolerance_value, soft_value, unmix_value,
        progress=gr.Progress(),
    ):
        blank = ("対象セッションを選んでください。", None, None, None, None, [], [], [])
        if not path:
            return blank
        try:
            result = feature3.run(
                io_paths.open_session(path),
                _upload_path(uploaded),
                pick=str(pick_value),
                resample=str(resample_value),
                chroma_tolerance=common.as_int(tolerance_value, keying.DEFAULT_TOLERANCE),
                chroma_soft=common.as_int(soft_value, keying.DEFAULT_SOFT_EDGE),
                unmix_strength=common.as_float(
                    unmix_value, keying.DEFAULT_UNMIX_STRENGTH
                ),
                restore_shadow=bool(shadow_value),
                fill_background_gaps=bool(gaps_value),
                progress=common.bridge(progress),
            )
        except Exception as exc:  # noqa: BLE001
            return (common.failure(exc), None, None, None, None, [], [], [])

        files = [
            found
            for found in (
                common.existing(result.original_alpha_sheet),
                common.existing(result.chromakey_sheet),
            )
            if found
        ]
        return (
            result.report,
            common.existing(result.original_alpha_sheet),
            common.existing(result.chromakey_sheet),
            common.existing(result.comparison_sheets.get("original_alpha")),
            common.existing(result.comparison_sheets.get("chromakey")),
            _preview_cells(result.original_alpha_cells, result.sampled),
            _preview_cells(result.chromakey_cells, result.sampled),
            files,
        )

    run.click(
        on_run,
        inputs=[
            session, upload, pick, resample,
            restore_shadow, fill_gaps, chroma_tolerance, chroma_soft, unmix,
        ],
        outputs=[
            report, alpha_sheet, chroma_sheet,
            alpha_compare, chroma_compare,
            alpha_gallery, chroma_gallery, downloads,
        ],
    )

    return {"tab": tab, "session": session}
