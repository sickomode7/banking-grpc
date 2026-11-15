# Video script — Banking system with gRPC

> **The four videos already exist as silent recordings in `video/`.** Their exact
> cue timings are in `video/VOICEOVER.md` — use that for recording, because it is
> generated from the same file that rendered the video and therefore matches to
> the frame. This document is the longer version: the reasoning behind each
> section, and the answers to questions a reviewer is likely to ask.

Total runtime ~11 minutes, four recordings matching the four deliverables.
Read the **SAY** lines close to as written; the rest is stage direction.

---

## Before you hit record

```bash
cd banking-grpc
# terminal 1
./run.sh
# terminal 2 (keep it free for the demo)
.venv/bin/python -m unittest test_client.py   # confirm 9/9 green first
```

Then **restart the servers** so balances are back to seed values
(`kill $(cat logs/*.pid); ./run.sh`) — the screenshots must show
alice 1000.00 / bob 250.50 / carol 0.00.

Keep three terminal tabs open, named if your terminal allows it:
`proto`, `servers`, `client`. Font size ~16 so text is readable on video.
Have `banking.proto`, `account_server.py`, `transaction_server.py`,
`client.py` open in your editor.

---

## Recording 1 — `banking.proto` (~2:30)

**On screen:** `banking.proto` scrolled to the top.

**SAY:**
> This is the contract for the whole system. Nothing talks to anything until
> these definitions exist, because gRPC generates both the client stubs and the
> server base classes from this file, and both sides are guaranteed to agree on
> the wire format.

**Point at `syntax = "proto3";` and `package banking;`.**

> Proto 3 is the current version. The package becomes the namespace in the
> generated Python module, and it also namespaces the fully-qualified service
> name — `banking.AccountService` — which is what the server registers and the
> client resolves. Without it, two services from different teams could collide.

**Point at `service AccountService` and its two methods.**

> The assignment asks for `getBalance` and `updateBalance`. I kept those names
> but capitalised them as `GetBalance` and `UpdateBalance`, because the Protobuf
> style guide requires PascalCase for method names and the generated Python
> stubs follow that. The mapping to the assignment is one-to-one.

> `GetBalance` is a read. `UpdateBalance` takes a **signed** amount: positive
> credits, negative debits, and the reason field is an audit label so the server
> log shows whether money moved because of a transfer or a manual adjustment.

**Point at the message definitions.**

> Every message here is a request/response pair for exactly one method. Field
> numbers — the `= 1`, `= 2` — are not ordering, they are the wire identity of
> the field. They must never be reused or renumbered after deployment, because
> a client compiled against the old schema would then decode the wrong bytes.
> That is also why I can add fields later without breaking anything.

**Scroll to `service TransactionService`.**

> The second service has `InitiateTransfer` and `GetTransactionHistory`.
> Transfers return a `transaction_id`, a status, and both new balances, so the
> caller does not need a follow-up `GetBalance` call to know the outcome.

**Point at `TransferRequest`.**

> `from_user_id`, `to_user_id`, `amount`, `note`. Note that the transfer request
> carries **no balances**. Money is not passed around; the transaction service
> asks the account service for it. That separation is the whole design.

**Point at `TransactionRecord` and `HistoryResponse`.**

> A history entry records direction IN or OUT, the counterparty, the amount,
> and `balance_after`, which is the important one — it lets a statement be
> reconstructed without replaying every transaction from the beginning.
> `HistoryResponse` uses `repeated`, which is a list on the wire, and a `count`
> so a client can page without counting the list itself.

**Point at `HistoryRequest.limit`.**

> A limit of zero means "server default", which I set to ten. The server also
> caps it at a hundred, so a client cannot ask for a million records and stall
> the service.

**Close:** `→ Next: python -m grpc_tools.protoc ...` show the two generated files.

> Two files come out: `banking_pb2.py` for the messages and
> `banking_pb2_grpc.py` for the service stubs. I never edit them.

---

## Recording 2 — server side (~4:00)

### 2a. `account_server.py`

**SAY:**
> This service owns balances and nothing else. It has no idea what a transfer
> is, which means it can be tested, scaled and replaced on its own.

**Point at `SEED_BALANCES`.**

> Three seeded accounts. In a real deployment this would be a database behind
> the same interface; the RPC surface would not change.

**Point at `AccountStore` and the lock.**

> This is the part that matters. gRPC serves requests on a thread pool, so two
> transfers can be in flight at the same time. A read-modify-write on a dict is
> not atomic, so both could read the same balance, both pass the funds check,
> and the account ends up overdrawn. The lock around the whole get-compare-set
> makes that impossible. There is a test that fires fifty parallel credits and
> asserts the balance grew by exactly fifty.

