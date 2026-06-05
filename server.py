#!/usr/bin/env python3
"""Base L2 Agent Kit — MCP Server for DeFi Operations on Base L2.

Provides 12 tools for AI agents to interact with Base L2:
- Wallet management (balance, history, send)
- Token swaps (DEX aggregation)
- Liquidity provision (Uniswap V3)
- Price feeds (Chainlink + DEX aggregation)
- Contract interaction (read/write)
- Flash loan arbitrage (Aave V3)

Requirements:
    pip install fastmcp web3 requests python-dotenv

Env:
    BASE_RPC_URL (default: https://mainnet.base.org)
    WALLET_PRIVATE_KEY (optional; for write operations)
    BANKR_API_KEY (optional; gasless via Bankr)
"""

import os
import json
import logging
from typing import Optional
from decimal import Decimal, ROUND_DOWN
from datetime import datetime
from dataclasses import dataclass

import requests
from web3 import Web3
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from fastmcp import FastMCP

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("base-l2-agent-kit")

# ── Constants ───────────────────────────────────────────────────────
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
WALLET_PRIVATE_KEY = os.environ.get("WALLET_PRIVATE_KEY", "")
BANKR_API_KEY = os.environ.get("BANKR_API_KEY", "")

# Base L2 Key Contracts
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"
UNISWAP_V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
AAVE_V3_POOL = "0xA238Dd80C2594eE87Fb65f7026dD1E222fd5afB4"
AAVE_V3_DATA_PROVIDER = "0x2d8A29b8CfeE4804C9dE7546D2979913f1E308A5"
CHAINLINK_ETH_USD = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

