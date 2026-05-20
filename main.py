
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import shutil
import os
import sys
from dotenv import load_dotenv
import pandas as pd
import requests
import io
import xlsxwriter
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Resolve base directory - works with both regular Python and PyInstaller bundled exe
# PyInstaller sets sys._MEIPASS when running as --onefile exe
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    BASE_DIR = sys._MEIPASS
else:
    # Running as regular Python script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(BASE_DIR, "static")
ENV_FILE = os.path.join(BASE_DIR, ".env")

# Fallback: if static dir doesn't exist in _MEIPASS, try the script's own directory
if not os.path.isdir(STATIC_DIR):
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _fallback_static = os.path.join(_script_dir, "static")
    if os.path.isdir(_fallback_static):
        STATIC_DIR = _fallback_static
        logger.info(f"Using fallback STATIC_DIR: {STATIC_DIR}")

if not os.path.exists(ENV_FILE):
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _fallback_env = os.path.join(_script_dir, ".env")
    if os.path.exists(_fallback_env):
        ENV_FILE = _fallback_env
        logger.info(f"Using fallback ENV_FILE: {ENV_FILE}")

load_dotenv(ENV_FILE)

app = FastAPI()

# Configure max upload size (50 MB for development, 25 MB for Vercel limit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add middleware to handle large file uploads
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class MaxUploadSizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_upload_size: int):
        super().__init__(app)
        self.max_upload_size = max_upload_size

    async def dispatch(self, request: Request, call_next):
        if request.method == 'POST':
            if 'content-length' in request.headers:
                content_length = int(request.headers['content-length'])
                if content_length > self.max_upload_size:
                    return HTTPException(status_code=413, detail="File too large")
        return await call_next(request)

# 25 MB limit (safe for Vercel)
MAX_UPLOAD_SIZE = 25 * 1024 * 1024
app.add_middleware(MaxUploadSizeMiddleware, max_upload_size=MAX_UPLOAD_SIZE)

# Mount static files
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# =====================================================================
# Helper functions for deduplication and suffix generation
# =====================================================================

