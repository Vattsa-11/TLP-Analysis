
import pdfplumber
import pandas as pd
import requests
import os
import re
import logging
import io

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def normalize_name(name):
    """Normalize teacher name for comparison - removes special characters."""
    if not name:
        return ""
    # Remove dots, spaces, parentheses, brackets, dashes, commas and lowercase
    return re.sub(r'[.\s()\[\]\-,/]', '', name).lower().strip()

def matches_query(query, text):
    """
    Flexible matching for faculty search. Supports:
    - Name only (e.g., "Dr.Smith")
    - Faculty ID only (e.g., "EMP123")
    - Both name and ID (e.g., "Dr.Smith EMP123")
    """
    if not query or not text:
        return False
    
    norm_text = normalize_name(text)
    norm_query = normalize_name(query)
    
    # Direct substring match (covers single name or single ID)
    if norm_query in norm_text:
        return True
    
    # Split query into tokens and check if ALL tokens match individually
    parts = query.strip().split()
    if len(parts) > 1:
        if all(normalize_name(part) in norm_text for part in parts):
            return True
    
    return False

def clean_faculty_name(raw_text):
    """
    Cleans the faculty name string by removing S.No and trailing metrics.
    Example: "107 Dr.Name(123) 43 0..." -> "Dr.Name(123)"
    """
    if not raw_text:
        return ""
        
    # Step 1: Remove S.No if present (digits at start followed by space)
    text = re.sub(r'^\d+\s+', '', raw_text)
    
    # Step 2: Remove trailing metrics (sequence of numbers at end)
    # Regex for sequence of numbers at end: ((\s+\d+(\.\d+)?)+)$
    match = re.search(r'(.*?)(\s+\d+(\.\d+)?)+\s*$', text)
    if match:
        return match.group(1).strip()
    
    return text.strip()

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _check_consistency(metrics):
    """
    Checks mathematical consistency of a metrics list.
    Pass% (index 3) should match (passed / appeared) * 100.
    Returns 1.0 (consistent), 0.5 (close), or 0.0 (inconsistent).
    """
    if len(metrics) < 4:
        return 0.0
    try:
        strength = float(metrics[0])
        absent   = float(metrics[1])
        fail     = float(metrics[2])
        pass_pct = float(metrics[3])
    except (ValueError, TypeError):
        return 0.0

    if strength <= 0:
        return 0.0
    appeared = strength - absent
    if appeared <= 0:
        return 0.0

    passed = appeared - fail
    calc_pct = (passed / appeared) * 100
    diff = abs(calc_pct - pass_pct)

    if diff <= 2:
        return 1.0
    elif diff <= 5:
        return 0.5
    return 0.0


def _build_results_list(all_matches, course, subject_code, test_name, method, raw_text):
    """Turns a list of match dicts into the standard results_list format."""
    results_list = []
    for match_data in all_matches:
        results_list.append({
            "course": course,
            "subject_code": subject_code,
            "dataset": test_name,
            "data": match_data,
            "method": method,
            "raw_text": raw_text,
        })
    return results_list


def _is_text_meaningful(text):
    """Returns True if text has sufficient, non-garbled content."""
    if text is None or len(text.strip()) < 50:
        return False
    good = sum(1 for c in text if c.isalnum() or c in ' \n.,:-/()')
    return (good / len(text)) >= 0.55


def _is_image_based_pdf(file_bytes):
    """
    Detects if a PDF is image-based (scanned document) vs native text PDF.

    Returns True if the PDF appears to be image-based (minimal native text).
    Returns False if the PDF has sufficient native text.
    """
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                return False

            # Extract text from first 2 pages
            full_text = ""
            for page in pdf.pages[:2]:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

            # Check if text is meaningful
            if not _is_text_meaningful(full_text):
                logger.info(f"[Image Detection] PDF appears image-based (extracted {len(full_text)} chars)")
                return True

            # If total extracted text is still very small, it's likely image-based
            if len(full_text.strip()) < 100:
                logger.info(f"[Image Detection] PDF appears image-based (only {len(full_text)} chars from 2 pages)")
                return True

            logger.info(f"[Image Detection] PDF has native text ({len(full_text)} chars)")
            return False

    except Exception as e:
        logger.error(f"[Image Detection] Error checking PDF: {e}")
        return False


