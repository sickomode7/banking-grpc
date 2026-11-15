#!/usr/bin/env python3
"""Render four silent walkthrough videos plus a timestamped voice-over sheet.

Every terminal frame in the demo video comes from a real command run against
real servers: nothing on screen is hand-typed output. The narration text lives
in this file next to the scene it belongs to, so the cue sheet and the video
timings cannot drift apart.

Usage:
    python make_videos.py                # build all four into video/
    python make_videos.py --only 4       # build one
    python make_videos.py --preview      # write a few PNG stills, no encoding
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT = Path(__file__).resolve().parent
OUT = PROJECT / "video"
FPS = 24

W, H = 1920, 1080
TOP, BOT = 46, 34
MARGIN = 56
PAD = 24
CODE_SIZE, CODE_LINE_H = 19, 27
TERM_SIZE, TERM_LINE_H = 18, 25
MAX_CODE_LINES = 30
MAX_TERM_LINES = 32

BG = (13, 17, 23)
PANEL = (13, 18, 26)
CHROME = (22, 27, 34)
BORDER = (33, 38, 45)
FG = (201, 209, 217)
DIM = (110, 118, 129)
ACCENT = (88, 166, 255)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
YELLOW = (210, 153, 34)
PURPLE = (188, 140, 255)
CYAN = (86, 182, 194)

FONT_REG = "/usr/share/fonts/noto/NotoSansMono-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/noto/NotoSansMono-Bold.ttf"
_FONTS: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    return _FONTS[key]


F_CODE, F_TERM = font(CODE_SIZE), font(TERM_SIZE)
F_LABEL, F_SMALL = font(16, True), font(15)
F_H1, F_H2 = font(52, True), font(24)
CW_CODE = F_CODE.getlength("M")
CW_TERM = F_TERM.getlength("M")

PY_KW = (
    "def class return if elif else for while with try except raise import from "
    "as in not and or is None True False lambda pass break continue global "
    "nonlocal assert del finally yield match case self async await"
).split()
PROTO_KW = (
    "syntax package service rpc returns message repeated string double int32 "
    "int64 bool stream"
).split()
KEYWORDS = set(PY_KW) | set(PROTO_KW)

TOKEN_RE = re.compile(
    r"#[^\n]*|//[^\n]*"
    r'|"""[^"]*?"""|"[^"\n]*"|\'[^\'\n]*\''
    r"|\b(?:" + "|".join(sorted(KEYWORDS, key=len, reverse=True)) + r")\b"
    r"|\b\d+\.?\d*\b"
)


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------
def colour_line(text: str) -> list[tuple[str, tuple[int, int, int]]]:
    """Split a source line into coloured segments."""
    segs: list[tuple[str, tuple[int, int, int]]] = []
    pos = 0
    for m in TOKEN_RE.finditer(text):
        if m.start() > pos:
            segs.append((text[pos:m.start()], FG))
        tok = m.group(0)
        if tok.startswith(("#", "//")):
            col = DIM
        elif tok[0] in "\"'":
            col = GREEN
        elif tok[0].isdigit():
            col = YELLOW
        else:
            col = PURPLE
        segs.append((tok, col))
        pos = m.end()
    if pos < len(text):
        segs.append((text[pos:], FG))
    return segs or [("", FG)]


def term_colour(text: str) -> tuple[int, int, int]:
    if "RPC failed" in text:
        return RED
    if "COMPLETED" in text:
        return GREEN
    if text.startswith("──") or text.startswith("==="):
        return DIM
    if re.match(r"^\s*(?:[a-e]\)|\d\. )", text):
        return ACCENT
    if "WARNING" in text or "CRITICAL" in text:
        return YELLOW
    if text.strip().startswith(("INFO", "python", "account_server", "transaction_server")):
        return DIM
    return FG


def wrap(line: str, width_chars: int) -> list[str]:
    if len(line) <= width_chars:
        return [line]
    out, cur = [], ""
    for word in re.split(r"(?<= )", line):
        if len(cur) + len(word) > width_chars:
            out.append(cur)
            cur = word
        else:
            cur += word
    out.append(cur)
    return out


def speech_seconds(text: str) -> float:
    return 1.0 + len(text.split()) / 2.45 + 1.0