def to_roman(num):
    """
    Converts an integer to Roman numerals.
    Example: 1→I, 2→II, 3→III, 4→IV, etc.
    """
    val = [
        1000, 900, 500, 400,
        100, 90, 50, 40,
        10, 9, 5, 4,
        1
    ]
    syms = [
        "M", "CM", "D", "CD",
        "C", "XC", "L", "XL",
        "X", "IX", "V", "IV",
        "I"
    ]
    roman_num = ''
    i = 0
    while num > 0:
        for _ in range(num // val[i]):
            roman_num += syms[i]
            num -= val[i]
        i += 1
    return roman_num


def add_dataset_suffixes(results):
    """
    Adds suffixes to duplicate dataset names using Roman numerals.

    Algorithm:
    1. Convert dataset names to Roman numerals (FT1→FT_I, FT2→FT_II, etc.)
    2. Group results by converted dataset name
    3. For groups with multiple entries, append _1, _2, _3, etc.
    4. For single entries, use converted name without suffix

    Example:
        Input: [FT1, FT1, FT2, FT2] → Output: [FT_I_1, FT_I_2, FT_II_1, FT_II_2]
    """
    if not results:
        return results

    # Step 1: Convert dataset names and group by converted name
    dataset_groups = {}
    for idx, result in enumerate(results):
        original_dataset = result.get('dataset', 'Unknown')

        # Extract number from dataset (e.g., "FT1" → 1, "FT2" → 2, "FT_I" → 1, "Midterm" → None)
        import re
        match = re.search(r'(\d+)', original_dataset)
        if match:
            test_num = int(match.group(1))
            roman = to_roman(test_num)
            # Create converted name like "FT_I", "FT_II"
            prefix = re.sub(r'\d+', '', original_dataset)  # Remove numbers: "FT1" → "FT"
            converted_dataset = f"{prefix}_{roman}"
        else:
            # No number found, keep as-is
            converted_dataset = original_dataset

        if converted_dataset not in dataset_groups:
            dataset_groups[converted_dataset] = []
        dataset_groups[converted_dataset].append(idx)

    # Step 2: Add suffixes for duplicates
    for converted_name, indices in dataset_groups.items():
        if len(indices) > 1:
            # Multiple entries with same converted name, add suffixes
            for suffix_num, original_idx in enumerate(indices, 1):
                results[original_idx]['dataset'] = f"{converted_name}_{suffix_num}"
        else:
            # Single entry, use converted name without suffix
            original_idx = indices[0]
            results[original_idx]['dataset'] = converted_name

    return results


@app.get("/", response_class=HTMLResponse)
async def read_root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        # Fallback: try looking relative to the script's actual directory (not _MEIPASS)
        fallback_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        fallback_path = os.path.join(fallback_dir, "index.html")
        if os.path.exists(fallback_path):
            index_path = fallback_path
        else:
            logger.error(f"index.html not found at {index_path} or {fallback_path}")
            raise HTTPException(status_code=500, detail=f"index.html not found. STATIC_DIR={STATIC_DIR}")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return HTMLResponse("", status_code=204)

@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def devtools():
    return HTMLResponse("", status_code=204)


@app.post("/analyze")
async def analyze(files: list[UploadFile] = File(...), teacher_name: str = Form(...)):
    logger.info(f"Received analyze request for teacher: {teacher_name}, files: {len(files)}")
    try:
        # 1. Process all files
        results = []
        
        # Sort files by filename explicitly as requested ("Sort according to PDF")
        # This ensures "FT1" comes before "FT2" usually.
        files.sort(key=lambda f: f.filename)
        
        from extractor import extract_pdf_data, extract_overall_data
        ocp_api_key = os.getenv("OCR_API_KEY")

        for file in files:
            if not file.filename.lower().endswith('.pdf'):
                continue
                
            contents = await file.read()
            try:
                extracted_list = extract_pdf_data(contents, teacher_name, ocp_api_key)
                if extracted_list:
                    # extracted_list is now a list of dicts
                    # Enrich each with filename
                    for item in extracted_list:
                        item['filename'] = file.filename
                        results.append(item)
                else:
                    # Log missed file
                    print(f"Skipping {file.filename}: Teacher not found.")
            except Exception as e:
                print(f"Error processing {file.filename}: {e}")

        if not results:
            raise HTTPException(status_code=404, detail=f"No data found for {teacher_name} in any of the uploaded files.")

        # Apply suffix generation for duplicate dataset names
        results = add_dataset_suffixes(results)

        # 2. Excel Generation (Consolidated)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet("Analysis")
        
        # Formats
        title_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 14})
        subtitle_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 12})
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'bg_color': '#FFEFD5', 'text_wrap': True}) # LightOrange/PapayaWhip often used? Or just clean gray. Let's use clean standard.
        header_format.set_bg_color('#FFFFFF') # White clean
        
        # Specific headers from screenshot style (often have rotated text or specific layouts, but standard table is safe)
        table_header_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
        data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        percent_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': '0.00'})

        # Metadata from first file for the Header
        first_meta = results[0]
        subject_text = f"Subject Code & Name: {first_meta.get('subject_code', '')} - {first_meta.get('course', '')}"

        # Global Header
        worksheet.merge_range('A1:M1', 'SRM Institute of Science and Technology, Kattankulathur', title_format)
        worksheet.merge_range('A2:M2', 'School of Computing', subtitle_format)
        worksheet.merge_range('A3:M3', 'Department of Computing Technologies', subtitle_format)
        worksheet.merge_range('A4:M4', '(ACADEMIC YEAR AY 2024-25)-Odd', subtitle_format)
        worksheet.merge_range('A5:M5', f'Course : {first_meta.get("course", "B.Tech")}   Year : II   Sem: III', subtitle_format)
        worksheet.merge_range('A6:M6', subject_text, title_format)
        
        # Faculty Name Header
        faculty_name = first_meta['data'].get('faculty_name', '')
        worksheet.merge_range('A7:M7', f"Faculty Name: {faculty_name}", title_format)

        # ---------------------------------------------------------
        # MAIN TABLE (Screenshot 1)
        # ---------------------------------------------------------
        # Cols: S.No, Test Component, Total Students, 0-49, 50-59, 60-69, 70-79, 80-89, 90-100, Absentees, Pass, Fail, Pass%
        
        headers = [
            "S.No", "Test Component", "Total Students", 
            "0-49", "50-59", "60-69", 
            "70-79", "80-89", "90-100", 
            "Absents", "Pass", "Fail", "Pass %"
        ]
        
        start_row = 7
        worksheet.write_row(start_row, 0, headers, table_header_format)
        worksheet.set_row(start_row, 40) # Taller header
        
        current_row = start_row + 1
        
        for idx, res in enumerate(results):
            m = res['data']['metrics'] # [Strength, Abs, Fail, Pass%, Ranges(6)...]
            # Ranges: 0-49(m[4]), 50-59(m[5]), 60-69(m[6]), 70-79(m[7]), 80-89(m[8]), 90-100(m[9])
            
            strength = int(float(m[0]))
            absent = int(float(m[1]))
            fail = int(float(m[2]))
            pass_pct = float(m[3])
            ranges = [int(float(x)) for x in m[4:]]
            passed = int(strength - absent - fail)
            
            # Row Data
            row_data = [
                idx + 1,                # S.No
                res['dataset'],         # Test Component (FT1...)
                strength,               # Total Students
                ranges[0], ranges[1], ranges[2], ranges[3], ranges[4], ranges[5], # Ranges
                absent,                 # Absentees
                passed,                 # Pass
                fail,                   # Fail
                pass_pct                # Pass %
            ]
            
            worksheet.write_row(current_row, 0, row_data, data_format)
            current_row += 1
            
        # ---------------------------------------------------------
        # SMALL TABLE (Summary for verify/charts)
        # ---------------------------------------------------------
        # Just below Main Table, a Transposed view of 'Test Component' and 'Pass %'
        
        summary_row = current_row + 2
        worksheet.write(summary_row, 0, "Test Component", table_header_format)
        worksheet.write(summary_row + 1, 0, "Pass %", table_header_format)
        
        for idx, res in enumerate(results):
             worksheet.write(summary_row, idx + 1, res['dataset'], data_format)
             worksheet.write(summary_row + 1, idx + 1, res['data']['metrics'][3], percent_format)
             
        # ---------------------------------------------------------
        # CHART 1: Overall Result Analysis (Grouped)
        # ---------------------------------------------------------
        # Y-Axis: Counts
        # X-Axis Categories: Metrics (Students, Ranges..., Abs, Pass, Fail, Pass%)
        # Series: Test Components
        
        chart1_row = summary_row + 4
        chart1 = workbook.add_chart({'type': 'column'})
        
        # We need to construct the series referencing the MAIN TABLE columns
        # Columns:
        # B: Test Component (System Name)
        # C: Students
        # D-I: Ranges
        # J: Abs
        # K: Pass
        # L: Fail
        # M: Pass % (Maybe exclude percentage from count chart? Screenshot 2 usually includes it heavily scaled or separate axis? 
        # Wait, if Pass % is 90, and Students is 100, it's visible. If Students is 60, nice.
        # But usually "Overall Result Analysis" focuses on counts.
        # Let's include everything as per typical user request for "Detailed Chart".
        # Categories: Headers C to M ?
        
        # Issue: Categories are headers. Series are Rows.
        # X-Axis = [Students, 0-49, ..., Pass, Fail, Pass%] (Headers C7:M7)
        # Series 1 (FT1) = Data C8:M8
        
        category_range = ['Analysis', start_row, 2, start_row, 12] # C to M
        
        for r_idx in range(len(results)):
            # Row index in data table
            d_row = start_row + 1 + r_idx
            series_name = ['Analysis', d_row, 1] # Column B
            values = ['Analysis', d_row, 2, d_row, 12] # C to M
            
            chart1.add_series({
                'name': series_name,
                'categories': category_range,
                'values': values,
            })
            
        subject_name = first_meta.get('course', '').split('-')[-1].strip()
        chart1.set_title({'name': f'Overall Result Analysis - {subject_name}'})
        chart1.set_style(10)
        chart1.set_size({'width': 800, 'height': 450})
        
        worksheet.insert_chart(chart1_row, 1, chart1)
        
        # ---------------------------------------------------------
        # CHART 2: Overall Pass Percentage (Simple Column)
        # ---------------------------------------------------------
        # X-Axis: Test Component (FT1, FT2...)
        # Y-Axis: Pass %
        # Data source: The Small Summary Table we made (Rows summary_row & summary_row+1)
        
        chart2_row = chart1_row + 25 # Below first chart
        chart2 = workbook.add_chart({'type': 'column'})
        
        # Series: Just one, "Pass %" values
        # Categories: FT1, FT2... (Row summary_row, Cols 1 to N)
        # Values: Pass % (Row summary_row+1, Cols 1 to N)
        
        num_tests = len(results)
        chart2.add_series({
            'name': 'Pass %',
            'categories': ['Analysis', summary_row, 1, summary_row, num_tests],
            'values':     ['Analysis', summary_row+1, 1, summary_row+1, num_tests],
            'data_labels': {'value': True},
            'fill': {'color': '#4285F4'}
        })
        
        chart2.set_title({'name': f'Overall Pass Percentage - {subject_name}'})
        chart2.set_y_axis({'min': 0, 'max': 100})
        chart2.set_size({'width': 600, 'height': 400})
        
        worksheet.insert_chart(chart2_row, 1, chart2)

        # Formatting width
        worksheet.set_column('A:A', 5)
        worksheet.set_column('B:B', 15)
        worksheet.set_column('C:M', 12)
        
        workbook.close()
        output.seek(0)
        
        headers = {
            'Content-Disposition': f'attachment; filename="{teacher_name}_TLP_Analysis.xlsx"'
        }
        return StreamingResponse(
            output, 
            headers=headers,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except Exception as e:
        logger.error(f"Error in analyze endpoint: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/analyze_overall")
async def analyze_overall(files: list[UploadFile] = File(...)):
    try:
        # 1. Process all files
        results = []
        files.sort(key=lambda f: f.filename)
        
        from extractor import extract_overall_data
        from collections import defaultdict
        
        import logging
        logging.basicConfig(filename='server_debug.log', level=logging.INFO, force=True)
        
        ocp_api_key = os.getenv("OCR_API_KEY")
        logging.info(f"Analyze Overall Request received. Files: {len(files)}")
        logging.info(f"OCR API Key Loaded: {bool(ocp_api_key)}")
        if ocp_api_key:
            logging.info(f"API Key start: {ocp_api_key[:4]}...")
        
        for file in files:
            if not file.filename.lower().endswith('.pdf'):
                continue
                
            contents = await file.read()
            try:
                # Extract aggregated data for this file
                logging.info(f"Processing {file.filename}, size {len(contents)}")
                extracted_data = extract_overall_data(contents, ocp_api_key)
                
                if extracted_data:
                    logging.info(f"SUCCESS extraction for {file.filename}")
                    extracted_data['filename'] = file.filename
                    results.append(extracted_data)
                else:
                    logging.warning(f"FAILED extraction for {file.filename}: No data found.")
            except Exception as e:
                logging.error(f"Error processing {file.filename}: {e}")
                import traceback
                logging.error(traceback.format_exc())

        if not results:
            raise HTTPException(status_code=404, detail="No valid data found in uploaded files.")

        # 2. Group results by subject_code, then by test component (dataset)
        subject_component_groups = defaultdict(lambda: defaultdict(list))
        for res in results:
            subject_code = res.get('subject_code', 'Unknown')
            dataset = res.get('dataset', 'Unknown')  # FT1, FT2, etc.
            subject_component_groups[subject_code][dataset].append(res)
        
        # 3. Generate single Excel workbook with multiple sheets (one per test component)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        
        # Formats
        title_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 14})
        subtitle_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 12})
        table_header_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
        data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        percent_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': '0.00'})
        totals_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'bg_color': '#D3D3D3'})
    
        # Process each subject
        used_sheet_names = set()
        for subject_code, component_groups in subject_component_groups.items():
            # Get metadata from THIS subject's first result (not global first)
            first_meta = list(component_groups.values())[0][0]
            course_name = first_meta.get('course', 'Unknown')
            course_code = first_meta.get('subject_code', subject_code)
            subject_text = f"Subject Code & Name: {course_code} - {course_name}"
            
            # Normalize component groups: clean names but keep individual results separate
            # Count occurrences of each cleaned component name
            clean_component_counts = defaultdict(int)
            component_entries = []
            for component_name in sorted(component_groups.keys()):
                for res in component_groups[component_name]:
                    clean_component = component_name.replace('-', '').replace(' ', '').strip()
                    clean_component_counts[clean_component] += 1
                    component_entries.append((clean_component, clean_component_counts[clean_component], res))
            
            # Check which components have duplicates
            has_duplicates = {k for k, v in clean_component_counts.items() if v > 1}
            
            # Create a sheet for each individual result
            for clean_component, idx, result_data in component_entries:
                component_results = [result_data]
                
                # Sheet name format: "course_code-component" or "course_code-component_N" if duplicates
                if clean_component in has_duplicates:
                    raw_sheet = f"{course_code}-{clean_component}_{idx}"
                else:
                    raw_sheet = f"{course_code}-{clean_component}"
                sheet_name = raw_sheet.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('[', '_').replace(']', '_')[:31]
                
                # Ensure uniqueness (safety net)
                base_name = sheet_name
                counter = 1
                while sheet_name.upper() in used_sheet_names:
                    suffix = f"_{counter}"
                    sheet_name = base_name[:31 - len(suffix)] + suffix
                    counter += 1
                used_sheet_names.add(sheet_name.upper())
                
                worksheet = workbook.add_worksheet(sheet_name)
                
                # Global Header
                worksheet.merge_range('A1:M1', 'SRM Institute of Science and Technology, Kattankulathur', title_format)
                worksheet.merge_range('A2:M2', 'School of Computing', subtitle_format)
                worksheet.merge_range('A3:M3', 'Department of Computing Technologies', subtitle_format)
                worksheet.merge_range('A4:M4', '(ACADEMIC YEAR AY 2024-25)-Odd', subtitle_format)
                worksheet.merge_range('A5:M5', f'Course : {course_name}   Year : II   Sem: III', subtitle_format)
                worksheet.merge_range('A6:M6', subject_text, title_format)
                
                # Headers
                headers = [
                    "S.No", "Test Component", "Total No. of\nStudents", 
                    "Range of\nmarks 0-49", "Range of\nmarks 50-59", "Range of\nmarks 60-69", 
                    "Range of\nmarks 70-79", "Range of\nmarks 80-89", "Range of\nmarks 90-100", 
                    "No. of\nAbsentees", "No. of Pass", "No. of Failure", "Pass %"
                ]
                
                start_row = 7
                worksheet.write_row(start_row, 0, headers, table_header_format)
                worksheet.set_row(start_row, 40)
                
                current_row = start_row + 1
                
                # Calculate totals for this component
                total_strength = 0
                total_absent = 0
                total_fail = 0
                total_ranges = [0, 0, 0, 0, 0, 0]
                
                for res in component_results:
                    m = res['data']['metrics']
                    strength = int(float(m[0]))
                    absent = int(float(m[1]))
                    fail = int(float(m[2]))
                    ranges = [int(float(x)) for x in m[4:]]
                    
                    # Add to totals
                    total_strength += strength
                    total_absent += absent
                    total_fail += fail
                    for i in range(len(ranges)):
                        total_ranges[i] += ranges[i]
                
                # Calculate totals
                total_appeared = total_strength - total_absent
                total_passed = total_appeared - total_fail
                total_pass_pct = 0.0
                if total_appeared > 0:
                    total_pass_pct = (total_passed / total_appeared) * 100
                
                # Write totals row (only one row per component)
                totals_row_data = [
                    1,
                    component_name,
                    total_strength,
                    total_ranges[0], total_ranges[1], total_ranges[2], total_ranges[3], total_ranges[4], total_ranges[5],
                    total_absent,
                    total_passed,
                    total_fail,
                    total_pass_pct
                ]
                
                worksheet.write_row(current_row, 0, totals_row_data, totals_format)
                current_row += 2
                
                # Add summary statistics below
                worksheet.write('A' + str(current_row), 'Summary Statistics:', workbook.add_format({'bold': True, 'font_size': 11}))
                current_row += 1
                
                summary_stats = [
                    ['Total Students', total_strength],
                    ['Students Appeared', total_appeared],
                    ['Absent', total_absent],
                    ['Failures', total_fail],
                    ['Passed', total_passed],
                    ['Pass Percentage', f'{total_pass_pct:.2f}%'],
                ]
                
                for stat_name, stat_value in summary_stats:
                    worksheet.write('A' + str(current_row), stat_name, workbook.add_format({'bold': True}))
                    worksheet.write('B' + str(current_row), stat_value, data_format)
                    current_row += 1
                
                # ---------------------------------------------------------
                # CHART 1: Marks Distribution (Bar Chart)
                # ---------------------------------------------------------
                # Data row is at start_row + 1 (the totals row)
                data_row = start_row + 1
                
                # Write range labels and values in a helper area for chart data
                chart_data_row = current_row + 2
                range_labels = ['0-49', '50-59', '60-69', '70-79', '80-89', '90-100']
                range_values = total_ranges
                
                worksheet.write(chart_data_row, 0, 'Marks Range', workbook.add_format({'bold': True}))
                worksheet.write(chart_data_row, 1, 'No. of Students', workbook.add_format({'bold': True}))
                for i, (label, val) in enumerate(zip(range_labels, range_values)):
                    worksheet.write(chart_data_row + 1 + i, 0, label, data_format)
                    worksheet.write(chart_data_row + 1 + i, 1, val, data_format)
                
                chart1 = workbook.add_chart({'type': 'column'})
                chart1.add_series({
                    'name': 'Students',
                    'categories': [sheet_name, chart_data_row + 1, 0, chart_data_row + 6, 0],
                    'values':     [sheet_name, chart_data_row + 1, 1, chart_data_row + 6, 1],
                    'fill': {'color': '#4285F4'},
                    'data_labels': {'value': True},
                })
                chart1.set_title({'name': f'Marks Distribution - {course_code} - {clean_component}'})
                chart1.set_x_axis({'name': 'Marks Range'})
                chart1.set_y_axis({'name': 'No. of Students'})
                chart1.set_style(10)
                chart1.set_size({'width': 600, 'height': 400})
                chart1.set_legend({'none': True})
                
                chart1_row = chart_data_row + 1
                worksheet.insert_chart(f'E{chart1_row}', chart1)
                
                # ---------------------------------------------------------
                # CHART 2: Pass vs Fail (Pie Chart)
                # ---------------------------------------------------------
                pie_data_row = chart1_row + 22  # 22 rows below chart1 to avoid overlap
                worksheet.write(pie_data_row, 0, 'Result', workbook.add_format({'bold': True}))
                worksheet.write(pie_data_row, 1, 'Count', workbook.add_format({'bold': True}))
                worksheet.write(pie_data_row + 1, 0, 'Pass', data_format)
                worksheet.write(pie_data_row + 1, 1, total_passed, data_format)
                worksheet.write(pie_data_row + 2, 0, 'Fail', data_format)
                worksheet.write(pie_data_row + 2, 1, total_fail, data_format)
                worksheet.write(pie_data_row + 3, 0, 'Absent', data_format)
                worksheet.write(pie_data_row + 3, 1, total_absent, data_format)
                
                chart2 = workbook.add_chart({'type': 'pie'})
                chart2.add_series({
                    'name': 'Pass vs Fail',
                    'categories': [sheet_name, pie_data_row + 1, 0, pie_data_row + 3, 0],
                    'values':     [sheet_name, pie_data_row + 1, 1, pie_data_row + 3, 1],
                    'data_labels': {'percentage': True, 'category': True},
                    'points': [
                        {'fill': {'color': '#34A853'}},
                        {'fill': {'color': '#EA4335'}},
                        {'fill': {'color': '#FBBC05'}},
                    ],
                })
                chart2.set_title({'name': f'Pass/Fail/Absent - {course_code} - {clean_component}'})
                chart2.set_size({'width': 500, 'height': 400})
                
                pie_chart_row = pie_data_row + 5
                worksheet.insert_chart(f'E{pie_chart_row}', chart2)
                
                worksheet.set_column('A:A', 25)
                worksheet.set_column('B:M', 12)
        
        workbook.close()
        output.seek(0)
        
        headers = {
            'Content-Disposition': 'attachment; filename="Overall_Analysis_All_Subjects.xlsx"'
        }
        
        return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze_fa")
