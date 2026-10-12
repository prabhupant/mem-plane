# mem-plane
The hybrid retrieval and context governance memory plane for production AI agents.

<div align="center">

# mem-plane

**The memory plane and context governance runtime for production AI agents.**

[![PyPI version](https://img.shields.io/pypi/v/mem-plane.svg?style=flat-square&color=blue)](https://pypi.org/project/mem-plane/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![MCP Ready](https://img.shields.io/badge/MCP-Compatible-green.svg?style=flat-square)](https://modelcontextprotocol.io/)

*Stop invalidating your hardware KV-cache with unstructured prompt dumping. Build on deterministic retrieval contracts, hybrid RRF search, and zero-infra local-to-production storage parity.*

[Features](#key-capabilities) • [Architecture](#the-memory-plane-architecture) • [Quickstart](#quickstart) • [Storage Parity](#storage-parity-sqlite--opensearch) • [MCP Server](#mcp-integration)

</div>

---

## Overview

Most agent memory frameworks treat context like an unstructured fact dumper. They pull top-$k$ conversational snippets and inject them into dynamic prompt sections—mutating prefix tokens on every turn, wiping out provider KV-cache reuse, and failing on exact syntactic code identifiers.

**`mem-plane`** is designed as a systems-level **memory plane** sitting between persistent search stores and the LLM execution window. It pairs lexical BM25 syntax matching with dense vector semantics, serves compact metadata surrogates to prevent context bloat, and enforces strict prompt-prefix zoning to preserve ~90% prompt-caching discounts.
