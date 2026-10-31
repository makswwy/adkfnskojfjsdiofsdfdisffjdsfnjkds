import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Проект должен быть в sys.path: worker запускается как отдельный скрипт,
# а состояние перезагрузки хранится во внешней PostgreSQL (json_store).
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.json_store import load as _json_store_load  # noqa: E402


VK_API_VERSION = "5.199"
MAX_PERCENT_BEFORE_READY = 94
TOTAL_SEGMENTS = 18
EDIT_INTERVAL_SECONDS = 2.4
INITIAL_DELAY_SECONDS = 2.0
MAX_RUNTIME_SECONDS = 120.0
MIN_ESTIMATE_SECONDS = 4.0
MAX_ESTIMATE_SECONDS = 30.0


def _clamp_estimate(value):
    try:
        return max(MIN_ESTIMATE_SECONDS, min(MAX_ESTIMATE_SECONDS, float(value)))
    except Exception:
        return 8.0


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


def _load_state(path):
    # Состояние перезагрузки хранится во внешней БД (таблица json_store).
    data = _json_store_load(path, default=None)
    return {} if data is None else data


def _progress_bar(percent):
    percent = max(0, min(100, int(percent)))
    filled = int(round(percent * TOTAL_SEGMENTS / 100))
    filled = max(0, min(TOTAL_SEGMENTS, filled))
    return "\u2588" * filled + "\u2591" * (TOTAL_SEGMENTS - filled)


def _progress_text(percent):
    percent = max(0, min(100, int(percent)))
    return f"\u041f\u0435\u0440\u0435\u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0431\u043e\u0442\u0430\n[{_progress_bar(percent)}] {percent:3d}%"


def _elapsed_percent(started_at, estimate_seconds):
    elapsed = max(0.0, time.time() - float(started_at))
    estimate_seconds = _clamp_estimate(estimate_seconds)
    return max(1, min(MAX_PERCENT_BEFORE_READY, round(MAX_PERCENT_BEFORE_READY * min(1.0, elapsed / estimate_seconds))))


def _is_same_restart(state, peer_id, cmid):
    try:
        return int(state.get("peer_id") or 0) == peer_id and int(state.get("cmid") or 0) == cmid
    except Exception:
        return False


def _vk_edit(token, group_id, peer_id, cmid, text):
    data = urllib.parse.urlencode(
        {
            "access_token": token,
            "v": VK_API_VERSION,
            "group_id": group_id,
            "peer_id": peer_id,
            "conversation_message_id": cmid,
            "message": text,
        }
    ).encode("utf-8")
    request = urllib.request.Request("https://api.vk.com/method/messages.edit", data=data)
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    error = payload.get("error")
    if not error:
        return "ok"
    error_text = str(error.get("error_msg") or "").lower()
    if int(error.get("error_code") or 0) == 9 or "flood control" in error_text or "too much messages" in error_text:
        return "flood"
    return "error"


def main():
    if len(sys.argv) < 6:
        return 2

    state_path = Path(sys.argv[1])
    peer_id = int(sys.argv[2])
    cmid = int(sys.argv[3])
    started_at = float(sys.argv[4])
    estimate_seconds = _clamp_estimate(sys.argv[5])

    config = _load_json("config.json")
    token = str(config.get("bot-token") or "")
    group_id = int(config.get("group_id") or 0)
    if not token or group_id <= 0:
        return 2

    time.sleep(INITIAL_DELAY_SECONDS)
    deadline = started_at + min(MAX_RUNTIME_SECONDS, estimate_seconds * 3 + 30)
    last_percent = 0

    while time.time() < deadline:
        state = _load_state(state_path)
        if not isinstance(state, dict) or state.get("done") or not _is_same_restart(state, peer_id, cmid):
            return 0

        percent = _elapsed_percent(started_at, estimate_seconds)
        if percent > last_percent:
            result = _vk_edit(token, group_id, peer_id, cmid, _progress_text(percent))
            if result == "ok":
                last_percent = percent
            elif result == "flood":
                time.sleep(4.0)
            else:
                return 1

        time.sleep(EDIT_INTERVAL_SECONDS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