async def analyze_fa(files: list[UploadFile] = File(...), fa_name: str = Form(...), faculty_data: str = Form(...)):
    """
    Faculty Advisor Analysis Endpoint
    Searches for specific faculty names and subject codes in uploaded PDFs
    and generates a consolidated Excel report grouped by test type.
    """
    import json
    from extractor import extract_pdf_data
    from collections import defaultdict
    
    ocp_api_key = os.getenv("OCR_API_KEY")
    
    # Parse faculty data
    try:
        faculty_list = json.loads(faculty_data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid faculty data format")
    
    if not faculty_list:
        raise HTTPException(status_code=400, detail="No faculty members provided")
    
    # Sort files
    files.sort(key=lambda f: f.filename)
    
    # Store results grouped by test type, then by faculty-subject
    # Structure: {test_type: [{faculty_name, subject_code, course, data, dataset}, ...]}
    test_type_results = defaultdict(list)
    
    # Process each file and search for matching faculty
    for file in files:
        if not file.filename.lower().endswith('.pdf'):
            continue
        
        contents = await file.read()
        
        # Try to extract data for each faculty member
        for faculty_info in faculty_list:
            faculty_name = faculty_info.get('name', '').strip()
            subject_code = faculty_info.get('subject_code', '').strip()
            
            if not faculty_name or not subject_code:
                continue
            
            try:
                # Extract data for this teacher
                extracted_list = extract_pdf_data(contents, faculty_name, ocp_api_key)
                
                if extracted_list:
                    # Filter by subject code (case-insensitive match)
                    for item in extracted_list:
                        item_subject = item.get('subject_code', '').strip()
                        if item_subject.lower() == subject_code.lower():
                            item['filename'] = file.filename
                            item['faculty_name'] = faculty_name
                            item['subject_code'] = subject_code
                            
                            # Group by test type (dataset like FT1, FT2, FJ1, etc.)
                            test_type = item.get('dataset', 'Unknown')
                            test_type_results[test_type].append(item)
            except Exception as e:
                print(f"Error processing {file.filename} for {faculty_name}: {e}")
                continue
    
    if not test_type_results:
        raise HTTPException(status_code=404, detail=f"No data found for the specified faculty members in any uploaded files.")
    
    # Generate consolidated Excel workbook with sheets per test type
    try:
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        
        # Formats
        title_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 14})
        subtitle_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 12})
        table_header_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True, 'bg_color': '#D3D3D3'})
        data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        percent_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': '0.00'})
        
        # Create a sheet for each test type
        for test_type in sorted(test_type_results.keys()):
            results = test_type_results[test_type]
            
            # Clean sheet name (Excel has 31 char limit and no special chars)
            sheet_name = test_type[:31].replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('[', '_').replace(']', '_')
            worksheet = workbook.add_worksheet(sheet_name)
            
            # Header
            worksheet.merge_range('A1:Q1', 'SRM Institute of Science and Technology, Kattankulathur', title_format)
            worksheet.merge_range('A2:Q2', 'Department of Computing Technologies', subtitle_format)
            worksheet.merge_range('A3:Q3', '(ACADEMIC YEAR AY 2025-26) ODD', subtitle_format)
            worksheet.merge_range('A4:Q4', f'Name of the Faculty Advisor: {fa_name}', subtitle_format)
            worksheet.merge_range('A5:Q5', f'Test Component: {test_type}', title_format)
            
            # Main table headers
            headers = [
                "S.No", "Course code", "Course Name", "Course Handling Faculty\nName",
                "Course Handling\nFaculty\nMobile Number", "Course Handling Faculty\nMail Id",
                "Total No.\nof\nStudents", "Range of\nmarks 0-49", "Range of\nmarks 50-59",
                "Range of\nmarks 60-69", "Range of\nmarks 70-79", "Range of\nmarks 80-89",
                "Range of\nmarks 90-100", "No. of\nAbsentees", "No. of Pass", "No. of Failure", "Pass %"
            ]
            
            start_row = 6
            worksheet.write_row(start_row, 0, headers, table_header_format)
            worksheet.set_row(start_row, 50)
            
            current_row = start_row + 1
            
            # Track data for summary
            subject_pass_fail = {}
            
            # Write data rows - one row per subject
            for idx, result in enumerate(results):
                m = result['data']['metrics']
                strength = int(float(m[0]))
                absent = int(float(m[1]))
                fail = int(float(m[2]))
                pass_pct = float(m[3])
                ranges = [int(float(x)) for x in m[4:]]
                passed = int(strength - absent - fail)
                
                # Get faculty info
                faculty_name = result.get('faculty_name', 'Unknown')
                subject_code = result.get('subject_code', 'Unknown')
                course = result.get('course', 'Unknown')
                
                # Extract course name (after dash if present)
                course_name = course.split('-')[-1].strip() if '-' in course else course
                
                row_data = [
                    idx + 1,
                    subject_code,
                    course_name,
                    faculty_name,
                    "",  # Mobile number (not in data)
                    "",  # Email (not in data)
                    strength,
                    ranges[0], ranges[1], ranges[2], ranges[3], ranges[4], ranges[5],
                    absent,
                    passed,
                    fail,
                    pass_pct
                ]
                
                worksheet.write_row(current_row, 0, row_data, data_format)
                worksheet.write(current_row, 16, pass_pct, percent_format)
                current_row += 1
                
                # Store for summary
                subject_pass_fail[subject_code] = {'pass': passed, 'fail': fail}
            
            # Add spacing
            current_row += 2
            
            # Summary table
            summary_start_row = current_row
            summary_headers = ["Sub Code", "PASS", "FAIL"]
            worksheet.write_row(summary_start_row, 0, summary_headers, table_header_format)
            
            summary_row = summary_start_row + 1
            for subject_code, pf_data in sorted(subject_pass_fail.items()):
                worksheet.write(summary_row, 0, subject_code, data_format)
                worksheet.write(summary_row, 1, pf_data['pass'], data_format)
                worksheet.write(summary_row, 2, pf_data['fail'], data_format)
                summary_row += 1
            
            # Add chart
            chart_row = summary_start_row
            chart = workbook.add_chart({'type': 'column'})
            
            # Add series for Pass and Fail
            num_subjects = len(subject_pass_fail)
            chart.add_series({
                'name': 'PASS',
                'categories': [sheet_name, summary_start_row + 1, 0, summary_start_row + num_subjects, 0],
                'values': [sheet_name, summary_start_row + 1, 1, summary_start_row + num_subjects, 1],
                'fill': {'color': '#4472C4'},
                'data_labels': {'value': True}
            })
            
            chart.add_series({
                'name': 'FAIL',
                'categories': [sheet_name, summary_start_row + 1, 0, summary_start_row + num_subjects, 0],
                'values': [sheet_name, summary_start_row + 1, 2, summary_start_row + num_subjects, 2],
                'fill': {'color': '#ED7D31'},
                'data_labels': {'value': True}
            })
            
            chart.set_title({'name': test_type})
            chart.set_x_axis({'name': 'Subject Code'})
            chart.set_y_axis({'name': 'Number of Students'})
            chart.set_size({'width': 720, 'height': 400})
            chart.set_legend({'position': 'right'})
            
            worksheet.insert_chart(chart_row, 5, chart)
            
            # Column widths
            worksheet.set_column('A:A', 6)
            worksheet.set_column('B:B', 12)
            worksheet.set_column('C:C', 35)
            worksheet.set_column('D:D', 25)
            worksheet.set_column('E:E', 15)
            worksheet.set_column('F:F', 25)
            worksheet.set_column('G:Q', 12)
        
        workbook.close()
        output.seek(0)
        
        headers = {
            'Content-Disposition': f'attachment; filename="FA_{fa_name}_Report.xlsx"'
        }
        
        return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze_single_course")
