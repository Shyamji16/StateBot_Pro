import csv
import io
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.agent import analyze_csv
from app.history import get_chat_history, save_chat
from app.operations import apply_confirmed_action, prepare_destructive_action


logging.basicConfig(level=logging.INFO)
APP_ROOT = Path(__file__).resolve().parent.parent
CHARTS_FOLDER = APP_ROOT / "charts"
UPLOAD_FOLDER = APP_ROOT / "uploads"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PENDING_ACTION_TTL = 300
MAX_REPORT_PAYLOAD_LENGTH = 2 * 1024 * 1024

CHARTS_FOLDER.mkdir(exist_ok=True)
UPLOAD_FOLDER.mkdir(exist_ok=True)

app = FastAPI(title="StatBot Pro", description="Explore CSV datasets with natural-language analytics.")
app.mount("/charts", StaticFiles(directory=str(CHARTS_FOLDER)), name="charts")
templates = Jinja2Templates(directory=str(APP_ROOT / "app" / "templates"))

pending_actions = {}
pending_actions_lock = threading.Lock()


def get_uploaded_files():
    return sorted(
        (
            path.name
            for path in UPLOAD_FOLDER.iterdir()
            if path.is_file() and path.suffix.lower() == ".csv"
        ),
        key=str.casefold,
    )


def _selected_file(file_name):
    available = get_uploaded_files()
    if file_name in available:
        return file_name
    return available[0] if available else None


def _csv_safe_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    elif value is None:
        value = ""
    else:
        value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _report_csv(payload):
    answer = payload.get("answer")
    rows = answer if isinstance(answer, list) else [answer]
    if rows and all(isinstance(row, dict) for row in rows):
        answer_fields = list(dict.fromkeys(key for row in rows for key in row))
        output_rows = rows
    elif isinstance(answer, dict):
        answer_fields = list(answer)
        output_rows = [answer]
    else:
        answer_fields = ["answer"]
        output_rows = [{"answer": answer}]
    metadata_fields = [
        field
        for field in ("Question asked", "Summary")
        if payload.get("question" if field == "Question asked" else "summary") is not None
    ]
    fieldnames = metadata_fields + [
        field for field in answer_fields if field not in metadata_fields
    ]

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in output_rows:
        report_row = {
            "Question asked": payload.get("question"),
            "Summary": payload.get("summary"),
            **row,
        }
        writer.writerow({key: _csv_safe_value(report_row.get(key)) for key in fieldnames})
    return output.getvalue()


def _render(request, view="dashboard", **context):
    is_chat = view == "chat"
    shared = {
        "request": request,
        "uploaded_files": get_uploaded_files(),
        "selected_file": _selected_file(context.get("selected_file")),
        "message": None,
        "message_type": "success",
        "asked_question": "",
        "answer": None,
        "summary": None,
        "steps": None,
        "chart_url": None,
        "chart_download_url": None,
        "pending_action": None,
        "confirmation_token": None,
        **context,
    }
    if is_chat:
        shared["chat_history"] = get_chat_history()
    return templates.TemplateResponse(
        request=request,
        name="chat.html" if is_chat else "index.html",
        context=shared,
    )


def _create_pending_action(action):
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with pending_actions_lock:
        expired = [key for key, value in pending_actions.items() if value["expires"] < now]
        for key in expired:
            pending_actions.pop(key, None)
        pending_actions[token] = {
            "action": action,
            "expires": now + PENDING_ACTION_TTL,
        }
    return token


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _render(request)


@app.get("/chat/", response_class=HTMLResponse)
def chat_page(request: Request, file_name: str = ""):
    return _render(request, view="chat", selected_file=file_name or None)


@app.post("/download/report/")
async def download_report(report_payload: str = Form(...)):
    if len(report_payload) > MAX_REPORT_PAYLOAD_LENGTH:
        return Response("Report is too large to download.", status_code=413)
    try:
        payload = json.loads(report_payload)
    except json.JSONDecodeError:
        return Response("Invalid report data.", status_code=400)
    if not isinstance(payload, dict) or "answer" not in payload:
        return Response("Invalid report data.", status_code=400)
    return Response(
        content=_report_csv(payload),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="statbot-report.csv"'},
    )


@app.get("/download/chart/{filename}")
def download_chart(filename: str):
    if Path(filename).name != filename or Path(filename).suffix.lower() != ".png":
        return Response("Chart not found.", status_code=404)
    chart_path = (CHARTS_FOLDER / filename).resolve()
    if chart_path.parent != CHARTS_FOLDER.resolve() or not chart_path.is_file():
        return Response("Chart not found.", status_code=404)
    return FileResponse(chart_path, media_type="image/png", filename=filename)


@app.get("/upload/", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"uploaded_files": get_uploaded_files(), "message": None, "message_type": "success"},
    )


