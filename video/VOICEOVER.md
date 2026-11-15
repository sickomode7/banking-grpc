# Voice-over cue sheet

Four silent videos, one per deliverable. Read the text under each
timestamp; the video holds long enough for that paragraph at a calm
pace. Times are the start of each cue.

Mux your recorded audio onto the silent video without re-encoding:

```bash
ffmpeg -i video/01-proto.mp4 -i 01-proto-voice.m4a \
       -c:v copy -c:a aac -shortest video/01-proto-final.mp4
```

`video/01-proto.srt` holds the same text as subtitles if you want them
burned in:

```bash
ffmpeg -i 01-proto-final.mp4 -vf "subtitles=video/01-proto.srt" out.mp4
```

## 1 · banking.proto — the contract

File `video/01-proto.mp4` — 192.2s

**00:00** — This is a distributed banking system built with gRPC and Protocol Buffers: an account service on port 50051, a transaction service on 50052 that calls it, a client, and a test suite. This first recording covers the proto contract.

**00:17** — This is the contract. gRPC generates both the client stub and the server base class from this file, so the two sides cannot disagree about the wire format. Proto 3 is the current version, and the package becomes the namespace: banking.AccountService is the name the server registers and the client resolves.

**00:40** — The assignment asks for getBalance and updateBalance. I kept those names but capitalised them as PascalCase, which the Protobuf style guide requires and the generated Python stubs follow. GetBalance is a read. UpdateBalance takes a signed amount: positive credits, negative debits, and a reason field for the audit log.

**01:02** — Every message here is a request and response pair for one method. The numbers after each field, equals one, equals two, are not ordering. They are the field's identity on the wire and must never be reused once deployed, because an old client would decode the wrong bytes.

**01:24** — The delta is the interesting field. Minus fifty debits, plus fifty credits, and the reason travels with it so the server log can say whether money moved because of a transfer or a manual adjustment.

**01:40** — The second service orchestrates transfers and owns history. InitiateTransfer answers with a transaction id, a status, and both new balances, so the caller does not need a follow-up read to know what happened.

**01:56** — Notice what is missing: no balances. Money is not passed around in messages. The transaction service asks the account service for balances, and only the account service ever writes them. That separation is the design.

**02:12** — A history row stores direction in or out, the counterparty, the amount, and balance_after. That last field is the useful one: it lets a statement be reconstructed without replaying every transaction from the beginning of time.

**02:29** — History comes back as a repeated field, which is a list on the wire, plus a count so a client can page without counting the list itself. The request takes a limit where zero means the server default of ten, and the server caps it at a hundred.

**02:50** — One protoc run produces two files. banking_pb2 holds the messages, banking_pb2_grpc holds the service stubs. They are generated, so I never edit them by hand.

**03:02** — Both are committed so a clone runs immediately, and the README still shows the command that regenerates them.

## 2 · server side — account and transaction services

File `video/02-server.mp4` — 326.6s

**00:00** — Now the server side: two processes and one RPC chain. The account service owns balances, the transaction service owns transfers and history, and it calls the account service for every balance it needs.

**00:15** — The account service owns balances and nothing else. It has no idea what a transfer is, which is why it can be tested, scaled and replaced on its own. Three seeded accounts: alice, bob and carol. A real deployment swaps this dict for a database behind the same interface.

**00:37** — This is the part that matters. gRPC serves requests on a thread pool, so two transfers can be in flight at once. A read, a compare and a write is not atomic, so both could read the same balance, both pass the funds check, and the account ends up overdrawn. The lock around the whole operation makes that impossible.

**01:03** — The store is the last line of defence: it refuses any update that would take the balance below zero, and it raises rather than returning a magic value. The service layer turns that raise into a status code. There is a test that fires fifty parallel credits and asserts the balance grew by exactly fifty.

**01:28** — Validation first: an empty user id is INVALID_ARGUMENT, not a lookup. An unknown account is NOT_FOUND. context.abort sends that code and the detail string, and on the client side the same call raises an error carrying both.

**01:45** — math.isfinite catches NaN and infinity, which is not paranoia: one NaN in a balance poisons every arithmetic result after it. A zero delta is rejected too, because it is a no-op that would only pollute the audit log.

**02:02** — Three outcomes, three codes. It worked. The account does not exist, NOT_FOUND. Or the balance would go negative, FAILED_PRECONDITION. A client can branch on those without parsing text.

**02:16** — The transaction service holds no balances, only history, and one channel created once and reused. Creating a channel per request would redo the HTTP/2 handshake every time. This process is now both a server and a client.

**02:33** — When the account service says NOT_FOUND, this re-raises exactly that code to our caller. Errors keep their meaning all the way through the chain instead of being flattened into INTERNAL.

**02:47** — One: validate. Both ids present, not the same account, amount positive and finite. All of that happens before a single balance is read, so bad input costs nothing.

