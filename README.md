# Distributed Banking System — gRPC + Protocol Buffers

Two independently running gRPC services plus a client. Balances live in one
service, transfer orchestration and history in the other, and they talk to each
other over gRPC.

```
                ┌──────────────────────┐
   client.py    │  TransactionService  │   :50052
 ──────────────▶│  InitiateTransfer    │
                │  GetTransactionHistory
                └──────────┬───────────┘
                           │ gRPC (service-to-service)
                           ▼
                ┌──────────────────────┐    :50051
                │   AccountService     │
                │   GetBalance         │
                │   UpdateBalance      │
                └──────────────────────┘
```

## Files

| file | role |
|---|---|
| `banking.proto` | service + message definitions (deliverable 1) |
| `account_server.py` | Account Management Service, own process, port 50051 |
| `transaction_server.py` | Transaction Service, own process, port 50052, calls the account service |
| `client.py` | CLI client + scripted `demo` mode |
| `test_client.py` | 10 automated checks: validation, funds, rollback, concurrency |
| `run.sh` | starts both servers in the background |
| `render_screenshots.py` | turns captured output into PNGs for the report |
| `make_videos.py` | renders the four silent walkthrough videos + cue sheet |
| `video/` | the four MP4s, SRT subtitles, and `VOICEOVER.md` |
| `SCRIPT.md` | full voice-over script and likely Q&A |
| `outputs/` | screenshots of the real runs |

## Screenshots (`outputs/`)

| image | shows |
|---|---|
| `demo-1-happy-path.png` | balances, transfer, post-transfer balances, both histories |
| `demo-2-errors.png` | all seven rejected calls, then balances unchanged |
| `cli.png` | single transfer + `--limit` history |
| `tests.png` | 10/10 tests green |
| `account-server.png` | account log: the RPC chain arriving at :50051 |
| `transaction-server.png` | transaction log: transfer id, direction, resulting balances |

Regenerate any of them from a real run:

```bash
.venv/bin/python client.py demo > logs/demo.txt
.venv/bin/python render_screenshots.py logs/demo.txt outputs/demo.png "python client.py demo"
```


## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install grpcio grpcio-tools
.venv/bin/python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. banking.proto
```

## Run

```bash
./run.sh                 # terminal 1: both servers
.venv/bin/python client.py demo      # terminal 2: full scenario
```

Manual commands:

```bash
.venv/bin/python client.py balance  alice
.venv/bin/python client.py update   alice -100
.venv/bin/python client.py transfer alice bob 200 --note "rent"
.venv/bin/python client.py history  alice --limit 5
```

Tests:

```bash
.venv/bin/python -m unittest -v test_client.py
```

## Seed data

| user | starting balance |
|---|---|
| alice | 1000.00 |
| bob | 250.50 |
| carol | 0.00 |

## Error handling

Errors travel as real gRPC status codes, not strings in a response body — so a
client can branch on them programmatically.

| case | code | example detail |
|---|---|---|
| unknown account | `NOT_FOUND` | `account 'mallory' does not exist` |
| transfer more than balance | `FAILED_PRECONDITION` | `insufficient funds: balance 450.50, requested 9999.00` |
| debit that would go negative | `FAILED_PRECONDITION` | `insufficient funds: balance would become -1.00` |
| missing user id | `INVALID_ARGUMENT` | `user_id is required` |
| zero / negative / NaN amount | `INVALID_ARGUMENT` | `amount must be a positive, finite number` |
| transfer to self | `INVALID_ARGUMENT` | `cannot transfer to the same account` |
| account service unreachable | `UNAVAILABLE` | passed through from the outbound call |
| account service slower than 3 s | `DEADLINE_EXCEEDED` | per-call timeout |

## Design notes worth defending in the review

- **Two processes, one RPC chain.** The transaction service holds no balances.
  Every read/write is an outbound RPC, so the two services can be scaled and
  deployed separately. That is what makes it distributed rather than two
  classes in one process.
- **A lock, not a hope.** Balance read-modify-write is guarded by
  `threading.Lock` because gRPC serves on a thread pool. Without it two
  concurrent transfers can both pass the funds check and overdraw the account.
  `test_concurrent_updates_are_not_lost` proves the 50 parallel credits all land.
- **Compensating rollback.** A transfer is two separate RPCs (debit then credit)
  with no shared transaction. If the credit fails, the debit is reversed and the
  client gets the error. A real system would use a saga with an idempotency key
  so a retried request is not applied twice.
- **Timeouts on every outbound call.** A hung account service surfaces as
  `DEADLINE_EXCEEDED` in 3 s instead of hanging the caller's thread forever.
- **History is written on both sides**, outbound for the sender and inbound for
  the receiver, sharing one `transaction_id`, newest first.

## Known limitations (honest list)

- Money is `double`; production should use `int64` minor units or a decimal
  string, since binary floats cannot represent `0.10` exactly.
- Storage is in-memory, so a restart resets balances and history.
- No auth, no TLS (`insecure_channel`), no idempotency keys, no persistence.
- `GetTransactionHistory` is unary, not `server streaming`. Streaming would be
  the natural next step for long histories.
