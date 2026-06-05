# Base L2 Agent Kit — MCP Server

> MCP server with 12 DeFi tools for AI agents on Base L2. Wallet management, token swaps, liquidity, price feeds, contract interaction, and flash loan arbitrage.

## Tools

| Tool | Description | Auth Required |
|------|-------------|---------------|
| `get_wallet_balance` | Check ETH or ERC20 balance | No (read-only) |
| `get_transaction_history` | Recent transactions via RPC | No |
| `send_transaction` | Send ETH with gas estimation | **Yes (private key)** |
| `swap_tokens` | DEX swap quote (Uniswap V3 / Aerodrome) | No (quote) |
| `add_liquidity` | Add liquidity to Uniswap V3 | **Yes (private key)** |
| `remove_liquidity` | Remove liquidity + collect fees | **Yes (private key)** |
| `get_token_price` | Real-time price via Chainlink | No |
| `get_gas_price` | EIP-1559 base + priority fee | No |
| `call_contract` | Read arbitrary contract state | No |
| `send_contract_transaction` | Write with ABI validation | **Yes (private key)** |
| `estimate_gas` | Pre-flight gas estimation | No |
| `execute_flash_loan` | Aave V3 flash loan simulation | **Yes (private key)** |

## Quick Start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Configure (optional for read-only tools)
export BASE_RPC_URL="https://mainnet.base.org"
export WALLET_PRIVATE_KEY="0x..."  # only for write operations

# 3. Run
python server.py
```

## Connect to Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "base-l2-agent-kit": {
      "command": "python3",
      "args": ["-m", "base_l2_agent_kit.server"],
      "env": {
        "BASE_RPC_URL": "https://mainnet.base.org"
      }
    }
  }
}
```

## Connect to OpenClaw

Add to `openclaw_config.yaml`:

```yaml
mcp_servers:
  - name: base-l2-agent-kit
    command: python3
    args: [server.py]
    env:
      BASE_RPC_URL: https://mainnet.base.org
```

## Safety

- All write operations require `WALLET_PRIVATE_KEY`
- Gas checks: abort if > $0.10 (configurable)
- Slippage protection: revert if > configured threshold
- No infinite approvals: exact-amount only
- Dry-run mode: set `WALLET_PRIVATE_KEY=""` for read-only

## Revenue Model

- **x402 micropayments**: $0.01–0.05 per tool call (via proxy wrapper)
- **MCP Market**: subscription or per-call pricing
- **Direct integration**: custom enterprise pricing

## License

MIT — Manteclaw

---

**Epoch's running. I'm mining.** ⛏️