async def analyze_single_course(files: list[UploadFile] = File(...)):
    """
    Single Course Analysis Endpoint
    Extracts and analyzes data for a single course from uploaded PDFs
    """
    try:
        results = []
        files.sort(key=lambda f: f.filename)

        from extractor import extract_overall_data
        from collections import defaultdict

        ocp_api_key = os.getenv("OCR_API_KEY")
        logger.info(f"Single Course Analysis Request received. Files: {len(files)}")

        for file in files:
            if not file.filename.lower().endswith('.pdf'):
                continue

            contents = await file.read()
            try:
                extracted_data = extract_overall_data(contents, ocp_api_key)

                if extracted_data:
                    extracted_data['filename'] = file.filename
                    results.append(extracted_data)
                else:
                    logger.warning(f"No data extracted from {file.filename}")
            except Exception as e:
                logger.error(f"Error processing {file.filename}: {e}")
                import traceback
                logger.error(traceback.format_exc())

        if not results:
            raise HTTPException(status_code=404, detail="No valid data found in uploaded files.")

        # Group by course
        course_groups = defaultdict(list)
        for res in results:
            course_code = res.get('subject_code', 'Unknown')
            course_groups[course_code].append(res)

        # Generate Excel for single course (use first course found)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        title_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 14})
        subtitle_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 12})
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
        data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        percent_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': '0.00'})

        first_course_code = list(course_groups.keys())[0]
        course_results = course_groups[first_course_code]
        first_meta = course_results[0]

        worksheet = workbook.add_worksheet("Single Course Analysis")

        # Headers
        worksheet.merge_range('A1:M1', 'SRM Institute of Science and Technology, Kattankulathur', title_format)
        worksheet.merge_range('A2:M2', 'School of Computing', subtitle_format)
        worksheet.merge_range('A3:M3', 'Department of Computing Technologies', subtitle_format)
        worksheet.merge_range('A4:M4', '(ACADEMIC YEAR AY 2024-25)-Odd', subtitle_format)

        course_name = first_meta.get('course', 'Unknown')
        worksheet.merge_range('A5:M5', f'Course Code: {first_course_code} - {course_name}', subtitle_format)

        # Table headers
        headers = [
            "S.No", "Test Component", "Total No. of\nStudents",
            "Range of\nmarks 0-49", "Range of\nmarks 50-59", "Range of\nmarks 60-69",
            "Range of\nmarks 70-79", "Range of\nmarks 80-89", "Range of\nmarks 90-100",
            "No. of\nAbsentees", "No. of Pass", "No. of Failure", "Pass %"
        ]

        start_row = 6
        worksheet.write_row(start_row, 0, headers, header_format)
        worksheet.set_row(start_row, 40)

        current_row = start_row + 1

        for idx, res in enumerate(course_results):
            m = res['data']['metrics']
            strength = int(float(m[0]))
            absent = int(float(m[1]))
            fail = int(float(m[2]))
            pass_pct = float(m[3])
            ranges = [int(float(x)) for x in m[4:]]
            passed = strength - absent - fail

            row_data = [
                idx + 1,
                res.get('dataset', 'Unknown'),
                strength,
                ranges[0], ranges[1], ranges[2], ranges[3], ranges[4], ranges[5],
                absent,
                passed,
                fail,
                pass_pct
            ]

            worksheet.write_row(current_row, 0, row_data, data_format)
            current_row += 1

        worksheet.set_column('A:A', 5)
        worksheet.set_column('B:B', 15)
        worksheet.set_column('C:M', 12)

        workbook.close()
        output.seek(0)

        headers = {
            'Content-Disposition': 'attachment; filename="Single_Course_Analysis.xlsx"'
        }

        return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in single course analysis: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze_multi_course")