def _extract_with_pdfplumber(file_bytes, teacher_name_query):
    """
    Runs pdfplumber-based extraction only.
    Returns a results_list (may be empty).
    """
    logger.info(f"[pdfplumber] Extracting for: {teacher_name_query}")
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                logger.warning("[pdfplumber] Empty PDF.")
                return []

            full_text = ""
            all_tables = []

            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
                tables = page.extract_tables()
                all_tables.extend(tables)

            if not _is_text_meaningful(full_text):
                logger.warning("[pdfplumber] Text is missing or garbled – likely scanned/bad-font PDF.")
                return []

            # --- Metadata ---
            course = "Unknown Course"
            subject_code = "Unknown Code"
            test_name = "Unknown Test"

            course_match = re.search(r"Course\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
            if course_match:
                course = course_match.group(1).strip()
                subject_code = course.split("-")[0].strip() if "-" in course else course.split()[0].strip()

            test_match = re.search(r"Test Name\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
            if test_match:
                test_name = test_match.group(1).strip()

            logger.info(f"[pdfplumber] Metadata – Course: {course}, Code: {subject_code}, Test: {test_name}")

            # --- Matches ---
            all_matches = []

            for table in all_tables:
                for row in table:
                    clean_row = [str(cell) if cell is not None else "" for cell in row]

                    merged_cell_match = False
                    for cell in clean_row:
                        if "\n" in cell and matches_query(teacher_name_query, cell):
                            cell_matches = parse_all_text_lines(cell, teacher_name_query)
                            if cell_matches:
                                all_matches.extend(cell_matches)
                                merged_cell_match = True
                            if merged_cell_match:
                                break

                    if merged_cell_match:
                        continue

                    if any("Faculty Name" in cell for cell in clean_row):
                        continue

                    found_name = None
                    for cell in clean_row:
                        if matches_query(teacher_name_query, cell):
                            found_name = cell
                            break

                    if found_name:
                        match = parse_table_row(clean_row, found_name)
                        if match:
                            all_matches.append(match)

            if not all_matches:
                logger.warning("[pdfplumber] Table extraction: no match. Trying line-by-line...")
                all_matches = parse_all_text_lines(full_text, teacher_name_query)

            if not all_matches:
                logger.warning(f"[pdfplumber] No data found for '{teacher_name_query}'.")
                return []

            results_list = _build_results_list(all_matches, course, subject_code, test_name,
                                       "pdfplumber_native", full_text)
            # Deduplicate before returning
            return deduplicate_results(results_list)

    except Exception as e:
        logger.error(f"[pdfplumber] Extraction failed: {e}")
        return []


def _extract_with_ocr(file_bytes, teacher_name_query, api_key):
    """
    Runs OCR-based extraction only.
    Returns a results_list (may be empty).
    """
    if not api_key:
        logger.warning("[OCR] No API key – skipping OCR extraction.")
        return []

    logger.info(f"[OCR] Extracting for: {teacher_name_query}")
    full_text = fetch_ocr_text(file_bytes, api_key)

    if not full_text:
        return []

    all_matches = parse_all_text_lines(full_text, teacher_name_query)

    if not all_matches:
        logger.warning(f"[OCR] No data found for '{teacher_name_query}'.")
        return []

    course = "Unknown (OCR)"
    test_name = "Unknown (OCR)"
    course_match = re.search(r"Course\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
    if course_match:
        course = course_match.group(1).strip()
    test_match = re.search(r"Test Name\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
    if test_match:
        test_name = test_match.group(1).strip()

    subject_code = course.split("-")[0].strip() if "-" in course else course.split()[0].strip()

    results_list = _build_results_list(all_matches, course, subject_code, test_name,
                               "ocr_api", full_text)
    # Deduplicate before returning
    return deduplicate_results(results_list)


def _extract_with_ocrmypdf(file_bytes, teacher_name_query):
    """
    Runs ocrmypdf-based extraction for image-based PDFs.
    Uses Tesseract OCR engine for local processing.
    Returns a results_list (may be empty).
    """
    logger.info(f"[ocrmypdf] Extracting for: {teacher_name_query}")

    try:
        import tempfile
        import ocrmypdf

        # Create temporary file for OCR processing
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_input:
            tmp_input.write(file_bytes)
            tmp_input_path = tmp_input.name

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_output:
            tmp_output_path = tmp_output.name

        try:
            # Run OCR with Tesseract engine
            logger.info(f"[ocrmypdf] Processing with Tesseract OCR...")
            ocrmypdf.ocr(
                tmp_input_path,
                tmp_output_path,
                language='eng',
                deskew=True,
                optimize=0,
                progress_bar=False,
                quiet=True
            )

            # Extract text from OCR'd PDF using pdfplumber
            logger.info(f"[ocrmypdf] Extracting text from OCR'd PDF...")
            full_text = ""
            with pdfplumber.open(tmp_output_path) as pdf:
                if not pdf.pages:
                    logger.warning("[ocrmypdf] OCR resulted in empty PDF.")
                    return []

                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        full_text += text + "\n"

            if not full_text or len(full_text.strip()) < 50:
                logger.warning(f"[ocrmypdf] No text extracted after OCR (only {len(full_text)} chars).")
                return []

            logger.info(f"[ocrmypdf] Extracted {len(full_text)} characters from OCR'd PDF.")

            # Parse extracted text
            all_matches = parse_all_text_lines(full_text, teacher_name_query)

            if not all_matches:
                logger.warning(f"[ocrmypdf] No data found for '{teacher_name_query}'.")
                return []

            # Extract metadata from OCR text
            course = "Unknown (ocrmypdf)"
            test_name = "Unknown (ocrmypdf)"
            course_match = re.search(r"Course\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
            if course_match:
                course = course_match.group(1).strip()
            test_match = re.search(r"Test Name\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
            if test_match:
                test_name = test_match.group(1).strip()

            subject_code = course.split("-")[0].strip() if "-" in course else course.split()[0].strip()

            results_list = _build_results_list(all_matches, course, subject_code, test_name,
                                       "ocrmypdf", full_text)
            logger.info(f"[ocrmypdf] Found {len(results_list)} result(s).")

            # Deduplicate before returning
            return deduplicate_results(results_list)

        finally:
            # Clean up temporary files
            import os
            if os.path.exists(tmp_input_path):
                try:
                    os.remove(tmp_input_path)
                except Exception as e:
                    logger.debug(f"[ocrmypdf] Could not delete temp input: {e}")
            if os.path.exists(tmp_output_path):
                try:
                    os.remove(tmp_output_path)
                except Exception as e:
                    logger.debug(f"[ocrmypdf] Could not delete temp output: {e}")

    except ImportError:
        logger.error("[ocrmypdf] ocrmypdf library not installed. Install via: pip install ocrmypdf")
        return []
    except Exception as e:
        logger.error(f"[ocrmypdf] Extraction failed: {e}")
        return []


def deduplicate_results(results_list):
    """
    Removes duplicate entries based on identical metrics arrays.
    Keeps only the first occurrence of each unique metric set.

    Args:
        results_list: List of result dicts, each containing a 'data' dict with 'metrics' array

    Returns:
        Deduplicated results list
    """
    if not results_list:
        return results_list

    seen_metrics = set()
    deduplicated = []

    for result in results_list:
        try:
            metrics = result.get('data', {}).get('metrics', [])
            # Convert metrics to tuple for hashing (first 4 elements are key: strength, absent, fail, pass%)
            # Include all metrics for full comparison
            metrics_tuple = tuple(float(m) for m in metrics[:10]) if len(metrics) >= 4 else tuple()

            if metrics_tuple and metrics_tuple not in seen_metrics:
                seen_metrics.add(metrics_tuple)
                deduplicated.append(result)
            elif metrics_tuple:
                logger.info(f"[Dedup] Skipping duplicate entry with metrics: {metrics_tuple}")
        except (TypeError, ValueError) as e:
            # If we can't hash the metrics, keep the entry to be safe
            deduplicated.append(result)

    if len(deduplicated) < len(results_list):
        logger.info(f"[Dedup] Removed {len(results_list) - len(deduplicated)} duplicate entries.")

    return deduplicated


def cross_verify_results(plumber_results, ocr_results, teacher_name_query):
    """
    Cross-verifies pdfplumber and OCR results.
    
    Strategy:
    - If both found data, compare key metrics (strength, absent, fail) per entry.
      * diff <= 3  → confirmed match → use pdfplumber (higher structural fidelity)
      * diff <= 15 → minor mismatch  → prefer pdfplumber but flag as partial
      * diff > 15  → major mismatch  → pick whichever is mathematically consistent
    - If only one found data, use that result (flagged as unverified/single-source).
    - If neither found data, return empty list.
    """
    if not plumber_results and not ocr_results:
        logger.warning(f"[CrossVerify] Both methods found no data for '{teacher_name_query}'.")
        return []

    if not plumber_results:
        logger.warning(f"[CrossVerify] pdfplumber: no data. Using OCR results only.")
        for r in ocr_results:
            r["method"] = "ocr_only"
            r["verified"] = False
        return ocr_results

    if not ocr_results:
        logger.info(f"[CrossVerify] OCR: no data. Using pdfplumber results (unverified).")
        for r in plumber_results:
            r["verified"] = False
        return plumber_results

    logger.info(
        f"[CrossVerify] pdfplumber={len(plumber_results)} entries, OCR={len(ocr_results)} entries."
    )

    verified = []

    for p_item in plumber_results:
        p_metrics = p_item.get("data", {}).get("metrics", [])
        if len(p_metrics) < 3:
            verified.append({**p_item, "verified": False, "method": "pdfplumber_unverified"})
            continue

        best_ocr = None
        best_diff = float("inf")

        for o_item in ocr_results:
            o_metrics = o_item.get("data", {}).get("metrics", [])
            if len(o_metrics) < 3:
                continue
            diff = sum(abs(float(p_metrics[i]) - float(o_metrics[i])) for i in range(3))
            if diff < best_diff:
                best_diff = diff
                best_ocr = o_item

        if best_ocr is None:
            # No comparable OCR entry found
            verified.append({**p_item, "verified": False, "method": "pdfplumber_no_ocr_match"})
            continue

        if best_diff <= 3:
            logger.info(f"[CrossVerify] CONFIRMED (diff={best_diff:.1f}): metrics match. Using pdfplumber.")
            verified.append({**p_item, "verified": True, "method": "pdfplumber_verified"})

        elif best_diff <= 15:
            logger.warning(f"[CrossVerify] MINOR MISMATCH (diff={best_diff:.1f}): preferring pdfplumber.")
            verified.append({**p_item, "verified": "partial", "method": "pdfplumber_preferred"})

        else:
            logger.warning(f"[CrossVerify] MAJOR MISMATCH (diff={best_diff:.1f}): checking consistency.")
            p_score = _check_consistency(p_metrics)
            o_metrics = best_ocr.get("data", {}).get("metrics", [])
            o_score = _check_consistency(o_metrics)
            logger.info(f"[CrossVerify] Consistency scores – pdfplumber={p_score}, OCR={o_score}")

            if p_score >= o_score:
                logger.info("[CrossVerify] Using pdfplumber (equal or better consistency).")
                verified.append({**p_item, "verified": False, "method": "pdfplumber_inconsistent"})
            else:
                logger.info("[CrossVerify] Using OCR (better consistency).")
                verified.append({**best_ocr, "verified": False, "method": "ocr_preferred"})

    # If OCR found more entries than pdfplumber, append the extras
    if len(ocr_results) > len(plumber_results):
        logger.info(
            f"[CrossVerify] OCR found {len(ocr_results) - len(plumber_results)} extra entries not in pdfplumber."
        )
        # Naive heuristic: extra entries are OCR results beyond the matched count
        for o_item in ocr_results[len(plumber_results):]:
            verified.append({**o_item, "verified": False, "method": "ocr_extra"})

    if not verified:
        logger.warning("[CrossVerify] Verification produced no results. Returning OCR results as fallback.")
        for r in ocr_results:
            r["method"] = "ocr_fallback"
        return ocr_results

    return verified


def extract_pdf_data(file_bytes, teacher_name_query, ocr_api_key=None):
    """
    Extracts data from a PDF (bytes) for a specific teacher.

    New strategy:
    1. Try pdfplumber first (native text extraction)
    2. Detect if PDF is image-based (minimal native text)
    3. If image-based: use ocrmypdf + Tesseract
    4. If native text: use OCR.space API for cross-verification
    5. Cross-verify and return most reliable data
    """
    logger.info(f"Starting extraction for teacher: {teacher_name_query}")

    # --- Step 1: pdfplumber (primary extraction) ---
    plumber_results = _extract_with_pdfplumber(file_bytes, teacher_name_query)

    # --- Step 2: Detect if PDF is image-based and choose OCR method ---
    is_image_based = _is_image_based_pdf(file_bytes)
    ocr_results = []

    if is_image_based:
        logger.info("[Extraction] PDF detected as image-based. Using ocrmypdf + Tesseract...")
        ocr_results = _extract_with_ocrmypdf(file_bytes, teacher_name_query)
    else:
        logger.info("[Extraction] PDF detected as native text. Using OCR.space API...")
        ocr_results = _extract_with_ocr(file_bytes, teacher_name_query, ocr_api_key)

    # --- Step 3: Cross-verify & return ---
    verified_results = cross_verify_results(plumber_results, ocr_results, teacher_name_query)

    # --- Step 4: Deduplicate final results ---
    deduplicated_results = deduplicate_results(verified_results)
    return deduplicated_results

def fetch_ocr_text(file_bytes, api_key):
    """
    Renders each PDF page to a PNG image and sends it to OCR.space one at a time.
    Concatenates text from all pages and returns it.
    """
    if not api_key:
        logger.error("OCR API key not provided. Skipping OCR fallback.")
        return ""

    url = 'https://api.ocr.space/parse/image'
    full_text = ""

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total_pages = len(pdf.pages)
            logger.info(f"Sending {total_pages} page(s) to OCR.space API...")

            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    img = page.to_image(resolution=200)
                    buf = io.BytesIO()
                    img.original.save(buf, format='PNG')
                    img_bytes = buf.getvalue()

                    logger.info(f"[OCR] Sending page {page_num}/{total_pages} ({len(img_bytes)} bytes)...")

                    response = requests.post(
                        url,
                        files={'file': ('page.png', img_bytes, 'image/png')},
                        data={
                            'apikey': api_key,
                            'language': 'eng',
                            'isTable': True,
                            'OCREngine': 2,
                            'scale': True,
                        },
                        timeout=60,
                    )

                    if response.status_code != 200:
                        logger.error(f"[OCR] Page {page_num} API error: {response.status_code} - {response.text}")
                        continue

                    result = response.json()

                    if result.get('IsErroredOnProcessing'):
                        logger.error(f"[OCR] Page {page_num} processing error: {result.get('ErrorMessage')}")
                        continue

                    if result.get('ParsedResults'):
                        for page_res in result['ParsedResults']:
                            full_text += page_res.get('ParsedText', '') + "\n"

                    logger.info(f"[OCR] Page {page_num} done. Running total: {len(full_text)} chars.")

                except Exception as e:
                    logger.error(f"[OCR] Page {page_num} failed: {e}")
                    continue

    except Exception as e:
        logger.error(f"[OCR] Failed to open PDF for rendering: {e}")
        return ""

    logger.info(f"OCR complete. Total extracted: {len(full_text)} characters.")
    return full_text

def extract_with_ocr_fallback(file_bytes, teacher_name_query, api_key):
    """
    Fallback extraction using OCR API when native text is missing/garbled.
    """
    logger.info("Attempting OCR fallback extraction...")
    full_text = fetch_ocr_text(file_bytes, api_key)
    
    if not full_text:
        return []
        
    # Search in OCR text
    all_matches = parse_all_text_lines(full_text, teacher_name_query)
    
    results = []
    if all_matches:
        # We don't have metadata easily from OCR text usually, unless we parse headers
        # Try to parse course/test from full text same as before
        course = "Unknown (OCR)"
        test_name = "Unknown (OCR)"
        
        course_match = re.search(r"Course\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
        if course_match: course = course_match.group(1).strip()
            
        test_match = re.search(r"Test Name\s*[:|-]\s*(.*)", full_text, re.IGNORECASE)
        if test_match: test_name = test_match.group(1).strip()
            
        aggregated = aggregate_metrics(all_matches)
        
        results.append({
            "course": course,
            "subject_code": course.split('-')[0].strip() if '-' in course else course.split()[0],
            "dataset": test_name,
            "data": aggregated, # Return aggregated for single teacher query?
            # Or should we return list? extract_pdf_data returns list.
            # But aggregate_metrics returns dict.
            # extract_pdf_data returns list of dicts with 'data' field being the row/match.
            # Let's return list of matches.
            "data": all_matches[0], # Return first match for now or loop?
            "method": "ocr_api",
            "raw_text": full_text
        })
        # Wait, previous loop in extract_pdf_data returned one entry per match.
        # Let's do that.
        results = []
        for idx, match in enumerate(all_matches):
             results.append({
                "course": course,
                "subject_code": course.split('-')[0].strip() if '-' in course else course.split()[0],
                "dataset": test_name,
                "data": match,
                "method": "ocr_api",
                "raw_text": full_text
            })
            
    return results

def parse_table_row(row_list, faculty_name=None):
    """
    Parses a standard table row.
    """
    numbers = []
    
    for cell in row_list:
        clean = cell.replace("%", "").strip()
        tokens = clean.replace("\n", " ").split()
        for t in tokens:
            try:
                val = float(t)
                numbers.append(val)
            except:
                continue
            
    if len(numbers) < 10:
        return None
        
    data_points = numbers[-10:] 
    
    return {
        "raw_row": row_list,
        "metrics": data_points,
        "faculty_name": clean_faculty_name(faculty_name) if faculty_name else ""
    }

def parse_all_text_lines(text, teacher_query):
    """
    Finds ALL occurrences of the teacher in the text lines.
    Supports searching by name, faculty ID, or both.
    Returns a list of match dicts.
    """
    matches = []
    lines = text.splitlines()
    for line in lines:
        if matches_query(teacher_query, line):
            numbers = re.findall(r"[-+]?\d*\.\d+|\d+", line)
            valid_nums = []
            for n in numbers:
                 try: valid_nums.append(float(n))
                 except: pass
            
            if len(valid_nums) >= 10:
                logger.info(f"Line match success: {line.strip()}")
                matches.append({
                    "raw_row": line,
                    "metrics": valid_nums[-10:],
                    "faculty_name": clean_faculty_name(line.strip()) 
                })
    return matches

def aggregate_metrics(matches_list):
    """
    Aggregates a list of match dictionaries into a single result.
    """
    if not matches_list:
        return None
        
    if len(matches_list) == 1:
        return matches_list[0]
        
    total_strength = 0.0
    total_absent = 0.0
    total_fail = 0.0
    total_ranges = [0.0] * 6
    
    for m in matches_list:
        metrics = m['metrics']
        total_strength += metrics[0]
        total_absent += metrics[1]
        total_fail += metrics[2]
        
        for i in range(6):
            if 4+i < len(metrics):
                total_ranges[i] += metrics[4+i]
                
    total_passed = total_strength - total_absent - total_fail
    
    new_pass_pct = 0.0
    if total_strength > 0:
        appeared = total_strength - total_absent
        if appeared > 0:
             new_pass_pct = (total_passed / appeared) * 100
    
    final_metrics = [
        total_strength,
        total_absent,
        total_fail,
        new_pass_pct
    ] + total_ranges
    
    return {
        "raw_row": "AGGREGATED",
        "metrics": final_metrics,
        "match_count": len(matches_list),
        "faculty_name": matches_list[0].get("faculty_name", "")
    }

def _overall_parse_ocr_lines(ocr_text):
    """Helper: parse numeric data rows from OCR text for overall extraction."""
    matches = []
    for line in ocr_text.splitlines():
        if "Total" in line or "Range" in line or "Faculty" in line:
            continue
        numbers = re.findall(r"[-+]?\d*\.\d+|\d+", line)
        valid_nums = []
        for n in numbers:
            try:
                valid_nums.append(float(n))
            except (ValueError, TypeError):
                pass
        if len(valid_nums) >= 10:
            matches.append({
                "raw_row": line,
                "metrics": valid_nums[-10:],
                "faculty_name": "",
            })
    return matches


def extract_overall_data(file_bytes, ocr_api_key=None):
    """
    Extracts ALL rows from a PDF and aggregates them into a single result.
    Used for Overall Result Analysis.

    Always runs BOTH pdfplumber and OCR (if API key is available).
    Cross-verifies by comparing row counts and total student numbers;
    chooses whichever source is more complete / internally consistent,
    then falls back to merging both if they complement each other.
    """
    logger.info("Starting dual overall extraction (all rows \u2192 aggregate).")

    # ------------------------------------------------------------------ #
    # STEP 1 \u2013 pdfplumber
    # ------------------------------------------------------------------ #
    plumber_matches = []
    plumber_text    = ""
    course          = "Unknown Course"
    subject_code    = "Unknown Code"
    test_name       = "Unknown Test"

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                raise Exception("Empty PDF")

            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    plumber_text += text + "\n"

                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        clean_row = [str(cell) if cell is not None else "" for cell in row]
                        if any(x in str(clean_row) for x in ["Faculty Name", "Test Component", "S.No"]):
                            continue
                        match = parse_table_row(clean_row)
                        if match:
                            plumber_matches.append(match)

        if plumber_text:
            cm = re.search(r"Course\s*[:|-]\s*(.*)", plumber_text, re.IGNORECASE)
            if cm:
                course = cm.group(1).strip()
                subject_code = course.split("-")[0].strip() if "-" in course else course.split()[0].strip()
            tm = re.search(r"Test Name\s*[:|-]\s*(.*)", plumber_text, re.IGNORECASE)
            if tm:
                test_name = tm.group(1).strip()

        logger.info(f"[pdfplumber overall] Found {len(plumber_matches)} data rows.")

    except Exception as e:
        logger.error(f"[pdfplumber overall] Extraction error: {e}")

    # ------------------------------------------------------------------ #
    # STEP 2 \u2013 OCR
    # ------------------------------------------------------------------ #
    ocr_matches = []
    ocr_text    = ""

    if ocr_api_key:
        ocr_text = fetch_ocr_text(file_bytes, ocr_api_key)
        if ocr_text:
            ocr_matches = _overall_parse_ocr_lines(ocr_text)
            logger.info(f"[OCR overall] Found {len(ocr_matches)} data rows.")

            # Fill in metadata from OCR if pdfplumber couldn\u2019t parse it
            if course == "Unknown Course":
                cm = re.search(r"Course\s*[:|-]\s*(.*)", ocr_text, re.IGNORECASE)
                if cm:
                    course = cm.group(1).strip()
                    subject_code = course.split("-")[0].strip() if "-" in course else course.split()[0].strip()
            if test_name == "Unknown Test":
                tm = re.search(r"Test Name\s*[:|-]\s*(.*)", ocr_text, re.IGNORECASE)
                if tm:
                    test_name = tm.group(1).strip()

    # ------------------------------------------------------------------ #
    # STEP 3 \u2013 Cross-verify & decide which set to aggregate
    # ------------------------------------------------------------------ #
    def _total_strength(matches):
        total = 0.0
        for m in matches:
            try:
                total += float(m["metrics"][0])
            except (IndexError, ValueError, TypeError):
                pass
        return total

    p_count  = len(plumber_matches)
    o_count  = len(ocr_matches)
    p_total  = _total_strength(plumber_matches)
    o_total  = _total_strength(ocr_matches)

    logger.info(
        f"[CrossVerify overall] pdfplumber: {p_count} rows / {p_total:.0f} students | "
        f"OCR: {o_count} rows / {o_total:.0f} students"
    )

    selected_matches = []
    method_used      = "none"

    if p_count > 0 and o_count > 0:
        # Both have data \u2013 pick the more complete one
        if p_count >= o_count and abs(p_total - o_total) / max(p_total, o_total, 1) < 0.05:
            # Very close \u2013 pdfplumber regarded as more accurate
            logger.info("[CrossVerify overall] Both agree. Using pdfplumber.")
            selected_matches = plumber_matches
            method_used      = "pdfplumber_verified"
        elif o_count > p_count:
            logger.warning(
                f"[CrossVerify overall] OCR found MORE rows ({o_count} > {p_count}). Using OCR."
            )
            selected_matches = ocr_matches
            method_used      = "ocr_preferred"
        else:
            # Similar row count but different totals \u2013 check internal consistency
            p_score = sum(_check_consistency(m["metrics"]) for m in plumber_matches)
            o_score = sum(_check_consistency(m["metrics"]) for m in ocr_matches)
            logger.info(f"[CrossVerify overall] Consistency scores \u2013 pdfplumber={p_score:.1f}, OCR={o_score:.1f}")
            if p_score >= o_score:
                selected_matches = plumber_matches
                method_used      = "pdfplumber_preferred"
            else:
                selected_matches = ocr_matches
                method_used      = "ocr_preferred"

    elif p_count > 0:
        logger.info("[CrossVerify overall] Only pdfplumber found data.")
        selected_matches = plumber_matches
        method_used      = "pdfplumber_only"

    elif o_count > 0:
        logger.warning("[CrossVerify overall] Only OCR found data.")
        selected_matches = ocr_matches
        method_used      = "ocr_only"

    else:
        logger.warning("[CrossVerify overall] No data found by either method.")
        return None

    # ------------------------------------------------------------------ #
    # STEP 4 \u2013 Aggregate & return
    # ------------------------------------------------------------------ #
    aggregated = aggregate_metrics(selected_matches)

    return {
        "course":        course,
        "subject_code":  subject_code,
        "dataset":       test_name,
        "data":          aggregated,
        "method":        method_used,
        "raw_text_len":  len(plumber_text or ocr_text),
    }

