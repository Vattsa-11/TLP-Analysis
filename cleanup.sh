#!/bin/bash
# Remove debug files
rm -f DEBUG_ANALYSIS.txt debug_ocr.py debug_ocr_output.txt debug_output.txt _debug_*.py _test_*.py test_300dpi.txt
rm -f run_server.py app_launcher.py server.log test_extract.py test_attendance_debug.py test_detailed_debug.py test_native.py verify_attendance.py

# Remove build artifacts
rm -rf build/ dist/ __pycache__/ *.spec
rm -f *.log
rm -f .pytest_cache/

# Remove test/sample PDFs and results
rm -rf fwdtlpanalysis/
rm -rf verified_reports/
rm -f *.xlsx
rm -f result_*.xlsx

# Remove Python cache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete

echo "Cleanup complete!"
