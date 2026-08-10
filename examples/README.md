# Examples

End-to-end examples for using the IRIDA Next Batch Uploader. Each example shows the input files, the command, and what happens on the IRIDA Next side.

## Directory Structure

```
examples/
├── samples_basic.tsv            # Basic TSV samplesheet (no metadata)
├── samples_with_metadata.csv    # CSV with metadata columns
├── data/                        # Simulated sequencing files
│   ├── 24-001_S1_L001_R1_001.fastq.gz
│   ├── 24-001_S1_L001_R2_001.fastq.gz
│   ├── 24-002_S2_L001_R1_001.fastq.gz
│   ├── 24-002_S2_L001_R2_001.fastq.gz
│   ├── ...
│   └── 24-005.fastq.gz
└── run_upload.sh                # Shell script showing the full workflow
```

## Example 1: Basic TSV Samplesheet

**Input:** `samples_basic.tsv`

```
sample_name	file1	file2
24-001	24-001_S1_L001_R1_001.fastq.gz	24-001_S1_L001_R2_001.fastq.gz
24-002	24-002_S2_L001_R1_001.fastq.gz	24-002_S2_L001_R2_001.fastq.gz
24-005	24-005.fastq.gz
```

- Tab-separated, no header row processing (first line is header)
- 5 samples: 4 paired-end, 1 single-end (Oxford Nanopore)
- No metadata — just sample names and files

**Command:**

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --samplesheet examples/samples_basic.tsv \
    --input-dir examples/data \
    --dry-run
```

**What happens in IRIDA Next:**

For each row:
1. `createSample(name="24-001", projectPuid="INXT_PRJ_AAAAAAAAAA")` → creates sample, returns PUID
2. `createDirectUpload(filename="24-001_S1_L001_R1_001.fastq.gz", ...)` → returns signed upload URL
3. PUT file binary to that URL
4. Repeat for `24-001_S1_L001_R2_001.fastq.gz`
5. `attachFilesToSample(files=["blob1", "blob2"], samplePuid="INXT_SAM_...")` → attaches both files

---

## Example 2: CSV with Metadata

**Input:** `samples_with_metadata.csv`

```csv
sample_name,forward_read,reverse_read,organism,isolate_id,serotype,collection_date,submitting_lab,platform
24-001,24-001_S1_L001_R1_001.fastq.gz,24-001_S1_L001_R2_001.fastq.gz,Salmonella enterica,SRC-001,Enteritidis,2024-01-15,NML Winnipeg,Illumina MiSeq
24-002,24-002_S2_L001_R1_001.fastq.gz,24-002_S2_L001_R2_001.fastq.gz,Escherichia coli,SRC-002,O157:H7,2024-01-20,NML Toronto,Illumina NextSeq
24-005,24-005.fastq.gz,,Campylobacter jejuni,SRC-005,,2024-02-15,NML Calgary,Oxford Nanopore MinION
```

- CSV with named columns
- `sample_name` → sample name column
- `forward_read`, `reverse_read` → file path columns
- All other columns (`organism`, `isolate_id`, `serotype`, `collection_date`, `submitting_lab`, `platform`) → sample metadata
- Note: `24-005` has no reverse read (empty `reverse_read` field) — treated as single-end
- Note: `24-005` has no serotype (empty field) — that metadata key is skipped for this sample

**Command:**

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --metadata-file examples/samples_with_metadata.csv \
    --sample-column sample_name \
    --file-columns forward_read reverse_read \
    --input-dir examples/data \
    --dry-run
```

**What happens in IRIDA Next:**

For each row:
1. `createSample(name="24-001", projectPuid="INXT_PRJ_AAAAAAAAAA")` → creates sample
2. Upload `forward_read` file via `createDirectUpload` → PUT
3. Upload `reverse_read` file via `createDirectUpload` → PUT
4. `attachFilesToSample(files=[...], samplePuid=...)` → attaches files
5. `updateSampleMetadata(metadata={"organism": "Salmonella enterica", "isolate_id": "SRC-001", "serotype": "Enteritidis", "collection_date": "2024-01-15", "submitting_lab": "NML Winnipeg", "platform": "Illumina MiSeq"}, samplePuid=...)` → applies metadata

For sample `24-005` (single-end, no serotype):
- Only one file uploaded (empty `reverse_read` skipped)
- Metadata omits `serotype` (empty value skipped)
- `updateSampleMetadata` receives: `{"organism": "Campylobacter jejuni", "isolate_id": "SRC-005", "collection_date": "2024-02-15", "submitting_lab": "NML Calgary", "platform": "Oxford Nanopore MinION"}`

---

## Example 3: Auto-discover Paired-End Files

No samplesheet needed — the tool scans a directory for `*_R1.fastq.gz` / `*_R2.fastq.gz` pairs.

**Command:**

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --auto-discover \
    --input-dir examples/data \
    --dry-run
```

The tool finds files matching `*_R1.fastq.gz` and `*_R2.fastq.gz`, pairs them by name prefix, and creates samples named after the prefix (e.g., `24-001_S1_L001` from `24-001_S1_L001_R1_001.fastq.gz`).

---

## Example 4: Parallel Upload with Workers

Upload 5 samples concurrently:

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --metadata-file examples/samples_with_metadata.csv \
    --sample-column sample_name \
    --file-columns forward_read reverse_read \
    --input-dir examples/data \
    --workers 4
```

---

## Example 5: Attach Files to a Project (No Samples)

For reference genomes, reports, or other non-sample files:

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --attach-to-project \
    --input-dir /data/project_files
```

Files are uploaded and attached directly to the project namespace — no sample objects created.

---

## Full End-to-End Walkthrough

See `run_upload.sh` for a complete script that demonstrates the full workflow with the example files.