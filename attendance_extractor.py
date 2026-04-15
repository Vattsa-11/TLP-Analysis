import pdfplumber
import re
import logging
import io
import os
import shutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def detect_subject_codes(pdf_bytes):
    """
    Scan the entire PDF and return all unique subject codes found.
    Tries pdfplumber native text first; falls back to OCR page-by-page
    when the PDF is a scanned / image-only document.
    Returns a sorted list of unique base subject codes (brackets stripped).
    """
    logger.info("Detecting subject codes from PDF")

    subject_code_pattern = r'\d{2}[A-Z]{3}\d{3}[A-Z](?:\([A-Z]\))?'
    found_codes = set()
    native_text_found = False

    # ---- Step 1: pdfplumber native ----
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text and len(text.strip()) > 20:
                    native_text_found = True
                    matches = re.findall(subject_code_pattern, text)
                    for m in matches:
                        base_code = re.sub(r'\([A-Z]\)$', '', m).strip()
                        found_codes.add(base_code)
    except Exception as e:
        logger.error(f"Error in native detect_subject_codes: {e}")

    if found_codes:
        result = sorted(found_codes)
        logger.info(f"Detected subject codes (native): {result}")
        return result

    # ---- Step 2: OCR fallback (scanned / image PDFs) ----
    if not native_text_found:
        logger.warning("No native text found. Falling back to OCR for subject code detection.")
        try:
            ocr_text = _ocr_pdf_to_text(pdf_bytes)
            if ocr_text:
                ocr_text = _normalize_ocr_text(ocr_text)
                matches = re.findall(subject_code_pattern, ocr_text)
                for m in matches:
                    base_code = re.sub(r'\([A-Z]\)$', '', m).strip()
                    found_codes.add(base_code)
        except Exception as e:
            logger.error(f"OCR fallback in detect_subject_codes failed: {e}")

    result = sorted(found_codes)
    logger.info(f"Detected subject codes (OCR): {result}")
    return result


def extract_attendance_data(pdf_bytes):
    """
    Extract low attendance data from PDF.
    Strategy:
    1. Try native pdfplumber text extraction (most reliable)
    2. If insufficient, try ocrmypdf + Tesseract (local OCR)
    """
    logger.info("Extracting attendance data from PDF")

    try:
        pdf_file = io.BytesIO(pdf_bytes)
        candidates = []

        with pdfplumber.open(pdf_file) as pdf:
            # Step 1: Native text extraction
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

            if len(full_text.strip()) >= 100:
                logger.info(f"Native text extracted: {len(full_text)} chars from {len(pdf.pages)} pages")
                native_result = _parse_attendance_native(full_text)
                if native_result:
                    logger.info(f"Native parser found {len(native_result)} students with low attendance")
                    candidates.append(("native", native_result))
                else:
                    logger.warning("Native parsing found no results.")

        # Step 2: Direct Tesseract OCR
        logger.info("Attempting direct Tesseract OCR extraction...")
        tesseract_result = _extract_attendance_tesseract(pdf_bytes)
        if tesseract_result:
            logger.info(f"Direct Tesseract OCR found {len(tesseract_result)} students with low attendance")
            candidates.append(("tesseract", tesseract_result))

        # Step 3: ocrmypdf + Tesseract
        logger.info("Attempting ocrmypdf + Tesseract extraction...")
        try:
            ocrmypdf_result = _extract_attendance_ocrmypdf(pdf_bytes)
            if ocrmypdf_result:
                logger.info(f"ocrmypdf found {len(ocrmypdf_result)} students with low attendance")
                candidates.append(("ocrmypdf", ocrmypdf_result))
            else:
                logger.warning("ocrmypdf extraction found no results.")
        except Exception as e:
            logger.warning(f"ocrmypdf fallback failed: {e}")

        # Step 4: OCR.space API fallback
        logger.info("Attempting OCR.space fallback extraction...")
        ocr_space_result = _extract_attendance_ocr(pdf_bytes)
        if ocr_space_result:
            logger.info(f"OCR.space found {len(ocr_space_result)} students with low attendance")
            candidates.append(("ocr_space", ocr_space_result))

        if not candidates:
            logger.warning("No attendance data extracted with pdfplumber, Tesseract, ocrmypdf, or OCR.space.")
            return []

        merged_result = _merge_attendance_results(candidates)
        best_source, best_rows = max(candidates, key=lambda item: _attendance_result_score(item[1]))

        merged_score = _attendance_result_score(merged_result)
        best_score = _attendance_result_score(best_rows)

        if merged_score >= best_score:
            logger.info(
                f"Using merged attendance result: {len(merged_result)} students "
                f"from sources {[name for name, _ in candidates]}"
            )
            return merged_result

        logger.info(f"Using best single source '{best_source}' with {len(best_rows)} students")
        return best_rows

    except Exception as e:
        logger.error(f"Error extracting attendance data: {e}")
        raise


