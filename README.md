<img width="1500" height="500" alt="Project_Balls_3" src="https://github.com/user-attachments/assets/b245ad9c-8a66-411d-ba85-d36e5c5c3df6" />

# Vorest

The memecoin that eats memecoins.

## What is this?

vorest is a Solana memecoin with a single mechanism: **100% of creator fees are used to buy and burn other memecoins.**

Every 2 minutes, the buyback script:
1. Claims creator fees from pump.fun
2. Adds a configurable % of wallet SOL to the budget
3. Splits the budget across up to 5 target tokens
4. Buys each target token via PumpPortal
5. Burns every purchased token using SPL Token BurnChecked

All transactions are on-chain and verifiable.

## Setup

```bash
pip install solders requests
```

## Configuration

All config is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `PRIVATE_KEY` | required | Base58 wallet private key |
| `RPC_URL` | Helius mainnet | Solana RPC endpoint |
| `WALLET_SOL_PERCENT` | 30 | % of wallet SOL added to budget each cycle |
| `CYCLE_INTERVAL_SEC` | 120 | Seconds between cycles |
| `MAX_CAS` | 5 | Max target tokens per cycle |
| `SOL_RESERVE` | 0.01 | SOL kept for tx fees |
| `MIN_BUY_SOL` | 0.001 | Minimum SOL to execute a buy |
| `TOKEN_DECIMALS` | 6 | Token decimal places |

## Usage

Add target token addresses to `cas.txt` (one per line):

```
<mint_address_1>
<mint_address_2>
<mint_address_3>
```

Run:

```bash
python buyback_burn.py
```

Test commands:

```bash
python buyback_burn.py --test-claim
python buyback_burn.py --test-buy <mint> 0.001
python buyback_burn.py --test-burn <mint>
```

## How it works

- **Fee claim**: Uses PumpPortal `collectCreatorFee` action
- **Buy**: Uses PumpPortal `buy` action with 25% slippage
- **Burn**: SPL Token-2022 `BurnChecked` instruction (discriminator 15)
- **Signing**: All transactions signed locally. Private key never leaves the machine.
- **State**: Stateless. Each cycle is independent. No database.

## Links

- Website: [vorest.fun](https://vorest.fun)
- Twitter: [@vorestcoin](https://x.com/vorestcoin)

## License

MIT
