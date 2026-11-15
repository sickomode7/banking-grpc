"""Automated checks for the gRPC system.

Each test spins up both services in-process on ephemeral ports, so the suite is
self-contained and does not depend on `run.sh` being up.

Run:  python -m unittest -v test_client.py
"""

from __future__ import annotations

import threading
from unittest import mock
import unittest
from concurrent import futures

import grpc

import banking_pb2 as pb
import banking_pb2_grpc as pb_grpc
from account_server import AccountService, AccountStore
from transaction_server import TransactionService

SEED = {"alice": 1000.00, "bob": 250.50, "carol": 0.00}


class BankingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.account_server = grpc.server(futures.ThreadPoolExecutor(8))
        pb_grpc.add_AccountServiceServicer_to_server(
            AccountService(AccountStore(SEED)), self.account_server
        )
        account_port = self.account_server.add_insecure_port("localhost:0")
        self.account_server.start()

        self.tx_server = grpc.server(futures.ThreadPoolExecutor(8))
        self.tx_service = TransactionService(f"localhost:{account_port}")
        pb_grpc.add_TransactionServiceServicer_to_server(
            self.tx_service, self.tx_server
        )
        tx_port = self.tx_server.add_insecure_port("localhost:0")
        self.tx_server.start()

        self.accounts = pb_grpc.AccountServiceStub(
            grpc.insecure_channel(f"localhost:{account_port}")
        )
        self.transactions = pb_grpc.TransactionServiceStub(
            grpc.insecure_channel(f"localhost:{tx_port}")
        )

    def tearDown(self) -> None:
        self.account_server.stop(0)
        self.tx_server.stop(0)

    def balance(self, user_id: str) -> float:
        return self.accounts.GetBalance(pb.BalanceRequest(user_id=user_id)).balance

    def transfer(self, sender, receiver, amount, note=""):
        return self.transactions.InitiateTransfer(
            pb.TransferRequest(from_user_id=sender, to_user_id=receiver,
                               amount=amount, note=note)
        )

    # ---------- tests ---------------------------------------------------
    def test_get_balance_returns_seeded_value(self):
        self.assertEqual(self.balance("alice"), 1000.00)

    def test_update_balance_applies_signed_delta(self):
        self.accounts.UpdateBalance(
            pb.UpdateBalanceRequest(user_id="alice", amount=-25.50, reason="test")
        )
        self.assertEqual(self.balance("alice"), 974.50)

    def test_transfer_moves_money_and_conserves_total(self):
        before = self.balance("alice") + self.balance("bob")
        reply = self.transfer("alice", "bob", 200.00, "rent")
        after = self.balance("alice") + self.balance("bob")

        self.assertEqual(reply.status, "COMPLETED")
        self.assertAlmostEqual(before, after, places=2)
        self.assertEqual(self.balance("alice"), 800.00)
        self.assertEqual(self.balance("bob"), 450.50)

    def test_insufficient_funds_does_not_move_money(self):
        with self.assertRaises(grpc.RpcError) as ctx:
            self.transfer("bob", "alice", 5000.00)
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        self.assertEqual(self.balance("bob"), 250.50)
        self.assertEqual(self.balance("alice"), 1000.00)

    def test_unknown_account_is_not_found(self):
        with self.assertRaises(grpc.RpcError) as ctx:
            self.balance("mallory")
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.NOT_FOUND)

    def test_invalid_arguments_are_rejected(self):
        cases = [
            pb.TransferRequest(from_user_id="alice", to_user_id="alice", amount=1),
            pb.TransferRequest(from_user_id="alice", to_user_id="bob", amount=0),
            pb.TransferRequest(from_user_id="alice", to_user_id="bob", amount=-5),
            pb.TransferRequest(from_user_id="", to_user_id="bob", amount=5),
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(grpc.RpcError) as ctx:
                    self.transactions.InitiateTransfer(case)
                self.assertEqual(
                    ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT
                )

    def test_history_records_both_legs_newest_first(self):
        self.transfer("alice", "bob", 10.00, "first")
        self.transfer("alice", "bob", 20.00, "second")

        alice = self.transactions.GetTransactionHistory(
            pb.HistoryRequest(user_id="alice")
        )
        bob = self.transactions.GetTransactionHistory(
            pb.HistoryRequest(user_id="bob")
        )

        self.assertEqual(alice.count, 2)
        self.assertEqual([t.note for t in alice.transactions], ["second", "first"])
        self.assertEqual({t.direction for t in alice.transactions}, {"OUT"})
        self.assertEqual({t.direction for t in bob.transactions}, {"IN"})

        limited = self.transactions.GetTransactionHistory(
            pb.HistoryRequest(user_id="alice", limit=1)
        )
        self.assertEqual(limited.count, 1)
        self.assertEqual(limited.transactions[0].note, "second")

    def test_history_empty_for_account_without_activity(self):
        reply = self.transactions.GetTransactionHistory(
            pb.HistoryRequest(user_id="carol")
        )
        self.assertEqual(reply.count, 0)

    def test_failed_credit_rolls_back_the_debit(self):
        """If the credit leg fails, the debit must be reversed."""

        class SimulatedFailure(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

            def details(self):
                return "simulated credit failure"

        def fake_update(user_id, delta, reason):
            if reason == "transfer-in":
                raise SimulatedFailure()
            return self.tx_service.account.UpdateBalance(
                pb.UpdateBalanceRequest(user_id=user_id, amount=delta, reason=reason)
            )

        with mock.patch.object(self.tx_service, "_update", side_effect=fake_update):
            with self.assertRaises(grpc.RpcError) as ctx:
                self.transfer("alice", "bob", 300.00)

        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNAVAILABLE)
        self.assertEqual(self.balance("alice"), 1000.00)
        self.assertEqual(self.balance("bob"), 250.50)

        history = self.transactions.GetTransactionHistory(
            pb.HistoryRequest(user_id="alice")
        )
        self.assertEqual(history.count, 0)  # failed transfer leaves no record

    def test_concurrent_updates_are_not_lost(self):
        """50 parallel +1.00 credits must all land: proves the store's lock."""
        threads = [
            threading.Thread(
                target=lambda: self.accounts.UpdateBalance(
                    pb.UpdateBalanceRequest(user_id="carol", amount=1.00,
                                            reason="concurrency-test")
                )
            )
            for _ in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.balance("carol"), 50.00)


if __name__ == "__main__":
    unittest.main(verbosity=2)
