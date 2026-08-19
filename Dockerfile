FROM node:20-slim AS node

FROM python:3.11-slim

WORKDIR /app

# System deps for building some Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Node.js/npx run the filesystem MCP server (src/tools/file_system.py).
# Copied from the official Node image rather than apt -- Debian's nodejs
# package lags far behind current LTS.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Agent's sandboxed workspace for the File System tool -- created before
# the npx warm-up below so it has a real directory to point at.
RUN mkdir -p /app/workspace

# uv/uvx spawn the Web Search MCP server (duckduckgo-mcp-server) as an
# on-demand subprocess -- see src/tools/mcp_client.py + web_search.py.
# Warm the uv cache at build time so the first live request isn't stuck
# downloading the server package.
RUN pip install --no-cache-dir uv \
    && uvx duckduckgo-mcp-server --help > /dev/null

# Same warm-up for the File System MCP server. It has no --help;
# with stdin closed (Docker RUN's default) it starts, logs ready, and
# exits 0 -- enough to populate npm's package cache in this layer.
RUN npx -y @modelcontextprotocol/server-filesystem@2026.7.10 /app/workspace

COPY src/ ./src/

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
