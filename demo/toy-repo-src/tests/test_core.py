from billsplit import split_bill


def test_even_split():
    assert split_bill(90, 3) == [30.0, 30.0, 30.0]