def _attendance_result_score(rows):
    """Score extraction quality by unique students first, then low-subject entries."""
    if not rows:
        return (0, 0)
    subject_entries = sum(len(r.get('subjects', [])) for r in rows)
    return (len(rows), subject_entries)


def _is_strict_reg(reg_no):
    return bool(reg_no and re.fullmatch(r'(?:RA|BA)\d{13}', reg_no))


def _dominant_reg_prefix(candidates, prefix_len=4):
    """Find dominant prefix (e.g., RA24) from high-confidence register numbers."""
    counts = {}
    for _, rows in candidates:
        for row in rows or []:
            reg = _normalize_reg_no(row.get('reg_number'))
            if _is_strict_reg(reg):
                p = reg[:prefix_len]
                counts[p] = counts.get(p, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _normalize_reg_no(reg_no):
    """Normalize register number to reduce OCR-induced duplicates across sources."""
    if not reg_no:
        return ""

    text = str(reg_no).upper().strip()
    text = re.sub(r'[^A-Z0-9]', '', text)

    if len(text) < 3:
        return text

    prefix = text[:2]
    suffix = text[2:]

    # Common OCR confusions in numeric section.
    trans = str.maketrans({
        'O': '0',
        'I': '1',
        'L': '1',
        'S': '5',
        'Z': '2',
        'B': '8',
    })
    suffix = suffix.translate(trans)

    return prefix + suffix


def _fuzzy_prefix_match(reg_no, dominant_prefix):
    """
    Check if register number's prefix is close to dominant prefix (allowing OCR corruption).
    Example: RA21 vs RA24 are considered close (same alpha part, numeric part within ±3).
    """
    if not dominant_prefix or not reg_no:
        return False
    
    if reg_no.startswith(dominant_prefix):
        return True  # Exact match, always OK
    
    # Check fuzzy: first 2 chars must match (e.g., both RA), numeric parts within range
    if len(reg_no) >= 4 and len(dominant_prefix) >= 4:
        if reg_no[:2] == dominant_prefix[:2]:  # Both start with RA or BA
            try:
                reg_num = int(reg_no[2:4])
                dom_num = int(dominant_prefix[2:4])
                if abs(reg_num - dom_num) <= 3:  # Allow ±3 variation (e.g., RA21-RA24)
                    return True
            except (ValueError, IndexError):
                pass
    
    return False


def _merge_attendance_results(candidates):
    """
    Merge extractor outputs by reg number and de-duplicate subject codes.
    Conservative filter to avoid OCR-noise IDs:
    - Keep if seen in >=2 sources, OR
    - Keep if strict-format reg and matches dominant prefix (fuzzy ±3 allowed).
    """
    merged = {}
    dominant_prefix = _dominant_reg_prefix(candidates, prefix_len=4)

    for source_name, rows in candidates:
        for row in rows or []:
            reg_no = _normalize_reg_no(row.get('reg_number'))
            if not reg_no:
                continue

            name = row.get('name') or "Unknown"
            subjects = row.get('subjects') or []

            if reg_no not in merged:
                merged[reg_no] = {
                    'reg_number': reg_no,
                    'name': name,
                    'subjects': [],
                    '_sources': set(),
                }

            merged[reg_no]['_sources'].add(source_name)

            if merged[reg_no]['name'] == "Unknown" and name != "Unknown":
                merged[reg_no]['name'] = name

            existing = {s['subject_code']: s for s in merged[reg_no]['subjects'] if 'subject_code' in s}
            for subj in subjects:
                code = subj.get('subject_code')
                pct = subj.get('attendance_percentage')
                if not code or pct is None:
                    continue

                if code not in existing:
                    item = {'subject_code': code, 'attendance_percentage': pct}
                    merged[reg_no]['subjects'].append(item)
                    existing[code] = item
                else:
                    existing[code]['attendance_percentage'] = min(existing[code]['attendance_percentage'], pct)

    filtered = []
    for reg_no, row in merged.items():
        source_count = len(row.get('_sources', set()))
        strict = _is_strict_reg(reg_no)
        prefix_ok = _fuzzy_prefix_match(reg_no, dominant_prefix)

        if source_count >= 2 or (strict and prefix_ok):
            row.pop('_sources', None)
            filtered.append(row)

    return sorted(filtered, key=lambda x: x.get('reg_number', ''))


def _extract_attendance_ocrmypdf(pdf_bytes):
    """
    Extract attendance data using ocrmypdf + Tesseract OCR.
    Returns list of student records with low attendance, or empty list if extraction fails.
    """
    try:
        import tempfile
        import ocrmypdf

        # Resolve Tesseract path and add to system PATH
        tesseract_cmd = _resolve_tesseract_cmd()
        if not tesseract_cmd:
            logger.warning("Tesseract executable not found. Skipping ocrmypdf extraction.")
            return []

        # Configure environment: set TESSERACT_CMD and add tesseract directory to PATH
        os.environ['TESSERACT_CMD'] = tesseract_cmd
        tesseract_dir = os.path.dirname(tesseract_cmd)
        current_path = os.environ.get('PATH', '')
        if tesseract_dir not in current_path:
            os.environ['PATH'] = tesseract_dir + os.pathsep + current_path
        
        logger.info(f"Configuring ocrmypdf to use Tesseract: {tesseract_cmd}")

        students_data = {}

        # Create temporary file for OCR processing
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_input:
            tmp_input.write(pdf_bytes)
            tmp_input_path = tmp_input.name

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_output:
            tmp_output_path = tmp_output.name

        try:
            # Run OCR with Tesseract engine
            logger.info("Processing PDF with ocrmypdf + Tesseract...")
            ocrmypdf.ocr(
                tmp_input_path,
                tmp_output_path,
                language='eng',
                deskew=True,
                optimize=0,
                progress_bar=False,
                quiet=True
            )

            # Extract text from OCR'd PDF
            logger.info("Extracting text from OCR'd PDF...")
            full_text = ""
            with pdfplumber.open(tmp_output_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    text = page.extract_text()
                    if text:
                        full_text += text + "\n"

            if len(full_text.strip()) < 50:
                logger.warning(f"ocrmypdf extracted insufficient text ({len(full_text)} chars)")
                return []

            logger.info(f"ocrmypdf extracted {len(full_text)} characters")

            # Parse the text to extract attendance data
            normalized = _normalize_ocr_text(full_text)
            _parse_page_text(normalized, students_data)

            result = list(students_data.values())
            logger.info(f"ocrmypdf found {len(result)} students with low attendance")
            return result

        finally:
            # Clean up temporary files
            if os.path.exists(tmp_input_path):
                try:
                    os.remove(tmp_input_path)
                except:
                    pass
            if os.path.exists(tmp_output_path):
                try:
                    os.remove(tmp_output_path)
                except:
                    pass

    except ImportError:
        logger.warning("ocrmypdf library not installed. Skipping ocrmypdf extraction.")
        return []
    except Exception as e:
        logger.error(f"ocrmypdf extraction failed: {e}")
        return []


def _resolve_tesseract_cmd():
    """Resolve tesseract executable path on Windows/Linux/macOS."""
    candidates = []

    env_cmd = os.getenv("TESSERACT_CMD")
    if env_cmd:
        candidates.append(env_cmd)

    which_cmd = shutil.which("tesseract")
    if which_cmd:
        candidates.append(which_cmd)

    # Common Windows install paths
    candidates.extend([
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ])

    for cmd in candidates:
        if cmd and os.path.exists(cmd):
            return cmd

    return None


def _extract_attendance_tesseract(pdf_bytes):
    """
    Direct local OCR using pytesseract on rendered PDF pages.
    Used when native parsing and ocrmypdf extraction don't return results.
    """
    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract is not installed. Skipping direct Tesseract OCR extraction.")
        return []

    tesseract_cmd = _resolve_tesseract_cmd()
    if not tesseract_cmd:
        logger.warning("Tesseract executable not found. Set TESSERACT_CMD or add tesseract to PATH.")
        return []

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    logger.info(f"Using Tesseract executable: {tesseract_cmd}")

    students_data = {}
    last_subjects = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, 1):
                logger.info(f"Tesseract OCR page {page_num}/{total}...")

                best_text = ""
                best_score = -1
                for dpi in (250, 320):
                    try:
                        page_img = page.to_image(resolution=dpi).original
                        text = pytesseract.image_to_string(page_img, lang='eng', config='--psm 6')
                        normalized = _normalize_ocr_text(text)
                        regs = len(_REG_RE.findall(normalized))
                        pcts = len(_PCT_RE.findall(normalized))
                        score = (regs * 100) + pcts

                        if score > best_score:
                            best_score = score
                            best_text = text
                    except Exception as e:
                        logger.warning(f"  Page {page_num} OCR failed at {dpi}dpi: {e}")

                if not best_text.strip():
                    logger.warning(f"  Page {page_num}: no text from local Tesseract OCR")
                    continue

                parsed = _parse_page_text(best_text, students_data, last_subjects)
                if parsed:
                    last_subjects = parsed

    except Exception as e:
        logger.error(f"Direct Tesseract OCR extraction failed: {e}")
        return []

    result = list(students_data.values())
    logger.info(f"Direct Tesseract OCR found {len(result)} students with low attendance")
    return result



def _normalize_ocr_text(text):
    """Fix common OCR misreads before parsing."""
    text = text.replace('|', ' ')
    # Bullet / dot prefixes before subject codes
    text = re.sub(r'[\u2022\u00b7\*]\s*', '', text)
    # '2I' / 'I' misread as '21' in subject codes
    text = re.sub(r'\b2I([A-Z]{3}\d{3}[A-Z])', r'21\1', text)
    text = re.sub(r'\bI(\d[A-Z]{3}\d{3}[A-Z])', r'2\1', text)
    # Comma-as-decimal: 75,00 → 75.00
    text = re.sub(r'\b(\d{1,3}),(\d{2})\b', r'\1.\2', text)
    return text


# ─────────────────────────── shared compiled patterns ────────────────────────
_SUBJ_RE  = re.compile(r'(?:\d{2}[A-Z]{3}|[A-Z]{2,4}\d{1,2})\d{3}[A-Z](?:\([A-Z]\))?')
_REG_RE   = re.compile(r'(?:RA|BA)\d{10,15}')
_PCT_RE   = re.compile(r'\b\d{1,3}\.\d{2}\b')
_PURE_PCT = re.compile(r'^\d{1,3}\.\d{2}$')
_REG_FUZZY_RE = re.compile(r'(?:RA|BA)[A-Z0-9]{10,18}')

_SKIP_WORDS = {
    'LAB', 'SLOTS', 'SLOT', 'NAME', 'S.NO', 'PHOTO', 'ID', 'NOTE', 'NSS',
    'NCC', 'FACULTY', 'ADVISOR', 'CONSOLIDATED', 'ACADEMIC', 'STATUS',
    'KINDLY', 'STUDENT_ACADEMIC_STATUS', 'RANGE', 'FORMAT', 'UPLOADED', 'WRONG',
}

def _is_subj_line(line):
    return bool(_SUBJ_RE.search(line))


def _get_subjects(line):
    return _SUBJ_RE.findall(line)


def _base_code(code):
    return re.sub(r'\([A-Z]\)$', '', code).strip()


def _extract_reg_no(line):
    """Extract register number from noisy OCR lines using strict + fuzzy matching."""
    if not line:
        return None

    strict = _REG_RE.search(line)
    if strict:
        return strict.group()

    compact = re.sub(r'\s+', '', line.upper())
    fuzzy = _REG_FUZZY_RE.search(compact)
    if not fuzzy:
        return None

    candidate = fuzzy.group()
    prefix = candidate[:2]
    suffix = candidate[2:]
    suffix = suffix.translate(str.maketrans({
        'O': '0',
        'I': '1',
        'L': '1',
        'S': '5',
        'Z': '2',
        'B': '8',
        'G': '6',
        'Q': '0',
    }))
    suffix = re.sub(r'[^0-9]', '', suffix)

    if 10 <= len(suffix) <= 15:
        return prefix + suffix
    return None


def _get_pcts(line):
    return [float(x) for x in _PCT_RE.findall(line)]


def _looks_like_name(line):
    if not line or re.match(r'^[\d\s.%\-()/]+$', line):
        return False
    words = line.split()
    upper = [w for w in words if w.isupper() and len(w) >= 2]
    skip  = any(w.upper() in _SKIP_WORDS for w in words)
    return len(upper) >= 1 and not skip


# ─────────────────────────── student data updater ────────────────────────────
def _add_student(students_data, reg_no, name, subjects, percentages):
    """Match subjects → percentages positionally; record those < 75%."""
    if not subjects:
        logger.warning(f"{reg_no}: no subjects to match")
        return

    low = []
    for idx, subj in enumerate(subjects):
        bc = _base_code(subj)
        if idx < len(percentages):
            pct = percentages[idx]
            if pct < 75.0:
                low.append({'subject_code': bc, 'attendance_percentage': pct})
        else:
            logger.warning(f"{reg_no}: missing pct for {bc} (idx {idx}), "
                           f"only {len(percentages)} for {len(subjects)} subjects")

    if not low:
        return

    if reg_no not in students_data:
        students_data[reg_no] = {'reg_number': reg_no, 'name': name, 'subjects': low}
    else:
        existing = {s['subject_code'] for s in students_data[reg_no]['subjects']}
        for s in low:
            if s['subject_code'] not in existing:
                students_data[reg_no]['subjects'].append(s)
                existing.add(s['subject_code'])

    for s in low:
        logger.info(f"Low attendance: {reg_no} ({name}) – "
                    f"{s['subject_code']}: {s['attendance_percentage']}%")




# ─────────────────────────── per-page entry point ────────────────────────────
def _parse_page_text(page_text, students_data, last_known_subjs=None):
    """
    Unified parser for all OCR layout variants using PROXIMITY MATCHING.

    KEY INSIGHT: OCR only captures bold/red (low) percentages reliably.
    Each low percentage appears on the same line or immediately after its subject code.
    We use PROXIMITY matching instead of positional matching.

    Algorithm:
    1. Split page into per-student blocks (between consecutive reg numbers)
    2. Within each block, scan lines for subject codes
    3. For each subject code, check the same line and next 2 lines for a percentage
    4. If percentage found AND < 75, record as low attendance
    """
    page_text = _normalize_ocr_text(page_text)
    lines = [l.strip() for l in page_text.split('\n') if l.strip()]

    # Find all reg number positions
    regs = []
    for i, line in enumerate(lines):
        reg_no = _extract_reg_no(line)
        if reg_no:
            regs.append((i, reg_no, line))

    if not regs:
        return None

    def _extract_name_from_block(reg_line_local, block_lines_local):
        # 1) Try inline name on the same reg line.
        reg_no_local = _extract_reg_no(reg_line_local)
        if reg_no_local:
            idx = reg_line_local.find(reg_no_local)
            if idx >= 0:
                tail = reg_line_local[idx + len(reg_no_local):].strip()
            else:
                # Fallback for fuzzy OCR IDs where exact sequence may differ in raw line.
                tail = re.sub(r'^(?:\s*(?:RA|BA)[A-Z0-9\s]{10,22})', '', reg_line_local).strip()
            tail = re.sub(r'^\d+\s*', '', tail).strip()
            if _looks_like_name(tail):
                return tail

        # 2) Try next lines by removing percentages and subject codes.
        for raw in block_lines_local[1:5]:
            candidate = re.sub(_PCT_RE, ' ', raw)
            candidate = re.sub(_SUBJ_RE, ' ', candidate)
            candidate = re.sub(_REG_RE, ' ', candidate)
            candidate = re.sub(r'\b\d+\b', ' ', candidate)
            candidate = re.sub(r'\s+', ' ', candidate).strip(' -|')
            if _looks_like_name(candidate):
                return candidate

        return "Unknown"

    # Process each student block
    recent_subjects = list(last_known_subjs or [])
    for bi, (li, reg_no, reg_line) in enumerate(regs):
        bend = regs[bi + 1][0] if bi + 1 < len(regs) else len(lines)
        block_lines = lines[li:bend]

        name = _extract_name_from_block(reg_line, block_lines)

        # Build ordered subject and percentage streams from the student block.
        MAX_SUBJECTS_PER_STUDENT = 10  # 9 slots + 1 lab, never more than this

        tokens = []
        subjects = []
        percentages = []
        for line in block_lines:
            for code in _get_subjects(line):
                if len(subjects) >= MAX_SUBJECTS_PER_STUDENT:
                    break
                base = _base_code(code)
                subjects.append(base)
                tokens.append(('subj', base))
            for pct in _get_pcts(line):
                percentages.append(pct)
                tokens.append(('pct', pct))

        # Some rows carry only reg+name+percentages; reuse previous row's subject order.
        if not subjects and recent_subjects and percentages:
            subjects = list(recent_subjects)
            for code in subjects:
                tokens.append(('subj', code))

        if subjects:
            recent_subjects = list(subjects)

        if not subjects or not percentages:
            continue

        low_subjects = []

        # Layout A (common in native extraction): percentages line appears before subjects.
        first_subj_idx = next((idx for idx, t in enumerate(tokens) if t[0] == 'subj'), None)
        first_pct_idx = next((idx for idx, t in enumerate(tokens) if t[0] == 'pct'), None)
        if first_subj_idx is not None and first_pct_idx is not None and first_pct_idx < first_subj_idx:
            pair_count = min(len(subjects), len(percentages))
            for idx in range(pair_count):
                pct = percentages[idx]
                if pct < 75.0:
                    low_subjects.append({'subject_code': subjects[idx], 'attendance_percentage': pct})
        else:
            # Layout B (OCR-like): use local proximity mapping by token order.
            i = 0
            while i < len(tokens):
                if tokens[i][0] == 'subj':
                    subj_code = tokens[i][1]
                    for j in range(i + 1, min(i + 12, len(tokens))):
                        if tokens[j][0] == 'pct':
                            pct = tokens[j][1]
                            if pct < 75.0:
                                low_subjects.append({'subject_code': subj_code, 'attendance_percentage': pct})
                            i = j
                            break
                i += 1

            # Fallback when proximity pairing fails due OCR noise between tokens.
            if not low_subjects and subjects and percentages:
                pair_count = min(len(subjects), len(percentages))
                for idx in range(pair_count):
                    pct = percentages[idx]
                    if pct < 75.0:
                        low_subjects.append({'subject_code': subjects[idx], 'attendance_percentage': pct})

        # De-duplicate subjects within a student while preserving first match.
        if low_subjects:
            dedup = []
            seen_codes = set()
            for item in low_subjects:
                code = item['subject_code']
                if code in seen_codes:
                    continue
                dedup.append(item)
                seen_codes.add(code)
            low_subjects = dedup

        if low_subjects:
            if reg_no not in students_data:
                students_data[reg_no] = {'reg_number': reg_no, 'name': name, 'subjects': low_subjects}
            else:
                existing = {s['subject_code'] for s in students_data[reg_no]['subjects']}
                for s in low_subjects:
                    if s['subject_code'] not in existing:
                        students_data[reg_no]['subjects'].append(s)
                        existing.add(s['subject_code'])
                        
            for s in low_subjects:
                logger.info(f"Low attendance: {reg_no} ({name}) – "
                            f"{s['subject_code']}: {s['attendance_percentage']}%")

    return recent_subjects

# ─────────────────────────── public native entry point ───────────────────────
def _parse_attendance_native(full_text):
    """Entry point for native-text PDFs."""
    students_data = {}
    _parse_page_text(full_text, students_data)
    result = list(students_data.values())
    logger.info(f"Found {len(result)} students with low attendance")
    return result


def _parse_attendance_native_OLD(full_text):  # kept for reference, not called
    """REPLACED – see _parse_page_text / _parse_layout_a / _parse_layout_b."""
    pass


OCR_API_KEY = "K81654833188957"


def _ocr_page_to_text(page_image_bytes, engine=2):
    """
    Send a single rendered page image to OCR.space and return the text.
    engine=1 (default OCR), engine=2 (OCR+), engine=3 (OCR Pro)
    """
    import requests
    try:
        resp = requests.post(
            'https://api.ocr.space/parse/image',
            files={'file': ('page.png', page_image_bytes, 'image/png')},
            data={
                'apikey': OCR_API_KEY,
                'language': 'eng',
                'isOverlayRequired': False,
                'detectOrientation': True,
                'scale': True,
                'OCREngine': engine,
            },
            timeout=90,
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get('IsErroredOnProcessing'):
            logger.error(f"OCR.space error: {result.get('ErrorMessage')}")
            return ""

        pages = result.get('ParsedResults') or []
        return "\n".join(p.get('ParsedText', '') for p in pages)

    except Exception as e:
        logger.error(f"OCR.space page request failed: {e}")
        return ""


def _ocr_pdf_to_text(pdf_bytes):
    """
    Render each page as a PNG image and OCR it one at a time.
    Returns the full concatenated text from all pages.
    """
    from io import BytesIO as _BytesIO

    full_text = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, 1):
                logger.info(f"OCR page {page_num}/{total}...")
                try:
                    img = page.to_image(resolution=300)
                    buf = _BytesIO()
                    img.original.save(buf, format='PNG')
                    buf.seek(0)
                    text = _ocr_page_to_text(buf.read())
                    if text:
                        full_text += text + "\n"
                        logger.info(f"  Page {page_num}: got {len(text)} chars")
                    else:
                        logger.warning(f"  Page {page_num}: no text returned")
                except Exception as e:
                    logger.error(f"  Page {page_num} render/OCR error: {e}")
                    continue
    except Exception as e:
        logger.error(f"_ocr_pdf_to_text failed: {e}")

    logger.info(f"OCR complete. Total chars: {len(full_text)}")
    return full_text


def _is_column_reading_failure(text, regs_found):
    """Returns True if OCR read the page column-by-column and dropped percentages."""
    if not regs_found or not text:
        return False
    pcts = _PCT_RE.findall(_normalize_ocr_text(text))
    # If we found reg numbers but fewer percentages than students, it's a failure
    return len(pcts) < len(regs_found)


def _extract_attendance_ocr(pdf_bytes):
    """
    OCR fallback — renders each page as PNG at 200 dpi, OCRs it with
    Engine 2, then parses with full layout detection.

    Smart retry logic: if a page shows column-by-column reading failure
    (regs found but mostly no percentages), re-render at 300 dpi and retry OCR.
    """
    from io import BytesIO as _BytesIO

    students_data = {}

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, 1):
                logger.info(f"OCR page {page_num}/{total}...")
                try:
                    img = page.to_image(resolution=200)
                    buf = _BytesIO()
                    img.original.save(buf, format='PNG')
                    img_bytes = buf.getvalue()
                    text = _ocr_page_to_text(img_bytes, engine=2)

                    if text:
                        normalized = _normalize_ocr_text(text)
                        regs_on_page = [r for r in (_extract_reg_no(l) for l in normalized.splitlines()) if r]
                        
                        # Detect column-by-column reading failure: regs found but percentages missing
                        pcts_on_page = _PCT_RE.findall(normalized)
                        if regs_on_page and len(pcts_on_page) < len(regs_on_page):
                            logger.warning(
                                f"  Page {page_num}: column-reading failure detected "
                                f"({len(regs_on_page)} regs, only {len(pcts_on_page)} pcts) "
                                f"— retrying at 300dpi"
                            )
                            img_hi = page.to_image(resolution=300)
                            buf_hi = _BytesIO()
                            img_hi.original.save(buf_hi, format='PNG')
                            text2 = _ocr_page_to_text(buf_hi.getvalue(), engine=2)
                            if text2:
                                pcts2 = _PCT_RE.findall(_normalize_ocr_text(text2))
                                if len(pcts2) > len(pcts_on_page):
                                    logger.info(f"  Page {page_num}: 300dpi retry improved: {len(pcts_on_page)} → {len(pcts2)} pcts")
                                    text = text2
                                else:
                                    logger.warning(f"  Page {page_num}: 300dpi retry did not improve, keeping original")

                    if text:
                        logger.info(f"  Page {page_num}: {len(text)} chars")
                        _parse_page_text(text, students_data)
                    else:
                        logger.warning(f"  Page {page_num}: no text from OCR")
                except Exception as e:
                    logger.error(f"  Page {page_num} error: {e}")
                    continue
    except Exception as e:
        logger.error(f"_extract_attendance_ocr failed: {e}")

    result = list(students_data.values())
    logger.info(f"OCR found {len(result)} students with low attendance")
    return result