async def analyze_multi_course(files: list[UploadFile] = File(...)):
    """
    Multi Course Analysis Endpoint
    Extracts and aggregates data across multiple courses from uploaded PDFs
    """
    try:
        results = []
        files.sort(key=lambda f: f.filename)

        from extractor import extract_overall_data
        from collections import defaultdict

        ocp_api_key = os.getenv("OCR_API_KEY")
        logger.info(f"Multi Course Analysis Request received. Files: {len(files)}")

        for file in files:
            if not file.filename.lower().endswith('.pdf'):
                continue

            contents = await file.read()
            try:
                extracted_data = extract_overall_data(contents, ocp_api_key)

                if extracted_data:
                    extracted_data['filename'] = file.filename
                    results.append(extracted_data)
                else:
                    logger.warning(f"No data extracted from {file.filename}")
            except Exception as e:
                logger.error(f"Error processing {file.filename}: {e}")
                import traceback
                logger.error(traceback.format_exc())

        if not results:
            raise HTTPException(status_code=404, detail="No valid data found in uploaded files.")

        # Group by course and test component
        course_component_groups = defaultdict(lambda: defaultdict(list))
        for res in results:
            course_code = res.get('subject_code', 'Unknown')
            dataset = res.get('dataset', 'Unknown')
            course_component_groups[course_code][dataset].append(res)

        # Generate Excel with multiple sheets (one per course)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        title_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 14})
        subtitle_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 12})
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
        data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        percent_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'num_format': '0.00'})

        used_sheet_names = set()

        for course_code in sorted(course_component_groups.keys()):
            component_groups = course_component_groups[course_code]
            first_meta = list(component_groups.values())[0][0]
            course_name = first_meta.get('course', 'Unknown')

            # Sheet name
            sheet_name = f"{course_code[:20]}".replace('/', '_').replace('\\', '_')[:31]
            if sheet_name.upper() in used_sheet_names:
                counter = 1
                base = sheet_name[:25]
                sheet_name = f"{base}_{counter}"[:31]
            used_sheet_names.add(sheet_name.upper())

            worksheet = workbook.add_worksheet(sheet_name)

            # Headers
            worksheet.merge_range('A1:M1', 'SRM Institute of Science and Technology, Kattankulathur', title_format)
            worksheet.merge_range('A2:M2', 'School of Computing', subtitle_format)
            worksheet.merge_range('A3:M3', 'Department of Computing Technologies', subtitle_format)
            worksheet.merge_range('A4:M4', '(ACADEMIC YEAR AY 2024-25)-Odd', subtitle_format)
            worksheet.merge_range('A5:M5', f'Course Code: {course_code} - {course_name}', subtitle_format)

            # Table headers
            headers = [
                "S.No", "Test Component", "Total No. of\nStudents",
                "Range of\nmarks 0-49", "Range of\nmarks 50-59", "Range of\nmarks 60-69",
                "Range of\nmarks 70-79", "Range of\nmarks 80-89", "Range of\nmarks 90-100",
                "No. of\nAbsentees", "No. of Pass", "No. of Failure", "Pass %"
            ]

            start_row = 6
            worksheet.write_row(start_row, 0, headers, header_format)
            worksheet.set_row(start_row, 40)

            current_row = start_row + 1

            for component_idx, (component_name, component_results) in enumerate(sorted(component_groups.items())):
                for res in component_results:
                    m = res['data']['metrics']
                    strength = int(float(m[0]))
                    absent = int(float(m[1]))
                    fail = int(float(m[2]))
                    pass_pct = float(m[3])
                    ranges = [int(float(x)) for x in m[4:]]
                    passed = strength - absent - fail

                    row_data = [
                        component_idx + 1,
                        component_name,
                        strength,
                        ranges[0], ranges[1], ranges[2], ranges[3], ranges[4], ranges[5],
                        absent,
                        passed,
                        fail,
                        pass_pct
                    ]

                    worksheet.write_row(current_row, 0, row_data, data_format)
                    current_row += 1

            worksheet.set_column('A:A', 5)
            worksheet.set_column('B:B', 15)
            worksheet.set_column('C:M', 12)

        workbook.close()
        output.seek(0)

        headers = {
            'Content-Disposition': 'attachment; filename="Multi_Course_Analysis.xlsx"'
        }

        return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in multi course analysis: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
