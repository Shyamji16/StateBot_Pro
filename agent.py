import logging
import os
import re
from difflib import get_close_matches

import pandas as pd

from app.graph import plot_aggregate, plot_column


MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
FIELD_ALIASES = {
    "state": ("state", "states", "satate", "region", "province"),
    "customer": ("customer", "customers", "client", "clients"),
    "customer name": ("customer name", "customers", "customer", "client"),
    "name": ("name", "names", "person", "people"),
    "product": ("product", "products", "item", "items", "category"),
    "date": ("date", "dates", "day", "time"),
    "sales": ("sales", "sale", "revenue", "total sales", "amount"),
}


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _requested_chart_type(question):
    words = set(_normalize(question).split())
    for chart_type in ("pie", "bar", "column", "line"):
        if chart_type in words or f"{chart_type}s" in words:
            return chart_type
    return None


def _numeric_columns(df):
    numeric = {}
    for column in df.columns:
        values = pd.to_numeric(
            df[column].astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False),
            errors="coerce",
        )
        if values.notna().sum() > 0 and values.notna().sum() >= max(1, len(df) * 0.5):
            numeric[column] = values
    return numeric


def _column_aliases(column):
    normalized = _normalize(column)
    aliases = {normalized}
    if normalized in ("satate", "state", "region", "province"):
        aliases.update(FIELD_ALIASES["state"])
    if "customer" in normalized and "name" in normalized:
        aliases.update(FIELD_ALIASES["customer name"])
    elif normalized in ("customer", "client"):
        aliases.update(FIELD_ALIASES["customer"])
    if normalized in ("product", "item", "category"):
        aliases.update(FIELD_ALIASES["product"])
    if normalized in ("date", "day", "time"):
        aliases.update(FIELD_ALIASES["date"])
    if "domain" in normalized:
        aliases.update(("domain", "domains"))
    return aliases


def _find_column(df, phrase, numeric_columns=None, exclude=None):
    query = _normalize(phrase)
    if not query:
        return None
    candidates = []
    for column in df.columns:
        if column == exclude:
            continue
        if numeric_columns is not None and column not in numeric_columns:
            continue
        normalized = _normalize(column)
        aliases = _column_aliases(column)
        if normalized and re.search(rf"\b{re.escape(normalized)}\b", query):
            candidates.append((len(normalized), column))
        elif any(alias and re.search(rf"\b{re.escape(alias)}\b", query) for alias in aliases):
            candidates.append((max(map(len, aliases)), column))
    if candidates:
        return max(candidates)[1]

    tokens = set(query.split())
    scored = []
    for column in df.columns:
        if column == exclude:
            continue
        if numeric_columns is not None and column not in numeric_columns:
            continue
        normalized = _normalize(column)
        column_tokens = set(normalized.split())
        overlap = len(tokens & column_tokens)
        if overlap:
            scored.append((overlap / max(1, len(column_tokens)), column))
    if scored:
        score, column = max(scored)
        if score >= 0.5:
            return column

    if numeric_columns:
        names = {_normalize(column): column for column in numeric_columns}
        close = get_close_matches(query, list(names), n=1, cutoff=0.82)
        if close:
            return names[close[0]]
    return None


def _resolve_sales_metric(df, numeric):
    for column in df.columns:
        name = _normalize(column)
        if "sales" in name and column in numeric:
            return column
    for column in df.columns:
        if _normalize(column) in MONTHS and column in numeric:
            continue
    return None


def _month_metric(df, numeric, question):
    present = [column for column in df.columns if _normalize(column) in MONTHS and column in numeric]
    if not present:
        return None
    mentioned = [column for column in present if re.search(rf"\b{_normalize(column)}\b", question)]
    if len(mentioned) == 1:
        return numeric[mentioned[0]], mentioned[0]
    if len(mentioned) >= 2 and any(term in question.split() for term in ("from", "between", "through", "to")):
        indices = sorted(MONTHS.index(_normalize(column)) for column in mentioned)
        selected = [column for column in present if indices[0] <= MONTHS.index(_normalize(column)) <= indices[-1]]
        return sum((numeric[column] for column in selected)), f"{selected[0]} to {selected[-1]} sales"
    return None