@app.post("/upload/", response_class=HTMLResponse)
async def upload_file(request: Request, file: UploadFile = File(...)):
    if not file.filename or Path(file.filename).suffix.lower() != ".csv":
        return templates.TemplateResponse(
            request=request,
            name="upload.html",
            context={
                "uploaded_files": get_uploaded_files(),
                "message": "Please choose a CSV file to continue.",
                "message_type": "error",
            },
            status_code=400,
        )

    safe_name = Path(file.filename).name
    if safe_name in ("", ".", ".."):
        return templates.TemplateResponse(
            request=request,
            name="upload.html",
            context={
                "uploaded_files": get_uploaded_files(),
                "message": "That file name is not valid.",
                "message_type": "error",
            },
            status_code=400,
        )

    file_path = UPLOAD_FOLDER / safe_name
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    counter = 2
    while file_path.exists():
        file_path = UPLOAD_FOLDER / f"{stem}_{counter}{suffix}"
        counter += 1

    byte_count = 0
    try:
        with file_path.open("xb") as destination:
            while chunk := await file.read(1024 * 1024):
                byte_count += len(chunk)
                if byte_count > MAX_UPLOAD_BYTES:
                    destination.close()
                    file_path.unlink(missing_ok=True)
                    raise ValueError("CSV files must be 25 MB or smaller.")
                destination.write(chunk)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="upload.html",
            context={
                "uploaded_files": get_uploaded_files(),
                "message": str(exc),
                "message_type": "error",
            },
            status_code=413,
        )
    except OSError:
        logging.exception("Could not save uploaded CSV.")
        file_path.unlink(missing_ok=True)
        return templates.TemplateResponse(
            request=request,
            name="upload.html",
            context={
                "uploaded_files": get_uploaded_files(),
                "message": "The CSV could not be saved. Please try again.",
                "message_type": "error",
            },
            status_code=500,
        )
    finally:
        await file.close()

    if byte_count == 0:
        file_path.unlink(missing_ok=True)
        return templates.TemplateResponse(
            request=request,
            name="upload.html",
            context={
                "uploaded_files": get_uploaded_files(),
                "message": "The selected file is empty.",
                "message_type": "error",
            },
            status_code=400,
        )

    logging.info("Uploaded CSV: %s (%s bytes)", file_path.name, byte_count)
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={
            "uploaded_files": get_uploaded_files(),
            "selected_file": file_path.name,
            "message": f"{file_path.name} is uploaded and ready to explore.",
            "message_type": "success",
        },
    )


@app.post("/ask/", response_class=HTMLResponse)
async def ask_question(
    request: Request,
    file_name: str = Form(...),
    question: str = Form(...),
    view: str = Form("dashboard"),
):
    view = "chat" if view == "chat" else "dashboard"
    file_name = Path(file_name).name
    question = question.strip()
    logging.info("File: %s; question: %s", file_name, question)

    destructive_action = prepare_destructive_action(file_name, question, str(UPLOAD_FOLDER))
    if destructive_action:
        if "error" in destructive_action:
            save_chat(question, destructive_action["error"])
            return _render(
                request,
                view=view,
                selected_file=file_name,
                asked_question=question,
                answer=destructive_action["error"],
                message_type="error",
            )
        token = _create_pending_action(destructive_action)
        answer = "This action can change or remove data. Review the operation and confirm below; no changes have been made yet."
        return _render(
            request,
            view=view,
            selected_file=file_name,
            asked_question=question,
            answer=answer,
            pending_action=destructive_action,
            confirmation_token=token,
            steps=["No data has been changed. Confirmation is required.", f"Confirmation expires in {PENDING_ACTION_TTL // 60} minutes."],
        )

    try:
        result = analyze_csv(file_name, question)
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError, ValueError):
        logging.exception("CSV question could not be analyzed.")
        return _render(
            request,
            view=view,
            selected_file=file_name,
            asked_question=question,
            answer="The CSV could not be analyzed. Check the file format and try again.",
            message_type="error",
        )
    except Exception:
        logging.exception("Unexpected error while analyzing CSV.")
        return _render(
            request,
            view=view,
            selected_file=file_name,
            asked_question=question,
            answer="An unexpected error occurred while analyzing the CSV. Check the server log for details.",
            message_type="error",
        )

    chart_url = None
    chart_download_url = None
    if isinstance(result, dict):
        answer = result.get("answer")
        summary = result.get("summary")
        steps = result.get("steps")
        chart_path = result.get("chart")
        if chart_path:
            resolved_chart = Path(chart_path).resolve()
            if resolved_chart.parent == CHARTS_FOLDER.resolve() and resolved_chart.is_file():
                chart_url = f"/charts/{resolved_chart.name}"
                chart_download_url = f"/download/chart/{resolved_chart.name}"
    else:
        answer = result
        summary = None
        steps = None

    save_chat(question, answer, file_name=file_name, chart_url=chart_url)
    return _render(
        request,
        view=view,
        selected_file=file_name,
        asked_question=question,
        answer=answer,
        summary=summary,
        chart_url=chart_url,
        chart_download_url=chart_download_url,
        steps=steps,
    )


@app.post("/confirm-action/", response_class=HTMLResponse)
async def confirm_action(
    request: Request,
    confirmation_token: str = Form(...),
    confirmed: str = Form(...),
    view: str = Form("chat"),
):
    view = "chat" if view == "chat" else "dashboard"
    with pending_actions_lock:
        pending = pending_actions.pop(confirmation_token, None)
    if not pending or pending["expires"] < time.monotonic():
        return _render(
            request,
            view=view,
            answer="This confirmation has expired or was already used. Ask again to preview the action; no changes were made.",
            message_type="error",
        )
    if confirmed != "yes":
        return _render(
            request,
            view=view,
            selected_file=pending["action"]["file_name"],
            answer="Action cancelled. No changes were made.",
        )

    try:
        result = apply_confirmed_action(pending["action"], str(UPLOAD_FOLDER))
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError, ValueError):
        logging.exception("Confirmed data operation failed.")
        result = {"error": "The confirmed operation failed. The original CSV was not intentionally overwritten; check the server log."}

    file_name = result.get("file_name") or pending["action"]["file_name"]
    message = result.get("error") or result.get("message")
    save_chat(f"Confirm action: {pending['action']['description']}", message)
    return _render(
        request,
        view=view,
        selected_file=file_name,
        answer=message,
        message_type="error" if result.get("error") else "success",
        steps=["Confirmation was received.", "The original file was kept." if result.get("file_name") else "The requested action was completed."],
    )