async def detect_subjects(file: UploadFile = File(...)):
    """
    Detect subject codes from an attendance PDF.
    Returns list of unique subject codes found in the PDF.
    """
    from attendance_extractor import detect_subject_codes
    contents = await file.read()
    codes = detect_subject_codes(contents)
    return {"subject_codes": codes}


@app.post("/analyze_attendance")
async def analyze_attendance(file: UploadFile = File(...), faculty_advisor: str = Form(...), section: str = Form(...), subjects_data: str = Form("")):
    """
    Low Attendance Analysis Endpoint
    Extracts students with attendance < 75% in any subject
    """
    logger.info(f"Received attendance analysis request for section: {section}")
    
    try:
        import json as json_module
        from attendance_extractor import extract_attendance_data
        
        # Parse subjects mapping (code -> name) from user input
        subjects_mapping = {}
        if subjects_data:
            try:
                subjects_list = json_module.loads(subjects_data)
                for s in subjects_list:
                    code = s.get('code', '').strip().upper()
                    name = s.get('name', '').strip()
                    if code and name:
                        subjects_mapping[code] = name
            except Exception as e:
                logger.warning(f"Failed to parse subjects_data: {e}")
        logger.info(f"Subjects mapping: {subjects_mapping}")
        
        # Read PDF file
        contents = await file.read()
        logger.info(f"Processing file: {file.filename}, size: {len(contents)} bytes")
        
        # Extract attendance data
        students_data = extract_attendance_data(contents)
        
        if not students_data:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": "No students with low attendance were found in this PDF.",
                    "hint": "Verify that the uploaded file is a consolidated attendance status PDF and that attendance percentages are visible.",
                    "file": file.filename,
                }
            )
        
        # Generate Excel report
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet("Low Attendance")
        
        # Formats
        title_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 14})
        subtitle_format = workbook.add_format({'bold': True, 'align': 'center', 'font_size': 12})
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'bg_color': '#CCCCCC',
            'text_wrap': True
        })
        data_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        data_left_format = workbook.add_format({'align': 'left', 'valign': 'vcenter', 'border': 1})
        
        # Write headers
        worksheet.merge_range('A1:F1', 'SRM INSTITUTE OF SCIENCE AND TECHNOLOGY', title_format)
        worksheet.merge_range('A2:F2', 'COLLEGE OF ENGINEERING AND TECHNOLOGY', title_format)
        worksheet.merge_range('A3:F3', 'DEPARTMENT OF COMPUTING TECHNOLOGIES', title_format)
        worksheet.write('A5', f'SECTION: {section}', subtitle_format)
        worksheet.write('D5', f'FACULTY ADVISOR: {faculty_advisor}', subtitle_format)
        
        # Count total students and subjects
        total_students_count = len(students_data)
        total_subjects_count = sum(len(student['subjects']) for student in students_data)
        
        worksheet.write('A6', f'TOTAL NUMBER OF STUDENTS: {total_students_count}', subtitle_format)
        worksheet.write('D6', f'Number of Students Less than 75% (Even in 1 subject): {total_students_count}', subtitle_format)
        
        # Table headers
        headers = ['S.No', 'Register Number', 'Student Name', 'Subject Code', 'Subject Name', 
                   'Attendance Percentage']
        
        worksheet.write_row('A7', headers, header_format)
        worksheet.set_row(6, 30)
        
        # Write student data
        current_row = 7
        s_no = 1
        
        for student in students_data:
            num_subjects = len(student['subjects'])
            start_row = current_row
            
            # Write student info with merged cells for multiple subjects
            if num_subjects > 1:
                worksheet.merge_range(start_row, 0, start_row + num_subjects - 1, 0, s_no, data_format)
                worksheet.merge_range(start_row, 1, start_row + num_subjects - 1, 1, student['reg_number'], data_left_format)
                worksheet.merge_range(start_row, 2, start_row + num_subjects - 1, 2, student['name'], data_left_format)
            else:
                worksheet.write(current_row, 0, s_no, data_format)
                worksheet.write(current_row, 1, student['reg_number'], data_left_format)
                worksheet.write(current_row, 2, student['name'], data_left_format)
            
            # Write subject details
            for subject in student['subjects']:
                subj_code = subject['subject_code']
                # Look up subject name from user-provided mapping
                subj_name = subjects_mapping.get(subj_code.upper(), '')
                worksheet.write(current_row, 3, subj_code, data_format)
                worksheet.write(current_row, 4, subj_name, data_left_format)
                worksheet.write(current_row, 5, subject['attendance_percentage'], data_format)
                current_row += 1
            
            s_no += 1
        
        # Set column widths
        worksheet.set_column('A:A', 6)
        worksheet.set_column('B:B', 18)
        worksheet.set_column('C:C', 25)
        worksheet.set_column('D:D', 20)
        worksheet.set_column('E:E', 35)
        worksheet.set_column('F:F', 20)
        worksheet.set_column('G:G', 18)
        worksheet.set_column('H:H', 18)
        
        workbook.close()
        output.seek(0)
        
        headers = {
            'Content-Disposition': f'attachment; filename="Low_Attendance_{section}.xlsx"'
        }
        
        return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in attendance analysis: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Attendance analysis failed due to an internal server error.",
                "hint": "Please retry once. If it still fails, share the file name and timestamp from console logs.",
            }
        )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
