import os
import re
from pathlib import Path

import pandas as pd


def _clean_column_name(value):
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _resolve_column(df, supplied_name):
    target = _clean_column_name(supplied_name.strip().strip("\"'`.,!?"))
    for column in df.columns:
        if _clean_column_name(column) == target:
            return column
    aliases = {"state": ("state", "satate", "region")}
    for canonical, options in aliases.items():
        if target == canonical or target in options:
            for column in df.columns:
                if _clean_column_name(column) in options:
                    return column
    return None


def prepare_destructive_action(file_name, question, uploads_dir):
    """Return a safe preview action, or None when the request is not explicit."""
    text = (question or "").strip()
    lowered = text.casefold()
    verb = re.search(r"\b(delete|remove|drop|clean)\b", lowered)
    if not verb:
        return None

    path = Path(uploads_dir) / file_name
    if not path.is_file():
        return None
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        return {"error": f"Could not inspect this CSV safely: {exc}"}
    df.columns = [str(column).strip() for column in df.columns]

    if re.search(r"\b(file|csv|dataset|spreadsheet|table)\b", lowered) and not re.search(r"\b(rows?|records?|duplicates?|values?|column)\b", lowered):
        return {
            "kind": "delete_file",
            "file_name": file_name,
            "description": f"Permanently remove the uploaded file “{file_name}” from this workspace.",
        }

    if re.search(r"\b(remove|delete|drop|clean)\b.*\bduplicates?\b|\bdeduplicate\b", lowered):
        removed = int(df.duplicated().sum())
        return {
            "kind": "deduplicate",
            "file_name": file_name,
            "removed": removed,
            "description": f"Create a cleaned copy of {file_name} with {removed:,} duplicate row(s) removed. The original file will be kept.",
        }

    column_match = re.search(
        r"\b(?:drop|remove|delete)\s+(?:the\s+)?column\s+(.+?)\s*$",
        text,
        re.IGNORECASE,
    )
    if column_match:
        column = _resolve_column(df, column_match.group(1))
        if not column:
            return {"error": f"I could not find column “{column_match.group(1).strip()}” in this CSV. No changes were made."}
        return {
            "kind": "drop_column",
            "file_name": file_name,
            "column": column,
            "description": f"Create a copy of {file_name} without the “{column}” column. The original file will be kept.",
        }

    rows_match = re.search(
        r"\b(?:delete|remove|drop)\s+(?:all\s+)?(?:rows?|records?|entries)\s+(?:where|with)\s+(.+?)\s*(?:=|:\s*|\bis\b|\bequals?\b)\s*(.+?)\s*[.!?]*$",
        text,
        re.IGNORECASE,
    )
    if rows_match:
        column = _resolve_column(df, rows_match.group(1))
        value = rows_match.group(2).strip().strip("\"'` ")
        if not column or not value:
            return {"error": "Specify an existing column and exact value, for example: remove rows where state is Kerala. No changes were made."}
        matches = df[column].astype(str).str.strip().str.casefold() == value.casefold()
        return {
            "kind": "delete_rows",
            "file_name": file_name,
            "column": column,
            "value": value,
            "removed": int(matches.sum()),
            "description": f"Create a filtered copy of {file_name} without {int(matches.sum()):,} row(s) where “{column}” equals “{value}”. The original file will be kept.",
        }

    value_match = re.search(
        r"\b(?:delete|remove)\s+(?:the\s+)?value\s+(.+?)\s+from\s+(?:the\s+)?column\s+(.+?)\s*[.!?]*$",
        text,
        re.IGNORECASE,
    )
    if value_match:
        value = value_match.group(1).strip().strip("\"'` ")
        column = _resolve_column(df, value_match.group(2))
        if not column or not value:
            return {"error": "Specify an existing column and exact value, for example: remove value Kerala from column state. No changes were made."}
        matches = df[column].astype(str).str.strip().str.casefold() == value.casefold()
        return {
            "kind": "delete_value",
            "file_name": file_name,
            "column": column,
            "value": value,
            "removed": int(matches.sum()),
            "description": f"Create a copy of {file_name} with {int(matches.sum()):,} matching “{value}” value(s) removed from “{column}”. The original file will be kept.",
        }

    return {
        "error": "I did not make any changes. For safety, specify exactly what to remove, such as “remove duplicates”, “drop column Product”, or “remove rows where state is Kerala”. I will show a confirmation before applying it."
    }


def apply_confirmed_action(action, uploads_dir):
    file_name = action["file_name"]
    source = Path(uploads_dir) / file_name
    if not source.is_file():
        return {"error": "The selected CSV no longer exists; no changes were made."}

    if action["kind"] == "delete_file":
        source.unlink()
        return {"message": f"Deleted {file_name} after confirmation."}

    df = pd.read_csv(source, dtype=str).fillna("")
    df.columns = [str(column).strip() for column in df.columns]
    if action["kind"] == "deduplicate":
        before = len(df)
        df = df.drop_duplicates()
        removed = before - len(df)
    elif action["kind"] == "drop_column":
        column = action["column"]
        if column not in df.columns:
            return {"error": f"Column “{column}” no longer exists; no changes were made."}
        df = df.drop(columns=[column])
        removed = None
    elif action["kind"] in ("delete_rows", "delete_value"):
        column = action["column"]
        if column not in df.columns:
            return {"error": f"Column “{column}” no longer exists; no changes were made."}
        matches = df[column].astype(str).str.strip().str.casefold() == action["value"].casefold()
        removed = int(matches.sum())
        if action["kind"] == "delete_rows":
            df = df.loc[~matches]
        else:
            df.loc[matches, column] = ""
    else:
        return {"error": "This confirmation request is not supported; no changes were made."}

    base = source.stem
    suffix = "_cleaned"
    output = source.with_name(f"{base}{suffix}.csv")
    counter = 2
    while output.exists():
        output = source.with_name(f"{base}{suffix}_{counter}.csv")
        counter += 1
    df.to_csv(output, index=False)

    if removed is not None:
        detail = f" Removed {removed:,} row(s)/duplicate(s)."
    else:
        detail = f" Removed the “{action['column']}” column."
    return {"message": f"Created cleaned copy {output.name}.{detail}", "file_name": output.name}