**03:00** — Two: both accounts must exist, so it asks the account service rather than assuming, and a NOT_FOUND from there comes straight back to the caller. Three: read the sender's balance and compare it before moving anything. Not enough means FAILED_PRECONDITION and no state change at all.

**03:21** — Then leg one: debit the sender. If that fails there is nothing to undo, so the error goes straight back.

**03:31** — Leg two credits the receiver. Here is the honest part of a distributed system: these are two separate RPCs on two different keys, and no transaction spans them. If the credit fails, money has vanished. So I debit again with the opposite amount to put it back, then report the original error. If the rollback itself fails I log it as critical with the amount that is missing, because a silent inconsistency is worse than a loud one.

**04:05** — One transfer writes two history rows sharing one transaction id: OUT for the sender, IN for the receiver. Each row keeps balance_after from the response, so the statement is self-consistent. Newest first.

**04:20** — History confirms the account exists by asking the account service, so a real account with no activity returns an empty list rather than NOT_FOUND. Then it applies the limit with a hard cap of a hundred.

**04:37** — A thread pool of ten on a plaintext port. In production this is add_secure_port with TLS credentials and the client uses a secure channel. I kept it plaintext so the demo is reproducible, and I list that as a limitation.

**04:55** — This is the account log after one transfer: two balance reads, then a debit, then a credit. Four RPCs on this service for a single client call.

**05:08** — And this is the transaction log: the transfer id, the direction of money, the resulting balances, and history reads. Reading the two logs side by side is how you debug a distributed call, because one transfer appears in both.

## 3 · client side

File `video/03-client.mp4` — 137.6s

**00:00** — The client: one CLI, two channels. Balance commands go to the account service, transfers and history go to the transaction service. Then the test suite that keeps all of this honest.

**00:14** — The client is a thin CLI with two channels, because it talks to two services. Balance commands go straight to the account service, transfers and history go to the transaction service. That mirrors how a real client would address separate backends.

**00:33** — Every call passes a deadline. Without it, a hung server blocks the client thread forever and the user just sees a frozen terminal.

**00:44** — Errors print the status code and the server's detail string. The code is what a program branches on, the detail is what a human reads. In the demo you will see FAILED_PRECONDITION, NOT_FOUND and INVALID_ARGUMENT telling three different stories.

**01:02** — History renders direction as a sign: out is minus, in is plus, and balance_after sits next to the amount. So the table reads like a bank statement instead of a debug dump.

**01:17** — The demo command is a scripted version of the whole run: happy path, then every error case, then re-read the balances to prove the rejected calls changed nothing. I scripted it so the recording is reproducible and I am not typing live.

**01:36** — Ten tests, and they spin both services up in process on ephemeral ports, so they do not need the servers running. They cover the seeded balance, a signed delta, a transfer conserving total money, insufficient funds leaving balances untouched, unknown account, four invalid argument cases, history ordering and limit, empty history, a failed credit rolling back the debit, and fifty concurrent updates.

**02:04** — Green across the board, and the rollback test is the one I would point a reviewer at, because it exercises the failure path rather than the happy one.

## 4 · live demo

File `video/04-demo.mp4` — 163.2s

**00:00** — And the live demo: both services starting from scratch, a real transfer, the history it produces, five calls that get rejected, and proof that the rejected calls changed nothing.

**00:13** — Two processes, two ports. The account service owns the balances, the transaction service owns transfers and history. Both are now listening.

**00:24** — Alice has a thousand, bob two fifty fifty. These are plain GetBalance calls straight to the account service on 50051, and you can see them arrive in the account log.

**00:38** — One call to the transaction service on 50052. Behind it: read both balances, debit alice, credit bob, then write two history rows. Four RPCs on the account service for one client call. The response carries the transaction id, the status and both new balances, so no follow-up read is needed.

**01:01** — Alice is down to eight hundred, bob up to four fifty fifty. The total is unchanged: a transfer moves value, it does not create it.

**01:13** — One transaction id, two histories. Alice's row says out, bob's says in, and balance_after matches what GetBalance just reported. That is the consistency check I care about.

**01:26** — FAILED_PRECONDITION for insufficient funds, with the actual balance against the requested amount in the detail. NOT_FOUND for an account that does not exist. Different codes, because they are different failures.

**01:40** — INVALID_ARGUMENT for a self transfer, caught before any balance is touched. And a negative amount is rejected too: a negative transfer is a disguised withdrawal, and withdrawals belong on UpdateBalance.

**01:54** — Carol has zero, and the store refuses to go below zero. Then a plain read confirms carol is still at zero: the failed call changed nothing.

**02:07** — Final state: one recorded transfer, all the rejected calls absent from history, and the transaction log showing the same id. That is the whole failure story in one screen.

**02:21** — What I would defend: status codes instead of strings in a body, one writer per balance guarded by a lock, a compensating rollback when the credit leg fails, and a deadline on every outbound call. What is honestly missing: persistence, TLS, idempotency keys, and integer money instead of doubles.
