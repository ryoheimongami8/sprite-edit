"""かんたん実行の画面。1 画面で完結させ、タブで分けない。

工程の全体はこうなっている。アップロードするのは ① と ② と ④ だけで、
あいだの受け渡しは全部アプリがやる。

    ① スプライトシート ─┐
                        ├─▶ 機能1・機能2・機能4（このボタン1つ）─▶ ③ 変更後 First Frame
    ② 変更案の画像 ─────┘                    └─▶ スプライト動画（等倍→拡大）

    ③ と スプライト動画 ─▶ 【手作業】esora 等で動画生成（旧・機能5）

    ④ 変更後の動画 ─────▶ 機能3 ─▶ ⑤ 最終スプライトシート

機能5（アプリ内での動画生成）は精度が出ないため、この画面では扱わない。
手作業で作った動画を ④ に入れてもらう前提で、その手前と後ろだけを自動化
している。

設定は既定値のまま通ることを優先し、素材ごとに振り直す必要が出るものだけ
表に出してある（背景色と、格子の自動検出が外れたときの列数・行数）。
残りはアコーディオンの中。
"""

from __future__ import annotations

import gradio as gr

from backend import config, esora_cli, feature3, io_paths, keying, pipeline, upscale

from . import common


def _upload_path(uploaded) -> str:
    if uploaded is None:
        return ""
    if isinstance(uploaded, (list, tuple)):
        uploaded = uploaded[0]
    return str(getattr(uploaded, "name", uploaded))


def _initial_session() -> str | None:
    """再起動をまたいでも続きから使えるように、最新のセッションを初期値にする。

    手作業の動画生成には時間がかかる。①〜③ を出した日と ④ を入れる日が
    別になることは普通に起きるので、ステップ2 が「さっき実行した分」だけ
    しか指せない設計にはしない。
    """
    choices = common.session_choices()
    return choices[0] if choices else None