# --------------------------------------------------------------------------
# scene model
# --------------------------------------------------------------------------
@dataclass
class Scene:
    kind: str                       # "title" | "code" | "term"
    say: str = ""
    title: str = ""                 # title card text
    subtitle: str = ""
    bullets: list[str] = field(default_factory=list)
    file: str = ""
    start: str = ""
    end: str = ""
    focus: str = ""
    lines: list[tuple[int, str]] = field(default_factory=list)
    hl: tuple[int, int] = (0, -1)
    steps: list[tuple] = field(default_factory=list)
    duration: float = 0.0
    t0: float = 0.0

    # ---- preparation -----------------------------------------------------
    def prepare(self) -> None:
        if self.kind == "code":
            src = (PROJECT / self.file).read_text().splitlines()
            try:
                si = next(i for i, l in enumerate(src) if self.start in l)
            except StopIteration:
                raise SystemExit(f"{self.file}: no start match for {self.start!r}")
            try:
                ei = next(i for i, l in enumerate(src) if self.end in l and i >= si)
            except StopIteration:
                raise SystemExit(f"{self.file}: no end match for {self.end!r}")

            while ei + 1 < len(src) and src[ei + 1].strip() in (")", "),",
                                                             "}", "},", "]", "],"):
                ei += 1

            if ei - si + 1 > MAX_CODE_LINES:
                raise SystemExit(
                    f"{self.file}:{si+1}-{ei+1} is {ei-si+1} lines, "
                    f"over the {MAX_CODE_LINES}-line budget"
                )
            top = max(0, si - 9)
            top = min(top, max(0, len(src) - MAX_CODE_LINES))
            if ei >= top + MAX_CODE_LINES:
                top = max(0, ei - MAX_CODE_LINES + 1)
            window = range(top, min(len(src), top + MAX_CODE_LINES))
            self.lines = [(i + 1, src[i]) for i in window]
            self.hl = (si + 1, ei + 1)

        if self.kind == "term":
            captured: list[tuple] = []
            for step in self.steps:
                captured.append(step)
                if step[0] == "cmd":
                    captured.append(("out", sh(step[1])))
            self.steps = captured
            self.events, self.anim = build_events(self.steps)

        self.duration = max(getattr(self, "anim", 0.0) + 0.8,
                            speech_seconds(self.say), 3.0)

    # ---- drawing ---------------------------------------------------------
    def draw(self, t: float, video_title: str, index: str,
             offset: float = 0.0) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, TOP], fill=CHROME)
        for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            cx = 26 + i * 22
            d.ellipse([cx - 7, 16, cx + 7, 30], fill=c)
        d.text((110, 14), video_title, font=F_LABEL, fill=ACCENT)
        if self.focus:
            d.text((W - MARGIN - F_LABEL.getlength(self.focus), 14),
                   self.focus, font=F_LABEL, fill=DIM)

        if self.kind == "title":
            self._draw_title(d)
        elif self.kind == "code":
            self._draw_code(d, t)
        else:
            self._draw_term(d, t)

        # bottom bar
        d.rectangle([0, H - BOT, W, H], fill=CHROME)
        d.text((MARGIN, H - BOT + 9), "banking-grpc", font=F_SMALL, fill=DIM)
        now = t + offset
        clock = f"{int(now // 60):02d}:{int(now % 60):02d}"
        d.text((W - MARGIN - F_SMALL.getlength(clock), H - BOT + 9),
               clock, font=F_SMALL, fill=DIM)
        d.text((W // 2 - F_SMALL.getlength(index) // 2, H - BOT + 9),
               index, font=F_SMALL, fill=DIM)
        return img

    def _draw_title(self, d: ImageDraw.ImageDraw) -> None:
        d.rectangle([MARGIN, 300, MARGIN + 90, 306], fill=ACCENT)
        d.text((MARGIN, 340), self.title, font=F_H1, fill=FG)
        d.text((MARGIN, 420), self.subtitle, font=F_H2, fill=ACCENT)
        y = 510
        for b in self.bullets:
            d.text((MARGIN + 8, y), "· " + b, font=F_H2, fill=DIM)
            y += 46

    def _draw_code(self, d: ImageDraw.ImageDraw, t: float) -> None:
        hl_from, hl_to = self.hl
        revealed = min(1.0, t / 0.35)
        d.text((MARGIN, TOP + 16), self.file, font=F_LABEL, fill=ACCENT)
        d.text((MARGIN, TOP + 40),
               f"lines {hl_from}–{hl_to}", font=F_SMALL, fill=DIM)

        y = TOP + 74
        for no, text in self.lines:
            inside = hl_from <= no <= hl_to
            if inside:
                d.rectangle([MARGIN - 12, y - 3, W - MARGIN, y + CODE_LINE_H - 2],
                            fill=(22, 29, 39) if revealed > 0.5 else BG)
                d.rectangle([MARGIN - 12, y - 3, MARGIN - 8, y + CODE_LINE_H - 2],
                            fill=ACCENT)
            num = f"{no:>3}"
            d.text((MARGIN + 6, y), num, font=F_CODE,
                   fill=DIM if not inside else (139, 148, 158))
            x = MARGIN + 6 + CW_CODE * 4
            for seg_text, seg_col in colour_line(text):
                draw_col = seg_col if inside or seg_col is not FG else DIM
                d.text((x, y), seg_text, font=F_CODE, fill=draw_col)
                x += F_CODE.getlength(seg_text)
            y += CODE_LINE_H

    def _draw_term(self, d: ImageDraw.ImageDraw, t: float) -> None:
        usable = int((W - 2 * MARGIN - 2 * PAD) / CW_TERM)
        rows: list[list[tuple[str, tuple[int, int, int]]]] = []

        for ev in self.events:
            if ev[0] == "cmd":
                _, cmd_text, start, dur = ev
                if t < start:
                    continue
                progress = min(1.0, (t - start) / dur)
                shown = cmd_text[: int(progress * len(cmd_text)) + 1]
                rows.append([("(venv) ", GREEN), ("$ ", GREEN), (shown, FG)])
            else:
                _, line, t_line = ev
                if t < t_line:
                    continue
                col = term_colour(line)
                for piece in wrap(line, usable):
                    rows.append([(piece, col)])

        rows = rows[-MAX_TERM_LINES:]

        top = TOP + 12
        height = min(H - BOT - 16 - top, 58 + len(rows) * TERM_LINE_H + 16)
        d.rounded_rectangle([MARGIN - 12, top, W - MARGIN + 12, top + height],
                            radius=12, fill=PANEL, outline=BORDER)
        d.text((MARGIN + 12, top + 14), self.title, font=F_SMALL, fill=DIM)
        d.line([MARGIN + 12, top + 38, W - MARGIN - 12, top + 38], fill=BORDER)

        y = top + 50
        for r in rows:
            x = MARGIN + PAD - 4
            for seg, col in r:
                d.text((x, y), seg, font=F_TERM, fill=col)
                x += F_TERM.getlength(seg)
            y += TERM_LINE_H


# --------------------------------------------------------------------------
# terminal event timeline
# --------------------------------------------------------------------------
def build_events(steps: list[tuple]) -> tuple[list[tuple], float]:
    events: list[tuple] = []
    t = 0.4
    for step in steps:
        kind = step[0]
        if kind == "cmd":
            text = step[1]
            dur = 0.55 + len(text) * 0.028
            events.append(("cmd", text, t, dur))
            t += dur + 0.25
        elif kind == "out":
            for line in step[1].splitlines():
                events.append(("line", line, t))
                t += 0.035 if line.strip() else 0.015
            t += 0.35
        elif kind == "pause":
            t += step[1]
    return events, t


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------
def sh(cmd: str, activate: bool = True) -> str:
    prefix = "source .venv/bin/activate && " if activate else ""
    proc = subprocess.run(["bash", "-lc", prefix + cmd], cwd=PROJECT,
                          capture_output=True, text=True)
    out = (proc.stdout + proc.stderr).rstrip("\n")
    keep = [l for l in out.splitlines() if "RuntimeWarning" not in l]
    return "\n".join(keep)


def restart_servers() -> None:
    subprocess.run("kill $(cat logs/*.pid) 2>/dev/null || true", shell=True,
                   cwd=PROJECT)
    subprocess.run("sleep 0.4", shell=True, cwd=PROJECT)
    sh("./run.sh", activate=False)


# --------------------------------------------------------------------------
# the four videos
# --------------------------------------------------------------------------
def videos() -> list[dict]:
    def t(title, subtitle, bullets, say=""):
        return Scene(kind="title", title=title, subtitle=subtitle,
                     bullets=list(bullets), say=say)

    v1 = {
        "slug": "01-proto",
        "title": "1 · banking.proto — the contract",
        "scenes": [
            t("Distributed Banking System", "gRPC + Protocol Buffers",
              ["Account Management Service  ·  :50051",
               "Transaction Service  ·  :50052",
               "banking.proto  ·  account_server.py  ·  transaction_server.py",
               "client.py  ·  test_client.py"],
              say="This is a distributed banking system built with gRPC and "
                  "Protocol Buffers: an account service on port 50051, a "
                  "transaction service on 50052 that calls it, a client, and a "
                  "test suite. This first recording covers the proto contract."),
            Scene(kind="code", file="banking.proto",
                  start="syntax =", end="package banking;",
                  focus="proto3 + package",
                  say="This is the contract. gRPC generates both the client stub "
                      "and the server base class from this file, so the two sides "
                      "cannot disagree about the wire format. Proto 3 is the "
                      "current version, and the package becomes the namespace: "
                      "banking.AccountService is the name the server registers "
                      "and the client resolves."),
            Scene(kind="code", file="banking.proto",
                  start="service AccountService", end="UpdateBalance (UpdateBalanceRequest) returns (BalanceResponse);",
                  focus="AccountService",
                  say="The assignment asks for getBalance and updateBalance. I "
                      "kept those names but capitalised them as PascalCase, "
                      "which the Protobuf style guide requires and the generated "
                      "Python stubs follow. GetBalance is a read. UpdateBalance "
                      "takes a signed amount: positive credits, negative debits, "
                      "and a reason field for the audit log."),
            Scene(kind="code", file="banking.proto",
                  start="message BalanceRequest", end="string currency = 3;",
                  focus="account messages",
                  say="Every message here is a request and response pair for one "
                      "method. The numbers after each field, equals one, equals "
                      "two, are not ordering. They are the field's identity on "
                      "the wire and must never be reused once deployed, because "
                      "an old client would decode the wrong bytes."),
            Scene(kind="code", file="banking.proto",
                  start="message UpdateBalanceRequest", end="string reason  = 3;",
                  focus="signed delta",
                  say="The delta is the interesting field. Minus fifty debits, "
                      "plus fifty credits, and the reason travels with it so the "
                      "server log can say whether money moved because of a "
                      "transfer or a manual adjustment."),
            Scene(kind="code", file="banking.proto",
                  start="service TransactionService", end="rpc GetTransactionHistory",
                  focus="TransactionService",
                  say="The second service orchestrates transfers and owns "
                      "history. InitiateTransfer answers with a transaction id, "
                      "a status, and both new balances, so the caller does not "
                      "need a follow-up read to know what happened."),
            Scene(kind="code", file="banking.proto",
                  start="message TransferRequest", end="string note         = 4;",
                  focus="no balances in the request",
                  say="Notice what is missing: no balances. Money is not passed "
                      "around in messages. The transaction service asks the "
                      "account service for balances, and only the account service "
                      "ever writes them. That separation is the design."),
            Scene(kind="code", file="banking.proto",
                  start="message TransactionRecord", end="= 7;",
                  focus="history entry",
                  say="A history row stores direction in or out, the counterparty, "
                      "the amount, and balance_after. That last field is the "
                      "useful one: it lets a statement be reconstructed without "
                      "replaying every transaction from the beginning of time."),
            Scene(kind="code", file="banking.proto",
                  start="message HistoryResponse", end="int32  count = 3;",
                  focus="repeated + count",
                  say="History comes back as a repeated field, which is a list on "
                      "the wire, plus a count so a client can page without "
                      "counting the list itself. The request takes a limit where "
                      "zero means the server default of ten, and the server caps "
                      "it at a hundred."),
            Scene(kind="term", title="generating the stubs",
                  steps=[("cmd", "python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. banking.proto"),
                         ("out", "")],
                  say="One protoc run produces two files. banking_pb2 holds the "
                      "messages, banking_pb2_grpc holds the service stubs. They "
                      "are generated, so I never edit them by hand."),
            Scene(kind="term", title="generated files",
                  steps=[("cmd", "ls -l banking_pb2*.py"),
                         ("cmd", "head -20 banking_pb2_grpc.py"),
                         ("pause", 1.0)],
                  say="Both are committed so a clone runs immediately, and the "
                      "README still shows the command that regenerates them."),
        ],
    }

    v2 = {
        "slug": "02-server",
        "title": "2 · server side — account and transaction services",
        "scenes": [
            t("Server side", "two processes, one RPC chain",
              ["account_server.py  ·  :50051  ·  owns balances",
               "transaction_server.py  ·  :50052  ·  owns transfers and history",
               "thread pool  ·  one lock  ·  compensating rollback"],
              say="Now the server side: two processes and one RPC chain. The "
                  "account service owns balances, the transaction service owns "
                  "transfers and history, and it calls the account service for "
                  "every balance it needs."),
            Scene(kind="code", file="account_server.py",
                  start="CURRENCY =", end="SEED_BALANCES = ",
                  focus="account_server.py",
                  say="The account service owns balances and nothing else. It has "
                      "no idea what a transfer is, which is why it can be tested, "
                      "scaled and replaced on its own. Three seeded accounts: "
                      "alice, bob and carol. A real deployment swaps this dict for "
                      "a database behind the same interface."),
            Scene(kind="code", file="account_server.py",
                  start="class AccountStore", end="return self._balances.get(user_id)",
                  focus="the lock",
                  say="This is the part that matters. gRPC serves requests on a "
                      "thread pool, so two transfers can be in flight at once. A "
                      "read, a compare and a write is not atomic, so both could "
                      "read the same balance, both pass the funds check, and the "
                      "account ends up overdrawn. The lock around the whole "
                      "operation makes that impossible."),
            Scene(kind="code", file="account_server.py",
                  start="def apply_delta", end="return new_balance",
                  focus="last line of defence",
                  say="The store is the last line of defence: it refuses any "
                      "update that would take the balance below zero, and it "
                      "raises rather than returning a magic value. The service "
                      "layer turns that raise into a status code. There is a test "
                      "that fires fifty parallel credits and asserts the balance "
                      "grew by exactly fifty."),
            Scene(kind="code", file="account_server.py",
                  start="def GetBalance", end="account '{user_id}' does not exist",
                  focus="validation → NOT_FOUND",
                  say="Validation first: an empty user id is INVALID_ARGUMENT, not "
                      "a lookup. An unknown account is NOT_FOUND. context.abort "
                      "sends that code and the detail string, and on the client "
                      "side the same call raises an error carrying both."),
            Scene(kind="code", file="account_server.py",
                  start="def UpdateBalance", end="amount must be a non-zero, finite number",
                  focus="isfinite",
                  say="math.isfinite catches NaN and infinity, which is not "
                      "paranoia: one NaN in a balance poisons every arithmetic "
                      "result after it. A zero delta is rejected too, because it "
                      "is a no-op that would only pollute the audit log."),
            Scene(kind="code", file="account_server.py",
                  start="new_balance = self.store.apply_delta", end="insufficient funds: {exc}",
                  focus="exceptions → status codes",
                  say="Three outcomes, three codes. It worked. The account does "
                      "not exist, NOT_FOUND. Or the balance would go negative, "
                      "FAILED_PRECONDITION. A client can branch on those without "
                      "parsing text."),
            Scene(kind="code", file="transaction_server.py",
                  start="class TransactionService", end="defaultdict(list)",
                  focus="transaction_server.py",
                  say="The transaction service holds no balances, only history, "
                      "and one channel created once and reused. Creating a channel "
                      "per request would redo the HTTP/2 handshake every time. "
                      "This process is now both a server and a client."),
            Scene(kind="code", file="transaction_server.py",
                  start="def _balance", end="return reply.balance",
                  focus="error translation",
                  say="When the account service says NOT_FOUND, this re-raises "
                      "exactly that code to our caller. Errors keep their meaning "
                      "all the way through the chain instead of being flattened "
                      "into INTERNAL."),
            Scene(kind="code", file="transaction_server.py",
                  start="def InitiateTransfer", end="cannot transfer to the same account",
                  focus="validate first",
                  say="One: validate. Both ids present, not the same account, "
                      "amount positive and finite. All of that happens before a "
                      "single balance is read, so bad input costs nothing."),
            Scene(kind="code", file="transaction_server.py",
                  start="if not math.isfinite(amount)", end="requested {amount:.2f}",
                  focus="existence, then funds",
                  say="Two: both accounts must exist, so it asks the account "
                      "service rather than assuming, and a NOT_FOUND from there "
                      "comes straight back to the caller. Three: read the sender's "
                      "balance and compare it before moving anything. Not enough "
                      "means FAILED_PRECONDITION and no state change at all."),
            Scene(kind="code", file="transaction_server.py",
                  start="debited = self._update", end="exc.code(), exc.details()",
                  focus="leg one: debit",
                  say="Then leg one: debit the sender. If that fails there is "
                      "nothing to undo, so the error goes straight back."),
            Scene(kind="code", file="transaction_server.py",
                  start="credited = self._update", end="debit rolled back",
                  focus="leg two + rollback",
                  say="Leg two credits the receiver. Here is the honest part of a "
                      "distributed system: these are two separate RPCs on two "
                      "different keys, and no transaction spans them. If the "
                      "credit fails, money has vanished. So I debit again with the "
                      "opposite amount to put it back, then report the original "
                      "error. If the rollback itself fails I log it as critical "
                      "with the amount that is missing, because a silent "
                      "inconsistency is worse than a loud one."),
            Scene(kind="code", file="transaction_server.py",
                  start="def _record", end="newest first",
                  focus="two rows, one id",
                  say="One transfer writes two history rows sharing one "
                      "transaction id: OUT for the sender, IN for the receiver. "
                      "Each row keeps balance_after from the response, so the "
                      "statement is self-consistent. Newest first."),
            Scene(kind="code", file="transaction_server.py",
                  start="def GetTransactionHistory", end="limit = min(limit, MAX_HISTORY_LIMIT)",
                  focus="existence, then limit",
                  say="History confirms the account exists by asking the account "
                      "service, so a real account with no activity returns an "
                      "empty list rather than NOT_FOUND. Then it applies the limit "
                      "with a hard cap of a hundred."),
            Scene(kind="code", file="transaction_server.py",
                  start="def serve", end="return server",
                  focus="thread pool, plaintext port",
                  say="A thread pool of ten on a plaintext port. In production "
                      "this is add_secure_port with TLS credentials and the client "
                      "uses a secure channel. I kept it plaintext so the demo is "
                      "reproducible, and I list that as a limitation."),
            Scene(kind="term", title="account service log — one transfer, four RPCs",
                  steps=[("cmd", "tail -n 14 logs/account.log"), ("pause", 0.5)],
                  say="This is the account log after one transfer: two balance "
                      "reads, then a debit, then a credit. Four RPCs on this "
                      "service for a single client call."),
            Scene(kind="term", title="transaction service log — the orchestration",
                  steps=[("cmd", "tail -n 8 logs/transaction.log"), ("pause", 0.5)],
                  say="And this is the transaction log: the transfer id, the "
                      "direction of money, the resulting balances, and history "
                      "reads. Reading the two logs side by side is how you debug a "
                      "distributed call, because one transfer appears in both."),
        ],
    }

    v3 = {
        "slug": "03-client",
        "title": "3 · client side",
        "scenes": [
            t("Client side", "one CLI, two channels",
              ["balance and update  →  :50051",
               "transfer and history  →  :50052",
               "delegates  ·  deadlines  ·  status codes"],
              say="The client: one CLI, two channels. Balance commands go to the "
                  "account service, transfers and history go to the transaction "
                  "service. Then the test suite that keeps all of this honest."),
            Scene(kind="code", file="client.py",
                  start="account_channel =", end="transactions = pb_grpc.TransactionServiceStub",
                  focus="two channels",
                  say="The client is a thin CLI with two channels, because it "
                      "talks to two services. Balance commands go straight to the "
                      "account service, transfers and history go to the "
                      "transaction service. That mirrors how a real client would "
                      "address separate backends."),
            Scene(kind="code", file="client.py",
                  start="TIMEOUT = 5.0", end="TIMEOUT = 5.0",
                  focus="deadline on every call",
                  say="Every call passes a deadline. Without it, a hung server "
                      "blocks the client thread forever and the user just sees a "
                      "frozen terminal."),
            Scene(kind="code", file="client.py",
                  start="def rpc_error", end="exc.details()",
                  focus="code plus detail",
                  say="Errors print the status code and the server's detail "
                      "string. The code is what a program branches on, the detail "
                      "is what a human reads. In the demo you will see "
                      "FAILED_PRECONDITION, NOT_FOUND and INVALID_ARGUMENT telling "
                      "three different stories."),
            Scene(kind="code", file="client.py",
                  start="def show_history", end="balance_after:>10,.2f}  {t.note}",
                  focus="statement, not a debug dump",
                  say="History renders direction as a sign: out is minus, in is "
                      "plus, and balance_after sits next to the amount. So the "
                      "table reads like a bank statement instead of a debug dump."),
            Scene(kind="code", file="client.py",
                  start="def cmd_demo", end="cmd_history(\"alice\")",
                  focus="scripted scenario",
                  say="The demo command is a scripted version of the whole run: "
                      "happy path, then every error case, then re-read the "
                      "balances to prove the rejected calls changed nothing. I "
                      "scripted it so the recording is reproducible and I am not "
                      "typing live."),
            Scene(kind="term", title="test suite",
                  steps=[("cmd", "python -m unittest test_client.py"),
                         ("pause", 1.0)],
                  say="Ten tests, and they spin both services up in process on "
                      "ephemeral ports, so they do not need the servers running. "
                      "They cover the seeded balance, a signed delta, a transfer "
                      "conserving total money, insufficient funds leaving balances "
                      "untouched, unknown account, four invalid argument cases, "
                      "history ordering and limit, empty history, a failed credit "
                      "rolling back the debit, and fifty concurrent updates."),
            Scene(kind="term", title="verbose list of the ten checks",
                  steps=[("cmd", "python -m unittest -v test_client.py 2>&1 | sed -n '1,24p'"),
                         ("pause", 0.8)],
                  say="Green across the board, and the rollback test is the one I "
                      "would point a reviewer at, because it exercises the failure "
                      "path rather than the happy one."),
        ],
    }

    v4 = {
        "slug": "04-demo",
        "title": "4 · live demo",
        "scenes": [
            t("Live demo", "both services, real money movement, real errors",
              ["start the services on :50051 and :50052",
               "check balances, transfer, verify history",
               "five rejected calls, then prove nothing changed"],
              say="And the live demo: both services starting from scratch, a real "
                  "transfer, the history it produces, five calls that get "
                  "rejected, and proof that the rejected calls changed nothing."),
            Scene(kind="term", title="starting both services",
                  steps=[("cmd", "source .venv/bin/activate"),
                         ("cmd", "./run.sh"), ("pause", 0.8)],
                  say="Two processes, two ports. The account service owns the "
                      "balances, the transaction service owns transfers and "
                      "history. Both are now listening."),
            Scene(kind="term", title="starting balances",
                  steps=[("cmd", "python client.py balance alice"),
                         ("cmd", "python client.py balance bob")],
                  say="Alice has a thousand, bob two fifty fifty. These are plain "
                      "GetBalance calls straight to the account service on 50051, "
                      "and you can see them arrive in the account log."),
            Scene(kind="term", title="transfer",
                  steps=[("cmd", "python client.py transfer alice bob 200 --note \"rent\""),
                         ("pause", 0.8)],
                  say="One call to the transaction service on 50052. Behind it: "
                      "read both balances, debit alice, credit bob, then write two "
                      "history rows. Four RPCs on the account service for one "
                      "client call. The response carries the transaction id, the "
                      "status and both new balances, so no follow-up read is "
                      "needed."),
            Scene(kind="term", title="verify balances",
                  steps=[("cmd", "python client.py balance alice"),
                         ("cmd", "python client.py balance bob")],
                  say="Alice is down to eight hundred, bob up to four fifty "
                      "fifty. The total is unchanged: a transfer moves value, it "
                      "does not create it."),
            Scene(kind="term", title="history, both sides",
                  steps=[("cmd", "python client.py history alice"),
                         ("cmd", "python client.py history bob")],
                  say="One transaction id, two histories. Alice's row says out, "
                      "bob's says in, and balance_after matches what GetBalance "
                      "just reported. That is the consistency check I care about."),
            Scene(kind="term", title="error handling — insufficient funds, unknown account",
                  steps=[("cmd", "python client.py transfer bob alice 5000 --note \"too much\""),
                         ("cmd", "python client.py balance mallory")],
                  say="FAILED_PRECONDITION for insufficient funds, with the actual "
                      "balance against the requested amount in the detail. "
                      "NOT_FOUND for an account that does not exist. Different "
                      "codes, because they are different failures."),
            Scene(kind="term", title="error handling — invalid input",
                  steps=[("cmd", "python client.py transfer alice alice 10"),
                         ("cmd", "python client.py transfer alice bob -50")],
                  say="INVALID_ARGUMENT for a self transfer, caught before any "
                      "balance is touched. And a negative amount is rejected too: "
                      "a negative transfer is a disguised withdrawal, and "
                      "withdrawals belong on UpdateBalance."),
            Scene(kind="term", title="error handling — the store's floor",
                  steps=[("cmd", "python client.py update carol -1"),
                         ("cmd", "python client.py balance carol")],
                  say="Carol has zero, and the store refuses to go below zero. "
                      "Then a plain read confirms carol is still at zero: the "
                      "failed call changed nothing."),
            Scene(kind="term", title="nothing changed, and the log trail",
                  steps=[("cmd", "python client.py balance alice"),
                         ("cmd", "python client.py history alice --limit 2"),
                         ("cmd", "tail -n 5 logs/transaction.log"), ("pause", 0.8)],
                  say="Final state: one recorded transfer, all the rejected calls "
                      "absent from history, and the transaction log showing the "
                      "same id. That is the whole failure story in one screen."),
            t("What I would defend in a review", "and what is honestly missing",
              ["status codes, not strings in a body",
               "one writer per balance, guarded by a lock",
               "compensating rollback on a failed credit",
               "deadline on every outbound call",
               "missing: persistence, TLS, idempotency keys, int64 money"],
              say="What I would defend: status codes instead of strings in a "
                  "body, one writer per balance guarded by a lock, a "
                  "compensating rollback when the credit leg fails, and a "
                  "deadline on every outbound call. What is honestly missing: "
                  "persistence, TLS, idempotency keys, and integer money "
                  "instead of doubles."),
        ],
    }
    return [v1, v2, v3, v4]


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------
def ffmpeg_cmd(path: Path) -> list[str]:
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(path),
    ]


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def cue_chunks(say: str, width: int = 88) -> list[str]:
    """Split narration into subtitle-sized cues, breaking at commas first."""
    chunks: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", say.strip()):
        if len(sentence) <= width:
            chunks.append(sentence)
            continue
        cur = ""
        for part in re.split(r"(?<=,)\s+", sentence):
            candidate = (cur + " " + part).strip()
            if cur and len(candidate) > width:
                chunks.append(cur)
                cur = part
            else:
                cur = candidate
        if cur:
            chunks.append(cur)
    return [c.strip() for c in chunks if c.strip()]


