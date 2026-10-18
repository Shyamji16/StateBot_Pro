import threading

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import os
import re
import uuid
import logging

PLOT_LOCK = threading.Lock()
CHART_TYPES = {"bar", "column", "line", "pie"}


def _validate_chart_type(chart_type):
    if chart_type not in CHART_TYPES:
        raise ValueError(f"Unsupported chart type: {chart_type}")


def plot_column(df, column, chart_type="line"):
    _validate_chart_type(chart_type)

    logging.info("Generating %s plot for column: %s", chart_type, column)

    numeric_series = pd.to_numeric(df[column], errors="coerce").dropna()

    if numeric_series.empty:
        return None

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    charts_dir = os.path.join(BASE_DIR, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    safe_column = re.sub(r"[^A-Za-z0-9_-]+", "_", str(column)).strip("_") or "column"
    file_name = f"{safe_column}_{uuid.uuid4().hex}.png"
    file_path = os.path.join(charts_dir, file_name)

    with PLOT_LOCK:
        values = numeric_series.head(50)
        figure, axis = plt.subplots(figsize=(10, 5))
        if chart_type == "line":
            axis.plot(values.to_numpy(), color="#7668d8", marker="o")
            axis.set_xlabel("Index")
            axis.set_ylabel(column)
        elif chart_type in ("bar", "column"):
            labels = [str(index + 1) for index in range(len(values))]
            if chart_type == "bar":
                axis.barh(labels, values.to_numpy(), color="#7668d8")
                axis.set_ylabel("Row")
                axis.set_xlabel(column)
            else:
                axis.bar(labels, values.to_numpy(), color="#7668d8")
                axis.set_xlabel("Row")
                axis.set_ylabel(column)
        else:
            values = values.head(20)
            if (values < 0).any() or values.sum() <= 0:
                plt.close(figure)
                return None
            axis.pie(
                values.to_numpy(),
                labels=[str(index + 1) for index in range(len(values))],
                autopct="%1.1f%%",
            )
            axis.set_aspect("equal")
        axis.set_title(f"{column} {chart_type.title()} Chart")
        figure.tight_layout()
        figure.savefig(file_path)
        plt.close(figure)

    logging.info("Plot saved at: %s", file_path)

    return file_path


def plot_aggregate(df, category_column, value_column, title=None, chart_type="bar"):
    _validate_chart_type(chart_type)
    logging.info(
        "Generating %s aggregate plot for %s by %s",
        chart_type,
        value_column,
        category_column,
    )

    plot_data = df[[category_column, value_column]].copy()
    plot_data[value_column] = pd.to_numeric(plot_data[value_column], errors="coerce")
    plot_data = plot_data.dropna(subset=[value_column]).head(40)
    if plot_data.empty:
        return None

    labels = plot_data[category_column].astype(str)
    values = plot_data[value_column]
    if chart_type == "pie" and ((values < 0).any() or values.sum() <= 0):
        return None

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    charts_dir = os.path.join(base_dir, "charts")
    os.makedirs(charts_dir, exist_ok=True)
    file_path = os.path.join(charts_dir, f"summary_{uuid.uuid4().hex}.png")
    with PLOT_LOCK:
        figure, axis = plt.subplots(figsize=(10, 5))
        if chart_type == "bar":
            axis.barh(labels, values, color="#7668d8")
            axis.set_xlabel(value_column.replace("_", " ").title())
            axis.set_ylabel(category_column)
        elif chart_type == "column":
            axis.bar(labels, values, color="#7668d8")
            axis.set_xlabel(category_column)
            axis.set_ylabel(value_column.replace("_", " ").title())
            axis.tick_params(axis="x", labelrotation=45)
        elif chart_type == "line":
            axis.plot(labels, values, color="#7668d8", marker="o")
            axis.set_xlabel(category_column)
            axis.set_ylabel(value_column.replace("_", " ").title())
            axis.tick_params(axis="x", labelrotation=45)
        else:
            axis.pie(values, labels=labels, autopct="%1.1f%%")
            axis.set_aspect("equal")
        axis.set_title(title or f"{value_column.replace('_', ' ').title()} by {category_column}")
        figure.tight_layout()
        figure.savefig(file_path)
        plt.close(figure)
    logging.info("Aggregate plot saved at: %s", file_path)
    return file_path
