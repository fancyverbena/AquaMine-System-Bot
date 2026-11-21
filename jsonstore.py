"""JSON設定ファイルの共有ストア。

全Cogがこのモジュール経由で設定ファイルを読み書きすることで、
読み込み→変更→書き込みの競合で設定が巻き戻るのを防ぐ。
書き込みは一時ファイル + os.replace で原子的（atomic）に行う。
JSONが壊れていた場合は、黙って初期化せず、壊れたファイルを
``<path>.corrupt`` に退避してからデフォルト値にフォールバックする。
"""

import copy
import json
import os
import threading

_lock = threading.RLock()


def _backup_corrupt(path: str) -> str:
    """壊れたファイルを <path>.corrupt に退避し、そのパスを返す。"""
    backup_path = f"{path}.corrupt"

    if os.path.exists(backup_path):
        counter = 1

        while os.path.exists(f"{backup_path}.{counter}"):
            counter += 1

        backup_path = f"{backup_path}.{counter}"

    os.replace(path, backup_path)

    print(
        f"⚠️ 設定ファイル {path} が壊れていたため "
        f"{backup_path} に退避しました。"
    )

    return backup_path


def _write_json_unlocked(path: str, data):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    temp_path = f"{path}.tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    os.replace(temp_path, path)


def _read_json_unlocked(path: str, default):
    if not os.path.exists(path):
        _write_json_unlocked(path, copy.deepcopy(default))
        return copy.deepcopy(default)

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            _write_json_unlocked(path, copy.deepcopy(default))
            return copy.deepcopy(default)

        data = json.loads(content)

    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        _backup_corrupt(path)
        _write_json_unlocked(path, copy.deepcopy(default))
        return copy.deepcopy(default)

    if not isinstance(data, type(default)):
        _backup_corrupt(path)
        _write_json_unlocked(path, copy.deepcopy(default))
        return copy.deepcopy(default)

    return data


def read_json(path: str, default):
    """ロック付きでJSONを読む。壊れていたら退避してデフォルトを返す。"""
    with _lock:
        return _read_json_unlocked(path, default)


def write_json(path: str, data):
    """ロック付きでJSONを原子的に書き込む。"""
    with _lock:
        _write_json_unlocked(path, data)


def update_json(path: str, default, mutator):
    """読み込み→変更→書き込みをロック内で一括して行う。

    mutator は読み込んだデータを受け取り、変更したデータを返す
    （その場で変更して None を返してもよい）。更新後のデータを返す。
    """
    with _lock:
        data = _read_json_unlocked(path, default)

        result = mutator(data)

        if result is not None:
            data = result

        _write_json_unlocked(path, data)

        return data
