#!/usr/bin/env python3
"""
Batch uploader for IRIDA Next via GraphQL API.

Uploads sequencing files (FASTQ etc.) to samples in IRIDA Next projects.
Supports paired-end reads, bulk sample creation, file attachment, and
sample metadata via CSV/TSV files.

Auth: HTTP Basic Auth with email + personal access token.

Usage:
    python irida_batch_uploader.py \\
        --url https://irida.example.com \\
        --email user@example.com \\
        --token INXT_PAT_xxxxx \\
        --project-puid INXT_PRJ_AAAAAAAAAA \\
        --samplesheet samples.tsv \\
        --input-dir /data/runs/run001

    # With metadata (CSV/TSV with named columns):
    python irida_batch_uploader.py \\
        --url https://irida.example.com \\
        --email user@example.com \\
        --token INXT_PAT_xxxxx \\
        --project-puid INXT_PRJ_AAAAAAAAAA \\
        --metadata-file samples_with_metadata.csv \\
        --sample-column sample_name \\
        --file-columns forward_read reverse_read \\
        --input-dir /data/runs/run001

Samplesheet format (TSV):
    sample_name    file1    file2
    sample1        sample1_R1.fastq.gz    sample1_R2.fastq.gz

Metadata file format (CSV or TSV):
    Any columns work. Specify which column is the sample name
    (--sample-column) and which columns are file paths (--file-columns).
    All remaining columns are treated as sample metadata and applied
    via the updateSampleMetadata GraphQL mutation.

    Example CSV:
        sample_name,forward_read,reverse_read,organism,isolate_id
        sample1,s1_R1.fastq.gz,s1_R2.fastq.gz,Salmonella,ST-001
        sample2,s2_R1.fastq.gz,s2_R2.fastq.gz,E. coli,ST-002

Can also auto-discover paired-end files by --auto-discover flag:
    sample1_R1.fastq.gz + sample1_R2.fastq.gz → sample "sample1"
"""

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


@dataclass
class UploadResult:
    sample_name: str
    success: bool
    message: str = ""
    sample_puid: str = ""
    files_uploaded: list = field(default_factory=list)


