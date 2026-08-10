"""
Unit tests for irida_batch_uploader.py

Tests cover:
- MD5 base64 checksum computation
- Auto-discover paired-end file detection
- Samplesheet TSV parsing
- Metadata file (CSV/TSV) parsing with sample + file columns
- Client initialization and auth header
- GraphQL query construction (mocked)
- Upload flow with metadata (mocked HTTP)
"""

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

import irida_batch_uploader as u


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    # cleanup
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def client():
    return u.IRIDANextClient(
        base_url="https://irida.example.com",
        email="test@lab.ca",
        token="fake-token-12345",
        verify_ssl=False,
    )


@pytest.fixture
def sample_fastq(tmp_dir):
    """Create a fake gzipped FASTQ file."""
    f = tmp_dir / "sample1_R1.fastq.gz"
    f.write_bytes(b"@SEQ_ID\nACGTACGT\n+\nIIIIIIII\n")
    return f


# ─── MD5 Checksum Tests ────────────────────────────────────────────────────

class TestMd5Base64:
    def test_known_content(self):
        """Verify MD5 base64 against known content."""
        content = b"test data here"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(content)
            path = Path(f.name)
        expected = base64.b64encode(hashlib.md5(content).digest()).decode()
        result = u.IRIDANextClient._compute_md5_base64(path)
        assert result == expected
        os.unlink(path)

    def test_empty_file(self, tmp_dir):
        """MD5 of empty file should match known empty MD5."""
        f = tmp_dir / "empty.gz"
        f.write_bytes(b"")
        expected = base64.b64encode(hashlib.md5(b"").digest()).decode()
        result = u.IRIDANextClient._compute_md5_base64(f)
        assert result == expected

    def test_large_file_chunked(self, tmp_dir):
        """Verify checksum works on files larger than the 8192-byte read chunk."""
        f = tmp_dir / "large.gz"
        content = b"A" * 100_000
        f.write_bytes(content)
        expected = base64.b64encode(hashlib.md5(content).digest()).decode()
        result = u.IRIDANextClient._compute_md5_base64(f)
        assert result == expected


# ─── Auto-Discover Tests ───────────────────────────────────────────────────

class TestAutoDiscover:
    def test_paired_end_r1_r2(self, tmp_dir):
        """Standard _R1/_R2 paired files."""
        (tmp_dir / "sample1_R1.fastq.gz").write_bytes(b"r1")
        (tmp_dir / "sample1_R2.fastq.gz").write_bytes(b"r2")
        (tmp_dir / "sample2_R1.fastq.gz").write_bytes(b"r1")
        (tmp_dir / "sample2_R2.fastq.gz").write_bytes(b"r2")
        result = u.auto_discover_samples(tmp_dir)
        names = [r[0] for r in result]
        assert "sample1" in names
        assert "sample2" in names
        assert len(result) == 2

    def test_paired_end_underscore_only(self, tmp_dir):
        """Files with _1/_2 instead of _R1/_R2."""
        (tmp_dir / "sA_1.fastq.gz").write_bytes(b"r1")
        (tmp_dir / "sA_2.fastq.gz").write_bytes(b"r2")
        result = u.auto_discover_samples(tmp_dir)
        names = [r[0] for r in result]
        assert "sA" in names

    def test_single_end(self, tmp_dir):
        """Non-paired file becomes single-end sample."""
        (tmp_dir / "singleton.fastq.gz").write_bytes(b"s")
        result = u.auto_discover_samples(tmp_dir)
        names = [r[0] for r in result]
        assert "singleton" in names
        assert len(result[0][1]) == 1

    def test_mixed_paired_and_single(self, tmp_dir):
        """Mix of paired and single files."""
        (tmp_dir / "paired_R1.fastq.gz").write_bytes(b"r1")
        (tmp_dir / "paired_R2.fastq.gz").write_bytes(b"r2")
        (tmp_dir / "single.fastq.gz").write_bytes(b"s")
        result = u.auto_discover_samples(tmp_dir)
        names = [r[0] for r in result]
        assert "paired" in names
        assert "single" in names
        assert len(result) == 2

    def test_fq_gz_extension(self, tmp_dir):
        """Files with .fq.gz extension."""
        (tmp_dir / "sampleX_R1.fq.gz").write_bytes(b"r1")
        (tmp_dir / "sampleX_R2.fq.gz").write_bytes(b"r2")
        result = u.auto_discover_samples(tmp_dir)
        names = [r[0] for r in result]
        assert "sampleX" in names

    def test_empty_dir(self, tmp_dir):
        """Empty directory returns empty list."""
        result = u.auto_discover_samples(tmp_dir)
        assert result == []

    def test_file_order_r1_before_r2(self, tmp_dir):
        """Files in a pair should be ordered R1 before R2."""
        # Create R2 first to test ordering
        (tmp_dir / "ord_R2.fastq.gz").write_bytes(b"r2")
        (tmp_dir / "ord_R1.fastq.gz").write_bytes(b"r1")
        result = u.auto_discover_samples(tmp_dir)
        sample = [r for r in result if r[0] == "ord"][0]
        assert "R1" in sample[1][0]
        assert "R2" in sample[1][1]


