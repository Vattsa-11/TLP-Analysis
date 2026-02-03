# MVS Analysis (TLP Result Finder)

A FastAPI-based web application that analyzes SRM TLP result PDFs and generates structured Excel reports with charts. It supports teacher-wise analysis, Faculty Advisor aggregation, overall multi-subject reporting, and low-attendance extraction.

## Features

- **Self Analysis**: Teacher-wise extraction from one or more TLP PDFs.
- **Faculty Advisor (FA) Analysis**: Consolidated report grouped by test components (FT1, FT2, etc.).
- **Overall Analysis**: Single Excel workbook with one sheet per subject.
- **Low Attendance Analysis**: Finds students below 75% attendance (OCR-based) and exports Excel.
- **PDF + OCR Processing**: Native text extraction with OCR fallback for scanned PDFs.
- **Excel Reports with Charts**: Auto-formatted tables and summary charts.
- **Modern Web UI**: Tab-based workflow with light/dark mode.

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: HTML, CSS, JavaScript
- **PDF Processing**: pdfplumber
- **OCR**: OCR.Space API (optional for TLP PDFs; required for attendance PDFs)
- **Excel Generation**: XlsxWriter
- **Data Processing**: Pandas

## Project Structure

- [main.py](main.py) — FastAPI app and API endpoints
- [extractor.py](extractor.py) — TLP PDF parsing and OCR fallback
- [attendance_extractor.py](attendance_extractor.py) — Low-attendance OCR extraction
- [static/index.html](static/index.html) — Web UI

## Getting Started

1. Clone the repository:

```bash
git clone https://github.com/Vattsa-11/MVS-Analysis-.git
cd MVS-Analysis-
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. (Optional) Create a `.env` file for OCR fallback in TLP extraction:

```
OCR_API_KEY=your_api_key_here
```

4. Run the application:

```bash
uvicorn main:app --reload
```

5. Open the UI in your browser:

```
http://localhost:8000
```

## Usage

- **Self**: Enter a teacher name, upload one or more TLP PDFs, and download the Excel report.
- **FA**: Enter FA name, add multiple faculty + subject codes, upload PDFs, and download a grouped report by test component.
- **Overall**: Upload all subject PDFs to generate a multi-sheet Excel workbook.
- **Low Attendance**: Upload attendance PDF to get a list of students with < 75% attendance.

## API Endpoints

- `POST /analyze` — Teacher-wise TLP analysis
- `POST /analyze_fa` — Faculty Advisor consolidated analysis
- `POST /analyze_overall` — Overall multi-subject analysis
- `POST /analyze_attendance` — Low attendance extraction

## Notes

- OCR is used automatically when PDFs are scanned or text extraction is weak.
- Attendance extraction relies on OCR and may require clean scans for best results.

## License

MIT License
