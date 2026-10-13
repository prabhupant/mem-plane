# MCP Host Configuration Guide

`mem-plane` provides an MCP server implementing the Model Context Protocol over stdio transport.

## Configuration for Claude Desktop / Claude Code

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mem-plane": {
      "command": "python",
      "args": ["-m", "mem_plane.server.mcp_server"],
      "env": {
        "MEM_PLANE_STORAGE_BACKEND": "sqlite",
        "MEM_PLANE_SQLITE_DB_PATH": "/path/to/your/agent_memory.db"
      }
    }
  }
}
```

## Production Configuration (PostgreSQL + OpenSearch)

```json
{
  "mcpServers": {
    "mem-plane": {
      "command": "python",
      "args": ["-m", "mem_plane.server.mcp_server"],
      "env": {
        "MEM_PLANE_STORAGE_BACKEND": "production",
        "MEM_PLANE_POSTGRES_DSN": "postgresql://user:pass@postgres.internal:5432/mem_plane",
        "MEM_PLANE_OPENSEARCH_URL": "http://opensearch.internal:9200"
      }
    }
  }
}
```
