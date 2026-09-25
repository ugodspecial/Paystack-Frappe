"""
An in-memory Paystack API for tests.

Patches PaystackClient._send (the only HTTP choke point) so no test talks to
Paystack. Tests drive the "payer" with pay() / fail() / abandon() and build
signed webhooks with webhook().
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import itertools
import json
from contextlib import contextmanager
from typing import Any, Optional
from unittest.mock import patch

from frappe_paystack.core.client import PaystackClient, PaystackResponse

_ids = itertools.count(4_000_000_001)


class FakePaystack:
    def __init__(self) -> None:
        self.transactions: dict = {}  # reference -> tx
        self.refunds: dict = {}  # id -> refund
        self.settlements: dict = {}  # id -> {"payout": {...}, "transactions": [...]}
        self.calls: list = []
        self.fail_next: Optional[str] = None  # raise a transport error on the next call
        self.charge_status = "success"
        self.initialize_error: Optional[str] = None

    # ------------------------------------------------------------------ transport

    def send(self, client: PaystackClient, method: str, path: str, json: Optional[dict] = None,
             params: Optional[dict] = None, timeout: int = 30) -> PaystackResponse:
        self.calls.append({"method": method, "path": path, "json": copy.deepcopy(json), "params": params,
                           "secret_key": client.secret_key})
        if self.fail_next:
            message, self.fail_next = self.fail_next, None
            raise ConnectionError(message)
        if client.secret_key.startswith("sk_bad"):
            return self.reply(401, False, "Invalid key")

        if method == "POST" and path == "/transaction/initialize":
            return self.initialize(json)
        if method == "GET" and path.startswith("/transaction/verify/"):
            tx = self.transactions.get(path.rsplit("/", 1)[-1])
            if not tx:
                return self.reply(404, False, "Transaction reference not found")
            return self.reply(200, True, "Verification successful", copy.deepcopy(tx))
        if method == "POST" and path == "/transaction/charge_authorization":
            return self.charge(json)
        if method == "GET" and path == "/transaction":
            return self.reply(200, True, "Transactions retrieved", [copy.deepcopy(t) for t in self.transactions.values()])
        if method == "GET" and path.startswith("/transaction/"):
            tx_id = path.rsplit("/", 1)[-1]
            for tx in self.transactions.values():
                if str(tx["id"]) == tx_id:
                    return self.reply(200, True, "Transaction retrieved", copy.deepcopy(tx))
            return self.reply(404, False, "Transaction not found")
        if method == "POST" and path == "/refund":
            return self.create_refund(json)
        if method == "GET" and path.startswith("/refund/"):
            refund = self.refunds.get(path.rsplit("/", 1)[-1])
            if not refund:
                return self.reply(404, False, "Refund not found")
            return self.reply(200, True, "Refund retrieved", copy.deepcopy(refund))
        if method == "GET" and path == "/refund":
            txn = str((params or {}).get("transaction"))
            return self.reply(200, True, "Refunds retrieved",
                              [r for r in self.refunds.values() if str(r["transaction"]["id"]) == txn])
        if method == "GET" and path.startswith("/settlement/") and path.endswith("/transactions"):
            settlement_id = path.split("/")[2]
            page = int((params or {}).get("page") or 1)
            rows = self.settlements.get(settlement_id, {}).get("transactions", []) if page == 1 else []
            return self.reply(200, True, "Transactions retrieved", rows)
        if method == "GET" and path == "/settlement":
            return self.reply(200, True, "Settlements retrieved", [s["payout"] for s in self.settlements.values()])
        return self.reply(404, False, f"Fake Paystack has no route for {method} {path}")

    @staticmethod
    def reply(status_code: int, ok: bool, message: str, data: Any = None) -> PaystackResponse:
        body = {"status": ok, "message": message}
        if data is not None:
            body["data"] = data
        return PaystackResponse(status_code, body, json.dumps(body))

    @contextmanager
    def installed(self):
        fake = self

        def _send(client_self, method, path, json=None, params=None, timeout=30):
            return fake.send(client_self, method, path, json=json, params=params, timeout=timeout)

        with patch.object(PaystackClient, "_send", _send):
            yield self

    # ------------------------------------------------------------------ transactions

    def initialize(self, body: dict) -> PaystackResponse:
        if self.initialize_error:
            return self.reply(400, False, self.initialize_error)
        reference = body["reference"]
        if reference in self.transactions:
            return self.reply(400, False, "Duplicate Transaction Reference")
        access_code = f"ac_{reference}"
        self.transactions[reference] = {
            "id": next(_ids),
            "domain": "test",
            "status": "abandoned",
            "reference": reference,
            "amount": body["amount"],
            "currency": body["currency"],
            "channel": None,
            "fees": None,
            "paid_at": None,
            "gateway_response": "The transaction was not completed",
            "metadata": body.get("metadata") or {},
            "customer": {"email": body["email"], "customer_code": f"CUS_{abs(hash(body['email'])) % 10**8}"},
            "authorization": {},
            "access_code": access_code,
            "callback_url": body.get("callback_url"),
        }
        return self.reply(200, True, "Authorization URL created", {
            "authorization_url": f"https://checkout.paystack.com/{access_code}",
            "access_code": access_code,
            "reference": reference,
        })

    def pay(self, reference: str, amount: Optional[int] = None, currency: Optional[str] = None,
            status: str = "success", reusable: bool = True, fees: int = 150) -> dict:
        """Simulate the payer completing (or failing) the transaction behind a reference."""
        tx = self.transactions[reference]
        tx.update({
            "status": status,
            "paid_at": "2026-09-25T10:00:00.000Z" if status == "success" else None,
            "channel": "card",
            "fees": fees if status == "success" else 0,
            "gateway_response": "Approved" if status == "success" else "Declined",
        })
        if amount is not None:
            tx["amount"] = amount
        if currency is not None:
            tx["currency"] = currency
        if status == "success":
            tx["authorization"] = {
                "authorization_code": f"AUTH_{reference}",
                "bin": "408408",
                "last4": "4081",
                "exp_month": "12",
                "exp_year": "2099",
                "channel": "card",
                "card_type": "visa",
                "bank": "TEST BANK",
                "brand": "visa",
                "reusable": reusable,
                "signature": f"SIG_{tx['customer']['email']}",
            }
        return copy.deepcopy(tx)

    def fail(self, reference: str) -> dict:
        return self.pay(reference, status="failed")

    def abandon(self, reference: str) -> dict:
        return self.pay(reference, status="abandoned")

    def charge(self, body: dict) -> PaystackResponse:
        reference = body["reference"]
        self.transactions[reference] = {
            "id": next(_ids),
            "domain": "test",
            "status": self.charge_status,
            "reference": reference,
            "amount": body["amount"],
            "currency": body["currency"],
            "channel": "card",
            "fees": 100,
            "paid_at": "2026-09-25T10:00:00.000Z",
            "gateway_response": "Approved" if self.charge_status == "success" else "Declined",
            "metadata": body.get("metadata") or {},
            "customer": {"email": body["email"], "customer_code": "CUS_saved"},
            "authorization": {"authorization_code": body["authorization_code"], "reusable": True, "channel": "card",
                              "signature": "SIG_saved", "last4": "4081"},
        }
        return self.reply(200, True, "Charge attempted", copy.deepcopy(self.transactions[reference]))

    def transaction(self, reference: str) -> dict:
        return copy.deepcopy(self.transactions[reference])

    # ------------------------------------------------------------------ refunds

    def create_refund(self, body: dict) -> PaystackResponse:
        tx = next((t for t in self.transactions.values() if str(t["id"]) == str(body["transaction"])), None)
        if not tx:
            return self.reply(404, False, "Transaction not found")
        refund_id = str(next(_ids))
        refund = {
            "id": int(refund_id),
            "status": "pending",
            "amount": body.get("amount") or tx["amount"],
            "currency": body.get("currency") or tx["currency"],
            "merchant_note": body.get("merchant_note"),
            "transaction": {"id": tx["id"], "reference": tx["reference"], "amount": tx["amount"], "currency": tx["currency"],
                            "authorization": tx.get("authorization")},
        }
        self.refunds[refund_id] = refund
        return self.reply(200, True, "Refund has been queued for processing", copy.deepcopy(refund))

    def set_refund_status(self, refund_id: str, status: str) -> dict:
        self.refunds[str(refund_id)]["status"] = status
        return copy.deepcopy(self.refunds[str(refund_id)])

    # ------------------------------------------------------------------ settlements

    def add_settlement(self, settlement_id: str, transactions: list, fees: int, currency: str = "NGN") -> dict:
        gross = sum(t["amount"] for t in transactions)
        payout = {"id": int(settlement_id), "status": "success", "currency": currency, "total_processed": gross,
                  "total_fees": fees, "total_amount": gross - fees, "effective_amount": gross - fees, "deductions": None,
                  "settlement_date": "2026-09-26T00:00:00.000Z"}
        rows = [{"id": t["id"], "reference": t["reference"], "amount": t["amount"], "fees": t.get("fees") or 0,
                 "currency": t["currency"], "status": "success"} for t in transactions]
        self.settlements[str(settlement_id)] = {"payout": payout, "transactions": rows}
        return copy.deepcopy(payout)


def sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()


def webhook(event: str, data: dict, secret: str) -> tuple:
    """Return (raw body, signature) for a Paystack webhook signed with secret."""
    body = json.dumps({"event": event, "data": data}).encode()
    return body, sign(body, secret)
