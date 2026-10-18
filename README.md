# StatBot Pro

StatBot Pro lets you upload a CSV file, ask questions about its contents, and view answers or generated charts in your browser.

## Run locally on Windows

From the project folder, open PowerShell and run:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The overview is at `/`, the chat workspace is at `/chat/`, and the dedicated uploader is at `/upload/`. Upload a `.csv` file and select it in chat.

The assistant can answer common questions about totals, averages, minimums and maximums, rankings, category counts, missing values, exact duplicates, filters, and charts. Use CSV column names where possible, for example:

- `Summarize this CSV`
- `Show the top 3 states by sales`
- `Average sales by state`
- `Show rows where sales are over 200000 and state is Kerala`
- `Create a chart of total sales by state`
- `Create a pie chart of sales by state`
- `Create a bar chart of sales by state`
- `Create a column chart of sales by state`
- `Create a line chart of sales by month`
- `Show duplicate rows`

Chart requests support pie, horizontal bar, vertical column, and line charts. Grouped data defaults to a horizontal bar chart; a chart of one numeric column defaults to a line chart. Pie charts require non-negative values with a positive total.

After an analysis, use **Download report (CSV)** to export the answer and **Download chart (PNG)** to save its chart for sharing. Chat questions and answers are saved locally in `chat_history.json`; entries older than seven days are automatically removed when history is opened or saved. Uploaded CSV files are not removed by this retention policy.

Requests to remove duplicate rows or values, drop a column, or delete an uploaded dataset display a confirmation prompt before proceeding. Confirmed cleanup operations create a new `_cleaned.csv` copy and keep the original. Deleting an uploaded dataset removes that file only after confirmation. Uploads are limited to 25 MB; uploading a file with an existing name saves a numbered copy rather than overwriting it. Uploaded files are stored in `uploads`, generated charts in `charts`, and recent chat history in `chat_history.json`.

If PowerShell prevents virtual-environment activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal, then activate the environment again.