# ─── Samplesheet Parsing Tests ─────────────────────────────────────────────

class TestParseSamplesheet:
    def test_paired_end(self, tmp_dir):
        """TSV with paired-end files."""
        sheet = tmp_dir / "samples.tsv"
        sheet.write_text(
            "sample_name\tfile1\tfile2\n"
            "s1\ts1_R1.fastq.gz\ts1_R2.fastq.gz\n"
            "s2\ts2_R1.fastq.gz\ts2_R2.fastq.gz\n"
        )
        result = u.parse_samplesheet(str(sheet))
        assert len(result) == 2
        assert result[0] == ("s1", ["s1_R1.fastq.gz", "s1_R2.fastq.gz"])
        assert result[1] == ("s2", ["s2_R1.fastq.gz", "s2_R2.fastq.gz"])

    def test_single_end(self, tmp_dir):
        """TSV with single-end files (2 columns)."""
        sheet = tmp_dir / "samples.tsv"
        sheet.write_text(
            "sample_name\tfile1\n"
            "solo\tsolo.fastq.gz\n"
        )
        result = u.parse_samplesheet(str(sheet))
        assert len(result) == 1
        assert result[0] == ("solo", ["solo.fastq.gz"])

    def test_empty_lines_skipped(self, tmp_dir):
        """Empty lines in samplesheet should be skipped."""
        sheet = tmp_dir / "samples.tsv"
        sheet.write_text(
            "sample_name\tfile1\tfile2\n"
            "s1\ts1_R1.fastq.gz\ts1_R2.fastq.gz\n"
            "\n"
            "s2\ts2.fastq.gz\n"
        )
        result = u.parse_samplesheet(str(sheet))
        assert len(result) == 2

    def test_missing_file_columns(self, tmp_dir):
        """Row with only sample name and no files."""
        sheet = tmp_dir / "samples.tsv"
        sheet.write_text(
            "sample_name\tfile1\tfile2\n"
            "lonely\n"
        )
        result = u.parse_samplesheet(str(sheet))
        assert len(result) == 1
        assert result[0] == ("lonely", [])


# ─── Metadata File Parsing Tests ──────────────────────────────────────────

