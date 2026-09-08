"""タブ間で共通の小物。

UI 側にロジックを置かないための緩衝材だけを入れる。判断はバックエンドが
持ち、ここは Gradio の型（float で返る Number、rgba() で返る ColorPicker）
を backend が期待する型へ均すことに徹する。
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable

import gradio as gr

from backend import esora_cli, io_paths
from backend.io_paths import Session


def as_int(value, fallback: int = 0) -> int:
    """Number コンポーネントは float も None も返す。"""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def as_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def bridge(progress: gr.Progress) -> Callable[[float, str], None]:
    """backend の ``(fraction, message)`` を Gradio の進捗へつなぐ。"""

    def report(fraction: float, message: str) -> None:
        progress(fraction, desc=message)

    return report


def failure(exc: Exception) -> str:
    """例外を、UI にそのまま出せる 1 ブロックにする。

    握り潰さない。素材ごとに落ちどころが違う工程なので、何行目で何が
    起きたかが見えないと手で直しようがない。
    """
    return f"[失敗] {exc}\n\n{traceback.format_exc()}"


def session_choices() -> list[str]:
    """work/ にある作業を新しい順で。表示文字列がそのままパスになる。"""
    return [str(session.root) for session in io_paths.list_sessions()]


def session_label(session: Session) -> str:
    return session.name


def existing(path: Path | None) -> str | None:
    """まだ書かれていない出力を Gradio に渡さないための門番。"""
    return str(path) if path and Path(path).exists() else None


# ─── 機能4・5 共通: esora API 接続まわり ───


def connection_report() -> str:
    """「① 接続確認」用。サインイン済みか、esora-api が見つかるかを見せる。

    このアプリ自身はログイン処理を持たない。`esora-api auth login` は
    別の端末で事前に済ませておく前提で、ここでは結果を読むだけにする。
    """
    try:
        who = esora_cli.whoami()
    except esora_cli.EsoraCliError as exc:
        return f"[未接続] {exc}"
    return "\n".join(
        [
            f"サインイン中: {who.get('email', '?')}",
            f"profile: {who.get('profile', '?')} / base_url: {who.get('base_url', '?')}",
        ]
    )


def model_dropdown_choices(models: list[dict]) -> list[tuple[str, str]]:
    """model list の結果を Dropdown の (表示, 値=id) に均す。"""
    return [
        (f"{model.get('label') or model.get('id')}  [{model.get('id')}]", model.get("id"))
        for model in models
        if model.get("id")
    ]


def resolve_models(task: str, keywords_text: str) -> tuple[list[tuple[str, str]], str]:
    """キーワード（空白区切り）でモデルを探す。見つからなければ全件を返す。

    表示名（「GPT image2」等）は esora の model id と一致するとは限らない
    ので、実行のたびにサーバーから引く。ネットワークが無い・未サインイン
    のときはここで失敗が分かるようにしている。
    """
    keywords = [word for word in keywords_text.replace("　", " ").split(" ") if word]
    try:
        hits = esora_cli.find_models(task, keywords)
        if hits:
            return model_dropdown_choices(hits), f"{len(hits)}件ヒット: {keywords}"
        every = esora_cli.list_models(task)
        note = (
            f"[注意] キーワード {keywords} に一致するモデルがありません。"
            f"{task}系の全モデル({len(every)}件)から選んでください。"
        )
        return model_dropdown_choices(every), note
    except esora_cli.EsoraCliError as exc:
        return [], f"[失敗] {exc}"
