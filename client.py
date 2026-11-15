"""gRPC client for the banking system.

Talks to the Transaction Service (:50052) for transfers and history, and
directly to the Account Service (:50051) for raw balance reads/writes.

Usage:
  python client.py balance  alice
  python client.py update   alice -100
  python client.py transfer alice bob 200 --note "rent"
  python client.py history  alice --limit 5
  python client.py demo                  # scripted end-to-end run
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import grpc

import banking_pb2 as pb
import banking_pb2_grpc as pb_grpc

ACCOUNT_ADDRESS = os.environ.get("ACCOUNT_ADDRESS", "localhost:50051")
TRANSACTION_ADDRESS = os.environ.get("TRANSACTION_ADDRESS", "localhost:50052")
TIMEOUT = 5.0

account_channel = grpc.insecure_channel(ACCOUNT_ADDRESS)
transaction_channel = grpc.insecure_channel(TRANSACTION_ADDRESS)
accounts = pb_grpc.AccountServiceStub(account_channel)
transactions = pb_grpc.TransactionServiceStub(transaction_channel)


# ---------- formatting ---------------------------------------------------
def rule(title: str = "") -> None:
    print(f"\n{'─' * 62}")
    if title:
        print(f"  {title}")
        print(f"{'─' * 62}")


def show_balance(reply: pb.BalanceResponse) -> None:
    print(f"  user    : {reply.user_id}")
    print(f"  balance : {reply.balance:,.2f} {reply.currency}")


def show_history(reply: pb.HistoryResponse) -> None:
    print(f"  user : {reply.user_id}    records: {reply.count}")
    if not reply.transactions:
        print("  (no transactions yet)")
        return
    print(f"\n  {'when':<17} {'id':<14} {'dir':<4} {'counterparty':<12} "
          f"{'amount':>11} {'balance':>10}  note")
    for t in reply.transactions:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(t.timestamp))
        sign = "−" if t.direction == "OUT" else "+"
        print(f"  {when:<17} {t.transaction_id:<14} {t.direction:<4} "
              f"{t.counterparty:<12} {sign + format(t.amount, ',.2f'):>11} "
              f"{t.balance_after:>10,.2f}  {t.note}")


def rpc_error(exc: grpc.RpcError) -> None:
    print(f"  ! RPC failed [{exc.code().name}] {exc.details()}")


# ---------- commands -----------------------------------------------------
def cmd_balance(user_id: str) -> bool:
    rule(f"GetBalance({user_id})")
    try:
        show_balance(accounts.GetBalance(pb.BalanceRequest(user_id=user_id),
                                         timeout=TIMEOUT))
        return True
    except grpc.RpcError as exc:
        rpc_error(exc)
        return False


def cmd_update(user_id: str, amount: float) -> bool:
    rule(f"UpdateBalance({user_id}, {amount:+.2f})")
    try:
        show_balance(accounts.UpdateBalance(
            pb.UpdateBalanceRequest(user_id=user_id, amount=amount,
                                    reason="manual-adjustment"),
            timeout=TIMEOUT,
        ))
        return True
    except grpc.RpcError as exc:
        rpc_error(exc)
        return False


def cmd_transfer(sender: str, receiver: str, amount: float, note: str) -> bool:
    rule(f"InitiateTransfer({sender} → {receiver}, {amount:,.2f})")
    try:
        reply = transactions.InitiateTransfer(
            pb.TransferRequest(from_user_id=sender, to_user_id=receiver,
                               amount=amount, note=note),
            timeout=TIMEOUT,
        )
        print(f"  transaction : {reply.transaction_id}")
        print(f"  status      : {reply.status}")
        print(f"  {sender} balance : {reply.from_new_balance:,.2f}")
        print(f"  {receiver} balance : {reply.to_new_balance:,.2f}")
        return True
    except grpc.RpcError as exc:
        rpc_error(exc)
        return False


def cmd_history(user_id: str, limit: int = 0) -> bool:
    rule(f"GetTransactionHistory({user_id}, limit={limit or 'default'})")
    try:
        show_history(transactions.GetTransactionHistory(
            pb.HistoryRequest(user_id=user_id, limit=limit), timeout=TIMEOUT))
        return True
    except grpc.RpcError as exc:
        rpc_error(exc)
        return False


def cmd_demo() -> None:
    print("\n=== Banking gRPC demo ===")
    print(f"Account service     : {ACCOUNT_ADDRESS}")
    print(f"Transaction service : {TRANSACTION_ADDRESS}")

    rule("1. Starting balances")
    for user in ("alice", "bob"):
        cmd_balance(user)

    rule("2. Happy path: alice pays bob 200.00")
    cmd_transfer("alice", "bob", 200.00, "rent")

    rule("3. Balances after the transfer")
    for user in ("alice", "bob"):
        cmd_balance(user)

    rule("4. History for both sides of that transfer")
    cmd_history("alice")
    cmd_history("bob")

    rule("5. Error handling")
    print("  a) insufficient funds")
    cmd_transfer("bob", "alice", 9999.00, "too much")
    print("\n  b) unknown account")
    cmd_balance("mallory")
    print("\n  c) transfer to self")
    cmd_transfer("alice", "alice", 10.00, "self")
    print("\n  d) negative amount")
    cmd_transfer("alice", "bob", -50.00, "negative")
    print("\n  e) empty user id")
    cmd_balance("")
    print("\n  f) debit below zero via UpdateBalance")
    cmd_update("carol", -1.00)
    print("\n  g) zero delta rejected")
    cmd_update("alice", 0)

    rule("6. State is unchanged by the rejected calls")
    for user in ("alice", "bob", "carol"):
        cmd_balance(user)
    cmd_history("alice")
    print()


# ---------- entry point --------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Banking gRPC client")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("balance", help="get an account balance")
    p.add_argument("user_id")

    p = sub.add_parser("update", help="apply a signed delta to a balance")
    p.add_argument("user_id")
    p.add_argument("amount", type=float)

    p = sub.add_parser("transfer", help="transfer money between accounts")
    p.add_argument("from_user_id")
    p.add_argument("to_user_id")
    p.add_argument("amount", type=float)
    p.add_argument("--note", default="")

    p = sub.add_parser("history", help="list transactions for an account")
    p.add_argument("user_id")
    p.add_argument("--limit", type=int, default=0)

    sub.add_parser("demo", help="run the scripted end-to-end scenario")

    args = parser.parse_args(argv)

    match args.command:
        case "balance":
            return 0 if cmd_balance(args.user_id) else 1
        case "update":
            return 0 if cmd_update(args.user_id, args.amount) else 1
        case "transfer":
            return 0 if cmd_transfer(args.from_user_id, args.to_user_id,
                                     args.amount, args.note) else 1
        case "history":
            return 0 if cmd_history(args.user_id, args.limit) else 1
        case "demo":
            cmd_demo()
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