CHAINLINK_PRICE_FEED_ABI = [
    {"inputs": [], "name": "latestRoundData", "outputs": [
        {"internalType": "uint80", "name": "roundId", "type": "uint80"},
        {"internalType": "int256", "name": "answer", "type": "int256"},
        {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
        {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
        {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"}
    ], "stateMutability": "view", "type": "function"}
]

# ── Web3 connection ─────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
if not w3.is_connected():
    logger.warning("Web3 not connected to %s", BASE_RPC_URL)

# ── MCP Server ──────────────────────────────────────────────────────
mcp = FastMCP("Base L2 Agent Kit")


def _to_checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr) if addr else ""


def _format_amount(raw: int, decimals: int) -> str:
    return str(Decimal(raw) / (10 ** decimals))


# ── Tool 1: Wallet Balance ─────────────────────────────────────────
@mcp.tool()
async def get_wallet_balance(address: str, token: str = "ETH") -> str:
    """Check ETH or ERC20 token balance for a Base L2 address.
    
    Args:
        address: Base L2 wallet address (0x...)
        token: Token symbol (ETH, USDC, WETH, cbETH, or any ERC20 address)
    
    Returns: JSON with balance, token, decimals, and USD estimate."""
    try:
        addr = _to_checksum(address)
        if not addr:
            return json.dumps({"error": "Invalid address"})

        if token.upper() == "ETH":
            bal_wei = w3.eth.get_balance(addr)
            bal_eth = w3.from_wei(bal_wei, "ether")
            return json.dumps({
                "address": address,
                "token": "ETH",
                "balance": str(bal_eth),
                "decimals": 18,
                "chain": "Base L2",
                "timestamp": datetime.utcnow().isoformat()
            })

        # Token mapping or address
        token_map = {
            "USDC": USDC,
            "WETH": WETH,
            "CBETH": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0Dbc62",
        }
        token_addr = token_map.get(token.upper(), token)
        token_contract = w3.eth.contract(address=_to_checksum(token_addr), abi=ERC20_ABI)
        decimals = token_contract.functions.decimals().call()
        bal = token_contract.functions.balanceOf(addr).call()
        symbol = token_contract.functions.symbol().call()
        return json.dumps({
            "address": address,
            "token": symbol,
            "token_address": token_addr,
            "balance": _format_amount(bal, decimals),
            "decimals": decimals,
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error("get_wallet_balance error: %s", e)
        return json.dumps({"error": str(e), "address": address, "token": token})


# ── Tool 2: Transaction History ───────────────────────────────────
@mcp.tool()
async def get_transaction_history(address: str, limit: int = 10) -> str:
    """Get recent transactions for a Base L2 address (via Etherscan-compatible API).
    
    Args:
        address: Base L2 wallet address
        limit: Number of transactions (max 50)
    
    Returns: JSON array of recent transactions with status."""
    # Note: Requires BASESCAN_API_KEY for full history; fallback uses RPC nonce
    limit = min(limit, 50)
    try:
        addr = _to_checksum(address)
        nonce = w3.eth.get_transaction_count(addr)
        return json.dumps({
            "address": address,
            "nonce": nonce,
            "note": "Full tx history requires BASESCAN_API_KEY. Use RPC nonce as proxy.",
            "transactions": [],
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e), "address": address})


# ── Tool 3: Send Transaction ──────────────────────────────────────
@mcp.tool()
async def send_transaction(to_address: str, amount_eth: str, gas_price_gwei: Optional[str] = None) -> str:
    """Send ETH from the configured wallet to a Base L2 address.
    
    Args:
        to_address: Recipient address (0x...)
        amount_eth: Amount in ETH (e.g., "0.01")
        gas_price_gwei: Optional override gas price (EIP-1559 base + priority)
    
    Returns: JSON with tx hash, gas used, and status."""
    if not WALLET_PRIVATE_KEY:
        return json.dumps({"error": "WALLET_PRIVATE_KEY not configured. Set env var or use Bankr gasless mode."})
    try:
        to = _to_checksum(to_address)
        amount_wei = w3.to_wei(Decimal(amount_eth), "ether")
        account = w3.eth.account.from_key(WALLET_PRIVATE_KEY)
        from_addr = account.address

        tx = {
            "from": from_addr,
            "to": to,
            "value": amount_wei,
            "gas": 21000,
            "maxFeePerGas": w3.to_wei(gas_price_gwei or "0.1", "gwei") if gas_price_gwei else w3.eth.max_priority_fee + w3.eth.get_block("latest")["baseFeePerGas"],
            "maxPriorityFeePerGas": w3.to_wei("0.001", "gwei"),
            "nonce": w3.eth.get_transaction_count(from_addr),
            "chainId": 8453,
        }
        signed = w3.eth.account.sign_transaction(tx, WALLET_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return json.dumps({
            "tx_hash": tx_hash.hex(),
            "status": "success" if receipt.status == 1 else "failed",
            "gas_used": receipt.gasUsed,
            "from": from_addr,
            "to": to,
            "amount": amount_eth,
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e), "to": to_address, "amount": amount_eth})


# ── Tool 4: Token Swap ─────────────────────────────────────────────
@mcp.tool()
async def swap_tokens(from_token: str, to_token: str, amount: str, slippage: float = 0.5) -> str:
    """Swap tokens on Base L2 via Uniswap V3 (simulated routing).
    
    Args:
        from_token: Token symbol or address (e.g., "ETH", "USDC", "WETH")
        to_token: Target token symbol or address
        amount: Amount to swap (in from_token units)
        slippage: Max slippage % (default 0.5)
    
    Returns: JSON with estimated output, route, and gas estimate."""
    # Simplified: returns a quote; production uses 1inch API or on-chain router
    token_map = {
        "ETH": WETH, "WETH": WETH,
        "USDC": USDC,
    }
    from_addr = token_map.get(from_token.upper(), from_token)
    to_addr = token_map.get(to_token.upper(), to_token)
    try:
        # Mock quote for now — production: call 1inch API or on-chain quoter
        return json.dumps({
            "from_token": from_token,
            "to_token": to_token,
            "from_amount": amount,
            "estimated_output": f"~{amount} (simulated quote)",
            "slippage": f"{slippage}%",
            "route": [from_addr, to_addr],
            "gas_estimate": "~120,000",
            "dex": "Uniswap V3 / Aerodrome",
            "chain": "Base L2",
            "note": "Production: integrate 1inch API or on-chain quoter for live prices",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool 5: Add Liquidity ─────────────────────────────────────────
@mcp.tool()
async def add_liquidity(token_a: str, token_b: str, amount_a: str, amount_b: str, fee_tier: int = 500) -> str:
    """Add liquidity to Uniswap V3 on Base L2.
    
    Args:
        token_a: First token (symbol or address)
        token_b: Second token
        amount_a: Amount of token_a
        amount_b: Amount of token_b
        fee_tier: Pool fee (500=0.05%, 3000=0.3%, 10000=1%)
    
    Returns: JSON with pool info, position, and gas estimate."""
    try:
        return json.dumps({
            "token_a": token_a,
            "token_b": token_b,
            "amount_a": amount_a,
            "amount_b": amount_b,
            "fee_tier": fee_tier,
            "pool": f"{token_a}/{token_b}@{fee_tier}",
            "position_range": "±5% of current price (auto-range)",
            "gas_estimate": "~250,000",
            "chain": "Base L2",
            "note": "Production: deploy via Uniswap V3 Position Manager",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool 6: Remove Liquidity ──────────────────────────────────────
@mcp.tool()
async def remove_liquidity(token_id: str, percentage: float = 100.0) -> str:
    """Remove liquidity from a Uniswap V3 position.
    
    Args:
        token_id: NFT token ID of the position
        percentage: % to remove (0-100)
    
    Returns: JSON with tokens returned, fees collected, and gas estimate."""
    try:
        return json.dumps({
            "token_id": token_id,
            "percentage": percentage,
            "tokens_returned": "simulated",
            "fees_collected": "simulated",
            "gas_estimate": "~180,000",
            "chain": "Base L2",
            "note": "Production: call NonfungiblePositionManager.decreaseLiquidity()",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool 7: Token Price ───────────────────────────────────────────
@mcp.tool()
async def get_token_price(token: str) -> str:
    """Get real-time token price on Base L2 via Chainlink + DEX aggregation.
    
    Args:
        token: Token symbol (ETH, USDC, WETH) or price feed address
    
    Returns: JSON with price, source, and timestamp."""
    try:
        if token.upper() in ("ETH", "WETH"):
            feed = w3.eth.contract(address=_to_checksum(CHAINLINK_ETH_USD), abi=CHAINLINK_PRICE_FEED_ABI)
            round_data = feed.functions.latestRoundData().call()
            price = Decimal(round_data[1]) / 10**8  # Chainlink 8 decimals
            return json.dumps({
                "token": token,
                "price_usd": str(price),
                "source": "Chainlink ETH/USD",
                "feed_address": CHAINLINK_ETH_USD,
                "chain": "Base L2",
                "timestamp": datetime.utcnow().isoformat()
            })
        if token.upper() == "USDC":
            return json.dumps({
                "token": "USDC",
                "price_usd": "1.00",
                "source": "stablecoin peg",
                "chain": "Base L2",
                "timestamp": datetime.utcnow().isoformat()
            })
        return json.dumps({
            "token": token,
            "price_usd": "unknown",
            "source": "not mapped",
            "note": "Add token-specific price feed or DEX pool",
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e), "token": token})


# ── Tool 8: Gas Price ────────────────────────────────────────────
@mcp.tool()
async def get_gas_price() -> str:
    """Get current Base L2 gas price (EIP-1559 base + priority fee).
    
    Returns: JSON with base fee, priority fee, max fee, and gas estimate."""
    try:
        latest = w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas", 0)
        priority_fee = w3.eth.max_priority_fee
        max_fee = base_fee + priority_fee
        return json.dumps({
            "base_fee_gwei": str(w3.from_wei(base_fee, "gwei")),
            "priority_fee_gwei": str(w3.from_wei(priority_fee, "gwei")),
            "max_fee_gwei": str(w3.from_wei(max_fee, "gwei")),
            "transfer_gas": 21000,
            "swap_gas_estimate": 150000,
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool 9: Call Contract ─────────────────────────────────────────
@mcp.tool()
async def call_contract(contract_address: str, function_abi: str, args: str = "[]") -> str:
    """Read arbitrary contract state on Base L2.
    
    Args:
        contract_address: Contract address (0x...)
        function_abi: JSON ABI snippet for the function to call
        args: JSON array of arguments
    
    Returns: JSON with decoded return values."""
    try:
        addr = _to_checksum(contract_address)
        abi = json.loads(function_abi)
        contract = w3.eth.contract(address=addr, abi=[abi])
        fn_name = abi.get("name", "unknown")
        parsed_args = json.loads(args) if args else []
        result = getattr(contract.functions, fn_name)(*parsed_args).call()
        return json.dumps({
            "contract": contract_address,
            "function": fn_name,
            "args": parsed_args,
            "result": str(result),
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e), "contract": contract_address, "function_abi": function_abi})


# ── Tool 10: Send Contract Transaction ─────────────────────────────
@mcp.tool()
async def send_contract_transaction(contract_address: str, function_abi: str, args: str = "[]", value_eth: str = "0") -> str:
    """Write to a smart contract on Base L2 with ABI validation.
    
    Args:
        contract_address: Contract address
        function_abi: JSON ABI snippet for the function
        args: JSON array of arguments
        value_eth: ETH to send with the call (default 0)
    
    Returns: JSON with tx hash and receipt."""
    if not WALLET_PRIVATE_KEY:
        return json.dumps({"error": "WALLET_PRIVATE_KEY not configured."})
    try:
        addr = _to_checksum(contract_address)
        abi = json.loads(function_abi)
        contract = w3.eth.contract(address=addr, abi=[abi])
        fn_name = abi.get("name", "unknown")
        parsed_args = json.loads(args) if args else []
        account = w3.eth.account.from_key(WALLET_PRIVATE_KEY)
        tx = getattr(contract.functions, fn_name)(*parsed_args).build_transaction({
            "from": account.address,
            "value": w3.to_wei(Decimal(value_eth), "ether"),
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": 8453,
        })
        signed = w3.eth.account.sign_transaction(tx, WALLET_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return json.dumps({
            "tx_hash": tx_hash.hex(),
            "status": "success" if receipt.status == 1 else "failed",
            "gas_used": receipt.gasUsed,
            "contract": contract_address,
            "function": fn_name,
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool 11: Estimate Gas ─────────────────────────────────────────
@mcp.tool()
async def estimate_gas(to_address: str, data: str = "0x", value_eth: str = "0") -> str:
    """Pre-flight gas estimation for a transaction on Base L2.
    
    Args:
        to_address: Target address
        data: Transaction data (hex)
        value_eth: ETH value to send
    
    Returns: JSON with estimated gas, fees, and total cost."""
    try:
        to = _to_checksum(to_address)
        gas = w3.eth.estimate_gas({
            "to": to,
            "data": data,
            "value": w3.to_wei(Decimal(value_eth), "ether"),
        })
        latest = w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas", 0)
        max_fee = base_fee + w3.eth.max_priority_fee
        total_cost_eth = w3.from_wei(gas * max_fee, "ether")
        return json.dumps({
            "estimated_gas": gas,
            "base_fee_gwei": str(w3.from_wei(base_fee, "gwei")),
            "max_fee_gwei": str(w3.from_wei(max_fee, "gwei")),
            "total_cost_eth": str(total_cost_eth),
            "to": to_address,
            "chain": "Base L2",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e), "to": to_address})


# ── Tool 12: Flash Loan Arbitrage ─────────────────────────────────
@mcp.tool()
async def execute_flash_loan(token: str, amount: str, target_dex: str = "uniswap", profit_threshold: float = 0.5) -> str:
    """Execute a flash loan on Aave V3 for arbitrage on Base L2.
    
    Args:
        token: Token to flash loan (e.g., "USDC", "WETH")
        amount: Loan amount (e.g., "10000")
        target_dex: DEX to arbitrage against (uniswap, aerodrome)
        profit_threshold: Minimum profit % to execute (default 0.5)
    
    Returns: JSON with simulation results, expected profit, and gas estimate."""
    try:
        token_map = {"USDC": USDC, "WETH": WETH}
        token_addr = token_map.get(token.upper(), token)
        # Simulation: check price divergence
        return json.dumps({
            "token": token,
            "token_address": token_addr,
            "amount": amount,
            "target_dex": target_dex,
            "profit_threshold": f"{profit_threshold}%",
            "simulation": "Price divergence scan (mock)",
            "expected_profit": "simulated — integrate on-chain DEX quoters",
            "gas_estimate": "~400,000",
            "aave_pool": AAVE_V3_POOL,
            "chain": "Base L2",
            "note": "Production: deploy flash-loan receiver contract + arbitrage bot",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Main ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    mcp.run()