class IRIDANextClient:
    """GraphQL client for IRIDA Next with Active Storage direct upload support."""

    def __init__(self, base_url: str, email: str, token: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.graphql_url = f"{self.base_url}/graphql"
        self.email = email
        self.token = token
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.session.headers.update({
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        })

    def execute_graphql(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query/mutation and return the data."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = self.session.post(
            self.graphql_url, json=payload, verify=self.verify_ssl, timeout=120
        )
        resp.raise_for_status()
        result = resp.json()
        if "errors" in result and result["errors"]:
            raise RuntimeError(f"GraphQL errors: {json.dumps(result['errors'], indent=2)}")
        return result.get("data", {})

    def create_sample(self, name: str, project_id: str = None, project_puid: str = None,
                      description: str = None) -> dict:
        """Create a sample in a project. Returns sample dict with id, puid."""
        mutation = """
        mutation CreateSample($name: String!, $projectId: ID, $projectPuid: ID, $description: String) {
            createSample(input: {
                name: $name,
                projectId: $projectId,
                projectPuid: $projectPuid,
                description: $description
            }) {
                sample {
                    id
                    puid
                    name
                }
                errors {
                    path
                    message
                }
            }
        }
        """
        variables = {"name": name, "description": description}
        if project_id:
            variables["projectId"] = project_id
        if project_puid:
            variables["projectPuid"] = project_puid
        data = self.execute_graphql(mutation, variables)
        result = data.get("createSample", {})
        if result.get("errors"):
            raise RuntimeError(f"Failed to create sample '{name}': {result['errors']}")
        return result.get("sample", {})

    def create_direct_upload(self, file_path: Path, content_type: str = "application/octet-stream") -> dict:
        """Request a direct upload URL from IRIDA Next. Returns dict with url, headers, signed_blob_id."""
        file_size = file_path.stat().st_size
        md5_b64 = self._compute_md5_base64(file_path)

        mutation = """
        mutation CreateDirectUpload($filename: String!, $byteSize: BigInt!, $checksum: String!, $contentType: String!) {
            createDirectUpload(input: {
                filename: $filename,
                byteSize: $byteSize,
                checksum: $checksum,
                contentType: $contentType
            }) {
                directUpload {
                    url
                    headers
                    blobId
                    signedBlobId
                }
            }
        }
        """
        variables = {
            "filename": file_path.name,
            "byteSize": file_size,
            "checksum": md5_b64,
            "contentType": content_type,
        }
        data = self.execute_graphql(mutation, variables)
        du = data["createDirectUpload"]["directUpload"]
        du["headers"] = json.loads(du["headers"]) if isinstance(du["headers"], str) else du["headers"]
        return du

    def upload_file(self, file_path: Path, content_type: str = "application/octet-stream") -> str:
        """
        Full file upload: create direct upload → PUT binary → return signed_blob_id.
        """
        du = self.create_direct_upload(file_path, content_type)
        upload_url = du["url"]
        headers = du["headers"]

        with open(file_path, "rb") as f:
            put_resp = requests.put(
                upload_url,
                data=f,
                headers=headers,
                verify=self.verify_ssl,
                timeout=600,
            )
        if put_resp.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"Direct upload PUT failed for {file_path.name}: "
                f"HTTP {put_resp.status_code} - {put_resp.text[:500]}"
            )
        return du["signedBlobId"]

    def attach_files_to_sample(self, signed_blob_ids: list, sample_id: str = None,
                               sample_puid: str = None) -> dict:
        """Attach uploaded files (by signed blob IDs) to a sample."""
        mutation = """
        mutation AttachFilesToSample($files: [String!]!, $sampleId: ID, $samplePuid: ID) {
            attachFilesToSample(input: {
                files: $files,
                sampleId: $sampleId,
                samplePuid: $samplePuid
            }) {
                sample {
                    id
                    puid
                }
                status
                errors {
                    path
                    message
                }
            }
        }
        """
        variables = {"files": signed_blob_ids}
        if sample_id:
            variables["sampleId"] = sample_id
        if sample_puid:
            variables["samplePuid"] = sample_puid
        data = self.execute_graphql(mutation, variables)
        result = data.get("attachFilesToSample", {})
        if result.get("errors"):
            raise RuntimeError(f"Attach files errors: {result['errors']}")
        return result

    def attach_files_to_project(self, signed_blob_ids: list, project_id: str = None,
                                project_puid: str = None) -> dict:
        """Attach uploaded files (by signed blob IDs) to a project."""
        mutation = """
        mutation AttachFilesToProject($files: [String!]!, $projectId: ID, $projectPuid: ID) {
            attachFilesToProject(input: {
                files: $files,
                projectId: $projectId,
                projectPuid: $projectPuid
            }) {
                project {
                    id
                    puid
                }
                status
                errors {
                    path
                    message
                }
            }
        }
        """
        variables = {"files": signed_blob_ids}
        if project_id:
            variables["projectId"] = project_id
        if project_puid:
            variables["projectPuid"] = project_puid
        data = self.execute_graphql(mutation, variables)
        result = data.get("attachFilesToProject", {})
        if result.get("errors"):
            raise RuntimeError(f"Attach files errors: {result['errors']}")
        return result

    def get_project(self, project_id: str = None, project_puid: str = None) -> dict:
        """Fetch project details."""
        query = """
        query GetProject($id: ID, $puid: ID) {
            project(id: $id, puid: $puid) {
                id
                puid
                name
                description
            }
        }
        """
        variables = {}
        if project_id:
            variables["id"] = project_id
        if project_puid:
            variables["puid"] = project_puid
        data = self.execute_graphql(query, variables)
        return data.get("project", {})

    def update_sample_metadata(self, metadata: dict, sample_id: str = None,
                              sample_puid: str = None) -> dict:
        """Update metadata for a sample via updateSampleMetadata mutation."""
        mutation = """
        mutation UpdateSampleMetadata($metadata: JSON!, $sampleId: ID, $samplePuid: ID) {
            updateSampleMetadata(input: {
                metadata: $metadata,
                sampleId: $sampleId,
                samplePuid: $samplePuid
            }) {
                sample {
                    id
                    puid
                }
                status
                errors {
                    path
                    message
                }
            }
        }
        """
        variables = {"metadata": metadata}
        if sample_id:
            variables["sampleId"] = sample_id
        if sample_puid:
            variables["samplePuid"] = sample_puid
        data = self.execute_graphql(mutation, variables)
        result = data.get("updateSampleMetadata", {})
        if result.get("errors"):
            raise RuntimeError(f"Metadata update errors: {result['errors']}")
        return result

    @staticmethod
    def _compute_md5_base64(file_path: Path) -> str:
        """Compute MD5 checksum and return as base64-encoded string."""
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
        return base64.b64encode(md5.digest()).decode()


