"""
vorest — Buyback + Burn Script
================================
Every cycle:
  1. Claim pumpfun creator fees
  2. Calculate budget = claimed fees + X% of wallet SOL balance
  3. Load target CAs from cas.txt (up to 5)
  4. Split budget equally across CAs -> buy each -> burn each
  5. Wait 2 minutes -> repeat

Usage:
    python buyback_burn.py                          # Run main loop
    python buyback_burn.py --test-claim             # Test fee claim
    python buyback_burn.py --test-buy <mint> [sol]  # Test buy
    python buyback_burn.py --test-burn <mint>       # Test burn
"""

import os
import sys
import time
import requests
import base64
import re
from datetime import datetime

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta

# ── Config (all via env vars) ─────────────────────────────────────

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
RPC_URL = os.getenv("RPC_URL", "https://mainnet.helius-rpc.com/?api-key=YOUR_KEY").strip()

WALLET_SOL_PERCENT = float(os.getenv("WALLET_SOL_PERCENT", "30"))
CYCLE_INTERVAL_SEC = float(os.getenv("CYCLE_INTERVAL_SEC", "120"))
MAX_CAS = int(os.getenv("MAX_CAS", "5"))
TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", "6"))
MIN_BUY_SOL = float(os.getenv("MIN_BUY_SOL", "0.001"))
SOL_RESERVE = float(os.getenv("SOL_RESERVE", "0.01"))

PUMPPORTAL_API = "https://pumpportal.fun/api/trade-local"
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

# ── Helpers ───────────────────────────────────────────────────────

def load_keypair():
    if not PRIVATE_KEY:
        print("[error] PRIVATE_KEY env var not set")
        sys.exit(1)
    kp = Keypair.from_base58_string(PRIVATE_KEY)
    print(f"[config] Wallet:      {kp.pubkey()}")
    print(f"[config] SOL %:       {WALLET_SOL_PERCENT}% of wallet balance per cycle")
    print(f"[config] Interval:    {CYCLE_INTERVAL_SEC}s")
    print(f"[config] Max CAs:     {MAX_CAS}")
    print(f"[config] SOL reserve: {SOL_RESERVE} SOL")
    return kp


def ts():
    return datetime.now().strftime("%H:%M:%S")


def rpc_post(payload):
    resp = requests.post(RPC_URL, json=payload, timeout=15)
    return resp.json()


