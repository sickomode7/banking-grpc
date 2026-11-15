"""Transaction Service — orchestrates transfers and keeps the history.

Listens on :50052. It owns no balances: for every balance read or write it
makes an outbound gRPC call to the Account Service. That is the distributed
part of the assignment: two processes, one RPC chain.

Run:  python transaction_server.py      (account_server.py must be up first)
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
import uuid
from collections import defaultdict
from concurrent import futures

import grpc

import banking_pb2 as pb
import banking_pb2_grpc as pb_grpc

ACCOUNT_ADDRESS = os.environ.get("ACCOUNT_ADDRESS", "localhost:50051")
CALL_TIMEOUT = 3.0  # seconds; a hung account service must not hang the caller forever
DEFAULT_HISTORY_LIMIT = 10
MAX_HISTORY_LIMIT = 100


class TransactionService(pb_grpc.TransactionServiceServicer):
    def __init__(self, account_address: str = ACCOUNT_ADDRESS) -> None:
        self.channel = grpc.insecure_channel(account_address)
        self.account = pb_grpc.AccountServiceStub(self.channel)
        self._lock = threading.Lock()
        self._history: dict[str, list[pb.TransactionRecord]] = defaultdict(list)

    # ---------- helpers -------------------------------------------------

    def _balance(self, user_id: str, context) -> float:
        """Ask the Account Service for a balance, translating its errors."""
        try:
            reply = self.account.GetBalance(
                pb.BalanceRequest(user_id=user_id), timeout=CALL_TIMEOUT
            )
        except grpc.RpcError as exc:
            context.abort(exc.code(), exc.details())
        return reply.balance

    def _update(self, user_id: str, delta: float, reason: str) -> pb.BalanceResponse:
        """Ask the Account Service to move money. Raises grpc.RpcError on failure."""
        return self.account.UpdateBalance(
            pb.UpdateBalanceRequest(user_id=user_id, amount=delta, reason=reason),
            timeout=CALL_TIMEOUT,
        )

    def _record(
        self, user_id: str, counterparty: str, direction: str, amount: float,
        balance_after: float, transaction_id: str, note: str,
    ) -> None:
        entry = pb.TransactionRecord(
            transaction_id=transaction_id,
            counterparty=counterparty,
            direction=direction,
            amount=round(amount, 2),
            balance_after=round(balance_after, 2),
            timestamp=int(time.time()),
            note=note,
        )
        with self._lock:
            self._history[user_id].insert(0, entry)  # newest first

    # ---------- RPCs ----------------------------------------------------

    def InitiateTransfer(self, request, context):
        sender = request.from_user_id.strip()
        receiver = request.to_user_id.strip()
        amount = round(request.amount, 2)

        if not sender or not receiver:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "from_user_id and to_user_id are required",
            )
        if sender == receiver:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "cannot transfer to the same account",
            )
        if not math.isfinite(amount) or amount <= 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "amount must be a positive, finite number",
            )

        # Both accounts must exist (NOT_FOUND bubbles up from here) and we need
        # the sender balance to decide on funds before touching either account.
        sender_balance = self._balance(sender, context)
        self._balance(receiver, context)

        if sender_balance < amount:
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                f"insufficient funds: balance {sender_balance:.2f}, "
                f"requested {amount:.2f}",
            )

        transaction_id = uuid.uuid4().hex[:12]
        note = request.note.strip()

        # Debit first...
        try:
            debited = self._update(sender, -amount, "transfer-out")
        except grpc.RpcError as exc:
            context.abort(exc.code(), exc.details())

        # ...then credit. If the credit fails we must undo the debit, otherwise
        # money disappears. Same reasoning a database transaction would use.
        try:
            credited = self._update(receiver, amount, "transfer-in")
        except grpc.RpcError as exc:
            try:
                self._update(sender, amount, "rollback")
                logging.warning(
                    "transfer %s rolled back: credit to %s failed (%s)",
                    transaction_id, receiver, exc.details(),
                )
            except grpc.RpcError as rollback_exc:
                logging.critical(
                    "transfer %s INCONSISTENT: credit failed (%s) and rollback "
                    "failed (%s); %s is %0.2f short",
                    transaction_id, exc.details(), rollback_exc.details(),
                    sender, amount,
                )
            context.abort(
                exc.code(),
                f"credit to '{receiver}' failed ({exc.details()}); debit rolled back",
            )

        self._record(sender, receiver, "OUT", amount, debited.balance,
                     transaction_id, note)
        self._record(receiver, sender, "IN", amount, credited.balance,
                     transaction_id, note)

        logging.info(
            "Transfer %s  %s -> %s  %.2f  (balances %.2f / %.2f)",
            transaction_id, sender, receiver, amount,
            debited.balance, credited.balance,
        )
        return pb.TransferResponse(
            transaction_id=transaction_id,
            status="COMPLETED",
            from_new_balance=debited.balance,
            to_new_balance=credited.balance,
        )

    def GetTransactionHistory(self, request, context):
        user_id = request.user_id.strip()
        if not user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")

        # Existence check lives in the account service; ask it rather than
        # guessing, so a valid account with no history is not misreported.
        self._balance(user_id, context)

        limit = request.limit or DEFAULT_HISTORY_LIMIT
        if limit < 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "limit must be zero or positive"
            )
        limit = min(limit, MAX_HISTORY_LIMIT)

        with self._lock:
            entries = self._history.get(user_id, [])[:limit]

        logging.info("History        user=%-6s returned=%d", user_id, len(entries))
        return pb.HistoryResponse(
            user_id=user_id,
            transactions=entries,
            count=len(entries),
        )


def serve(port: int = 50052) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_TransactionServiceServicer_to_server(
        TransactionService(), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logging.info(
        "TransactionService listening on :%d (account service at %s)",
        port, ACCOUNT_ADDRESS,
    )
    return server


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )
    server = serve(int(os.environ.get("TRANSACTION_PORT", 50052)))
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)
