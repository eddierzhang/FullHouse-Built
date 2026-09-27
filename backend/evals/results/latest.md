# Agent evaluation

Run 2026-09-13 06:47 UTC. Each cell is the share of checks passed.

| Scenario | `qwen2.5:7b` | `qwen3.5:4b` | `scripted` |
|---|---|---|---|
| inventory-shortage | 100% | 100% | 60% |
| inventory-nothing-low | 100% | 100% | 100% |
| marketing-slow-seller | 80% | 80% | 40% |
| supply-purchase-order | 40% | 100% | 60% |
| employee-coverage-gap | 20% | 100% | 60% |
| profit-report-read-only | 100% | 100% | 100% |
| **Overall** | **73%** | **97%** | **70%** |
| Median seconds | 9 | 12 | 0 |

## Failed checks

- `qwen2.5:7b` · marketing-slow-seller · **proposal applies cleanly** — needs operator input: new_price
- `qwen2.5:7b` · supply-purchase-order · **made the right proposal** — expected a purchase order for Flour from Sysco Foods
- `qwen2.5:7b` · supply-purchase-order · **proposal applies cleanly** — no matching proposal to apply
- `qwen2.5:7b` · supply-purchase-order · **no stray proposals** — 1 proposal(s) for the wrong thing
- `qwen2.5:7b` · employee-coverage-gap · **used expected tools** — missing ['list_staff']
- `qwen2.5:7b` · employee-coverage-gap · **made the right proposal** — expected a shift tomorrow for Ana Diaz
- `qwen2.5:7b` · employee-coverage-gap · **proposal applies cleanly** — no matching proposal to apply
- `qwen2.5:7b` · employee-coverage-gap · **no stray proposals** — 1 proposal(s) for the wrong thing
- `qwen3.5:4b` · marketing-slow-seller · **proposal applies cleanly** — needs operator input: new_price
- `scripted` · inventory-shortage · **made the right proposal** — expected a reorder for Tomatoes
- `scripted` · inventory-shortage · **proposal applies cleanly** — no matching proposal to apply
- `scripted` · marketing-slow-seller · **used expected tools** — missing ['get_slow_moving_items']
- `scripted` · marketing-slow-seller · **made the right proposal** — expected a menu change for Garlic Bread
- `scripted` · marketing-slow-seller · **proposal applies cleanly** — no matching proposal to apply
- `scripted` · supply-purchase-order · **made the right proposal** — expected a purchase order for Flour from Sysco Foods
- `scripted` · supply-purchase-order · **proposal applies cleanly** — no matching proposal to apply
- `scripted` · employee-coverage-gap · **made the right proposal** — expected a shift tomorrow for Ana Diaz
- `scripted` · employee-coverage-gap · **proposal applies cleanly** — no matching proposal to apply