def send_signed_tx(raw_tx_bytes):
    encoded = base64.b64encode(raw_tx_bytes).decode("utf-8")
    resp = requests.post(
        RPC_URL,
        headers={"Content-Type": "application/json"},
        json={
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [encoded, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"}],
        },
        timeout=15,
    )
    data = resp.json()
    if "error" in data:
        raise Exception(f"RPC error: {data['error']}")
    return data["result"]


def get_sol_balance(pubkey_str):
    try:
        data = rpc_post({
            "jsonrpc": "2.0", "id": 1,
            "method": "getBalance",
            "params": [pubkey_str, {"commitment": "confirmed"}],
        })
        return data["result"]["value"] / 1e9
    except Exception:
        return 0.0


def load_cas_from_file():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cas.txt")
    cas = []
    if os.path.exists(path):
        with open(path, "r") as f:
            for line in f:
                ca = line.strip()
                if ca and SOLANA_ADDRESS_RE.match(ca):
                    cas.append(ca)
    return cas[:MAX_CAS]


# ── Claim PumpFun Fees ────────────────────────────────────────────

def claim_pumpfun_fees(keypair):
    pub = str(keypair.pubkey())
    print(f"[{ts()}] Claiming pumpfun fees for {pub[:8]}...")

    try:
        resp = requests.post(PUMPPORTAL_API, data={
            "publicKey": pub,
            "action": "collectCreatorFee",
            "priorityFee": 0.000001,
        }, timeout=15)

        if resp.status_code != 200:
            print(f"[claim] PumpPortal error: {resp.status_code} {resp.text[:200]}")
            return None

        tx_bytes = resp.content
        unsigned_tx = VersionedTransaction.from_bytes(tx_bytes)
        signed_tx = VersionedTransaction(unsigned_tx.message, [keypair])
        sig = send_signed_tx(bytes(signed_tx))
        print(f"[claim] OK: https://solscan.io/tx/{sig}")
        return sig

    except Exception as e:
        print(f"[claim] Failed: {e}")
        return None


# ── Buy Token ─────────────────────────────────────────────────────

def buy_token(keypair, mint, sol_amount):
    pub = str(keypair.pubkey())
    print(f"[{ts()}] Buying {sol_amount:.6f} SOL of {mint[:12]}...")

    try:
        resp = requests.post(PUMPPORTAL_API, data={
            "publicKey": pub,
            "action": "buy",
            "mint": mint,
            "amount": sol_amount,
            "denominatedInSol": "true",
            "slippage": 25,
            "priorityFee": 0.000005,
        }, timeout=15)

        if resp.status_code != 200:
            print(f"[buy] PumpPortal error: {resp.status_code} {resp.text[:200]}")
            return None

        tx_bytes = resp.content
        unsigned_tx = VersionedTransaction.from_bytes(tx_bytes)
        signed_tx = VersionedTransaction(unsigned_tx.message, [keypair])
        sig = send_signed_tx(bytes(signed_tx))
        print(f"[buy] OK: https://solscan.io/tx/{sig}")
        return sig

    except Exception as e:
        print(f"[buy] Failed: {e}")
        return None


# ── Burn Tokens ───────────────────────────────────────────────────

def get_token_balance(wallet_pubkey, mint_str):
    try:
        data = rpc_post({
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                str(wallet_pubkey),
                {"mint": mint_str},
                {"encoding": "jsonParsed"},
            ],
        })
        accounts = data.get("result", {}).get("value", [])
        if not accounts:
            return 0, None
        info = accounts[0]["account"]["data"]["parsed"]["info"]
        raw = int(info["tokenAmount"]["amount"])
        ata_addr = accounts[0]["pubkey"]
        return raw, ata_addr
    except Exception as e:
        print(f"[balance] Error: {e}")
        return 0, None


