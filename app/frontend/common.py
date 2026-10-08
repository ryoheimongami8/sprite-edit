"""タブ間で共通の小物。

UI 側にロジックを置かないための緩衝材だけを入れる。判断はバックエンドが
持ち、ここは Gradio の型（float で返る Number、rgba() で返る ColorPicker）
を backend が期待する型へ均すことに徹する。
"""

from __future__ import annotations

import threading
import time
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


class ProgressLog:
    """進捗メッセージを経過時間つきで溜める。画面のリアルタイム表示と、
    どの区間が遅かったかの内訳（ボトルネック特定）の両方に使う。

    backend の ``(fraction, message)`` をそのまま差し込める。別スレッドから
    書いて UI スレッドから読むので、ロックで守る。
    """

    def __init__(self, gradio_progress: gr.Progress | None = None) -> None:
        self._start = time.perf_counter()
        self._events: list[tuple[float, float, str]] = []
        self._lock = threading.Lock()
        self._gradio = gradio_progress

    def __call__(self, fraction: float, message: str) -> None:
        now = time.perf_counter() - self._start
        with self._lock:
            self._events.append((now, fraction, message))
        if self._gradio is not None:
            try:
                self._gradio(fraction, desc=message)
            except Exception:  # noqa: BLE001
                pass

    def elapsed(self) -> float:
        return time.perf_counter() - self._start

    def render(self, *, running: bool) -> str:
        with self._lock:
            events = list(self._events)
        lines = [
            f"[{t:6.1f}s] {fraction * 100:3.0f}%  {message}"
            for t, fraction, message in events
        ]
        if running:
            lines.append(f"[{self.elapsed():6.1f}s] ...実行中")
        return "\n".join(lines)

    def breakdown(self, top: int = 6) -> str:
        """各メッセージから次のメッセージまでの所要時間が長い順。

        メッセージは「その作業を始めた」時点で出すので、区間の長さがそのまま
        直前メッセージの作業時間になる。
        """
        with self._lock:
            events = list(self._events)
        end = self.elapsed()
        spans = []
        for index, (t, _fraction, message) in enumerate(events):
            until = events[index + 1][0] if index + 1 < len(events) else end
            spans.append((until - t, message))
        spans.sort(key=lambda item: item[0], reverse=True)
        total = max(end, 1e-9)
        lines = [f"── 所要時間の内訳（合計 {end:.1f}s、長い順）──"]
        for seconds, message in spans[:top]:
            lines.append(f"{seconds:7.1f}s ({seconds / total * 100:3.0f}%)  {message}")
        return "\n".join(lines)


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


def connection_status() -> tuple[bool, str]:
    """(接続できているか, 表示用メッセージ)。起動時の通知と接続確認ボタンで共有。"""
    try:
        who = esora_cli.whoami()
    except esora_cli.EsoraCliError as exc:
        return False, f"[未接続] {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"[未接続] {exc}"
    return True, "\n".join(
        [
            f"サインイン中: {who.get('email', '?')}",
            f"profile: {who.get('profile', '?')} / base_url: {who.get('base_url', '?')}",
        ]
    )


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
