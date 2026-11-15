"""Account Management Service — the single source of truth for balances.

Listens on :50051. No knowledge of transfers or history; it only knows how to
read a balance and apply a signed delta to one. That keeps the two services
independently deployable.

Run:  python account_server.py
"""

from __future__ import annotations

import logging
import math
import os
import threading
from concurrent import futures

import grpc

import banking_pb2 as pb
import banking_pb2_grpc as pb_grpc

CURRENCY = "USD"
MAX_WORKERS = 10

# Seeded so the demo has something to talk to. A real service would back this
# with a database; the interface would not change.
SEED_BALANCES = {"alice": 1000.00, "bob": 250.50, "carol": 0.00}


class AccountStore:
    """In-memory balance table guarded by a lock.

    gRPC serves requests on multiple threads, so read-modify-write must be
    atomic or two concurrent transfers can both pass the funds check and
    overdraw the account.
    """

    def __init__(self, seed: dict[str, float]) -> None:
        self._lock = threading.Lock()
        self._balances = {user: round(bal, 2) for user, bal in seed.items()}

    def get(self, user_id: str) -> float | None:
        with self._lock:
            return self._balances.get(user_id)

    def apply_delta(self, user_id: str, delta: float) -> float:
        """Atomically add `delta`. Raises KeyError / ValueError on rejection."""
        with self._lock:
            if user_id not in self._balances:
                raise KeyError(user_id)
            new_balance = round(self._balances[user_id] + delta, 2)
            if new_balance < 0:
                raise ValueError(
                    f"balance would become {new_balance:.2f}; "
                    f"current {self._balances[user_id]:.2f}, delta {delta:+.2f}"
                )
            self._balances[user_id] = new_balance
            return new_balance


class AccountService(pb_grpc.AccountServiceServicer):
    def __init__(self, store: AccountStore) -> None:
        self.store = store

    def GetBalance(self, request, context):
        user_id = request.user_id.strip()
        if not user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")

        balance = self.store.get(user_id)
        if balance is None:
            context.abort(
                grpc.StatusCode.NOT_FOUND, f"account '{user_id}' does not exist"
            )

        logging.info("GetBalance     user=%-6s balance=%.2f", user_id, balance)
        return pb.BalanceResponse(
            user_id=user_id, balance=balance, currency=CURRENCY
        )

    def UpdateBalance(self, request, context):
        user_id = request.user_id.strip()
        if not user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
        if not math.isfinite(request.amount) or request.amount == 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "amount must be a non-zero, finite number",
            )

        try:
            new_balance = self.store.apply_delta(user_id, request.amount)
        except KeyError:
            context.abort(
                grpc.StatusCode.NOT_FOUND, f"account '{user_id}' does not exist"
            )
        except ValueError as exc:
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION, f"insufficient funds: {exc}"
            )

        logging.info(
            "UpdateBalance  user=%-6s delta=%+.2f (%s) -> %.2f",
            user_id,
            request.amount,
            request.reason or "unspecified",
            new_balance,
        )
        return pb.BalanceResponse(
            user_id=user_id, balance=new_balance, currency=CURRENCY
        )


def serve(port: int = 50051) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    pb_grpc.add_AccountServiceServicer_to_server(
        AccountService(AccountStore(SEED_BALANCES)), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logging.info("AccountService listening on :%d", port)
    return server


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )
    server = serve(int(os.environ.get("ACCOUNT_PORT", 50051)))
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)