def parse_samplesheet(filepath: str) -> list:
    """
    Parse a TSV samplesheet. Returns list of (sample_name, [file_paths]).
    Columns: sample_name, file1, [file2]
    """
    samples = []
    with open(filepath, "r") as f:
        header = f.readline().strip().split("\t")
        for line in f:
            line = line.strip()
            if not line:
                continue
            cols = line.split("\t")
            sample_name = cols[0]
            files = [c for c in cols[1:] if c]
            samples.append((sample_name, files))
    return samples


def parse_metadata_file(filepath: str, sample_column: str, file_columns: list) -> list:
    """
    Parse a CSV/TSV file containing sample names, file paths, and metadata.

    Args:
        filepath: Path to the CSV or TSV file.
        sample_column: Name of the column containing the sample name.
        file_columns: List of column names containing file paths.

    Returns:
        List of (sample_name, [file_paths], {metadata_key: value, ...}).
        All columns except sample_column and file_columns are treated as metadata.
    """
    # Detect delimiter: TSV if .tsv/.tab, CSV if .csv, otherwise sniff
    ext = Path(filepath).suffix.lower()
    if ext in (".tsv", ".tab"):
        delimiter = "\t"
    elif ext == ".csv":
        delimiter = ","
    else:
        # Sniff delimiter from first non-empty line
        with open(filepath, "r") as f:
            first_line = f.readline()
        delimiter = "\t" if "\t" in first_line else ","

    samples = []
    with open(filepath, "r", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError(f"No header row found in {filepath}")

        if sample_column not in reader.fieldnames:
            raise ValueError(
                f"Sample column '{sample_column}' not found in header. "
                f"Available columns: {', '.join(reader.fieldnames)}"
            )

        for col in file_columns:
            if col not in reader.fieldnames:
                raise ValueError(
                    f"File column '{col}' not found in header. "
                    f"Available columns: {', '.join(reader.fieldnames)}"
                )

        # Metadata columns = everything except sample_column and file_columns
        metadata_columns = [
            c for c in reader.fieldnames
            if c != sample_column and c not in file_columns
        ]

        for row in reader:
            sample_name = row[sample_column].strip()
            if not sample_name:
                continue

            # Collect file paths (skip empty)
            files = []
            for fc in file_columns:
                val = row.get(fc, "").strip()
                if val:
                    files.append(val)

            # Collect metadata (skip empty values)
            metadata = {}
            for mc in metadata_columns:
                val = row.get(mc, "").strip()
                if val:
                    metadata[mc] = val

            samples.append((sample_name, files, metadata))

    return samples


def auto_discover_samples(input_dir: Path) -> list:
    """
    Auto-discover paired-end FASTQ files in a directory.
    Paired: *_R1.fastq.gz + *_R2.fastq.gz → sample name is prefix without _R1/_R2
    Single: anything not matching paired pattern.
    """
    files = sorted(input_dir.glob("*.fastq.gz"))
    # Also try .fq.gz
    files.extend(sorted(input_dir.glob("*.fq.gz")))

    paired_pattern = re.compile(r"^(.+?)_R?([12])\.(fastq|fq)\.gz$", re.IGNORECASE)
    paired = {}  # sample_name → {"1": path, "2": path}
    singles = []

    for f in files:
        m = paired_pattern.match(f.name)
        if m:
            sample = m.group(1)
            mate = m.group(2)
            if sample not in paired:
                paired[sample] = {}
            paired[sample][mate] = str(f)
        else:
            # Non-paired file: treat as single-end with filename minus extension as sample
            sample = f.name.replace(".fastq.gz", "").replace(".fq.gz", "")
            singles.append((sample, [str(f)]))

    result = []
    for sample, mates in paired.items():
        files_sorted = []
        if "1" in mates:
            files_sorted.append(mates["1"])
        if "2" in mates:
            files_sorted.append(mates["2"])
        result.append((sample, files_sorted))
    result.extend(singles)
    return result


def upload_sample(client: IRIDANextClient, sample_name: str, file_paths: list,
                  input_dir: Path, project_id: str = None, project_puid: str = None,
                  description: str = None, metadata: dict = None) -> UploadResult:
    """Upload a single sample: create sample → upload files → attach → update metadata."""
    result = UploadResult(sample_name=sample_name, success=False)

    try:
        # 1. Create the sample
        sample = client.create_sample(
            name=sample_name,
            project_id=project_id,
            project_puid=project_puid,
            description=description,
        )
        result.sample_puid = sample.get("puid", "")
        sample_id = sample.get("id")
        sample_puid = sample.get("puid")

        # 2. Upload each file via direct upload
        signed_blob_ids = []
        for rel_path in file_paths:
            file_path = input_dir / rel_path if not os.path.isabs(rel_path) else Path(rel_path)
            if not file_path.exists():
                result.message = f"File not found: {file_path}"
                return result

            # Determine content type
            ct = "application/octet-stream"
            if file_path.name.endswith(".gz"):
                ct = "application/gzip"

            signed_id = client.upload_file(file_path, content_type=ct)
            signed_blob_ids.append(signed_id)
            result.files_uploaded.append(file_path.name)

        # 3. Attach files to sample
        attach_result = client.attach_files_to_sample(
            signed_blob_ids=signed_blob_ids,
            sample_id=sample_id,
            sample_puid=sample_puid,
        )

        # 4. Update metadata if provided
        if metadata:
            client.update_sample_metadata(
                metadata=metadata,
                sample_id=sample_id,
                sample_puid=sample_puid,
            )

        result.success = True
        msg = f"Uploaded {len(signed_blob_ids)} file(s)"
        if metadata:
            msg += f", metadata: {len(metadata)} field(s)"
        result.message = msg
    except Exception as e:
        result.message = str(e)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Batch uploader for IRIDA Next",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload from samplesheet
  %(prog)s --url https://irida.example.com --email me@lab.ca --token INXT_PAT_xxx \\
      --project-puid INXT_PRJ_AAAAAAAAAA --samplesheet samples.tsv --input-dir /data/run001

  # Upload with metadata from CSV
  %(prog)s --url https://irida.example.com --email me@lab.ca --token INXT_PAT_xxx \\
      --project-puid INXT_PRJ_AAAAAAAAAA --metadata-file samples.csv \\
      --sample-column sample_name --file-columns fwd_read rev_read \\
      --input-dir /data/run001

  # Auto-discover paired-end files
  %(prog)s --url https://irida.example.com --email me@lab.ca --token INXT_PAT_xxx \\
      --project-puid INXT_PRJ_AAAAAAAAAA --auto-discover --input-dir /data/run001

  # Attach files directly to a project (no samples)
  %(prog)s --url https://irida.example.com --email me@lab.ca --token INXT_PAT_xxx \\
      --project-puid INXT_PRJ_AAAAAAAAAA --attach-to-project --input-dir /data/reports
        """,
    )
    parser.add_argument("--url", required=True, help="IRIDA Next base URL")
    parser.add_argument("--email", required=True, help="User email for authentication")
    parser.add_argument("--token", required=True, help="Personal Access Token")
    parser.add_argument("--project-id", help="Project GraphQL Node ID (gid://irida/Project/...)")
    parser.add_argument("--project-puid", help="Project PUID (INXT_PRJ_...)")
    parser.add_argument("--samplesheet", help="TSV samplesheet: sample_name<TAB>file1[<TAB>file2]")
    parser.add_argument("--metadata-file",
                        help="CSV or TSV file with sample names, file paths, and metadata columns")
    parser.add_argument("--sample-column", default="sample_name",
                        help="Column name in --metadata-file for the sample name (default: sample_name)")
    parser.add_argument("--file-columns", nargs="+", default=["file1", "file2"],
                        help="Column name(s) in --metadata-file for file paths (default: file1 file2). "
                             "All other columns become sample metadata.")
    parser.add_argument("--input-dir", default=".", help="Base directory for file paths in samplesheet")
    parser.add_argument("--auto-discover", action="store_true",
                        help="Auto-discover paired-end FASTQ files in --input-dir")
    parser.add_argument("--attach-to-project", action="store_true",
                        help="Attach files to project directly instead of creating samples")
    parser.add_argument("--description", help="Description for created samples")
    parser.add_argument("--no-verify-ssl", action="store_true", help="Disable SSL verification")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel upload workers (default: 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be uploaded without making changes")
    args = parser.parse_args()

    if not args.project_id and not args.project_puid:
        parser.error("Either --project-id or --project-puid is required")

    if not args.samplesheet and not args.auto_discover and not args.attach_to_project and not args.metadata_file:
        parser.error("Either --samplesheet, --metadata-file, --auto-discover, or --attach-to-project is required")

    verify_ssl = not args.no_verify_ssl
    client = IRIDANextClient(args.url, args.email, args.token, verify_ssl)

    # Verify connectivity
    print(f"Connecting to {args.url} ...")
    project = client.get_project(project_id=args.project_id, project_puid=args.project_puid)
    if not project:
        print(f"ERROR: Project not found. Check your --project-id/--project-puid and credentials.")
        sys.exit(1)
    print(f"Connected. Project: {project.get('name', '?')} ({project.get('puid', '?')})")

    input_dir = Path(args.input_dir)

    # Build upload list
    metadata_map = {}  # sample_name → metadata dict
    if args.attach_to_project:
        # Upload all files in input_dir to the project
        files = sorted(list(input_dir.glob("*.gz")) + list(input_dir.glob("*.fastq")))
        if not files:
            print(f"ERROR: No files found in {input_dir}")
            sys.exit(1)
        upload_list = [(f.name, [str(f)]) for f in files]
    elif args.metadata_file:
        # Parse metadata file: returns [(sample_name, [files], {metadata})]
        parsed = parse_metadata_file(args.metadata_file, args.sample_column, args.file_columns)
        upload_list = [(name, files) for name, files, meta in parsed]
        for name, files, meta in parsed:
            metadata_map[name] = meta
    elif args.auto_discover:
        upload_list = auto_discover_samples(input_dir)
    else:
        upload_list = parse_samplesheet(args.samplesheet)

    if not upload_list:
        print("ERROR: No samples/files to upload. Check your samplesheet or input directory.")
        sys.exit(1)

    print(f"\nFound {len(upload_list)} sample(s) to upload:")
    for name, files in upload_list:
        meta = metadata_map.get(name, {})
        meta_str = f" [metadata: {', '.join(f'{k}={v}' for k,v in meta.items())}]" if meta else ""
        print(f"  {name}: {', '.join(Path(f).name for f in files)}{meta_str}")

    if args.dry_run:
        print("\n--dry-run: no uploads performed.")
        return

    print()

    # Execute uploads
    results = []
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {}
            for sample_name, file_paths in upload_list:
                if args.attach_to_project:
                    continue
                meta = metadata_map.get(sample_name, {})
                fut = pool.submit(
                    upload_sample, client, sample_name, file_paths,
                    input_dir, args.project_id, args.project_puid, args.description,
                    meta if meta else None,
                )
                futures[fut] = sample_name
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                status = "✓" if r.success else "✗"
                print(f"  {status} {r.sample_name}: {r.message}")
    else:
        for sample_name, file_paths in upload_list:
            if args.attach_to_project:
                continue
            meta = metadata_map.get(sample_name, {})
            r = upload_sample(
                client, sample_name, file_paths, input_dir,
                args.project_id, args.project_puid, args.description,
                meta if meta else None,
            )
            results.append(r)
            status = "✓" if r.success else "✗"
            print(f"  {status} {r.sample_name}: {r.message}")

    # Handle attach-to-project mode
    if args.attach_to_project:
        print("\nAttaching files directly to project...")
        all_signed_ids = []
        for name, file_paths in upload_list:
            for fp in file_paths:
                file_path = Path(fp)
                if not file_path.exists():
                    print(f"  ✗ File not found: {file_path}")
                    continue
                ct = "application/gzip" if file_path.name.endswith(".gz") else "application/octet-stream"
                signed_id = client.upload_file(file_path, content_type=ct)
                all_signed_ids.append(signed_id)
                print(f"  ✓ Uploaded {file_path.name}")
        if all_signed_ids:
            client.attach_files_to_project(
                signed_blob_ids=all_signed_ids,
                project_id=args.project_id,
                project_puid=args.project_puid,
            )
            print(f"  ✓ Attached {len(all_signed_ids)} file(s) to project")

    # Summary
    print(f"\n{'='*50}")
    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded
    print(f"Upload complete: {succeeded} succeeded, {failed} failed")
    if failed:
        print("\nFailed samples:")
        for r in results:
            if not r.success:
                print(f"  {r.sample_name}: {r.message}")
        sys.exit(1)


if __name__ == "__main__":
    main()