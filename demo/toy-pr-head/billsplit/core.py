def split_bill(total, people, tip_pct=0):
    """Split ``total`` evenly between ``people`` friends.

    ``tip_pct`` is added on top of the total, e.g. ``tip_pct=10`` for a 10% tip.
    """
    if people <= 0:
        return []
    total = total + total * tip_pct / 100
    share = round(total / people, 2)
    return [share] * people
