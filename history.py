import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
HISTORY_RETENTION = timedelta(days=7)
HISTORY_LOCK = threading.Lock()


def _retained_history(data, now):
    retained = []
    for record in data:
        if not isinstance(record, dict):
            continue
        try:
            saved_at = datetime.fromisoformat(record["time"])
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=now.tzinfo)
            if now - saved_at.astimezone(timezone.utc) < HISTORY_RETENTION:
                retained.append(record)
        except (KeyError, TypeError, ValueError):
            logging.warning("Dropping chat history record with an invalid timestamp.")
    return retained


def _read_history(now):
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as history_file:
            data = json.load(history_file)
        if not isinstance(data, list):
            raise ValueError("Chat history must contain a JSON list.")
        return _retained_history(data, now)
    except (OSError, json.JSONDecodeError, ValueError):
        logging.exception("Error loading chat history.")
        return None


def _write_history(data):
    history_dir = os.path.dirname(HISTORY_FILE)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=history_dir,
            prefix=".chat_history_",
            suffix=".tmp",
            delete=False,
        ) as history_file:
            temporary_path = history_file.name
            json.dump(data, history_file, indent=4, ensure_ascii=False)
        os.replace(temporary_path, HISTORY_FILE)
    except OSError:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def save_chat(question, answer, file_name=None, chart_url=None):
    now = datetime.now().astimezone()
    record = {
        "question": question,
        "answer": answer,
        "time": now.isoformat(),
    }
    if file_name:
        record["file_name"] = file_name
    if chart_url:
        record["chart_url"] = chart_url

    with HISTORY_LOCK:
        data = _read_history(now)
        if data is None:
            raise ValueError("Chat history could not be read, so it was not overwritten.")
        data.append(record)
        _write_history(data)
    logging.info("Chat history saved successfully.")


def get_chat_history(limit=20):
    now = datetime.now().astimezone()
    with HISTORY_LOCK:
        data = _read_history(now)
        if data is None:
            return []
        _write_history(data)
    return data[-limit:]
