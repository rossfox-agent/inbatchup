#!/bin/bash
#
# Full end-to-end walkthrough: IRIDA Next Batch Uploader
#
# This script demonstrates the complete workflow using the example files.
# It does NOT actually upload to a live IRIDA Next instance — it uses --dry-run
# so you can see what would happen. Remove --dry-run to perform real uploads.
#
# Prerequisites:
#   pip install requests
#
# Usage:
#   # Edit the variables below, then run:
#   bash examples/run_upload.sh
#
#   # Or override from the command line:
#   IRIDA_URL=https://irida.my.lab IRIDA_EMAIL=me@lab.ca IRIDA_TOKEN=INXT_PAT_xxx \
#       IRIDA_PROJECT_PUID=INXT_PRJ_AAAAAAAAAA bash examples/run_upload.sh

set -euo pipefail

# ── Configuration (edit these or set as environment variables) ────────────────

IRIDA_URL="${IRIDA_URL:-https://irida.yourlab.ca}"
IRIDA_EMAIL="${IRIDA_EMAIL:-you@lab.ca}"
IRIDA_TOKEN="${IRIDA_TOKEN:-INXT_PAT_xxxxx}"
IRIDA_PROJECT_PUID="${IRIDA_PROJECT_PUID:-INXT_PRJ_AAAAAAAAAA}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
UPLOADER="$PARENT_DIR/inbatchup.py"
DATA_DIR="$SCRIPT_DIR/data"

echo "═══════════════════════════════════════════════════════════════"
echo "  IRIDA Next Batch Uploader — End-to-End Walkthrough"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Server:    $IRIDA_URL"
echo "  Email:     $IRIDA_EMAIL"
echo "  Project:   $IRIDA_PROJECT_PUID"
echo "  Data dir:  $DATA_DIR"
echo ""

# ── Step 0: Create dummy data files for the walkthrough ──────────────────────
echo "── Step 0: Creating dummy sequencing files ────────────────────"
mkdir -p "$DATA_DIR"

# Create fake .fastq.gz files (real ones would be gzipped FASTQ data)
SAMPLES=(
  "24-001_S1_L001_R1_001.fastq.gz|24-001_S1_L001_R2_001.fastq.gz"
  "24-002_S2_L001_R1_001.fastq.gz|24-002_S2_L001_R2_001.fastq.gz"
  "24-003_S3_L001_R1_001.fastq.gz|24-003_S3_L001_R2_001.fastq.gz"
  "24-004_S4_L001_R1_001.fastq.gz|24-004_S4_L001_R2_001.fastq.gz"
  "24-005.fastq.gz|"
)

for pair in "${SAMPLES[@]}"; do
  IFS='|' read -r r1 r2 <<< "$pair"
  if [ ! -f "$DATA_DIR/$r1" ]; then
    echo "@SEQ_ID
ACGTACGTACGT
+
IIIIIIIIIIII" | gzip > "$DATA_DIR/$r1"
    echo "  ✓ Created $r1"
  fi
  if [ -n "$r2" ] && [ ! -f "$DATA_DIR/$r2" ]; then
    echo "@SEQ_ID
ACGTACGTACGT
+
IIIIIIIIIIII" | gzip > "$DATA_DIR/$r2"
    echo "  ✓ Created $r2"
  fi
done
echo ""

# ── Step 1: Dry-run with basic TSV samplesheet ────────────────────────────────
echo "── Step 1: Dry-run with basic TSV samplesheet ────────────────"
echo ""
python3 "$UPLOADER" \
  --url "$IRIDA_URL" \
  --email "$IRIDA_EMAIL" \
  --token "$IRIDA_TOKEN" \
  --project-puid "$IRIDA_PROJECT_PUID" \
  --samplesheet "$SCRIPT_DIR/samples_basic.tsv" \
  --input-dir "$DATA_DIR" \
  --dry-run
echo ""

# ── Step 2: Dry-run with CSV + metadata ───────────────────────────────────────
echo "── Step 2: Dry-run with CSV + metadata ───────────────────────"
echo ""
python3 "$UPLOADER" \
  --url "$IRIDA_URL" \
  --email "$IRIDA_EMAIL" \
  --token "$IRIDA_TOKEN" \
  --project-puid "$IRIDA_PROJECT_PUID" \
  --metadata-file "$SCRIPT_DIR/samples_with_metadata.csv" \
  --sample-column sample_name \
  --file-columns forward_read reverse_read \
  --input-dir "$DATA_DIR" \
  --dry-run
echo ""

# ── Step 3: Dry-run with auto-discover ────────────────────────────────────────
echo "── Step 3: Dry-run with auto-discover ────────────────────────"
echo ""
python3 "$UPLOADER" \
  --url "$IRIDA_URL" \
  --email "$IRIDA_EMAIL" \
  --token "$IRIDA_TOKEN" \
  --project-puid "$IRIDA_PROJECT_PUID" \
  --auto-discover \
  --input-dir "$DATA_DIR" \
  --dry-run
echo ""

# ── Step 4: Real upload with metadata (uncomment to run) ─────────────────────
echo "── Step 4: Real upload with metadata ──────────────────────────"
echo ""
echo "  Uncomment the block below to perform a real upload:"
echo ""
echo "  # python3 \"$UPLOADER\" \\"
echo "  #     --url \"$IRIDA_URL\" \\"
echo "  #     --email \"$IRIDA_EMAIL\" \\"
echo "  #     --token \"$IRIDA_TOKEN\" \\"
echo "  #     --project-puid \"$IRIDA_PROJECT_PUID\" \\"
echo "  #     --metadata-file \"$SCRIPT_DIR/samples_with_metadata.csv\" \\"
echo "  #     --sample-column sample_name \\"
echo "  #     --file-columns forward_read reverse_read \\"
echo "  #     --input-dir \"$DATA_DIR\" \\"
echo "  #     --workers 3"
echo ""
echo "  This would:"
echo "    1. Create 5 samples in the project"
echo "    2. Upload 9 FASTQ files (4 paired-end + 1 single-end) via direct upload"
echo "    3. Attach files to each sample"
echo "    4. Apply metadata (organism, isolate_id, serotype, etc.) to each sample"
echo "    5. Use 3 parallel workers for faster uploads"
echo ""

# ── Cleanup ───────────────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════════════"
echo "  Walkthrough complete. Dummy files are in: $DATA_DIR"
echo "  Remove them with: rm -rf $DATA_DIR"
echo "═══════════════════════════════════════════════════════════════"