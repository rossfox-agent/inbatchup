# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial batch uploader for IRIDA Next via GraphQL API
- `createDirectUpload` → PUT → `attachFilesToSample` direct upload flow
- Three upload modes: samplesheet (TSV), auto-discover paired-end FASTQ, attach-to-project
- Parallel upload workers (`--workers N`)
- Dry-run mode for previewing uploads
- SSL verification toggle for self-hosted instances
- GitHub Actions CI workflow (Python 3.10–3.12)