def burn_tokens(keypair, mint_str, decimals=None):
    decimals = decimals if decimals is not None else TOKEN_DECIMALS
    wallet = keypair.pubkey()

    raw_amount, ata_addr = get_token_balance(wallet, mint_str)
    if raw_amount == 0:
        print(f"[burn] No tokens to burn for {mint_str[:12]}")
        return None

    ui_amount = raw_amount / (10 ** decimals)
    print(f"[{ts()}] Burning {ui_amount:,.{decimals}f} tokens of {mint_str[:12]}...")

    mint = Pubkey.from_string(mint_str)
    token_account = Pubkey.from_string(ata_addr)

    burn_data = bytearray([15])
    burn_data.extend(raw_amount.to_bytes(8, byteorder="little"))
    burn_data.append(decimals)

    burn_ix = Instruction(
        program_id=TOKEN_PROGRAM_ID,
        accounts=[
            AccountMeta(pubkey=token_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=mint, is_signer=False, is_writable=True),
            AccountMeta(pubkey=wallet, is_signer=True, is_writable=False),
        ],
        data=bytes(burn_data),
    )

    try:
        bh_data = rpc_post({
            "jsonrpc": "2.0", "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "finalized"}],
        })
        blockhash = Hash.from_string(bh_data["result"]["value"]["blockhash"])

        msg = MessageV0.try_compile(
            payer=wallet,
            instructions=[burn_ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(msg, [keypair])
        sig = send_signed_tx(bytes(tx))
        print(f"[burn] OK -- {ui_amount:,.{decimals}f} burned: https://solscan.io/tx/{sig}")
        return sig

    except Exception as e:
        print(f"[burn] Failed: {e}")
        return None


# ── Main Loop ─────────────────────────────────────────────────────

def main_loop():
    kp = load_keypair()
    wallet_str = str(kp.pubkey())
    print()

    cas = load_cas_from_file()
    if cas:
        print(f"[file] Loaded {len(cas)} CAs from cas.txt")
    else:
        print("[file] No cas.txt found or empty -- add target CAs to cas.txt")

    print(f"\n[{ts()}] Starting buyback+burn loop...")
    print(f"{'='*70}\n")

    cycle_count = 0

    while True:
        try:
            cas = load_cas_from_file()

            cycle_count += 1
            print(f"{'='*70}")
            print(f"[{ts()}] === CYCLE #{cycle_count} ===")
            print(f"{'='*70}")

            bal_before = get_sol_balance(wallet_str)
            print(f"[{ts()}] SOL balance before claim: {bal_before:.6f}")

            claim_sig = claim_pumpfun_fees(kp)
            if claim_sig:
                time.sleep(3)

            bal_after_claim = get_sol_balance(wallet_str)
            claimed = max(0, bal_after_claim - bal_before)
            print(f"[{ts()}] SOL balance after claim:  {bal_after_claim:.6f}")
            print(f"[{ts()}] Claimed fees: {claimed:.6f} SOL")

            usable_balance = max(0, bal_after_claim - SOL_RESERVE)
            wallet_contribution = usable_balance * (WALLET_SOL_PERCENT / 100)
            total_budget = claimed + wallet_contribution
            print(f"[{ts()}] Wallet contribution ({WALLET_SOL_PERCENT}% of {usable_balance:.6f}): {wallet_contribution:.6f} SOL")
            print(f"[{ts()}] Total budget: {total_budget:.6f} SOL")

            if not cas:
                print(f"[{ts()}] No CAs available -- skipping buy+burn")
            elif total_budget < MIN_BUY_SOL:
                print(f"[{ts()}] Budget too low ({total_budget:.6f} SOL) -- skipping buy+burn")
            else:
                sol_per_ca = total_budget / len(cas)
                print(f"[{ts()}] Buying+burning {len(cas)} CAs @ {sol_per_ca:.6f} SOL each")
                print()

                results = []
                for i, ca in enumerate(cas, 1):
                    print(f"  --- CA {i}/{len(cas)}: {ca} ---")
                    buy_sig = buy_token(kp, ca, sol_per_ca)
                    if buy_sig:
                        time.sleep(3)
                        burn_sig = burn_tokens(kp, ca)
                    else:
                        burn_sig = None
                    results.append({"ca": ca, "buy": buy_sig, "burn": burn_sig})
                    print()

                print(f"--- Cycle #{cycle_count} Summary ---")
                print(f"  Claim:  {claim_sig or 'skipped/failed'} ({claimed:.6f} SOL)")
                print(f"  Budget: {total_budget:.6f} SOL")
                for r in results:
                    buy_s = "OK" if r["buy"] else "FAIL"
                    burn_s = "OK" if r["burn"] else "FAIL"
                    print(f"  {r['ca'][:16]}... buy={buy_s} burn={burn_s}")

            final_bal = get_sol_balance(wallet_str)
            print(f"  Final balance: {final_bal:.6f} SOL")
            print(f"  Next cycle in {CYCLE_INTERVAL_SEC:.0f}s")
            print(f"{'='*70}\n")

            remaining = CYCLE_INTERVAL_SEC
            while remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                sys.stdout.write(f"\r[{ts()}] Next cycle in {mins:02d}:{secs:02d}  |  CAs: {len(cas)}    ")
                sys.stdout.flush()
                step = min(5, remaining)
                time.sleep(step)
                remaining -= step
            print()

        except KeyboardInterrupt:
            print(f"\n\n[{ts()}] Stopped after {cycle_count} cycles.")
            break
        except Exception as e:
            print(f"\n[{ts()}] Loop error: {e}")
            time.sleep(10)


# ── CLI ───────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        kp = load_keypair()

        if cmd == "--test-claim":
            claim_pumpfun_fees(kp)
        elif cmd == "--test-buy":
            mint = sys.argv[2] if len(sys.argv) > 2 else None
            sol = float(sys.argv[3]) if len(sys.argv) > 3 else 0.001
            if not mint:
                print("Usage: --test-buy <mint> [sol_amount]")
                return
            buy_token(kp, mint, sol)
        elif cmd == "--test-burn":
            mint = sys.argv[2] if len(sys.argv) > 2 else None
            if not mint:
                print("Usage: --test-burn <mint>")
                return
            burn_tokens(kp, mint)
        else:
            print(f"Unknown: {cmd}")
            print("Usage: --test-claim | --test-buy <mint> [sol] | --test-burn <mint>")
    else:
        main_loop()


if __name__ == "__main__":
    main()