def write_cues(slug: str, scenes: list[Scene]) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for s in scenes:
        if not s.say.strip():
            continue
        chunks = cue_chunks(s.say)
        words = [len(c.split()) for c in chunks]
        span = s.duration - 0.3
        cursor = s.t0 + 0.15
        for c, w in zip(chunks, words):
            dur = max(1.2, span * w / max(1, sum(words)))
            cues.append((cursor, cursor + dur, c))
            cursor += dur

    (OUT / f"{slug}.srt").write_text(
        "".join(
            f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n\n"
            for i, (a, b, text) in enumerate(cues, 1)
        )
    )
    return cues


def build(video: dict, only_preview: bool = False,
          cues_only: bool = False) -> None:
    slug, title = video["slug"], video["title"]
    scenes: list[Scene] = video["scenes"]
    for s in scenes:
        s.prepare()

    total = sum(s.duration for s in scenes)
    t0 = 0.0
    for s in scenes:
        s.t0 = t0
        t0 += s.duration

    mp4 = OUT / f"{slug}.mp4"
    cues = write_cues(slug, scenes)

    if cues_only:
        print(f"{slug}: cues only, {total:.1f}s, {len(cues)} cues")
        return {"slug": slug, "title": title, "duration": total,
                "scenes": scenes, "cues": cues}

    if only_preview:
        for idx in (0, len(scenes) // 2, len(scenes) - 1):
            s = scenes[idx]
            s.draw(s.duration * 0.7, title, f"{idx + 1}/{len(scenes)}").save(
                OUT / f"preview-{slug}-{idx}.png"
            )
        print(f"{slug}: {len(scenes)} scenes, {total:.1f}s, cues={len(cues)}")
        return

    proc = subprocess.Popen(ffmpeg_cmd(mp4), stdin=subprocess.PIPE)
    assert proc.stdin
    done = 0
    total_frames = int(total * FPS)
    for i, s in enumerate(scenes):
        frames = max(1, int(s.duration * FPS))
        index = f"{i + 1}/{len(scenes)}"
        for f in range(frames):
            img = s.draw(f / FPS, title, index, offset=s.t0)
            proc.stdin.write(img.tobytes())
            done += 1
        if done % (FPS * 10) < frames:
            print(f"\r  {slug}: {done}/{total_frames} frames", end="", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg failed for {slug}")
    print(f"\r  {slug}: {total:.1f}s, {total_frames} frames -> {mp4.name}")
    return {"slug": slug, "title": title, "duration": total, "scenes": scenes,
            "cues": cues}


def cue_sheet(built: list[dict]) -> str:
    lines = [
        "# Voice-over cue sheet",
        "",
        "Four silent videos, one per deliverable. Read the text under each",
        "timestamp; the video holds long enough for that paragraph at a calm",
        "pace. Times are the start of each cue.",
        "",
        "Mux your recorded audio onto the silent video without re-encoding:",
        "",
        "```bash",
        "ffmpeg -i video/01-proto.mp4 -i 01-proto-voice.m4a \\",
        "       -c:v copy -c:a aac -shortest video/01-proto-final.mp4",
        "```",
        "",
        "`video/01-proto.srt` holds the same text as subtitles if you want them",
        "burned in:",
        "",
        "```bash",
        "ffmpeg -i 01-proto-final.mp4 -vf \"subtitles=video/01-proto.srt\" out.mp4",
        "```",
        "",
    ]
    for v in built:
        lines.append(f"## {v['title']}")
        lines.append("")
        lines.append(f"File `video/{v['slug']}.mp4` — {v['duration']:.1f}s")
        lines.append("")
        for s in v["scenes"]:
            mm, ss = divmod(int(s.t0), 60)
            lines.append(f"**{mm:02d}:{ss:02d}** — {s.say}")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, choices=(1, 2, 3, 4))
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--cues-only", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not found")

    if not args.preview:
        print("preparing logs for the server walkthrough…")
        restart_servers()
        for cmd in ('python client.py transfer alice bob 200 --note "rent"',
                    "python client.py history alice",
                    "python client.py balance bob",
                    'python client.py transfer bob carol 25 --note "split"'):
            sh(cmd)
        print("  logs ready\n")

    vids = videos()
    if args.only:
        vids = [vids[args.only - 1]]

    built = []
    for v in vids:
        if v["slug"] == "04-demo":
            # the demo's own ./run.sh must be the thing that starts the servers
            subprocess.run("kill $(cat logs/*.pid) 2>/dev/null || true",
                           shell=True, cwd=PROJECT)
            subprocess.run("sleep 0.8", shell=True, cwd=PROJECT)
        print(f"building {v['slug']}")
        info = build(v, only_preview=args.preview,
                     cues_only=args.cues_only)
        if info:
            built.append(info)

    if built:
        (OUT / "VOICEOVER.md").write_text(cue_sheet(built))
        (OUT / "manifest.json").write_text(json.dumps(
            [{"slug": b["slug"], "title": b["title"],
              "duration": round(b["duration"], 2)} for b in built], indent=2))
        print("\nwrote video/VOICEOVER.md and video/*.srt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