def _metric(df, numeric, question):
    month_metric = _month_metric(df, numeric, question)
    if month_metric:
        return month_metric
    metric_column = _find_column(df, question, numeric_columns=numeric)
    if metric_column and _normalize(metric_column).endswith(" id") and " id " not in f" {question} ":
        metric_column = None
    if metric_column:
        return numeric[metric_column], metric_column
    if "sales" in question or "revenue" in question or "amount" in question:
        sales_column = _resolve_sales_metric(df, numeric)
        if sales_column:
            return numeric[sales_column], sales_column
        monthly = [column for column in df.columns if _normalize(column) in MONTHS and column in numeric]
        if monthly:
            return sum((numeric[column] for column in monthly)), "January to December sales"
    return None


def _group_column(df, question, numeric, metric_column=None):
    normalized_question = _normalize(question)
    patterns = (
        r"\b(?:by|per|each|every|among|across|for each)\s+([a-z][a-z0-9 _-]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized_question)
        if not match:
            continue
        phrase = match.group(1).split(" with ")[0].split(" where ")[0]
        phrase = re.sub(r"\b(sales|revenue|total|average|mean|sum|count|ascending|descending)\b.*", "", phrase).strip()
        if phrase:
            found = _find_column(df, phrase, exclude=metric_column)
            if found and found not in numeric:
                return found

    candidates = []
    for column in df.columns:
        if column == metric_column:
            continue
        aliases = _column_aliases(column)
        normalized = _normalize(column)
        alias_hit = any(
            alias and re.search(rf"\b{re.escape(alias)}\b", normalized_question)
            for alias in aliases
        )
        header_hit = bool(normalized and re.search(rf"\b{re.escape(normalized)}\b", normalized_question))
        if (alias_hit or header_hit) and column not in numeric:
            candidates.append((len(normalized), column))
    if candidates:
        return max(candidates)[1]
    return None


def _requested_limit(question, default=10):
    match = re.search(
        r"\b(?:top|bottom|first|last)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        question,
    )
    if not match:
        return default
    value = match.group(1)
    return int(value) if value.isdigit() else NUMBER_WORDS[value]


def _apply_filters(df, question, numeric):
    filtered = df.copy()
    details = []
    comparator = re.search(
        r"\b(less than|under|below|at most|no more than|greater than|more than|over|above|at least|no less than)\s+([-+]?\d[\d,]*(?:\.\d+)?)",
        question,
    )
    symbolic = re.search(r"(>=|<=|>|<|=)\s*([-+]?\d[\d,]*(?:\.\d+)?)", question)
    if comparator or symbolic:
        match = comparator or symbolic
        operation, raw_value = match.groups()
        before = question[:match.start()]
        column = _find_column(df, before, numeric_columns=numeric)
        if not column and any(term in before.split() for term in ("sales", "revenue", "amount")):
            column = _resolve_sales_metric(df, numeric)
        if column:
            value = float(raw_value.replace(",", ""))
            series = numeric[column]
            if operation in ("less than", "under", "below", "<"):
                mask = series < value
                operator = "<"
            elif operation in ("at most", "no more than", "<="):
                mask = series <= value
                operator = "<="
            elif operation in ("at least", "no less than", ">="):
                mask = series >= value
                operator = ">="
            elif operation in ("greater than", "more than", "over", "above", ">"):
                mask = series > value
                operator = ">"
            else:
                mask = series == value
                operator = "="
            filtered = filtered[mask]
            details.append(f"Filtered {column} {operator} {value:g}")

    categories = [column for column in df.columns if column not in numeric]
    matches = []
    normalized_question = f" {_normalize(question)} "
    for column in categories:
        values = df[column].astype(str).str.strip()
        unique_values = values[values != ""].drop_duplicates()
        if len(unique_values) > 2000:
            continue
        for value in unique_values:
            normalized_value = _normalize(value)
            if len(normalized_value) < 2:
                continue
            if f" {normalized_value} " in normalized_question:
                matches.append((len(normalized_value), column, value))
    if matches:
        _, column, value = max(matches)
        if not any(detail.startswith(f"Filtered {column} ") for detail in details):
            filtered = filtered[filtered[column].astype(str).str.strip().str.casefold() == value.casefold()]
            details.append(f"Filtered {column} = {value}")
    return filtered, details


def _format_value(value):
    if pd.isna(value):
        return "no value"
    if isinstance(value, (int,)) or (isinstance(value, float) and value.is_integer()):
        return f"{int(value):,}"
    if isinstance(value, (float,)):
        return f"{value:,.2f}"
    return str(value)


def analyze_csv(file_name: str, question: str):
    question = (question or "").strip()
    steps = []
    if not question:
        return {"answer": "Please enter a question about your CSV data.", "steps": steps}

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    uploads_dir = os.path.abspath(os.path.join(base_dir, "uploads"))
    if not file_name or os.path.basename(file_name) != file_name:
        return {"answer": "Invalid file name.", "steps": steps}
    file_path = os.path.abspath(os.path.join(uploads_dir, file_name))
    if os.path.commonpath([uploads_dir, file_path]) != uploads_dir:
        return {"answer": "Unauthorized file access.", "steps": steps}
    if not os.path.isfile(file_path):
        return {"answer": "File not found. Upload the CSV again and retry.", "steps": steps}

    df = pd.read_csv(file_path, dtype=str).fillna("")
    df.columns = [str(column).strip() for column in df.columns]
    df = df.loc[:, [column for column in df.columns if column and not column.lower().startswith("unnamed:")]]
    if df.empty or len(df.columns) == 0:
        return {"answer": "The CSV has no usable rows or columns.", "steps": ["Loaded the CSV but found no usable data."]}

    steps.extend([
        f"Loaded {len(df):,} rows and {len(df.columns)} columns.",
        f"Available columns: {', '.join(df.columns)}.",
    ])
    normalized_question = _normalize(question)
    numeric = _numeric_columns(df)
    low = normalized_question
    email_column = next(
        (column for column in df.columns if "email" in _normalize(column)),
        None,
    )
    if "domain" in low.split() and email_column:
        df["Email domain"] = (
            df[email_column].astype(str).str.strip().str.lower()
            .str.extract(r"@([^@\s]+)", expand=False).fillna("")
        )
        steps.append(f"Extracted email domains from {email_column}.")

    requested_chart_type = _requested_chart_type(low)
    if any(word in low.split() for word in ("column", "columns", "header", "headers")) and not (
        requested_chart_type == "column"
        and any(word in low.split() for word in ("chart", "graph", "plot", "visualize", "visualise", "diagram"))
    ):
        return {"answer": f"The CSV contains {len(df.columns)} columns: {', '.join(df.columns)}.", "steps": steps}
    if any(word in low.split() for word in ("summary", "summarize", "summarise", "overview", "profile")):
        summary_rows = []
        for column in df.columns:
            values = df[column].astype(str).str.strip()
            non_empty = values[values != ""]
            row = {
                "column": column,
                "non_empty": int(non_empty.count()),
                "missing": int(len(df) - non_empty.count()),
                "unique": int(non_empty.nunique()),
            }
            if column in numeric:
                numeric_values = numeric[column]
                row.update({
                    "minimum": _format_value(numeric_values.min()),
                    "average": _format_value(numeric_values.mean()),
                    "maximum": _format_value(numeric_values.max()),
                })
            summary_rows.append(row)
        steps.append(f"Profiled {len(df):,} rows across {len(df.columns)} columns.")
        steps.append("Calculated non-empty, missing and unique counts; numeric columns also include min/average/max.")
        return {
            "answer": summary_rows,
            "steps": steps,
            "summary": f"This CSV contains {len(df):,} rows and {len(df.columns)} columns.",
        }
    if any(word in low.split() for word in ("duplicate", "duplicates", "deduplicate")):
        duplicates = df[df.duplicated(keep=False)]
        steps.append(f"Checked all columns and found {len(duplicates):,} rows that belong to duplicate groups.")
        if any(word in low.split() for word in ("how", "many", "count", "number")) or "count" in low.split():
            return {
                "answer": f"Found {len(duplicates):,} rows that are exact duplicates of another row.",
                "steps": steps,
            }
        return {
            "answer": duplicates.head(50).to_dict(orient="records"),
            "steps": steps + [f"Showing at most 50 of {len(duplicates):,} duplicate rows."],
        }
    if any(word in low.split() for word in ("missing", "null", "blank", "empty")):
        missing = df.apply(lambda column: column.astype(str).str.strip().eq("").sum())
        result = [
            {"column": column, "missing_values": int(count)}
            for column, count in missing.items()
        ]
        steps.append("Counted blank or missing values in each column.")
        return {"answer": result, "steps": steps}
    if re.search(r"\b(rows?|records|entries|how many)\b", low) and not re.search(r"\b(top|bottom|highest|lowest|sales|average|sum|total)\b", low):
        return {"answer": f"The CSV contains {len(df):,} data rows.", "steps": steps}
    if re.search(r"\b(unique|distinct|different)\b", low):
        column = _find_column(df, low)
        if column:
            count = df[column].astype(str).str.strip().replace("", pd.NA).nunique()
            steps.append(f"Counted distinct non-empty values in {column}.")
            return {"answer": f"There are {count:,} distinct non-empty values in {column}.", "steps": steps}

    working, filter_steps = _apply_filters(df, low, numeric)
    steps.extend(filter_steps)
    metric = _metric(df, numeric, low)
    metric_values, metric_label = metric if metric else (None, None)
    metric_display = (
        "sales" if metric_label and "sales" in metric_label.lower()
        else metric_label
    )
    group_column = _group_column(df, low, numeric, metric_label if metric_label in df.columns else None)
    wants_chart = any(word in low.split() for word in ("chart", "graph", "plot", "visualize", "visualise", "diagram"))
    chart_type = requested_chart_type
    wants_rank = any(word in low.split() for word in ("top", "bottom", "highest", "lowest", "largest", "smallest", "best", "worst", "maximum", "minimum", "max", "min"))
    wants_grouping = group_column is not None and any(
        word in low.split() for word in ("by", "per", "each", "every", "among", "across", "state", "states", "product", "products", "customer", "customers", "category", "categories")
    )

    # A "which month" question is a comparison across the wide monthly columns.
    if "month" in low.split() and any(word in low.split() for word in ("highest", "lowest", "top", "bottom", "sales", "total")):
        month_columns = [column for column in df.columns if _normalize(column) in MONTHS and column in numeric]
        if month_columns:
            monthly = pd.DataFrame({
                "month": [_normalize(column).title() for column in month_columns],
                "total_sales": [numeric[column].sum() for column in month_columns],
            })
            ascending = any(word in low.split() for word in ("lowest", "bottom", "smallest", "minimum", "min"))
            is_ranking = any(word in low.split() for word in ("highest", "lowest", "top", "bottom", "largest", "smallest"))
            limit = _requested_limit(low, 1 if is_ranking else len(month_columns))
            monthly = monthly.sort_values("total_sales", ascending=ascending).head(limit)
            direction = "lowest" if ascending else "highest"
            steps.append(f"Summed the monthly columns across {len(working):,} rows and ranked the months.")
            answer = monthly.to_dict(orient="records")
            if wants_chart:
                selected_chart_type = chart_type or "bar"
                chart = plot_aggregate(
                    monthly,
                    "month",
                    "total_sales",
                    title="Total sales by month",
                    chart_type=selected_chart_type,
                )
                steps.append(f"Generated a {selected_chart_type} chart of the monthly totals.")
                return {"answer": answer, "chart": chart, "steps": steps}
            return {"answer": answer, "steps": steps, "summary": f"The {direction} sales month is {monthly.iloc[0]['month']}."}

    if metric_values is not None:
        if metric_label in df.columns:
            values = pd.to_numeric(
                working[metric_label].astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False),
                errors="coerce",
            )
        else:
            values = metric_values.reindex(working.index)
        metric_values = values
        if metric_label not in df.columns and _normalize(metric_label).endswith("sales"):
            metric_values = metric_values

    if metric_values is not None and wants_grouping and group_column:
        group_data = working[[group_column]].copy()
        group_data["__metric"] = metric_values
        group_data[group_column] = group_data[group_column].astype(str).str.strip()
        group_data = group_data[group_data[group_column] != ""]
        if any(word in low.split() for word in ("average", "avg", "mean")):
            aggregation = "mean"
            aggregation_label = "average"
        elif "count" in low.split():
            aggregation = "count"
            aggregation_label = "count"
        elif any(word in low.split() for word in ("lowest", "smallest", "minimum", "min", "worst")):
            aggregation = "min"
            aggregation_label = "minimum"
        elif any(word in low.split() for word in ("highest", "largest", "maximum", "max", "best")) and not wants_rank:
            aggregation = "max"
            aggregation_label = "maximum"
        else:
            aggregation = "sum"
            aggregation_label = "total"
        grouped = group_data.groupby(group_column)["__metric"].agg(aggregation).dropna()
        ascending = any(word in low.split() for word in ("lowest", "bottom", "smallest", "minimum", "min", "worst"))
        ranking_requested = wants_rank
        if ranking_requested:
            grouped = grouped.sort_values(ascending=ascending).head(_requested_limit(low, 1))
        else:
            grouped = grouped.sort_values(ascending=False)
        display_group = "state" if _normalize(group_column) == "satate" else group_column
        result_column = f"{aggregation_label}_sales" if "sales" in metric_label.lower() else f"{aggregation_label}_{metric_label}"
        result = pd.DataFrame({display_group: grouped.index, result_column: grouped.values})
        summary = (
            f"Sales were aggregated by {group_column} using {aggregation} for {len(working):,} matching rows."
            if "sales" in metric_label.lower()
            else f"{metric_label} was aggregated by {group_column} using {aggregation} for {len(working):,} matching rows."
        )
        steps.append(summary)
        steps.append(f"Sorted the grouped results {'ascending' if ascending else 'descending'} and returned {len(result)} groups.")
        chart = None
        if wants_chart:
            selected_chart_type = chart_type or "bar"
            chart = plot_aggregate(
                result,
                display_group,
                result_column,
                title=f"{aggregation_label.title()} sales by {display_group}",
                chart_type=selected_chart_type,
            )
            steps.append(f"Generated a {selected_chart_type} chart for {display_group} and {result_column}.")
            if chart is None and selected_chart_type == "pie":
                steps.append("A pie chart requires non-negative values with a positive total.")
        answer = result.head(50).to_dict(orient="records")
        if wants_rank and len(answer) == 1:
            row = answer[0]
            summary = f"{row[display_group]} has the {'lowest' if ascending else 'highest'} {metric_display} at {_format_value(row[result_column])}."
            return {"answer": summary, "chart": chart, "steps": steps}
        return {"answer": answer, "chart": chart, "steps": steps}

    if metric_values is None and group_column and (
        any(word in low.split() for word in ("common", "frequent", "frequency", "count", "number", "top", "bottom", "highest", "lowest"))
    ):
        counts = working[group_column].astype(str).str.strip()
        counts = counts[counts != ""].value_counts()
        ascending = any(word in low.split() for word in ("lowest", "bottom", "least"))
        if ascending:
            counts = counts.sort_values(ascending=True)
        counts = counts.head(_requested_limit(low, 1 if any(word in low.split() for word in ("most", "common", "frequent", "highest", "lowest")) else 10))
        display_group = "state" if _normalize(group_column) == "satate" else group_column
        result = pd.DataFrame({display_group: counts.index, "count": counts.values})
        steps.append(f"Counted non-empty values in {group_column} across {len(working):,} matching rows.")
        steps.append(f"Returned the {len(result)} most {'least ' if ascending else ''}frequent values.")
        chart = None
        if wants_chart:
            selected_chart_type = chart_type or "bar"
            chart = plot_aggregate(
                result,
                display_group,
                "count",
                title=f"Record count by {display_group}",
                chart_type=selected_chart_type,
            )
            steps.append(f"Generated a {selected_chart_type} chart of counts by {display_group}.")
        return {"answer": result.to_dict(orient="records"), "chart": chart, "steps": steps}

    if metric_values is not None:
        clean_values = metric_values.dropna()
        if clean_values.empty:
            return {"answer": f"I could not find numeric values for {metric_label}.", "steps": steps}

        wants_total = any(word in low.split() for word in ("total", "sum", "combined", "altogether"))
        wants_average = any(word in low.split() for word in ("average", "mean", "avg"))
        wants_count = "count" in low.split()
        wants_min = any(word in low.split() for word in ("lowest", "smallest", "minimum", "min", "bottom", "worst"))
        wants_max = any(word in low.split() for word in ("highest", "largest", "maximum", "max", "top", "best"))

        if wants_min and wants_max and not wants_grouping:
            minimum = clean_values.min()
            maximum = clean_values.max()
            steps.append(f"Calculated minimum and maximum {metric_label} from {clean_values.count():,} numeric values.")
            return {
                "answer": f"Minimum {metric_display}: {_format_value(minimum)}. Maximum {metric_display}: {_format_value(maximum)}.",
                "steps": steps,
            }
        if wants_chart:
            if metric_label in df.columns:
                selected_chart_type = chart_type or "line"
                chart = plot_column(working, metric_label, chart_type=selected_chart_type)
                steps.append(f"Plotted the numeric column {metric_label} for {len(working):,} matching rows.")
                if chart is None and selected_chart_type == "pie":
                    steps.append("A pie chart requires non-negative values with a positive total.")
                return {
                    "answer": f"Generated a chart for {metric_label}. It plots the numeric values from {len(working):,} matching rows.",
                    "chart": chart,
                    "steps": steps,
                }
            return {
                "answer": "To chart these combined values, ask for a chart grouped by a column, such as 'chart total sales by state'.",
                "steps": steps,
            }

        if wants_total or wants_average or wants_count:
            if wants_count:
                result = int(clean_values.count())
                label = "number of numeric records"
            elif wants_average:
                result = clean_values.mean()
                label = f"average {metric_display}"
            else:
                result = clean_values.sum()
                label = f"total {metric_display}"
            steps.append(f"Calculated {label} using {clean_values.count():,} numeric values from {len(working):,} matching rows.")
            return {"answer": f"The {label} is {_format_value(result)}.", "steps": steps}

        if wants_rank:
            ascending = wants_min
            ranked = working.copy()
            ranked["__metric"] = metric_values
            ranked = ranked.dropna(subset=["__metric"]).sort_values("__metric", ascending=ascending)
            limit = _requested_limit(low, 1)
            selected = ranked.head(limit)
            steps.append(f"Ranked {len(ranked):,} matching rows by {metric_label} and selected {len(selected)}.")
            if limit == 1:
                row = selected.iloc[0]
                identity_column = _group_column(df, low, numeric, metric_label)
                identity = f"{identity_column} {row[identity_column]}" if identity_column and identity_column in row else f"row {row.name + 1}"
                direction = "lowest" if ascending else "highest"
                return {
                    "answer": f"The {direction} {metric_display} is {_format_value(row['__metric'])} for {identity}.",
                    "steps": steps,
                }
            fields = [column for column in df.columns if column in selected.columns]
            result = selected[fields].copy()
            result[metric_label] = selected["__metric"]
            return {"answer": result.to_dict(orient="records"), "steps": steps}

        if metric_label in df.columns and (any(word in low.split() for word in ("show", "list", "find", "filter", "where")) or filter_steps):
            columns_to_show = [column for column in df.columns if column in working.columns]
            steps.append(f"Returned up to 50 matching rows out of {len(working):,}.")
            return {"answer": working[columns_to_show].head(50).to_dict(orient="records"), "steps": steps}

        return {
            "answer": f"I found {metric_display} as a numeric field. Ask for its total, average, highest/lowest value, a ranking, or a chart.",
            "steps": steps,
        }

    if filter_steps or any(word in low.split() for word in ("show", "list", "find", "filter", "where")):
        steps.append(f"Returned up to 50 rows from {len(working):,} matching rows.")
        return {"answer": working.head(50).to_dict(orient="records"), "steps": steps}

    available_numeric = ", ".join(numeric.keys()) or "none detected"
    available_categories = ", ".join(column for column in df.columns if column not in numeric) or "none detected"
    return {
        "answer": (
            "I could not confidently determine the analysis you want. "
            f"This CSV has numeric fields ({available_numeric}) and category/text fields ({available_categories}). "
            "Try asking for a total, average, highest/lowest value, top N by a field, a filter (for example, 'sales over 100000 in Kerala'), "
            "or a chart (for example, 'chart total sales by state')."
        ),
        "steps": steps + ["No supported analysis intent or matching column could be identified."],
    }
