"""Tests for v6_sentinel. Standard-library unittest only -- nothing to install.

Run everything from the repo root:

    python -m unittest discover -s tests -t . -v

These tests never touch a real broker: anything that could place an order
uses a fake broker AND booby-traps MetaTrader5.order_send so it raises if it
is ever reached. They also never read or write the real v6s_* state files
(each test builds its own temp files).
"""
