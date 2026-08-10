# IRIDA Next Batch Uploader

A Python CLI tool for batch-uploading sequencing files to [IRIDA Next](https://github.com/phac-nml/irida-next) via its GraphQL API.

## How It Works

IRIDA Next exposes a **GraphQL API** at `/graphql` backed by **Rails Active Storage direct uploads**. The upload flow per file is:

1. **`createDirectUpload`** mutation → returns a signed URL, HTTP headers, and a `signed_blob_id`
2. **PUT** the file binary to that signed URL (direct to the storage backend — disk, S3, GCS, etc.)
3. **`attachFilesToSample`** or **`attachFilesToProject`** mutation with the `signed_blob_id` list

Authentication uses **HTTP Basic Auth** with your email as the username and a **Personal Access Token** (PAT) as the password.

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Samplesheet  │     │  createSample    │     │  createDirect   │
│  Metadata CSV │────▶│  (GraphQL)       │────▶│  Upload (GraphQL)│
│  or Auto-     │     │  → sample.puid   │     │  → url, blob_id │
│  discover     │     └──────────────────┘     └────────┬────────┘
└─────────────┘                                         │
                                                ┌────────▼────────┐
                                                │  PUT binary to  │
                                                │  signed URL     │
                                                └────────┬────────┘
                                                         │
                                                ┌────────▼────────┐
                                                │  attachFiles     │
                                                │  ToSample        │
                                                │  (GraphQL)        │
                                                └────────┬────────┘
                                                         │
                                                ┌────────▼────────┐
                                                │  updateSample   │
                                                │  Metadata       │
                                                │  (GraphQL)       │
                                                └─────────────────┘
```

## Prerequisites

```bash
pip install requests pytest
```

You need a **Personal Access Token** from IRIDA Next:
1. Log in to your IRIDA Next instance
2. Go to Profile → Access Tokens
3. Create a token with appropriate scopes

## Usage

### Option 1: Samplesheet (TSV)

Create a tab-separated file:

```
sample_name	file1	file2
sample1	sample1_R1.fastq.gz	sample1_R2.fastq.gz
sample2	sample2_R1.fastq.gz	sample2_R2.fastq.gz
sample3	sample3.fastq.gz
```

- Column 1: sample name (will be created in the project)
- Columns 2+: file paths (relative to `--input-dir`)
- Paired-end: 2 file columns; single-end: 1 file column

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --samplesheet samples.tsv \
    --input-dir /data/runs/run001
```

### Option 2: Metadata file (CSV/TSV with named columns)

Upload files and apply sample metadata in one pass. Any CSV or TSV file works — you specify which column is the sample name and which columns contain file paths. All remaining columns become sample metadata applied via the `updateSampleMetadata` GraphQL mutation.

Example CSV (`samples_with_metadata.csv`):

```csv
sample_name,forward_read,reverse_read,organism,isolate_id,serotype,collection_date
sample1,s1_R1.fastq.gz,s1_R2.fastq.gz,Salmonella enterica,ST-001,Enteritidis,2024-01-15
sample2,s2_R1.fastq.gz,s2_R2.fastq.gz,Escherichia coli,ST-002,O157:H7,2024-01-20
sample3,s3.fastq.gz,,Listeria monocytogenes,ST-003,4b,2024-02-01
```

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --metadata-file samples_with_metadata.csv \
    --sample-column sample_name \
    --file-columns forward_read reverse_read \
    --input-dir /data/runs/run001
```

- `--sample-column`: which column holds the sample name (default: `sample_name`)
- `--file-columns`: which columns hold file paths (default: `file1 file2`)
- All other columns → sample metadata via `updateSampleMetadata`
- Empty file/metadata values are skipped
- Delimiter auto-detected from file extension (`.csv` → comma, `.tsv` → tab)

### Option 3: Auto-discover paired-end files

If your directory contains `*_R1.fastq.gz` / `*_R2.fastq.gz` (or `*_1.fastq.gz` / `*_2.fastq.gz`) pairs:

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --auto-discover \
    --input-dir /data/runs/run001
```

Files are auto-paired by name prefix. Non-paired files become single-end samples.

### Option 4: Attach files directly to a project

No sample creation — just attach files to the project namespace (for reports, reference genomes, etc.):

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --attach-to-project \
    --input-dir /data/reports
```

### Parallel uploads

Upload multiple samples concurrently:

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --samplesheet samples.tsv \
    --input-dir /data/runs/run001 \
    --workers 4
```

### Dry run

Preview what would be uploaded without making any changes:

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --auto-discover \
    --input-dir /data/runs/run001 \
    --dry-run
```

## Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--url` | ✅ | IRIDA Next base URL |
| `--email` | ✅ | User email for auth |
| `--token` | ✅ | Personal Access Token |
| `--project-id` | one of | Project GraphQL Node ID (`gid://irida/Project/...`) |
| `--project-puid` | one of | Project PUID (`INXT_PRJ_...`) |
| `--samplesheet` | one of | TSV file: `sample_name<TAB>file1[<TAB>file2]` |
| `--metadata-file` | one of | CSV or TSV file with sample names, file paths, and metadata columns |
| `--sample-column` | | Column name in `--metadata-file` for the sample name (default: `sample_name`) |
| `--file-columns` | | Column name(s) in `--metadata-file` for file paths (default: `file1 file2`). All other columns become sample metadata. |
| `--auto-discover` | one of | Auto-discover paired-end FASTQ files in `--input-dir` |
| `--attach-to-project` | one of | Attach files to project instead of creating samples |
| `--input-dir` | | Base directory for resolving file paths (default: `.`) |
| `--description` | | Description for created samples |
| `--no-verify-ssl` | | Disable SSL certificate verification |
| `--workers` | | Number of parallel upload workers (default: 1) |
| `--dry-run` | | List what would be uploaded without changes |

## File Requirements

- All files should be **gzip-compressed** (`.gz`). IRIDA Next accepts any binary, but gzipped FASTQ is the standard.
- Paired-end files are auto-detected by `_R1` / `_R2` (or `_1` / `_2`) in the filename.
- Both `.fastq.gz` and `.fq.gz` extensions are recognized.

## API Flow Detail

For each sample in the upload list:

```
1. createSample(name, projectPuid) → sample.id, sample.puid
2. For each file in the sample:
   a. Compute MD5 checksum (base64)
   b. createDirectUpload(filename, byteSize, checksum, contentType)
      → { url, headers, blobId, signedBlobId }
   c. PUT file binary → url with headers
3. attachFilesToSample(files=[signedBlobId, ...], samplePuid) → status
4. updateSampleMetadata(metadata={key: value, ...}, samplePuid) → status
```

## Testing

```bash
# Install test dependencies
pip install pytest

# Run tests
pytest

# Run with verbose output
pytest -v
```

Tests cover:
- MD5 base64 checksum computation (known content, empty file, large file)
- Auto-discover paired-end detection (R1/R2, _1/_2, .fq.gz, mixed, empty dir, ordering)
- Samplesheet TSV parsing (paired, single, empty lines, missing columns)
- Client initialization (auth header, URL normalization, content type)
- GraphQL execution (success, errors)
- Upload flow (full success, create failure, file not found, PUT failure)
- Direct upload (response parsing, PUT success/failure)
- Attach files (success, errors)
- UploadResult dataclass

## Project Structure

```
irida-next-batch-uploader/
├── irida_batch_uploader.py   # Main CLI tool
├── tests/
│   └── test_uploader.py      # Unit tests (45 tests)
├── example_samplesheet.tsv   # Example TSV samplesheet
├── example_metadata.csv      # Example CSV with metadata columns
├── pytest.ini                # Pytest configuration
├── CHANGELOG.md              # Version history
└── README.md                 # This file
```

## Git Flow

This repository follows the [Git Flow](https://nvie.com/posts/a-successful-git-branching-model/) branching model:

- **`main`** — production-ready releases
- **`develop`** — integration branch for features
- **`feat/*`** — feature branches, merged to `develop` via PR
- **`release/*`** — release preparation, merged to `main`

## License

MIT