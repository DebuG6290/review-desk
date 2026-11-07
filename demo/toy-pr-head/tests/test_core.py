from billsplit import split_bill


def test_even_split():
    assert split_bill(90, 3) == [30.0, 30.0, 30.0]


def test_tip():
    assert split_bill(100, 2, tip_pct=10) == [55.0, 55.0]
