from decimal import Decimal

def is_balanced(entries):
    debits = sum((e[0] for e in entries if e[1] == "debit"), Decimal("0"))
    credits = sum((e[0] for e in entries if e[1] == "credit"), Decimal("0"))
    return debits == credits

def test_double_entry_invariant():
    assert is_balanced([(Decimal("12.34"), "debit"), (Decimal("12.34"), "credit")])
    assert not is_balanced([(Decimal("12.34"), "debit"), (Decimal("12.33"), "credit")])