**Point at `apply_delta`'s rejection.**

> The store is the last line of defence: it refuses any update that would take
> the balance below zero. It raises, and the service layer converts that into a
> status code.

**Point at `GetBalance`.**

> Validation first — empty `user_id` is `INVALID_ARGUMENT`, not a lookup. Then
> `NOT_FOUND` for an unknown account. `context.abort` on the server side becomes
> an exception on the client side carrying that code and detail string.

**Point at `UpdateBalance`.**

> `math.isfinite` catches `NaN` and infinity, which is not paranoia: JSON and
> float parsing both let those through, and one `NaN` in a balance poisons every
> arithmetic result after it. A zero delta is rejected too, because it is a
> no-op that would pollute the audit log.

**Point at the logging line.**

> Every mutation is logged with the reason field, so the terminal shows the
> money moving.

### 2b. `transaction_server.py`

**SAY:**
> This service holds no balances. It holds the history and it orchestrates.
> For every balance read or write it makes an outbound gRPC call to the account
> service, which is what makes the system distributed rather than two classes
> in one process.

**Point at `__init__` and the stub.**

> One channel, created once and reused. Creating a channel per request would
> redo the HTTP/2 handshake every time. The transaction service is now a
> **client** of the account service, so it is both a server and a client in the
> same process.

**Point at `_balance`.**

> Notice the error translation: if the account service says `NOT_FOUND`, this
> re-raises exactly that code to our caller. The error keeps its meaning all the
> way through the chain instead of being flattened into `INTERNAL`.

**Walk `InitiateTransfer` in order.**

> 1. Validate: both ids present, not the same account, amount positive and finite.
> 2. Both accounts must exist. Asking the account service, not assuming.
> 3. Read the sender's balance and compare with the amount **before** moving
>    anything. If there is not enough, `FAILED_PRECONDITION` and no state
>    changes at all.
> 4. Debit first, then credit.

**Point at the rollback block and slow down.**

> This is the honest part of a distributed system. A transfer is two separate
> RPCs on two different keys — there is no transaction spanning them. If the
> debit succeeds and the credit fails, money has vanished. So on a credit
> failure I call `UpdateBalance` again with the opposite amount to put it back,
> then report the original error with the note that the debit was rolled back.
> And if the rollback itself fails, I log it as critical with the amount that is
> missing, because a silent inconsistency is worse than a loud one.
>
> A production system would replace this with a saga plus an idempotency key, so
> a retry cannot apply the same transfer twice. I would mention that as the next
> step.

**Point at `_record` twice for one transfer.**

> One transfer writes two history rows sharing one `transaction_id`: OUT for the
> sender, IN for the receiver. Each row stores `balance_after` from the response,
> so the statement is self-consistent.

**Point at `GetTransactionHistory`.**

> It validates, confirms the account exists by asking the account service — so a
> real account with no activity returns an empty list, not `NOT_FOUND` — then
> applies the limit with a hard cap of a hundred.

**Point at `serve()`.**

> A thread pool of ten, plain-text port. In production this is
> `add_secure_port` with TLS credentials, and the client would use
> `secure_channel` with the server certificate. I deliberately kept it insecure
> so the demo is reproducible, and I list that as a limitation.

**Show the two server logs side by side for ten seconds.**

> Two log files. The account log shows the raw balance mutations. The
> transaction log shows the transfer id, the direction of money, and the
> resulting balances. Reading them together is how you debug a distributed call
> — a single transfer appears in both.

---

## Recording 3 — client side (~2:30)

**On screen:** `client.py`.

**SAY:**
> The client is a thin CLI. It creates two channels because it talks to two
> services — balance commands go straight to the account service, transfers and
> history go to the transaction service. That mirrors how a real client would
> address separate backends.

**Point at `TIMEOUT = 5.0` and `timeout=TIMEOUT`.**

> Every call passes a deadline. Without it, a hung server blocks the client
> thread forever and the user just sees a frozen terminal.

**Point at `rpc_error`.**

> Errors are printed with the status **code** and the server's detail string.
> The code is what a program branches on; the detail is what a human reads. I
> print both because in the demo you will see `FAILED_PRECONDITION` versus
> `NOT_FOUND` versus `INVALID_ARGUMENT` telling three different stories.

**Point at the history printer.**

> Direction is rendered as a sign: OUT is minus, IN is plus. So the table reads
> like a bank statement instead of a debug dump.

**Point at `cmd_demo`.**