def build(session_state: gr.State) -> dict:
    with gr.Column() as pane:
        gr.Markdown(
            "### かんたん実行\n"
            "アップロードするのは **スプライトシート**・**変更案の画像**・"
            "**変更後の動画** の3つだけです。あいだの受け渡しは自動で行います。"
        )

        # ─── ステップ1: 機能1 → 機能2 → 機能4 ───

        with gr.Group():
            gr.Markdown(
                "#### ステップ1　素材を入れて実行（機能1 → 機能2 → 機能4）"
            )
            with gr.Row():
                sheet_upload = gr.File(
                    label="① スプライトシート (PNG / 透過つき)",
                    file_types=[".png", ".webp", ".gif", ".tif", ".tiff"],
                    file_count="single",
                    type="filepath",
                )
                design_upload = gr.File(
                    label="② 変更案の画像（衣装デザインの参照）",
                    file_types=["image"],
                    file_count="single",
                    type="filepath",
                )
            with gr.Row():
                background = gr.Dropdown(
                    label="背景色（透過を潰す色）",
                    choices=list(config.BACKGROUND_PRESETS),
                    value=next(iter(config.BACKGROUND_PRESETS)),
                    scale=2,
                )
                cols = gr.Number(
                    label="列数（0=自動検出）", value=0, precision=0, scale=1
                )
                rows = gr.Number(
                    label="行数（0=自動検出）", value=0, precision=0, scale=1
                )

            with gr.Accordion("詳細設定（通常は変更不要）", open=False):
                with gr.Row():
                    fps = gr.Number(label="fps", value=config.DEFAULT_FPS, precision=0)
                    duration = gr.Number(
                        label="尺（秒）", value=config.DEFAULT_DURATION_S
                    )
                    target = gr.Number(
                        label="拡大の目標長辺(px)",
                        value=config.DEFAULT_TARGET_SIZE,
                        precision=0,
                    )
                with gr.Row():
                    model = gr.Textbox(
                        label="画像生成モデル id（空欄=自動で探す）",
                        value="",
                        placeholder=" ".join(config.ESORA_IMAGE_MODEL_HINTS),
                    )
                    aspect_ratio = gr.Textbox(
                        label="アスペクト比", value=config.DEFAULT_IMAGE_ASPECT_RATIO
                    )
                    image_size = gr.Textbox(
                        label="画像サイズ", value=config.DEFAULT_IMAGE_SIZE
                    )
                    seed = gr.Number(label="seed（空欄可）", value=None, precision=0)
                prompt = gr.Textbox(
                    label="機能4 のプロンプト",
                    value=config.DEFAULT_IMAGE_PROMPT,
                    lines=12,
                    max_lines=30,
                )

            with gr.Row():
                run_prepare = gr.Button(
                    "▶ ステップ1 を実行（機能1 → 機能2 → 機能4）",
                    variant="primary",
                    scale=4,
                )
                check = gr.Button("esora 接続確認", scale=1)

            prepare_report = gr.Textbox(label="結果", lines=10, max_lines=24)
            with gr.Row():
                first_frame_view = gr.Image(
                    label="First Frame（元）", height=280, type="filepath"
                )
                generated_view = gr.Image(
                    label="変更後 First Frame（機能4 の出力）",
                    height=280,
                    type="filepath",
                )
                video_view = gr.Video(label="スプライト動画（拡大後）", height=280)
            prepare_files = gr.File(
                label="手作業の動画生成に渡すファイル（動画 + 変更後 First Frame）",
                file_count="multiple",
            )

        # ─── 手作業（旧・機能5） ───

        gr.Markdown(
            "#### ステップ2　動画生成（手作業）\n"
            "上で出た **スプライト動画** と **変更後 First Frame** を使って、"
            "esora などで衣装を差し替えた動画を作ってください。"
            "できた動画を次のステップに入れます。"
        )

        # ─── ステップ3: 機能3 ───

        with gr.Group():
            gr.Markdown("#### ステップ3　動画をスプライトシートに戻す（機能3）")
            with gr.Row():
                session = gr.Dropdown(
                    label="対象セッション（ステップ1 の結果。既定は最新）",
                    choices=common.session_choices(),
                    value=_initial_session(),
                    allow_custom_value=True,
                    scale=5,
                )
                refresh = gr.Button("↻", scale=1, min_width=48)
            video_upload = gr.File(
                label="④ 変更後の動画 (mp4 / mov / webm)",
                file_types=[".mp4", ".mov", ".webm", ".mkv"],
                file_count="single",
                type="filepath",
            )

            with gr.Accordion("詳細設定（通常は変更不要）", open=False):
                with gr.Row():
                    pick = gr.Dropdown(
                        label="各ポーズのどこを取るか",
                        choices=[
                            ("保持区間の中央（推奨）", "middle"),
                            ("保持区間の先頭", "first"),
                            ("保持区間の末尾", "last"),
                        ],
                        value=config.DEFAULT_PICK,
                    )
                    resample = gr.Dropdown(
                        label="縮小方式",
                        choices=list(upscale.RESAMPLE),
                        value=config.DOWNSCALE_RESAMPLE,
                    )
                with gr.Row():
                    restore_shadow = gr.Checkbox(
                        label="地面の影を元スプライトから戻す", value=True
                    )
                    fill_gaps = gr.Checkbox(
                        label="生成側が背景と判定した画素を元RGBで埋める", value=True
                    )
                with gr.Row():
                    chroma_tolerance = gr.Slider(
                        label="tolerance ※クロマキー版のみ",
                        minimum=0, maximum=150, step=1,
                        value=keying.DEFAULT_TOLERANCE,
                    )
                    chroma_soft = gr.Slider(
                        label="縁のやわらかさ ※クロマキー版のみ",
                        minimum=0, maximum=100, step=1,
                        value=keying.DEFAULT_SOFT_EDGE,
                    )
                    unmix = gr.Slider(
                        label="色被り補正の強さ",
                        minimum=0.0, maximum=1.0, step=0.05,
                        value=keying.DEFAULT_UNMIX_STRENGTH,
                    )

            run_extract = gr.Button("▶ ステップ3 を実行（機能3）", variant="primary")
            extract_report = gr.Textbox(label="結果", lines=10, max_lines=24)
            with gr.Row():
                alpha_sheet = gr.Image(
                    label="⑤ 最終スプライト（元アルファ版）",
                    height=260,
                    type="filepath",
                )
                chroma_sheet = gr.Image(
                    label="⑤ 最終スプライト（クロマキー版）",
                    height=260,
                    type="filepath",
                )
            with gr.Row():
                alpha_compare = gr.Image(
                    label="元アルファ版を黒/白/グレーに重ねた比較",
                    height=240,
                    type="filepath",
                )
                chroma_compare = gr.Image(
                    label="クロマキー版を黒/白/グレーに重ねた比較",
                    height=240,
                    type="filepath",
                )
            extract_files = gr.File(label="最終成果物", file_count="multiple")

    # ─── 配線 ───

    check.click(lambda: common.connection_report(), outputs=prepare_report)

    refresh.click(
        lambda: gr.update(choices=common.session_choices()), outputs=session
    )

    def on_prepare(
        sheet_value, design_value, background_value, cols_value, rows_value,
        fps_value, duration_value, target_value,
        model_value, aspect_value, size_value, seed_value, prompt_value,
        progress=gr.Progress(),
    ):
        blank = (None, None, None, [], gr.update(), None)
        try:
            sheet_path = _upload_path(sheet_value)
            design_path = _upload_path(design_value)
            if not sheet_path or not design_path:
                return ("① スプライトシートと ② 変更案の画像を選んでください。", *blank)
            result = pipeline.run(
                sheet_path,
                design_path,
                background=config.BACKGROUND_PRESETS.get(
                    str(background_value), config.DEFAULT_BACKGROUND
                ),
                cols=common.as_int(cols_value, 0),
                rows=common.as_int(rows_value, 0),
                fps=max(1, common.as_int(fps_value, config.DEFAULT_FPS)),
                duration_s=common.as_float(
                    duration_value, config.DEFAULT_DURATION_S
                ),
                target=max(1, common.as_int(target_value, config.DEFAULT_TARGET_SIZE)),
                prompt=str(prompt_value),
                model=str(model_value or ""),
                aspect_ratio=str(aspect_value),
                image_size=str(size_value),
                seed=common.as_int(seed_value, 0) or None,
                progress=common.bridge(progress),
            )
        except (esora_cli.EsoraCliError, ValueError) as exc:
            return (f"[失敗] {exc}", *blank)
        except Exception as exc:  # noqa: BLE001
            return (common.failure(exc), *blank)

        # 手作業の動画生成に要るのは、拡大後の動画と変更後の First Frame。
        handoff = [
            found
            for found in (
                common.existing(result.video_path),
                common.existing(result.generated_image),
            )
            if found
        ]
        return (
            result.report,
            common.existing(result.first_frame),
            common.existing(result.generated_image),
            common.existing(result.video_path),
            handoff,
            gr.update(
                choices=common.session_choices(), value=str(result.session.root)
            ),
            str(result.session.root),
        )

    run_prepare.click(
        on_prepare,
        inputs=[
            sheet_upload, design_upload, background, cols, rows,
            fps, duration, target,
            model, aspect_ratio, image_size, seed, prompt,
        ],
        outputs=[
            prepare_report, first_frame_view, generated_view, video_view,
            prepare_files, session, session_state,
        ],
    )

    def on_extract(
        path, video_value, pick_value, resample_value,
        shadow_value, gaps_value, tolerance_value, soft_value, unmix_value,
        progress=gr.Progress(),
    ):
        blank = (None, None, None, None, [])
        try:
            video_path = _upload_path(video_value)
            if not path:
                return ("対象セッションを選んでください。", *blank)
            if not video_path:
                return ("④ 変更後の動画を選んでください。", *blank)
            result = feature3.run(
                io_paths.open_session(path),
                video_path,
                pick=str(pick_value),
                resample=str(resample_value),
                chroma_tolerance=common.as_int(
                    tolerance_value, keying.DEFAULT_TOLERANCE
                ),
                chroma_soft=common.as_int(soft_value, keying.DEFAULT_SOFT_EDGE),
                unmix_strength=common.as_float(
                    unmix_value, keying.DEFAULT_UNMIX_STRENGTH
                ),
                restore_shadow=bool(shadow_value),
                fill_background_gaps=bool(gaps_value),
                progress=common.bridge(progress),
            )
        except ValueError as exc:
            return (f"[失敗] {exc}", *blank)
        except Exception as exc:  # noqa: BLE001
            return (common.failure(exc), *blank)

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
            files,
        )

    run_extract.click(
        on_extract,
        inputs=[
            session, video_upload, pick, resample,
            restore_shadow, fill_gaps, chroma_tolerance, chroma_soft, unmix,
        ],
        outputs=[
            extract_report, alpha_sheet, chroma_sheet,
            alpha_compare, chroma_compare, extract_files,
        ],
    )

    return {"pane": pane, "session": session}
