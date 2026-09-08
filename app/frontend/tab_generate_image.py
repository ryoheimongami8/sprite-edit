"""機能4 のタブ: esora API で衣装を差し替えた画像を1枚生成する。

機能1〜3 と違い、ここでの「実行」はローカルの画素操作ではなく esora の
サーバーへの生成依頼になる。手元でやり直しが効かない（＝生成のたびに
費用と時間がかかる）ので、実行の前に接続確認とモデル確認を独立したボタンに
分けてある。
"""

from __future__ import annotations

import gradio as gr

from backend import config, esora_cli, feature4, io_paths

from . import common


def _upload_path(uploaded) -> str:
    if uploaded is None:
        return ""
    if isinstance(uploaded, (list, tuple)):
        uploaded = uploaded[0]
    return str(getattr(uploaded, "name", uploaded))


def build(session_state: gr.State) -> dict:
    with gr.Tab("機能4: 画像生成（衣装差し替え）") as tab:
        gr.Markdown(
            "**画像1（ベース）** をそのまま編集し、衣装・防具・兜だけを "
            "**画像2（デザイン参照）** に差し替えた1枚を esora に生成させます。"
            "esora には `@img:1` のような番号付き参照記法は無いため、"
            "プロンプトは「1枚目の参照画像」「2枚目の参照画像」という言い方に"
            "揃えてあります。--img-ref もこの順（画像1 → 画像2）で渡します。"
        )

        with gr.Row():
            with gr.Column(scale=1):
                with gr.Row():
                    session = gr.Dropdown(
                        label="対象セッション（任意 — 記録の保存先）",
                        choices=common.session_choices(),
                        value=None,
                        allow_custom_value=True,
                        scale=5,
                    )
                    refresh = gr.Button("↻", scale=1, min_width=48)

                check = gr.Button("① 接続確認 (esora-api me)", variant="secondary")
                connection_note = gr.Markdown("")

                with gr.Group():
                    gr.Markdown(
                        "**② 参照画像** — セッションを選ぶと機能2の "
                        "First Frame を画像1の初期値にします。画像2は"
                        "衣装デザインの参照なので手動アップロードしてください。"
                    )
                    base_image = gr.File(
                        label="画像1: ベース画像（構図・ポーズ・画風の基準）",
                        file_types=["image"],
                        type="filepath",
                    )
                    design_image = gr.File(
                        label="画像2: デザイン参照（衣装の配色・装備のみ）",
                        file_types=["image"],
                        type="filepath",
                    )

                with gr.Group():
                    gr.Markdown("**③ モデル**")
                    with gr.Row():
                        model_hint = gr.Textbox(
                            label="モデルを探すキーワード（空白区切り）",
                            value=" ".join(config.ESORA_IMAGE_MODEL_HINTS),
                            scale=3,
                        )
                        find_model = gr.Button("モデルを確認", scale=1)
                    model = gr.Dropdown(
                        label="使用モデル (id)",
                        choices=[],
                        value=None,
                        allow_custom_value=True,
                        info="表示名(GPT image2など)はesora側のidと一致するとは"
                        "限らないため、実行時に一覧から探しています。",
                    )
                    model_note = gr.Markdown("")

                with gr.Group():
                    gr.Markdown("**④ サイズとプロンプト**")
                    with gr.Row():
                        aspect_ratio = gr.Textbox(
                            label="アスペクト比", value=config.DEFAULT_IMAGE_ASPECT_RATIO
                        )
                        image_size = gr.Textbox(
                            label="画像サイズ（モデルにより表記が違う。auto可）",
                            value=config.DEFAULT_IMAGE_SIZE,
                        )
                        seed = gr.Number(label="seed（空欄可）", value=None, precision=0)
                    prompt = gr.Textbox(
                        label="プロンプト（編集可）",
                        value=config.DEFAULT_IMAGE_PROMPT,
                        lines=18,
                        max_lines=40,
                    )

                run = gr.Button("⑤ 実行（esoraに生成依頼）", variant="primary")

            with gr.Column(scale=1):
                report = gr.Textbox(label="結果", lines=14, max_lines=28)
                result_image = gr.Image(
                    label="生成結果", height=360, type="filepath"
                )
                downloads = gr.File(label="書き出したファイル", file_count="multiple")

    # ─── 配線 ───

    refresh.click(
        lambda: gr.update(choices=common.session_choices()), outputs=session
    )

    def adopt(path):
        if not path:
            return gr.update(), gr.update()
        default_base = io_paths.latest_file(
            io_paths.open_session(path).first_frame, "*.png"
        )
        return (
            gr.update(choices=common.session_choices(), value=str(path)),
            str(default_base) if default_base else gr.update(),
        )

    session_state.change(adopt, inputs=session_state, outputs=[session, base_image])

    def on_check():
        return common.connection_report()

    check.click(on_check, outputs=connection_note)

    def on_find_model(hint_value):
        choices, note = common.resolve_models("image", str(hint_value))
        value = choices[0][1] if choices else None
        return gr.update(choices=choices, value=value), note

    find_model.click(on_find_model, inputs=model_hint, outputs=[model, model_note])

    def on_run(
        path, base_value, design_value, model_value,
        aspect_value, size_value, seed_value, prompt_value,
        progress=gr.Progress(),
    ):
        try:
            base_path = _upload_path(base_value)
            design_path = _upload_path(design_value)
            if not base_path or not design_path:
                return "画像1・画像2の両方を用意してください。", None, []
            session = (
                io_paths.open_session(path) if path else io_paths.new_session("feature4")
            )
            result = feature4.run(
                session,
                base_path,
                design_path,
                prompt=str(prompt_value),
                model=str(model_value or ""),
                aspect_ratio=str(aspect_value),
                image_size=str(size_value),
                seed=common.as_int(seed_value, 0) or None,
                progress=common.bridge(progress),
            )
        except (esora_cli.EsoraCliError, ValueError) as exc:
            return f"[失敗] {exc}", None, []
        except Exception as exc:  # noqa: BLE001
            return common.failure(exc), None, []

        return (
            result.report,
            common.existing(result.image_path),
            [found for found in (common.existing(result.image_path),) if found],
        )

    run.click(
        on_run,
        inputs=[
            session, base_image, design_image, model,
            aspect_ratio, image_size, seed, prompt,
        ],
        outputs=[report, result_image, downloads],
    )

    return {"tab": tab, "session": session}