> `demo` is a scripted version of the whole test, so the run is reproducible
> and I am not typing commands live. It walks happy path, then every error case,
> then re-reads the balances to prove the rejected calls changed nothing.

**Mention the test suite briefly.**

> There are nine automated tests: seeded balance, signed delta, transfer
> conserving total money, insufficient funds leaving balances untouched, unknown
> account, four invalid-argument cases, history ordering and limit, empty
> history, and fifty concurrent updates. They spin both services up in-process
> on ephemeral ports, so they do not depend on the servers running.

---

## Recording 4 — demo (~3:00)

**On screen:** terminal with servers already running. Type the commands, pause
after each so the output is readable.

### 1. Starting balances

```bash
python client.py balance alice
```

**SAY:**
> Alice has one thousand. This is a plain `GetBalance` straight to the account
> service on port 50051. On the account log you can see the request arrive.

```bash
python client.py balance bob
```

### 2. Transfer

```bash
python client.py transfer alice bob 200 --note "rent"
```

**SAY:**
> One call to the transaction service on 50052. Watch what happens across the
> two logs: the transaction service first reads both balances, then debits
> alice, then credits bob, and writes two history rows. Four RPCs on the account
> service for one client call — that is the RPC chain.

> The response gives us the transaction id, the status, and both new balances,
> so no follow-up read is needed.

### 3. Verify

```bash
python client.py balance alice
python client.py history alice
python client.py history bob
```

**SAY:**
> Alice is down to eight hundred, bob up to four-fifty-fifty. Total money is
> unchanged — a transfer moves value, it does not create it.

> The same transaction id appears in both histories with opposite directions.
> Alice's row says OUT, bob's says IN, and `balance_after` matches what
> `GetBalance` reported.

### 4. Errors — the graded part

```bash
python client.py transfer bob alice 5000 --note "too much"
```

> `FAILED_PRECONDITION`, insufficient funds, and it prints the actual balance
> against the requested amount. Bob still has two-fifty-fifty.

```bash
python client.py balance mallory
```

> `NOT_FOUND`, account does not exist.

```bash
python client.py transfer alice alice 10
```

> `INVALID_ARGUMENT`, cannot transfer to the same account — caught before any
> balance is touched.

```bash
python client.py transfer alice bob -50
```

> `INVALID_ARGUMENT`, amount must be positive. A negative transfer is a
> disguised withdrawal, so it is rejected; withdrawals belong on
> `UpdateBalance`.

```bash
python client.py update carol -1
```

> `FAILED_PRECONDITION`. Carol has zero and the store refuses to go below zero.

```bash
python client.py balance carol
```

> Still zero. That is the point: the failed calls changed nothing.

### 5. Close

**SAY:**
> To summarise the design decisions I would defend: errors are real gRPC status
> codes, not strings in a body; balances are owned by exactly one service; the
> store takes a lock so concurrent transfers cannot overdraw; the two-leg
> transfer has a compensating rollback; every outbound call has a deadline; and
> history is written on both sides with a shared transaction id.
>
> The known limitations are in the README: in-memory storage resets on restart,
> money is a `double` where production would use `int64` minor units, and there
> is no auth, TLS, or idempotency key yet. Those are the three things I would add
> next.

---

## Likely questions and short answers

**Why two services instead of one?**
Separate ownership. Balance is a hot, sensitive resource; history and
orchestration change at a different rate. It also lets me show real
service-to-service RPC rather than two classes in one file.

**What happens if the server crashes mid-transfer?**
Today: the debit is committed and the history row may be missing. The rollback
only covers a failed RPC, not a crash. The fix is a saga log or a two-phase
commit, and an idempotency key so the retry is safe.

**Why not write the balance inside the transaction service?**
Then two services would own the same data and the lock would be worthless
across processes. One writer.

**Why `double` for money?**
For a demo it is fine, but binary floating point cannot represent 0.10 exactly,
so cents drift. Production uses integer minor units or a decimal string. I list
it as a limitation rather than pretending it is correct.

**Is `GetTransactionHistory` a stream?**
No, unary with a limit. Streaming would be better for long histories; the proto
would change `returns (stream TransactionRecord)` and the client would iterate.

**What does `context.abort` do?**
On the server it terminates the RPC and sends the status code plus detail; on
the client the same call raises `grpc.RpcError` carrying `code()` and
`details()`. That is why error handling is one `except` block rather than
per-method parsing.

**Why a lock if Python has the GIL?**
The GIL guarantees one bytecode at a time, not one logical operation. A read,
a compare and a write are three bytecodes with a thread switch possible between
them, so interleaving still corrupts the balance.
