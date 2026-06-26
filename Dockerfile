FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e "."

# Expose port
EXPOSE 4022

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:4022/health')" || exit 1

# Run the MCP server
CMD ["python", "-m", "base_l2_agent_kit.server"]
