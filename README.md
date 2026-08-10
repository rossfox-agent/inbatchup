# IRIDA Next Batch Uploader

Python CLI tool for batch-uploading sequencing files to [IRIDA Next](https://github.com/phac-nml/irida-next) via its GraphQL API.

## How It Works

IRIDA Next uses a **GraphQL API** at `/graphql` with **Rails Active Storage direct uploads**. The upload flow per file is:

1. **`createDirectUpload`** mutation → returns a signed URL + HTTP headers + `signed_blob_id`
2. **PUT** the file binary to that signed URL (direct to storage backend — disk, S3, etc.)
3. **`attachFilesToSample`** or **`attachFilesToProject`** mutation with the `signed_blob_id` list

Authentication uses **HTTP Basic Auth** with your email as the username and a **Personal Access Token** (PAT) as the password.

## Prerequisites

```bash
pip install requests
```

You need a Personal Access Token from IRIDA Next (Profile → Access Tokens → create with appropriate scopes).

## Usage

### Option 1: Samplesheet (TSV)

Create a tab-separated file:

```
sample_name	file1	file2
sample1	sample1_R1.fastq.gz	sample1_R2.fastq.gz
sample2	sample2_R1.fastq.gz	sample2_R2.fastq.gz
```

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --samplesheet samples.tsv \
    --input-dir /data/runs/run001
```

### Option 2: Auto-discover paired-end files

If your directory contains `*_R1.fastq.gz` / `*_R2.fastq.gz` pairs:

```bash
python irida_batch_uploader.py \
    --url https://irida.yourlab.ca \
    --email you@lab.ca \
    --token INXT_PAT_xxxxx \
    --project-puid INXT_PRJ_AAAAAAAAAA \
    --auto-discover \
    --input-dir /data/runs/run001
```

### Option 3: Attach files directly to a project

No sample creation — just attach files to the project namespace:

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

| Flag | Description |
|------|-------------|
| `--url` | IRIDA Next base URL (required) |
| `--email` | User email for auth (required) |
| `--token` | Personal Access Token (required) |
| `--project-id` | Project GraphQL Node ID (`gid://irida/Project/...`) |
| `--project-puid` | Project PUID (`INXT_PRJ_...`) |
| `--samplesheet` | TSV file: `sample_name<TAB>file1[<TAB>file2]` |
| `--input-dir` | Base directory for resolving file paths (default: `.`) |
| `--auto-discover` | Auto-discover paired-end FASTQ files in `--input-dir` |
| `--attach-to-project` | Attach files to project instead of creating samples |
| `--description` | Description for created samples |
| `--no-verify-ssl` | Disable SSL certificate verification |
| `--workers` | Number of parallel upload workers (default: 1) |
| `--dry-run` | List what would be uploaded without changes |

## File Requirements

- All files should be **gzip-compressed** (`.gz`). The `inext_cli` tool requires this; IRIDA Next itself accepts any binary, but gzipped FASTQ is the standard.
- Paired-end files are auto-detected by `_R1` / `_R2` (or `_1` / `_2`) in the filename.

## API Flow Detail

```
For each sample:
  1. createSample(name, projectPuid) → sample.id, sample.puid
  2. For each file:
     a. createDirectUpload(filename, byteSize, md5_checksum, contentType) → url, headers, signedBlobId
     b. PUT file binary → url with headers
  3. attachFilesToSample(files=[signedBlobId, ...], samplePuid) → status
```

## License

MIT