class TestParseMetadataFile:
    def test_csv_with_metadata(self, tmp_dir):
        """CSV file with sample name, file columns, and metadata columns."""
        f = tmp_dir / "samples.csv"
        f.write_text(
            "sample_name,forward_read,reverse_read,organism,isolate_id\n"
            "s1,s1_R1.fastq.gz,s1_R2.fastq.gz,Salmonella,ST-001\n"
            "s2,s2_R1.fastq.gz,s2_R2.fastq.gz,E. coli,ST-002\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["forward_read", "reverse_read"])
        assert len(result) == 2
        # First sample
        assert result[0][0] == "s1"
        assert result[0][1] == ["s1_R1.fastq.gz", "s1_R2.fastq.gz"]
        assert result[0][2] == {"organism": "Salmonella", "isolate_id": "ST-001"}
        # Second sample
        assert result[1][0] == "s2"
        assert result[1][2] == {"organism": "E. coli", "isolate_id": "ST-002"}

    def test_tsv_with_metadata(self, tmp_dir):
        """TSV file with metadata."""
        f = tmp_dir / "samples.tsv"
        f.write_text(
            "sample_name\tfile1\tfile2\torganism\n"
            "s1\ts1_R1.fq.gz\ts1_R2.fq.gz\tSalmonella\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["file1", "file2"])
        assert len(result) == 1
        assert result[0][2] == {"organism": "Salmonella"}

    def test_single_end_with_metadata(self, tmp_dir):
        """Single file column with metadata."""
        f = tmp_dir / "samples.csv"
        f.write_text(
            "sample_name,fastq,organism,collection_date\n"
            "solo,solo.fastq.gz,Listeria,2024-01-15\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["fastq"])
        assert len(result) == 1
        assert result[0][1] == ["solo.fastq.gz"]
        assert result[0][2] == {"organism": "Listeria", "collection_date": "2024-01-15"}

    def test_custom_column_names(self, tmp_dir):
        """Non-default column names for sample and files."""
        f = tmp_dir / "samples.csv"
        f.write_text(
            "id,fwd,rev,species\n"
            "sx,sx_R1.fastq.gz,sx_R2.fastq.gz,Campylobacter\n"
        )
        result = u.parse_metadata_file(str(f), "id", ["fwd", "rev"])
        assert len(result) == 1
        assert result[0][0] == "sx"
        assert result[0][2] == {"species": "Campylobacter"}

    def test_empty_metadata_values_skipped(self, tmp_dir):
        """Empty metadata values should be omitted, not included as empty strings."""
        f = tmp_dir / "samples.csv"
        f.write_text(
            "sample_name,file1,organism,serotype\n"
            "s1,s1.fastq.gz,Salmonella,\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["file1"])
        assert len(result) == 1
        assert "organism" in result[0][2]
        assert "serotype" not in result[0][2]

    def test_no_metadata_columns(self, tmp_dir):
        """All columns are sample name or file columns — no metadata."""
        f = tmp_dir / "samples.csv"
        f.write_text(
            "sample_name,file1,file2\n"
            "s1,s1_R1.fastq.gz,s1_R2.fastq.gz\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["file1", "file2"])
        assert len(result) == 1
        assert result[0][2] == {}

    def test_empty_file_column_values_skipped(self, tmp_dir):
        """Empty file column values should be skipped, not included."""
        f = tmp_dir / "samples.csv"
        f.write_text(
            "sample_name,file1,file2,organism\n"
            "s1,s1.fastq.gz,,Salmonella\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["file1", "file2"])
        assert len(result) == 1
        assert result[0][1] == ["s1.fastq.gz"]  # only one file, second was empty
        assert result[0][2] == {"organism": "Salmonella"}

    def test_blank_sample_name_skipped(self, tmp_dir):
        """Rows with empty sample name should be skipped."""
        f = tmp_dir / "samples.csv"
        f.write_text(
            "sample_name,file1,organism\n"
            "s1,s1.fastq.gz,Salmonella\n"
            ",empty.fastq.gz,Skipped\n"
            "s2,s2.fastq.gz,E. coli\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["file1"])
        assert len(result) == 2
        names = [r[0] for r in result]
        assert "s1" in names
        assert "s2" in names

    def test_invalid_sample_column(self, tmp_dir):
        """Should raise ValueError if sample column doesn't exist."""
        f = tmp_dir / "samples.csv"
        f.write_text("sample_name,file1\ns1,s1.fastq.gz\n")
        with pytest.raises(ValueError, match="Sample column 'nonexistent' not found"):
            u.parse_metadata_file(str(f), "nonexistent", ["file1"])

    def test_invalid_file_column(self, tmp_dir):
        """Should raise ValueError if file column doesn't exist."""
        f = tmp_dir / "samples.csv"
        f.write_text("sample_name,file1\ns1,s1.fastq.gz\n")
        with pytest.raises(ValueError, match="File column 'nonexistent' not found"):
            u.parse_metadata_file(str(f), "sample_name", ["nonexistent"])

    def test_delimiter_auto_detection_tsv(self, tmp_dir):
        """Should auto-detect TSV from .tsv extension."""
        f = tmp_dir / "samples.tsv"
        f.write_text(
            "sample_name\tfile1\torganism\n"
            "s1\ts1.fastq.gz\tSalmonella\n"
        )
        result = u.parse_metadata_file(str(f), "sample_name", ["file1"])
        assert len(result) == 1
        assert result[0][2] == {"organism": "Salmonella"}


# ─── Metadata Upload Tests (Mocked) ────────────────────────────────────────

class TestMetadataUpload:
    @patch.object(u.IRIDANextClient, "upload_file")
    @patch.object(u.IRIDANextClient, "create_sample")
    @patch.object(u.IRIDANextClient, "attach_files_to_sample")
    @patch.object(u.IRIDANextClient, "update_sample_metadata")
    def test_upload_sample_with_metadata(self, mock_meta, mock_attach, mock_create, mock_upload, client, tmp_dir):
        """upload_sample applies metadata after files are uploaded."""
        mock_create.return_value = {"id": "gid://irida/Sample/1", "puid": "INXT_SAM_ABC"}
        mock_upload.return_value = "signed-blob-1"

        f = tmp_dir / "test.fastq.gz"
        f.write_bytes(b"data")

        metadata = {"organism": "Salmonella", "isolate_id": "ST-001"}
        result = u.upload_sample(
            client, "sample1", [str(f)], tmp_dir,
            project_puid="INXT_PRJ_TEST", metadata=metadata,
        )

        assert result.success
        assert "metadata: 2 field(s)" in result.message
        mock_meta.assert_called_once()
        call_kwargs = mock_meta.call_args
        assert call_kwargs[1]["metadata"] == metadata or call_kwargs[0][0] == metadata

    @patch.object(u.IRIDANextClient, "upload_file")
    @patch.object(u.IRIDANextClient, "create_sample")
    @patch.object(u.IRIDANextClient, "attach_files_to_sample")
    @patch.object(u.IRIDANextClient, "update_sample_metadata")
    def test_upload_sample_no_metadata(self, mock_meta, mock_attach, mock_create, mock_upload, client, tmp_dir):
        """upload_sample without metadata should not call update_sample_metadata."""
        mock_create.return_value = {"id": "gid://irida/Sample/1", "puid": "INXT_SAM_X"}
        mock_upload.return_value = "signed-blob-1"

        f = tmp_dir / "test.fastq.gz"
        f.write_bytes(b"data")

        result = u.upload_sample(
            client, "sample1", [str(f)], tmp_dir,
            project_puid="INXT_PRJ_TEST", metadata=None,
        )

        assert result.success
        mock_meta.assert_not_called()

    @patch.object(u.IRIDANextClient, "execute_graphql")
    def test_update_sample_metadata_success(self, mock_gql, client):
        """update_sample_metadata parses response correctly."""
        mock_gql.return_value = {
            "updateSampleMetadata": {
                "sample": {"id": "1", "puid": "INXT_SAM_X"},
                "status": {"organism": "added"},
                "errors": [],
            }
        }
        result = client.update_sample_metadata(
            metadata={"organism": "Salmonella"}, sample_puid="INXT_SAM_X"
        )
        assert result["sample"]["puid"] == "INXT_SAM_X"

    @patch.object(u.IRIDANextClient, "execute_graphql")
    def test_update_sample_metadata_errors(self, mock_gql, client):
        """Metadata update errors raise RuntimeError."""
        mock_gql.return_value = {
            "updateSampleMetadata": {
                "sample": None,
                "status": None,
                "errors": [{"path": ["metadata"], "message": "Invalid"}],
            }
        }
        with pytest.raises(RuntimeError, match="Metadata update errors"):
            client.update_sample_metadata(
                metadata={"bad": "data"}, sample_puid="INXT_SAM_X"
            )


# ─── Client Initialization Tests ────────────────────────────────────────────

class TestClientInit:
    def test_auth_header(self):
        """Verify Basic auth header is correctly base64-encoded."""
        client = u.IRIDANextClient(
            base_url="https://irida.example.com/",
            email="user@lab.ca",
            token="token123",
        )
        expected = "Basic " + base64.b64encode(b"user@lab.ca:token123").decode()
        assert client.session.headers["Authorization"] == expected

    def test_url_trailing_slash(self):
        """Base URL should have trailing slash stripped."""
        client = u.IRIDANextClient("https://irida.example.com/", "a@b.com", "t")
        assert client.base_url == "https://irida.example.com"
        assert client.graphql_url == "https://irida.example.com/graphql"

    def test_url_no_trailing_slash(self):
        """Base URL without trailing slash should work too."""
        client = u.IRIDANextClient("https://irida.example.com", "a@b.com", "t")
        assert client.graphql_url == "https://irida.example.com/graphql"

    def test_content_type_header(self):
        """Content-Type should be application/json."""
        client = u.IRIDANextClient("https://example.com", "a@b.com", "t")
        assert client.session.headers["Content-Type"] == "application/json"


# ─── GraphQL Execution Tests (Mocked) ──────────────────────────────────────

class TestGraphQLExecution:
    def test_execute_graphql_success(self, client):
        """Successful GraphQL response returns data."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"project": {"id": "1"}}}
        mock_response.raise_for_status = MagicMock()
        client.session.post = MagicMock(return_value=mock_response)

        result = client.execute_graphql("query { project { id } }")
        assert result == {"project": {"id": "1"}}

    def test_execute_graphql_errors(self, client):
        """GraphQL errors raise RuntimeError."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "errors": [{"message": "Unauthorized"}]
        }
        mock_response.raise_for_status = MagicMock()
        client.session.post = MagicMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="GraphQL errors"):
            client.execute_graphql("query { bad }")


# ─── Upload Flow Tests (Mocked) ─────────────────────────────────────────────

class TestUploadFlow:
    @patch.object(u.IRIDANextClient, "upload_file")
    @patch.object(u.IRIDANextClient, "create_sample")
    @patch.object(u.IRIDANextClient, "attach_files_to_sample")
    def test_upload_sample_success(self, mock_attach, mock_create, mock_upload, client, tmp_dir):
        """upload_sample completes the full flow."""
        mock_create.return_value = {"id": "gid://irida/Sample/123", "puid": "INXT_SAM_ABC"}
        mock_upload.return_value = "signed-blob-id-1"

        f = tmp_dir / "test.fastq.gz"
        f.write_bytes(b"data")

        result = u.upload_sample(
            client, "sample1", [str(f)], tmp_dir,
            project_puid="INXT_PRJ_TEST",
        )

        assert result.success
        assert result.sample_puid == "INXT_SAM_ABC"
        assert "test.fastq.gz" in result.files_uploaded
        mock_create.assert_called_once()
        mock_upload.assert_called_once()
        mock_attach.assert_called_once()

    @patch.object(u.IRIDANextClient, "create_sample")
    def test_upload_sample_create_fails(self, mock_create, client, tmp_dir):
        """If create_sample raises, upload_sample returns failure."""
        mock_create.side_effect = RuntimeError("Sample creation failed")
        f = tmp_dir / "test.fastq.gz"
        f.write_bytes(b"data")

        result = u.upload_sample(
            client, "sample1", [str(f)], tmp_dir,
            project_puid="INXT_PRJ_TEST",
        )
        assert not result.success
        assert "Sample creation failed" in result.message

    @patch.object(u.IRIDANextClient, "create_sample")
    def test_upload_sample_file_not_found(self, mock_create, client, tmp_dir):
        """Missing file should return failure."""
        mock_create.return_value = {"id": "gid://irida/Sample/1", "puid": "INXT_SAM_X"}
        result = u.upload_sample(
            client, "sample1", ["nonexistent.fastq.gz"], tmp_dir,
            project_puid="INXT_PRJ_TEST",
        )
        assert not result.success
        assert "not found" in result.message.lower()


# ─── Direct Upload Tests (Mocked) ──────────────────────────────────────────

class TestDirectUpload:
    @patch.object(u.IRIDANextClient, "execute_graphql")
    def test_create_direct_upload(self, mock_gql, client, sample_fastq):
        """create_direct_upload parses response correctly."""
        mock_gql.return_value = {
            "createDirectUpload": {
                "directUpload": {
                    "url": "https://storage.example.com/upload/abc",
                    "headers": '{"Content-Type": "application/octet-stream"}',
                    "blobId": "1",
                    "signedBlobId": "signed-blob-123",
                }
            }
        }

        du = client.create_direct_upload(sample_fastq)
        assert du["url"] == "https://storage.example.com/upload/abc"
        assert du["signedBlobId"] == "signed-blob-123"
        assert du["headers"] == {"Content-Type": "application/octet-stream"}

    @patch.object(u.IRIDANextClient, "execute_graphql")
    @patch("irida_batch_uploader.requests.put")
    def test_upload_file_full_flow(self, mock_put, mock_gql, client, sample_fastq):
        """Full upload_file returns signed_blob_id after PUT."""
        mock_gql.return_value = {
            "createDirectUpload": {
                "directUpload": {
                    "url": "https://storage.example.com/upload",
                    "headers": '{"Content-Type": "application/octet-stream"}',
                    "blobId": "1",
                    "signedBlobId": "signed-blob-456",
                }
            }
        }
        mock_put.return_value.status_code = 200

        signed_id = client.upload_file(sample_fastq)
        assert signed_id == "signed-blob-456"
        mock_put.assert_called_once()

    @patch.object(u.IRIDANextClient, "execute_graphql")
    @patch("irida_batch_uploader.requests.put")
    def test_upload_file_put_fails(self, mock_put, mock_gql, client, sample_fastq):
        """PUT failure raises RuntimeError."""
        mock_gql.return_value = {
            "createDirectUpload": {
                "directUpload": {
                    "url": "https://storage.example.com/upload",
                    "headers": '{}',
                    "blobId": "1",
                    "signedBlobId": "signed-blob-789",
                }
            }
        }
        mock_put.return_value.status_code = 500
        mock_put.return_value.text = "Internal Server Error"

        with pytest.raises(RuntimeError, match="Direct upload PUT failed"):
            client.upload_file(sample_fastq)


# ─── Attach Files Tests (Mocked) ───────────────────────────────────────────

class TestAttachFiles:
    @patch.object(u.IRIDANextClient, "execute_graphql")
    def test_attach_files_to_sample_success(self, mock_gql, client):
        """Successful attach returns status dict."""
        mock_gql.return_value = {
            "attachFilesToSample": {
                "sample": {"id": "1", "puid": "INXT_SAM_X"},
                "status": {"blob1": "success"},
                "errors": [],
            }
        }
        result = client.attach_files_to_sample(["blob1"], sample_puid="INXT_SAM_X")
        assert result["sample"]["puid"] == "INXT_SAM_X"

    @patch.object(u.IRIDANextClient, "execute_graphql")
    def test_attach_files_to_sample_errors(self, mock_gql, client):
        """Attach errors raise RuntimeError."""
        mock_gql.return_value = {
            "attachFilesToSample": {
                "sample": None,
                "status": None,
                "errors": [{"path": ["blob"], "message": "Invalid blob"}],
            }
        }
        with pytest.raises(RuntimeError, match="Attach files errors"):
            client.attach_files_to_sample(["bad-blob"], sample_puid="INXT_SAM_X")


# ─── UploadResult Dataclass Tests ──────────────────────────────────────────

class TestUploadResult:
    def test_default_values(self):
        r = u.UploadResult(sample_name="test", success=False)
        assert r.sample_name == "test"
        assert r.success is False
        assert r.message == ""
        assert r.sample_puid == ""
        assert r.files_uploaded == []

    def test_with_values(self):
        r = u.UploadResult(
            sample_name="s1",
            success=True,
            message="Uploaded 2 files",
            sample_puid="INXT_SAM_ABC",
            files_uploaded=["s1_R1.fastq.gz", "s1_R2.fastq.gz"],
        )
        assert r.success
        assert len(r.files_uploaded) == 2